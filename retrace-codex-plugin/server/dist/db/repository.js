import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import Database from "better-sqlite3";
import { RawHookEventSchema, normalizeRawHookEvent } from "../schemas/events.js";
import { evaluateStall, IssueChainSchema, StallAssessmentSchema, TurnClassificationSchema } from "../schemas/stall.js";
import { M3InputSchema, PreSurveyResponseSchema, PublicReentryContextSchema, ReentryStateSchema, ReentrySnapshotSchema } from "../schemas/reentry.js";
import { ContextItemTypeSchema, ContextReviewSchema, ReconstructionSchema, ReviewActionSchema, validateReconstructionProvenance } from "../schemas/context.js";
import { buildDeterministicReconstruction } from "../runtime/context-reconstructor.js";
import { InvestigationSchema, InvestigationResultSchema, NextPromptDraftSchema, PRE_SURVEY_QUESTIONS, PRE_SURVEY_QUESTION_SET_VERSION, ResumeCompletionReasonSchema } from "../schemas/m3c.js";
import { buildInvestigation, buildNextPromptDraft, buildReviewedState } from "../runtime/m3c-runtime.js";
export class Repository {
    config;
    db;
    insertEventStatement;
    nextSequenceStatement;
    constructor(config, db = new Database(config.dbPath)) {
        this.config = config;
        this.db = db;
        this.db.pragma("journal_mode = WAL");
        this.db.pragma("foreign_keys = ON");
        const schemaPath = join(dirname(fileURLToPath(import.meta.url)), "schema.sql");
        this.db.exec(readFileSync(schemaPath, "utf8"));
        for (const statement of [
            "ALTER TABLE reentry_runs ADD COLUMN trigger_event_id TEXT",
            "ALTER TABLE reentry_runs ADD COLUMN completion_reason TEXT",
            "ALTER TABLE reentry_runs ADD COLUMN completed_by_action TEXT",
            "ALTER TABLE prompt_drafts ADD COLUMN edited_at TEXT",
            "ALTER TABLE prompt_drafts ADD COLUMN sent_at TEXT",
            "ALTER TABLE prompt_drafts ADD COLUMN review_version INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE prompt_drafts ADD COLUMN status TEXT NOT NULL DEFAULT 'GENERATED'",
        ]) {
            try {
                this.db.exec(statement);
            }
            catch (error) {
                if (!(error instanceof Error) || !error.message.includes("duplicate column name"))
                    throw error;
            }
        }
        this.insertEventStatement = this.db.prepare(`
      INSERT OR IGNORE INTO raw_events
        (event_id, session_id, turn_id, tool_use_id, hook_event_name, event_type,
         source, observed_at, collector_seq, payload_hash, dedupe_key,
         content_text, content_json, raw_payload_json)
      VALUES
        (@eventId, @sessionId, @turnId, @toolUseId, @hookEventName, @eventType,
         @source, @observedAt, @collectorSeq, @payloadHash, @dedupeKey,
         @contentText, @contentJson, @rawPayloadJson)
    `);
        this.nextSequenceStatement = this.db.prepare("SELECT COALESCE(MAX(collector_seq), 0) + 1 AS next_seq FROM raw_events WHERE session_id = ?");
    }
    ingestRawEvent(rawInput) {
        const raw = RawHookEventSchema.parse(rawInput);
        const transaction = this.db.transaction(() => {
            const nextSeq = this.nextSequenceStatement.get(raw.session_id).next_seq;
            const normalized = normalizeRawHookEvent(raw, this.config.participantId, nextSeq);
            const result = this.insertEventStatement.run({
                ...normalized,
                hookEventName: raw.hook_event_name,
                contentText: normalized.contentText ?? null,
                contentJson: normalized.contentJson === undefined ? null : JSON.stringify(normalized.contentJson),
                rawPayloadJson: JSON.stringify(raw),
            });
            if (result.changes === 1)
                this.upsertSession(normalized);
            const canonical = result.changes === 1
                ? normalized.eventId
                : this.db.prepare("SELECT event_id FROM raw_events WHERE dedupe_key = ?").get(normalized.dedupeKey).event_id;
            return {
                inserted: result.changes === 1,
                duplicate: result.changes === 0,
                normalized,
                canonicalEventId: canonical,
            };
        });
        return transaction();
    }
    assessUserPrompt(rawInput, classification) {
        const ingestion = this.ingestRawEvent(rawInput);
        const existingClassification = this.db.prepare("SELECT output_json FROM turn_classifications WHERE event_id = ? ORDER BY created_at DESC LIMIT 1").get(ingestion.canonicalEventId);
        if (existingClassification) {
            const existingAssessment = this.db.prepare("SELECT * FROM stall_assessments WHERE as_of_event_id = ? ORDER BY created_at DESC LIMIT 1").get(ingestion.canonicalEventId);
            const existingChain = this.getIssueChainById(existingAssessment?.issue_chain_id);
            if (existingAssessment && existingChain) {
                return {
                    classification: TurnClassificationSchema.parse(JSON.parse(existingClassification.output_json)),
                    issueChain: existingChain,
                    stallAssessment: rowToStallAssessment(existingAssessment),
                    reentryRunId: this.getActiveRun(rawInput.session_id)?.reentry_run_id,
                    shouldBlock: Boolean(existingAssessment.eligible),
                };
            }
        }
        const activeIssueChain = this.getActiveIssueChain(rawInput.session_id);
        const userTurnIndex = this.countUserTurns(rawInput.session_id);
        const detection = evaluateStall({
            sessionId: rawInput.session_id,
            eventId: ingestion.canonicalEventId,
            userTurnIndex,
            classification,
            activeIssueChain,
            activeReentry: Boolean(this.getActiveRun(rawInput.session_id)),
        });
        const now = new Date().toISOString();
        if (detection.previousIssueChainId) {
            this.db.prepare("UPDATE issue_chains SET status = 'CLOSED', updated_at = ? WHERE issue_chain_id = ?")
                .run(now, detection.previousIssueChainId);
        }
        this.db.prepare(`
      INSERT INTO issue_chains
        (issue_chain_id, session_id, issue_key, issue_summary, status, unmet_report_count,
         cooldown_until_user_turn, first_event_id, last_event_id, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(issue_chain_id) DO UPDATE SET
        issue_summary = excluded.issue_summary,
        status = excluded.status,
        unmet_report_count = excluded.unmet_report_count,
        cooldown_until_user_turn = excluded.cooldown_until_user_turn,
        last_event_id = excluded.last_event_id,
        updated_at = excluded.updated_at
    `).run(detection.issueChain.issueChainId, detection.issueChain.sessionId, detection.issueChain.issueKey, detection.issueChain.issueSummary, detection.issueChain.status, detection.issueChain.unmetReportCount, detection.issueChain.cooldownUntilUserTurn, detection.issueChain.firstEventId, detection.issueChain.lastEventId, now, now);
        this.db.prepare(`
      INSERT OR IGNORE INTO turn_classifications
        (id, event_id, issue_chain_id, model, schema_version, output_json, confidence, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `).run(`${ingestion.canonicalEventId}:classification`, ingestion.canonicalEventId, detection.issueChain.issueChainId, "heuristic-m2", "retrace-turn-classification-v2", JSON.stringify(classification), classification.confidence, now);
        this.db.prepare(`
      INSERT OR IGNORE INTO stall_assessments
        (id, issue_chain_id, as_of_event_id, eligible, reason_json, created_at)
      VALUES (?, ?, ?, ?, ?, ?)
    `).run(detection.assessment.stallAssessmentId, detection.assessment.issueChainId, detection.assessment.asOfEventId, detection.assessment.eligible ? 1 : 0, JSON.stringify(detection.assessment), now);
        let reentryRunId;
        if (detection.assessment.eligible && !this.getActiveRun(rawInput.session_id)) {
            reentryRunId = this.createInvitationRun(rawInput.session_id, detection.issueChain.issueChainId, ingestion.normalized.contentText ?? "", detection.assessment.asOfEventId);
        }
        return {
            classification,
            issueChain: detection.issueChain,
            stallAssessment: detection.assessment,
            reentryRunId,
            shouldBlock: detection.assessment.eligible && classification.confidence === "HIGH",
        };
    }
    countUserTurns(sessionId) {
        return this.db.prepare("SELECT COUNT(*) AS count FROM raw_events WHERE session_id = ? AND event_type = 'USER_PROMPT'").get(sessionId).count;
    }
    getIssueChainById(issueChainId) {
        if (!issueChainId)
            return undefined;
        return rowToIssueChain(this.db.prepare("SELECT * FROM issue_chains WHERE issue_chain_id = ?").get(issueChainId));
    }
    getActiveIssueChain(sessionId) {
        return rowToIssueChain(this.db.prepare("SELECT * FROM issue_chains WHERE session_id = ? AND status = 'ACTIVE' ORDER BY updated_at DESC LIMIT 1").get(sessionId));
    }
    createInvitationRun(sessionId, issueChainId, prePromptText, triggerEventId) {
        const reentryRunId = randomUUID();
        const now = new Date().toISOString();
        this.db.prepare(`
      INSERT INTO reentry_runs
        (reentry_run_id, session_id, issue_chain_id, trigger_type, trigger_event_id, state, state_version,
         invited_at, pre_prompt_text, created_at, updated_at)
      VALUES (?, ?, ?, 'AUTO_STALL', ?, 'INVITED', 1, ?, ?, ?, ?)
    `).run(reentryRunId, sessionId, issueChainId, triggerEventId, now, prePromptText, now, now);
        this.db.prepare(`
      INSERT OR IGNORE INTO ui_actions
        (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at)
      VALUES (?, ?, ?, 'INVITATION_SHOWN', ?, 1, ?)
    `).run(randomUUID(), reentryRunId, sessionId, JSON.stringify({ failureCount: this.getIssueChainById(issueChainId)?.unmetReportCount ?? 0 }), now);
        return reentryRunId;
    }
    getActiveRun(sessionId) {
        return this.db.prepare(`
      SELECT * FROM reentry_runs
      WHERE session_id = ? AND state IN ('INVITED', 'PRE_SURVEY', 'REENTRY_CONTEXT', 'USER_REVIEW', 'NEXT_PROMPT_READY', 'RESUMABLE')
      ORDER BY created_at DESC LIMIT 1
    `).get(sessionId);
    }
    getPublicState(sessionId) {
        const run = this.getActiveRun(sessionId);
        if (!run)
            return { stateVersion: 1, sessionId, uiState: "IDLE" };
        if (run.state !== "INVITED") {
            const uiState = ReentryStateSchema.parse(run.state);
            const state = { stateVersion: run.state_version, sessionId, reentryRunId: run.reentry_run_id, uiState };
            if (uiState === "PRE_SURVEY") {
                state.preSurvey = {
                    questionSetVersion: PRE_SURVEY_QUESTION_SET_VERSION,
                    questions: PRE_SURVEY_QUESTIONS.map((question) => ({ ...question, scaleMin: 1, scaleMax: 7 })),
                };
            }
            if (uiState !== "PRE_SURVEY") {
                const snapshot = this.getReentrySnapshot(run.reentry_run_id);
                if (snapshot)
                    state.context = PublicReentryContextSchema.parse({
                        snapshotVersion: snapshot.snapshot_version,
                        issueSummary: snapshot.input.issue_chain.issueSummary,
                        unmetReportCount: snapshot.input.stall_assessment.unmetReportCount,
                        triggerEventId: snapshot.input.trigger_event_id,
                    });
                const reconstruction = this.getLatestReconstruction(run.reentry_run_id);
                if (reconstruction?.status === "READY" && reconstruction.output && uiState !== "REENTRY_CONTEXT") {
                    state.reconstruction = reconstruction.output;
                    state.reviewedState = this.getReviewedState(run.reentry_run_id);
                }
                if (reconstruction?.status === "FAILED") {
                    state.reconstructionError = {
                        code: reconstruction.failure_code ?? "RECONSTRUCTION_FAILED",
                        message: reconstruction.failure_message ?? "Re-entry context reconstruction failed.",
                    };
                }
                state.reviewActions = this.getContextReviews(run.reentry_run_id).map((review) => ({
                    itemType: review.itemType,
                    itemId: review.itemId,
                    action: review.action,
                }));
                state.investigations = this.getInvestigations(run.reentry_run_id);
                const promptDraft = this.getNextPromptDraft(run.reentry_run_id);
                if (promptDraft)
                    state.nextPrompt = promptDraft;
                if (run.completion_reason)
                    state.completionReason = ResumeCompletionReasonSchema.parse(run.completion_reason);
            }
            return state;
        }
        const chain = this.getIssueChainById(run.issue_chain_id ?? undefined);
        return {
            stateVersion: run.state_version,
            sessionId,
            reentryRunId: run.reentry_run_id,
            uiState: "INVITATION",
            invitation: {
                title: "这个问题已经试了几次，要不要先弄清楚再继续？",
                body: "最近几次修改还没有解决这个问题。你可以继续让 Agent 尝试，也可以先整理当前问题和已有信息，再决定下一步。",
                failureCount: chain?.unmetReportCount ?? 0,
            },
        };
    }
    recordInvitationChoice(input) {
        const prior = this.db.prepare("SELECT action_type FROM ui_actions WHERE interaction_id = ? AND reentry_run_id = ?").get(input.interactionId, input.reentryRunId);
        if (prior) {
            const run = this.db.prepare("SELECT pre_prompt_text FROM reentry_runs WHERE reentry_run_id = ?").get(input.reentryRunId);
            const shouldSendDirect = prior.action_type === "INVITATION_CONTINUE_DIRECT";
            return {
                state: this.getPublicState(input.sessionId),
                originalPrompt: shouldSendDirect ? run?.pre_prompt_text ?? "" : undefined,
                shouldSendDirect,
            };
        }
        const run = this.db.prepare("SELECT * FROM reentry_runs WHERE reentry_run_id = ? AND session_id = ?").get(input.reentryRunId, input.sessionId);
        if (!run)
            throw new Error("REENTRY_NOT_FOUND");
        if (run.state_version !== input.stateVersion)
            throw new Error("STALE_STATE");
        if (run.state !== "INVITED")
            throw new Error("INVALID_TRANSITION");
        const now = new Date().toISOString();
        const nextState = input.choice === "ENTER_REENTRY" ? "PRE_SURVEY" : "DISMISSED";
        const nextVersion = run.state_version + 1;
        const transition = this.db.transaction(() => {
            if (input.choice === "ENTER_REENTRY") {
                const m3Input = this.buildM3Input(run);
                this.db.prepare(`
          INSERT INTO reentry_snapshots
            (snapshot_id, reentry_run_id, snapshot_version, summary_json, as_of_event_id, created_at)
          VALUES (?, ?, 1, ?, ?, ?)
        `).run(randomUUID(), input.reentryRunId, JSON.stringify({ input: m3Input }), m3Input.trigger_event_id, now);
            }
            this.db.prepare(`
        UPDATE reentry_runs SET state = ?, state_version = ?, started_at = CASE WHEN ? = 'PRE_SURVEY' THEN ? ELSE started_at END,
          dismissed_at = CASE WHEN ? = 'DISMISSED' THEN ? ELSE dismissed_at END, updated_at = ?
        WHERE reentry_run_id = ?
      `).run(nextState, nextVersion, nextState, now, nextState, now, now, input.reentryRunId);
            if (input.choice !== "ENTER_REENTRY" && run.issue_chain_id) {
                this.db.prepare("UPDATE issue_chains SET cooldown_until_user_turn = ?, updated_at = ? WHERE issue_chain_id = ?")
                    .run(this.countUserTurns(input.sessionId) + 2, now, run.issue_chain_id);
            }
            this.db.prepare(`
        INSERT INTO ui_actions
          (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
      `).run(input.interactionId, input.reentryRunId, input.sessionId, `INVITATION_${input.choice}`, JSON.stringify({ participantId: input.participantId }), nextVersion, now);
        });
        transition();
        const state = this.getPublicState(input.sessionId);
        return {
            state,
            originalPrompt: input.choice === "CONTINUE_DIRECT" ? run.pre_prompt_text ?? "" : undefined,
            shouldSendDirect: input.choice === "CONTINUE_DIRECT",
        };
    }
    submitPreSurvey(input) {
        const prior = this.db.prepare("SELECT action_type FROM ui_actions WHERE interaction_id = ? AND reentry_run_id = ?").get(input.interactionId, input.reentryRunId);
        if (prior)
            return { state: this.getPublicState(input.sessionId) };
        const run = this.db.prepare("SELECT * FROM reentry_runs WHERE reentry_run_id = ? AND session_id = ?").get(input.reentryRunId, input.sessionId);
        if (!run)
            throw new Error("REENTRY_NOT_FOUND");
        if (run.state_version !== input.stateVersion)
            throw new Error("STALE_STATE");
        if (run.state !== "PRE_SURVEY")
            throw new Error("INVALID_TRANSITION");
        const response = PreSurveyResponseSchema.parse(input.response);
        const now = new Date().toISOString();
        const nextVersion = run.state_version + 1;
        const transition = this.db.transaction(() => {
            this.db.prepare(`
        INSERT INTO survey_responses
          (survey_id, reentry_run_id, phase, question_set_version, responses_json, created_at)
        VALUES (?, ?, 'PRE', ?, ?, ?)
      `).run(randomUUID(), input.reentryRunId, response.questionSetVersion, JSON.stringify(response.responses), now);
            this.db.prepare("UPDATE reentry_runs SET state = 'REENTRY_CONTEXT', state_version = ?, updated_at = ? WHERE reentry_run_id = ?")
                .run(nextVersion, now, input.reentryRunId);
            this.db.prepare(`
        INSERT INTO ui_actions
          (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at)
        VALUES (?, ?, ?, 'PRE_SURVEY_SUBMITTED', ?, ?, ?)
      `).run(input.interactionId, input.reentryRunId, input.sessionId, JSON.stringify({ participantId: input.participantId }), nextVersion, now);
        });
        transition();
        return { state: this.getPublicState(input.sessionId) };
    }
    transitionReentryState(input) {
        const prior = this.db.prepare("SELECT action_type FROM ui_actions WHERE interaction_id = ? AND reentry_run_id = ?").get(input.interactionId, input.reentryRunId);
        if (prior)
            return { state: this.getPublicState(input.sessionId) };
        const run = this.db.prepare("SELECT * FROM reentry_runs WHERE reentry_run_id = ? AND session_id = ?").get(input.reentryRunId, input.sessionId);
        if (!run)
            throw new Error("REENTRY_NOT_FOUND");
        if (run.state_version !== input.stateVersion)
            throw new Error("STALE_STATE");
        const nextState = ReentryStateSchema.parse(input.nextState);
        const allowed = {
            REENTRY_CONTEXT: ["USER_REVIEW"],
            USER_REVIEW: ["NEXT_PROMPT_READY"],
            NEXT_PROMPT_READY: ["NEXT_PROMPT_READY", "RESUMABLE"],
            RESUMABLE: [],
            PRE_SURVEY: [],
        };
        if (!allowed[run.state]?.includes(nextState))
            throw new Error("INVALID_TRANSITION");
        const hasM3CReconstruction = Boolean(this.getLatestReconstruction(input.reentryRunId)?.output);
        if (hasM3CReconstruction && ((run.state === "USER_REVIEW" && nextState === "NEXT_PROMPT_READY") || (run.state === "NEXT_PROMPT_READY" && nextState === "RESUMABLE"))) {
            throw new Error("EXPLICIT_AUTHORIZATION_REQUIRED");
        }
        const now = new Date().toISOString();
        const nextVersion = run.state_version + 1;
        const transition = this.db.transaction(() => {
            this.db.prepare("UPDATE reentry_runs SET state = ?, state_version = ?, updated_at = ? WHERE reentry_run_id = ?")
                .run(nextState, nextVersion, now, input.reentryRunId);
            this.db.prepare(`
        INSERT INTO ui_actions
          (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at)
        VALUES (?, ?, ?, 'REENTRY_STATE_TRANSITION', ?, ?, ?)
      `).run(input.interactionId, input.reentryRunId, input.sessionId, JSON.stringify({ participantId: input.participantId, nextState }), nextVersion, now);
        });
        transition();
        return { state: this.getPublicState(input.sessionId) };
    }
    getReentrySnapshot(reentryRunId) {
        const row = this.db.prepare("SELECT * FROM reentry_snapshots WHERE reentry_run_id = ? ORDER BY snapshot_version DESC LIMIT 1").get(reentryRunId);
        if (!row)
            return undefined;
        const summary = JSON.parse(row.summary_json);
        return ReentrySnapshotSchema.parse({
            snapshot_id: row.snapshot_id,
            reentry_run_id: row.reentry_run_id,
            snapshot_version: row.snapshot_version,
            as_of_event_id: row.as_of_event_id,
            input: summary.input,
            created_at: row.created_at,
        });
    }
    reconstructReentryContext(input) {
        const prior = this.db.prepare("SELECT action_type FROM ui_actions WHERE interaction_id = ? AND reentry_run_id = ?").get(input.interactionId, input.reentryRunId);
        if (prior)
            return { state: this.getPublicState(input.sessionId) };
        const run = this.db.prepare("SELECT * FROM reentry_runs WHERE reentry_run_id = ? AND session_id = ?").get(input.reentryRunId, input.sessionId);
        if (!run)
            throw new Error("REENTRY_NOT_FOUND");
        if (run.state_version !== input.stateVersion)
            throw new Error("STALE_STATE");
        if (run.state !== "REENTRY_CONTEXT")
            throw new Error("INVALID_TRANSITION");
        const snapshot = this.getReentrySnapshot(run.reentry_run_id);
        if (!snapshot)
            throw new Error("M3_SNAPSHOT_NOT_FOUND");
        try {
            const frozenEvents = this.getFrozenEvents(snapshot);
            const reconstruction = buildDeterministicReconstruction({
                reconstructionId: randomUUID(),
                snapshotVersion: snapshot.snapshot_version,
                issueChain: snapshot.input.issue_chain,
                events: frozenEvents,
                generatedAt: new Date().toISOString(),
            });
            validateReconstructionProvenance(reconstruction, new Set(frozenEvents.map((event) => event.eventId)));
            const now = new Date().toISOString();
            const nextVersion = run.state_version + 1;
            this.db.transaction(() => {
                this.db.prepare(`
          INSERT INTO reentry_reconstructions
            (reconstruction_id, reentry_run_id, snapshot_version, status, output_json, failure_code, failure_message, created_at)
          VALUES (?, ?, ?, 'READY', ?, NULL, NULL, ?)
          ON CONFLICT(reentry_run_id, snapshot_version) DO UPDATE SET
            reconstruction_id = excluded.reconstruction_id,
            status = excluded.status,
            output_json = excluded.output_json,
            failure_code = NULL,
            failure_message = NULL,
            created_at = excluded.created_at
        `).run(reconstruction.reconstructionId, input.reentryRunId, snapshot.snapshot_version, JSON.stringify(reconstruction), now);
                this.db.prepare("DELETE FROM agent_claims WHERE reentry_run_id = ? AND reconstruction_id != ?").run(input.reentryRunId, reconstruction.reconstructionId);
                for (const claim of reconstruction.agentClaims) {
                    this.db.prepare(`
            INSERT INTO agent_claims
              (claim_id, reentry_run_id, reconstruction_id, claim_kind, claim_text, source_event_id,
               supporting_evidence_ids_json, verification_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
          `).run(claim.claimId, input.reentryRunId, reconstruction.reconstructionId, claim.kind, claim.text, claim.sourceEventId, JSON.stringify(claim.supportingEvidenceIds), claim.verificationStatus, now);
                }
                this.db.prepare("UPDATE reentry_runs SET state = 'USER_REVIEW', state_version = ?, updated_at = ? WHERE reentry_run_id = ?")
                    .run(nextVersion, now, input.reentryRunId);
                this.db.prepare(`
          INSERT INTO ui_actions
            (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at)
          VALUES (?, ?, ?, 'REENTRY_CONTEXT_RECONSTRUCTED', ?, ?, ?)
        `).run(input.interactionId, input.reentryRunId, input.sessionId, JSON.stringify({ participantId: input.participantId, reconstructionId: reconstruction.reconstructionId }), nextVersion, now);
            })();
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            this.saveReconstructionFailure(input, snapshot.snapshot_version, message);
            this.recordRuntimeError({
                sessionId: input.sessionId,
                reentryRunId: input.reentryRunId,
                component: "context-reconstruction",
                code: message,
                message,
                recoverable: true,
            });
        }
        return { state: this.getPublicState(input.sessionId) };
    }
    recordContextReview(input) {
        const prior = this.db.prepare("SELECT review_id FROM context_reviews WHERE interaction_id = ?").get(input.interactionId);
        if (prior)
            return { state: this.getPublicState(input.sessionId) };
        const run = this.db.prepare("SELECT * FROM reentry_runs WHERE reentry_run_id = ? AND session_id = ?").get(input.reentryRunId, input.sessionId);
        if (!run)
            throw new Error("REENTRY_NOT_FOUND");
        if (run.state_version !== input.stateVersion)
            throw new Error("STALE_STATE");
        if (run.state !== "USER_REVIEW")
            throw new Error("INVALID_TRANSITION");
        const reconstruction = this.getLatestReconstruction(input.reentryRunId);
        if (!reconstruction?.output || reconstruction.status !== "READY")
            throw new Error("RECONSTRUCTION_NOT_READY");
        const action = ReviewActionSchema.parse(input.action);
        const itemType = ContextItemTypeSchema.parse(input.itemType);
        if ((action === "EDIT" || action === "ADD") && input.after === undefined)
            throw new Error("REVIEW_PAYLOAD_REQUIRED");
        if (action !== "ADD" && !input.itemId?.trim())
            throw new Error("REVIEW_TARGET_REQUIRED");
        if (action !== "ADD" && !this.isReviewableItem(input.reentryRunId, itemType, input.itemId?.trim() ?? "")) {
            throw new Error("REVIEW_TARGET_NOT_FOUND");
        }
        const now = new Date().toISOString();
        const itemId = input.itemId?.trim() || `user-add-${randomUUID()}`;
        const review = ContextReviewSchema.parse({
            reviewId: randomUUID(),
            reentryRunId: input.reentryRunId,
            reconstructionId: reconstruction.output.reconstructionId,
            itemType,
            itemId,
            action,
            before: input.before,
            after: input.after,
            interactionId: input.interactionId,
            createdAt: now,
        });
        this.db.transaction(() => {
            this.db.prepare(`
        INSERT INTO context_reviews
          (review_id, reentry_run_id, reconstruction_id, item_type, item_id, action, before_json, after_json, interaction_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(review.reviewId, review.reentryRunId, review.reconstructionId, review.itemType, review.itemId, review.action, review.before === undefined ? null : JSON.stringify(review.before), review.after === undefined ? null : JSON.stringify(review.after), review.interactionId, review.createdAt);
            this.db.prepare(`
        INSERT INTO ui_actions
          (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at)
        VALUES (?, ?, ?, 'CONTEXT_REVIEW', ?, ?, ?)
      `).run(input.interactionId, input.reentryRunId, input.sessionId, JSON.stringify({ participantId: input.participantId, itemType, itemId, action }), run.state_version, now);
        })();
        return { state: this.getPublicState(input.sessionId) };
    }
    getFrozenEvents(snapshot) {
        const rows = this.db.prepare(`
      SELECT event_id, event_type, observed_at, content_text
      FROM raw_events
      WHERE session_id = ? AND collector_seq <= (
        SELECT collector_seq FROM raw_events WHERE event_id = ? AND session_id = ?
      )
      ORDER BY collector_seq ASC
    `).all(snapshot.input.issue_chain.sessionId, snapshot.input.trigger_event_id, snapshot.input.issue_chain.sessionId);
        if (rows.length === 0)
            throw new Error("FROZEN_EVENTS_NOT_FOUND");
        return rows.map((row) => ({ eventId: row.event_id, eventType: row.event_type, observedAt: row.observed_at, contentText: row.content_text }));
    }
    saveReconstructionFailure(input, snapshotVersion, message) {
        const now = new Date().toISOString();
        this.db.prepare(`
      INSERT INTO reentry_reconstructions
        (reconstruction_id, reentry_run_id, snapshot_version, status, output_json, failure_code, failure_message, created_at)
      VALUES (?, ?, ?, 'FAILED', NULL, 'RECONSTRUCTION_FAILED', ?, ?)
      ON CONFLICT(reentry_run_id, snapshot_version) DO UPDATE SET
        status = 'FAILED', output_json = NULL, failure_code = 'RECONSTRUCTION_FAILED', failure_message = excluded.failure_message, created_at = excluded.created_at
    `).run(randomUUID(), input.reentryRunId, snapshotVersion, message, now);
    }
    getLatestReconstruction(reentryRunId) {
        const row = this.db.prepare("SELECT * FROM reentry_reconstructions WHERE reentry_run_id = ? ORDER BY created_at DESC LIMIT 1").get(reentryRunId);
        if (!row)
            return undefined;
        return {
            ...row,
            output: row.output_json === null ? undefined : ReconstructionSchema.parse(JSON.parse(row.output_json)),
        };
    }
    getContextReviews(reentryRunId) {
        const rows = this.db.prepare("SELECT * FROM context_reviews WHERE reentry_run_id = ? ORDER BY created_at ASC").all(reentryRunId);
        return rows.map((row) => ContextReviewSchema.parse({
            reviewId: row.review_id,
            reentryRunId: row.reentry_run_id,
            reconstructionId: row.reconstruction_id,
            itemType: row.item_type,
            itemId: row.item_id,
            action: row.action,
            before: row.before_json === null ? undefined : JSON.parse(row.before_json),
            after: row.after_json === null ? undefined : JSON.parse(row.after_json),
            interactionId: row.interaction_id,
            createdAt: row.created_at,
        }));
    }
    isReviewableItem(reentryRunId, itemType, itemId) {
        const reconstruction = this.getLatestReconstruction(reentryRunId)?.output;
        if (!reconstruction)
            return false;
        if (itemType === "GOAL")
            return reconstruction.goal.some((item) => item.id === itemId);
        if (itemType === "EVIDENCE") {
            if (reconstruction.evidenceItems.some((item) => item.id === itemId))
                return true;
            return this.getInvestigations(reentryRunId).some((investigation) => investigation.result?.evidenceCandidateIds.includes(itemId) ?? false);
        }
        if (itemType === "EXPLANATION")
            return reconstruction.explanations.some((item) => item.id === itemId);
        if (itemType === "UNCERTAINTY")
            return reconstruction.uncertainties.some((item) => item.id === itemId);
        if (itemType === "AGENT_CLAIM")
            return reconstruction.agentClaims.some((item) => item.claimId === itemId);
        return this.getContextReviews(reentryRunId).some((review) => review.action === "ADD" && review.itemType === "GOVERNANCE_CONSTRAINT" && review.itemId === itemId);
    }
    getReviewedState(reentryRunId) {
        const reconstruction = this.getLatestReconstruction(reentryRunId);
        if (!reconstruction?.output)
            return undefined;
        const investigationCandidates = this.getInvestigations(reentryRunId).flatMap((investigation) => {
            if (!investigation.result)
                return [];
            return investigation.result.evidenceCandidates.map((candidate) => ({
                ...candidate,
                investigationId: investigation.investigationId,
                resultId: investigation.result?.resultId ?? "",
                resultEventIds: investigation.result?.resultEventIds ?? [],
                createdAt: investigation.result?.createdAt ?? investigation.updatedAt,
            }));
        });
        return buildReviewedState(reconstruction.output, this.getContextReviews(reentryRunId), reentryRunId, investigationCandidates);
    }
    generateNextPrompt(input) {
        const prior = this.db.prepare("SELECT action_type FROM ui_actions WHERE interaction_id = ? AND reentry_run_id = ?").get(input.interactionId, input.reentryRunId);
        if (prior)
            return { state: this.getPublicState(input.sessionId) };
        const run = this.getRun(input.reentryRunId, input.sessionId);
        this.assertRunVersionAndState(run, input.stateVersion, ["USER_REVIEW"]);
        const reconstruction = this.getLatestReconstruction(input.reentryRunId);
        const reviewed = this.getReviewedState(input.reentryRunId);
        if (!reconstruction?.output || !reviewed)
            throw new Error("REVIEWED_STATE_NOT_READY");
        if (reviewed.reviewVersion === 0)
            throw new Error("REVIEW_REQUIRED");
        const generatedAt = new Date().toISOString();
        let draft;
        try {
            draft = buildNextPromptDraft(reviewed, randomUUID(), generatedAt);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            this.recordRuntimeError({ sessionId: input.sessionId, reentryRunId: input.reentryRunId, component: "composer", code: message, message, recoverable: true });
            return this.completeReentry({ ...input, action: "FAILED_OPEN" });
        }
        const nextVersion = run.state_version + 1;
        this.db.transaction(() => {
            this.db.prepare(`
        INSERT INTO prompt_drafts
          (prompt_id, reentry_run_id, kind, structured_json, generated_text, user_edited_text, created_at, copied_at, edited_at, sent_at, review_version, status)
        VALUES (?, ?, 'NEXT_PROMPT', ?, ?, NULL, ?, NULL, NULL, NULL, ?, 'GENERATED')
      `).run(draft.promptId, input.reentryRunId, JSON.stringify(draft), draft.promptText, generatedAt, draft.reviewVersion);
            this.db.prepare("UPDATE reentry_runs SET state = 'NEXT_PROMPT_READY', state_version = ?, updated_at = ? WHERE reentry_run_id = ?")
                .run(nextVersion, generatedAt, input.reentryRunId);
            this.db.prepare(`
        INSERT INTO ui_actions
          (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at)
        VALUES (?, ?, ?, 'NEXT_PROMPT_GENERATED', ?, ?, ?)
      `).run(input.interactionId, input.reentryRunId, input.sessionId, JSON.stringify({ participantId: input.participantId, promptId: draft.promptId, reviewVersion: draft.reviewVersion }), nextVersion, generatedAt);
        })();
        return { state: this.getPublicState(input.sessionId) };
    }
    editNextPrompt(input) {
        const prior = this.db.prepare("SELECT action_type FROM ui_actions WHERE interaction_id = ? AND reentry_run_id = ?").get(input.interactionId, input.reentryRunId);
        if (prior)
            return { state: this.getPublicState(input.sessionId) };
        const run = this.getRun(input.reentryRunId, input.sessionId);
        this.assertRunVersionAndState(run, input.stateVersion, ["NEXT_PROMPT_READY"]);
        const editedPrompt = input.editedPrompt.trim();
        if (!editedPrompt)
            throw new Error("PROMPT_REQUIRED");
        const draft = this.getNextPromptDraft(input.reentryRunId);
        if (!draft)
            throw new Error("NEXT_PROMPT_NOT_READY");
        const now = new Date().toISOString();
        this.db.transaction(() => {
            this.db.prepare("UPDATE prompt_drafts SET user_edited_text = ?, edited_at = ?, status = 'EDITED' WHERE prompt_id = ?")
                .run(editedPrompt, now, draft.promptId);
            this.db.prepare(`
        INSERT INTO ui_actions
          (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at)
        VALUES (?, ?, ?, 'NEXT_PROMPT_EDITED', ?, ?, ?)
      `).run(input.interactionId, input.reentryRunId, input.sessionId, JSON.stringify({ participantId: input.participantId, promptId: draft.promptId }), run.state_version, now);
        })();
        return { state: this.getPublicState(input.sessionId) };
    }
    completeReentry(input) {
        const prior = this.db.prepare("SELECT action_type FROM ui_actions WHERE interaction_id = ? AND reentry_run_id = ?").get(input.interactionId, input.reentryRunId);
        if (prior)
            return { state: this.getPublicState(input.sessionId), shouldSend: false };
        const run = this.getRun(input.reentryRunId, input.sessionId);
        this.assertRunVersionAndState(run, input.stateVersion, ["USER_REVIEW", "NEXT_PROMPT_READY"]);
        const draft = this.getNextPromptDraft(input.reentryRunId);
        const expectedPrompt = draft?.editedPrompt ?? draft?.promptText;
        if ((input.action === "COPY" || input.action === "SENT") && (!draft || !expectedPrompt || input.finalPrompt !== expectedPrompt))
            throw new Error("FINAL_PROMPT_MISMATCH");
        const reason = input.action === "COPY" ? "COPIED" : input.action === "SENT" ? "SENT" : input.action === "CANCEL" ? "CANCELLED" : "FAILED_OPEN";
        const now = new Date().toISOString();
        const nextVersion = run.state_version + 1;
        this.db.transaction(() => {
            this.db.prepare("UPDATE reentry_runs SET state = 'RESUMABLE', state_version = ?, completion_reason = ?, completed_by_action = ?, completed_at = ?, updated_at = ? WHERE reentry_run_id = ?")
                .run(nextVersion, reason, input.action, now, now, input.reentryRunId);
            if (draft)
                this.db.prepare("UPDATE prompt_drafts SET copied_at = CASE WHEN ? = 'COPIED' THEN ? ELSE copied_at END, sent_at = CASE WHEN ? = 'SENT' THEN ? ELSE sent_at END, status = ? WHERE prompt_id = ?")
                    .run(reason, now, reason, now, reason, draft.promptId);
            this.db.prepare(`
        INSERT INTO ui_actions
          (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at)
        VALUES (?, ?, ?, 'REENTRY_COMPLETED', ?, ?, ?)
      `).run(input.interactionId, input.reentryRunId, input.sessionId, JSON.stringify({ participantId: input.participantId, action: input.action, completionReason: reason }), nextVersion, now);
        })();
        return { state: this.getPublicState(input.sessionId), promptToSend: input.action === "SENT" ? expectedPrompt : undefined, shouldSend: false };
    }
    createInvestigation(input) {
        const prior = this.db.prepare("SELECT action_type FROM ui_actions WHERE interaction_id = ? AND reentry_run_id = ?").get(input.interactionId, input.reentryRunId);
        if (prior)
            return { state: this.getPublicState(input.sessionId) };
        const run = this.getRun(input.reentryRunId, input.sessionId);
        this.assertRunVersionAndState(run, input.stateVersion, ["USER_REVIEW"]);
        const reviewed = this.getReviewedState(input.reentryRunId);
        if (!reviewed)
            throw new Error("REVIEWED_STATE_NOT_READY");
        let investigation;
        try {
            investigation = buildInvestigation(reviewed, {
                investigationId: randomUUID(),
                targetReviewItemId: input.targetReviewItemId,
                targetItemType: input.targetItemType,
                question: input.question,
                evidenceRequirement: input.evidenceRequirement,
                createdAt: new Date().toISOString(),
            });
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            this.recordRuntimeError({ sessionId: input.sessionId, reentryRunId: input.reentryRunId, component: "investigation", code: message, message, recoverable: true });
            return this.completeReentry({ ...input, action: "FAILED_OPEN" });
        }
        const now = new Date().toISOString();
        this.db.transaction(() => {
            this.insertInvestigation(investigation);
            this.db.prepare(`
        INSERT INTO ui_actions
          (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at)
        VALUES (?, ?, ?, 'INVESTIGATION_GENERATED', ?, ?, ?)
      `).run(input.interactionId, input.reentryRunId, input.sessionId, JSON.stringify({ participantId: input.participantId, investigationId: investigation.investigationId }), run.state_version, now);
        })();
        return { state: this.getPublicState(input.sessionId) };
    }
    editInvestigation(input) {
        const prior = this.db.prepare("SELECT action_type FROM ui_actions WHERE interaction_id = ? AND reentry_run_id = ?").get(input.interactionId, input.reentryRunId);
        if (prior)
            return { state: this.getPublicState(input.sessionId) };
        const run = this.getRun(input.reentryRunId, input.sessionId);
        this.assertRunVersionAndState(run, input.stateVersion, ["USER_REVIEW"]);
        if (!input.editedPrompt.trim())
            throw new Error("PROMPT_REQUIRED");
        const now = new Date().toISOString();
        const result = this.db.prepare("UPDATE investigations SET edited_prompt = ?, action = 'EDIT', status = 'EDITED', updated_at = ? WHERE investigation_id = ? AND reentry_run_id = ?").run(input.editedPrompt.trim(), now, input.investigationId, input.reentryRunId);
        if (result.changes !== 1)
            throw new Error("INVESTIGATION_NOT_FOUND");
        this.db.prepare(`INSERT INTO ui_actions (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at) VALUES (?, ?, ?, 'INVESTIGATION_EDITED', ?, ?, ?)`).run(input.interactionId, input.reentryRunId, input.sessionId, JSON.stringify({ participantId: input.participantId, investigationId: input.investigationId }), run.state_version, now);
        return { state: this.getPublicState(input.sessionId) };
    }
    copyInvestigation(input) {
        const prior = this.db.prepare("SELECT action_type FROM ui_actions WHERE interaction_id = ? AND reentry_run_id = ?").get(input.interactionId, input.reentryRunId);
        if (prior) {
            const existing = this.getInvestigation(input.investigationId);
            if (!existing)
                throw new Error("INVESTIGATION_NOT_FOUND");
            return { state: this.getPublicState(input.sessionId), prompt: existing.editedPrompt ?? existing.generatedPrompt };
        }
        const run = this.getRun(input.reentryRunId, input.sessionId);
        this.assertRunVersionAndState(run, input.stateVersion, ["USER_REVIEW"]);
        const investigation = this.getInvestigation(input.investigationId);
        if (!investigation)
            throw new Error("INVESTIGATION_NOT_FOUND");
        const now = new Date().toISOString();
        this.db.prepare("UPDATE investigations SET action = 'COPY', status = 'COPIED', updated_at = ? WHERE investigation_id = ? AND reentry_run_id = ?").run(now, input.investigationId, input.reentryRunId);
        this.db.prepare(`INSERT INTO ui_actions (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at) VALUES (?, ?, ?, 'INVESTIGATION_COPIED', ?, ?, ?)`).run(input.interactionId, input.reentryRunId, input.sessionId, JSON.stringify({ participantId: input.participantId, investigationId: input.investigationId }), run.state_version, now);
        return { state: this.getPublicState(input.sessionId), prompt: investigation.editedPrompt ?? investigation.generatedPrompt };
    }
    recordInvestigationResult(input) {
        const prior = this.db.prepare("SELECT action_type FROM ui_actions WHERE interaction_id = ? AND reentry_run_id = ?").get(input.interactionId, input.reentryRunId);
        if (prior)
            return { state: this.getPublicState(input.sessionId) };
        const run = this.getRun(input.reentryRunId, input.sessionId);
        this.assertRunVersionAndState(run, input.stateVersion, ["USER_REVIEW"]);
        const investigation = this.getInvestigation(input.investigationId);
        if (!investigation)
            throw new Error("INVESTIGATION_NOT_FOUND");
        const eventIds = [...input.resultEventIds, ...input.evidenceCandidates.flatMap((candidate) => candidate.sourceEventIds)];
        if (eventIds.some((eventId) => !this.eventBelongsToSession(eventId, input.sessionId)))
            throw new Error("INVALID_RESULT_PROVENANCE");
        const createdAt = new Date().toISOString();
        const evidenceCandidates = input.evidenceCandidates.map((candidate, index) => ({ id: `${input.investigationId}:evidence-${index + 1}`, claim: candidate.claim, sourceEventIds: candidate.sourceEventIds }));
        const result = InvestigationResultSchema.parse({ resultId: randomUUID(), investigationId: input.investigationId, resultEventIds: input.resultEventIds, evidenceCandidateIds: evidenceCandidates.map((candidate) => candidate.id), evidenceCandidates, createdAt });
        this.db.transaction(() => {
            this.db.prepare("INSERT INTO investigation_results (result_id, investigation_id, result_event_ids_json, evidence_candidate_ids_json, evidence_candidates_json, created_at) VALUES (?, ?, ?, ?, ?, ?)").run(result.resultId, result.investigationId, JSON.stringify(result.resultEventIds), JSON.stringify(result.evidenceCandidateIds), JSON.stringify(result.evidenceCandidates), result.createdAt);
            this.db.prepare("UPDATE investigations SET action = 'RESULT_IMPORTED', status = 'RESULT_PENDING_REVIEW', updated_at = ? WHERE investigation_id = ?").run(result.createdAt, input.investigationId);
            this.db.prepare("INSERT INTO ui_actions (interaction_id, reentry_run_id, session_id, action_type, payload_json, state_version, created_at) VALUES (?, ?, ?, 'INVESTIGATION_RESULT_IMPORTED', ?, ?, ?)").run(input.interactionId, input.reentryRunId, input.sessionId, JSON.stringify({ participantId: input.participantId, investigationId: input.investigationId, resultId: result.resultId }), run.state_version, result.createdAt);
        })();
        return { state: this.getPublicState(input.sessionId) };
    }
    getRun(reentryRunId, sessionId) {
        const run = this.db.prepare("SELECT * FROM reentry_runs WHERE reentry_run_id = ? AND session_id = ?").get(reentryRunId, sessionId);
        if (!run)
            throw new Error("REENTRY_NOT_FOUND");
        return run;
    }
    assertRunVersionAndState(run, stateVersion, states) {
        if (run.state_version !== stateVersion)
            throw new Error("STALE_STATE");
        if (!states.includes(run.state))
            throw new Error("INVALID_TRANSITION");
    }
    insertInvestigation(investigation) {
        this.db.prepare(`
      INSERT INTO investigations
        (investigation_id, reentry_run_id, target_review_item_id, target_item_type, question_to_verify,
         evidence_requirement, relevant_context_json, constraints_json, expected_observable_result,
         generated_prompt, edited_prompt, action, status, source_review_ids_json, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(investigation.investigationId, investigation.reentryRunId, investigation.targetReviewItemId ?? null, investigation.targetItemType, investigation.questionToVerify, investigation.evidenceRequirement, JSON.stringify(investigation.relevantContext), JSON.stringify(investigation.constraints), investigation.expectedObservableResult, investigation.generatedPrompt, investigation.editedPrompt ?? null, investigation.action, investigation.status, JSON.stringify(investigation.sourceReviewIds), investigation.createdAt, investigation.updatedAt);
    }
    getInvestigation(investigationId) {
        const row = this.db.prepare("SELECT * FROM investigations WHERE investigation_id = ?").get(investigationId);
        if (!row)
            return undefined;
        const resultRow = this.db.prepare("SELECT * FROM investigation_results WHERE investigation_id = ? ORDER BY created_at DESC LIMIT 1").get(investigationId);
        return InvestigationSchema.parse({
            investigationId: row.investigation_id,
            reentryRunId: row.reentry_run_id,
            targetReviewItemId: row.target_review_item_id ?? undefined,
            targetItemType: row.target_item_type,
            questionToVerify: row.question_to_verify,
            evidenceRequirement: row.evidence_requirement,
            relevantContext: JSON.parse(row.relevant_context_json),
            constraints: JSON.parse(row.constraints_json),
            expectedObservableResult: row.expected_observable_result,
            generatedPrompt: row.generated_prompt,
            editedPrompt: row.edited_prompt ?? undefined,
            action: row.action,
            status: row.status,
            sourceReviewIds: JSON.parse(row.source_review_ids_json),
            createdAt: row.created_at,
            updatedAt: row.updated_at,
            result: resultRow ? this.rowToInvestigationResult(resultRow) : undefined,
        });
    }
    getInvestigations(reentryRunId) {
        const rows = this.db.prepare("SELECT investigation_id FROM investigations WHERE reentry_run_id = ? ORDER BY created_at ASC").all(reentryRunId);
        return rows.flatMap((row) => {
            const investigation = this.getInvestigation(row.investigation_id);
            return investigation ? [investigation] : [];
        });
    }
    rowToInvestigationResult(row) {
        return InvestigationResultSchema.parse({
            resultId: row.result_id,
            investigationId: row.investigation_id,
            resultEventIds: JSON.parse(row.result_event_ids_json),
            evidenceCandidateIds: JSON.parse(row.evidence_candidate_ids_json),
            evidenceCandidates: JSON.parse(row.evidence_candidates_json),
            createdAt: row.created_at,
        });
    }
    getNextPromptDraft(reentryRunId) {
        const row = this.db.prepare("SELECT * FROM prompt_drafts WHERE reentry_run_id = ? AND kind = 'NEXT_PROMPT' ORDER BY created_at DESC LIMIT 1").get(reentryRunId);
        if (!row?.structured_json)
            return undefined;
        return NextPromptDraftSchema.parse({
            ...JSON.parse(row.structured_json),
            editedPrompt: row.user_edited_text ?? undefined,
            editedAt: row.edited_at ?? undefined,
        });
    }
    eventBelongsToSession(eventId, sessionId) {
        return Boolean(this.db.prepare("SELECT 1 FROM raw_events WHERE event_id = ? AND session_id = ?").get(eventId, sessionId));
    }
    buildM3Input(run) {
        const issueChain = this.getIssueChainById(run.issue_chain_id ?? undefined);
        const assessmentRow = this.db.prepare(`
      SELECT * FROM stall_assessments
      WHERE issue_chain_id = ? AND (as_of_event_id = ? OR eligible = 1)
      ORDER BY CASE WHEN as_of_event_id = ? THEN 0 ELSE 1 END, created_at DESC
      LIMIT 1
    `).get(run.issue_chain_id, run.trigger_event_id, run.trigger_event_id);
        if (!issueChain || !assessmentRow || !run.pre_prompt_text || !run.trigger_event_id)
            throw new Error("M3_INPUT_UNAVAILABLE");
        return M3InputSchema.parse({
            reentry_run_id: run.reentry_run_id,
            issue_chain: issueChain,
            stall_assessment: rowToStallAssessment(assessmentRow),
            pre_prompt_text: run.pre_prompt_text,
            trigger_event_id: run.trigger_event_id,
        });
    }
    upsertSession(event) {
        const now = event.observedAt;
        this.db.prepare(`
      INSERT INTO runtime_sessions
        (session_id, participant_id, cwd, started_at, plugin_version, platform_capabilities_json)
      VALUES (@sessionId, @participantId, @cwd, @startedAt, @pluginVersion, @platformCapabilities)
      ON CONFLICT(session_id) DO UPDATE SET
        cwd = COALESCE(excluded.cwd, runtime_sessions.cwd),
        ended_at = CASE WHEN @eventType = 'SESSION_END' THEN @observedAt ELSE runtime_sessions.ended_at END
    `).run({
            sessionId: event.sessionId,
            participantId: event.participantId,
            cwd: event.cwd,
            startedAt: now,
            pluginVersion: this.config.pluginVersion,
            platformCapabilities: JSON.stringify({ uiSyncMode: "manual-or-polling-pending-m0" }),
            eventType: event.eventType,
            observedAt: event.observedAt,
        });
    }
    recordRuntimeError(input) {
        this.db.prepare(`
      INSERT INTO runtime_errors
        (error_id, session_id, reentry_run_id, component, code, message, recoverable, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `).run(randomUUID(), input.sessionId ?? null, input.reentryRunId ?? null, input.component, input.code, input.message, input.recoverable ? 1 : 0, new Date().toISOString());
    }
    getRuntimeStatus() {
        const sessionCount = this.db.prepare("SELECT COUNT(*) AS count FROM runtime_sessions").get().count;
        const rawEventCount = this.db.prepare("SELECT COUNT(*) AS count FROM raw_events").get().count;
        const last = this.db.prepare("SELECT observed_at FROM raw_events ORDER BY observed_at DESC, rowid DESC LIMIT 1").get();
        return {
            ok: true,
            dbPath: this.config.dbPath,
            sessionCount,
            rawEventCount,
            lastEventAt: last?.observed_at ?? null,
            schema: "retrace-sqlite-v3",
        };
    }
    getLatestSessionId() {
        const row = this.db.prepare("SELECT session_id FROM runtime_sessions ORDER BY started_at DESC, rowid DESC LIMIT 1").get();
        return row?.session_id ?? null;
    }
    getSessionEventCount(sessionId) {
        return this.db.prepare("SELECT COUNT(*) AS count FROM raw_events WHERE session_id = ?").get(sessionId).count;
    }
    close() {
        this.db.close();
    }
}
function rowToIssueChain(row) {
    if (!row)
        return undefined;
    return IssueChainSchema.parse({
        issueChainId: row.issue_chain_id,
        sessionId: row.session_id,
        issueKey: row.issue_key,
        issueSummary: row.issue_summary,
        status: row.status,
        unmetReportCount: row.unmet_report_count,
        firstEventId: row.first_event_id,
        lastEventId: row.last_event_id,
        cooldownUntilUserTurn: row.cooldown_until_user_turn,
    });
}
function rowToStallAssessment(row) {
    const stored = JSON.parse(row.reason_json);
    return StallAssessmentSchema.parse({
        ...(typeof stored === "object" && stored !== null ? stored : {}),
        stallAssessmentId: row.id,
        issueChainId: row.issue_chain_id,
        asOfEventId: row.as_of_event_id,
        eligible: Boolean(row.eligible),
    });
}
