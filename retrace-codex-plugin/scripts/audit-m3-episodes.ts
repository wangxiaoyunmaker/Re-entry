import { rm } from "node:fs/promises";
import { Repository } from "../server/src/db/repository.js";
import { classifyPrompt } from "../server/src/runtime/turn-classifier.js";
import { makeConfig, makeTempDataDir, rawEvent } from "../tests/helpers.js";
import type { ContextReviewInput } from "../server/src/db/repository.js";

type ReviewSpec = Omit<ContextReviewInput, "participantId" | "sessionId" | "reentryRunId" | "stateVersion" | "interactionId"> & {
  key: string;
  interactionId: string;
};

type EpisodeSpec = {
  id: string;
  label: string;
  goal: string;
  corrections: [string, string];
  history: Array<{ eventId: string; hook: "PostToolUse" | "Stop"; text: string; turnId: string }>;
  reviews: ReviewSpec[];
  investigation?: { targetItemType: "EVIDENCE" | "UNCERTAINTY"; targetKey: string; resultText: string };
  editNextPrompt?: string;
  completion: "COPY" | "CANCEL";
};

const PARTICIPANT_ID = "p-m3-audit";

const episodes: EpisodeSpec[] = [
  {
    id: "A",
    label: "premature closure without observable verification",
    goal: "请让登录按钮在移动端显示",
    corrections: ["还是不对，再改一下", "还是不对，再修一下"],
    history: [
      { eventId: "a-tool", hook: "PostToolUse", text: "browser check: mobile viewport button selector returned 0 nodes", turnId: "u2" },
      { eventId: "a-agent", hook: "Stop", text: "已修复，按钮现在 working。", turnId: "u2" },
    ],
    reviews: [
      { key: "goal-confirm", interactionId: "a-goal-confirm", itemType: "GOAL", itemId: "goal-original", action: "CONFIRM" },
      { key: "claim-reject", interactionId: "a-claim-reject", itemType: "AGENT_CLAIM", itemId: "claim-1", action: "REJECT", before: "已修复，按钮现在 working。" },
      { key: "uncertainty-confirm", interactionId: "a-uncertainty-confirm", itemType: "UNCERTAINTY", itemId: "uncertainty-claim-1", action: "CONFIRM" },
      { key: "verification-add", interactionId: "a-verification-add", itemType: "EVIDENCE", action: "ADD", after: { kind: "EVIDENCE_REQUIREMENT", claim: "必须运行移动端 integration test 并返回可观察结果。", sourceEventIds: [], sourceType: "USER_REVIEW" } },
    ],
    editNextPrompt: "请先运行移动端 integration test，返回实际结果；不要直接声称已修复。",
    completion: "COPY",
  },
  {
    id: "B",
    label: "causal explanation exceeds available evidence",
    goal: "请修复支付接口返回 500",
    corrections: ["还是不对，再改一下", "还是不对，再修一下"],
    history: [
      { eventId: "b-tool", hook: "PostToolUse", text: "GET /checkout returned HTTP 500; no server log or token validation output was captured", turnId: "u2" },
      { eventId: "b-agent", hook: "Stop", text: "问题是认证 token 过期导致的。", turnId: "u2" },
    ],
    reviews: [
      { key: "goal-confirm", interactionId: "b-goal-confirm", itemType: "GOAL", itemId: "goal-original", action: "CONFIRM" },
      { key: "evidence-confirm", interactionId: "b-evidence-confirm", itemType: "EVIDENCE", itemId: "evidence-1", action: "CONFIRM" },
      { key: "explanation-reject", interactionId: "b-explanation-reject", itemType: "EXPLANATION", itemId: "explanation-claim-1", action: "REJECT", before: "问题是认证 token 过期导致的。" },
      { key: "uncertainty-edit", interactionId: "b-uncertainty-edit", itemType: "UNCERTAINTY", itemId: "uncertainty-claim-1", action: "EDIT", before: "未验证 Agent 声明：问题是认证 token 过期导致的。", after: "仍不知道 500 是否由 token 造成，需要检查服务端日志。" },
      { key: "log-add", interactionId: "b-log-add", itemType: "EVIDENCE", action: "ADD", after: { kind: "EVIDENCE_REQUIREMENT", claim: "必须检查 server logs 后再判断原因。", sourceEventIds: [], sourceType: "USER_REVIEW" } },
    ],
    completion: "CANCEL",
  },
  {
    id: "C",
    label: "goal drift from EXIF-preserving import to PNG-only import",
    goal: "请实现照片导入并保留 EXIF 信息",
    corrections: ["还是不对，再改一下", "还是不对，再修一下"],
    history: [
      { eventId: "c-tool", hook: "PostToolUse", text: "manual check: JPEG import path was not exercised; EXIF retention is unknown", turnId: "u2" },
      { eventId: "c-agent", hook: "Stop", text: "为了快速上线，现在只支持 PNG。", turnId: "u2" },
    ],
    reviews: [
      { key: "goal-edit", interactionId: "c-goal-edit", itemType: "GOAL", itemId: "goal-original", action: "EDIT", before: "原始用户目标：请实现照片导入并保留 EXIF 信息", after: "用户重新界定目标：支持 JPEG 和 PNG 导入，并保留 EXIF 信息。" },
      { key: "claim-reject", interactionId: "c-claim-reject", itemType: "AGENT_CLAIM", itemId: "claim-1", action: "REJECT", before: "为了快速上线，现在只支持 PNG。" },
      { key: "scope-add", interactionId: "c-scope-add", itemType: "GOVERNANCE_CONSTRAINT", action: "ADD", after: { kind: "SCOPE", text: "不要把只支持 PNG 当成已批准的范围；需要验证 JPEG 和 PNG 的 EXIF 保留。", source: "USER_ADD", sourceReviewId: "pending", createdAt: "2026-08-29T00:00:00.000Z" } },
      { key: "exif-add", interactionId: "c-exif-add", itemType: "EVIDENCE", action: "ADD", after: { kind: "EVIDENCE_REQUIREMENT", claim: "必须分别验证 JPEG 和 PNG 导入后的 EXIF 信息。", sourceEventIds: [], sourceType: "USER_REVIEW" } },
    ],
    completion: "COPY",
  },
  {
    id: "D",
    label: "bounded investigation produces a pending evidence candidate",
    goal: "请修复导出 PDF 为空白",
    corrections: ["还是不对，再改一下", "还是不对，再修一下"],
    history: [
      { eventId: "d-tool", hook: "PostToolUse", text: "export command exits 0 but the generated PDF is blank", turnId: "u2" },
      { eventId: "d-agent", hook: "Stop", text: "导出已经成功。", turnId: "u2" },
    ],
    reviews: [
      { key: "goal-confirm", interactionId: "d-goal-confirm", itemType: "GOAL", itemId: "goal-original", action: "CONFIRM" },
      { key: "evidence-confirm", interactionId: "d-evidence-confirm", itemType: "EVIDENCE", itemId: "evidence-1", action: "CONFIRM" },
      { key: "claim-reject", interactionId: "d-claim-reject", itemType: "AGENT_CLAIM", itemId: "claim-1", action: "REJECT", before: "导出已经成功。" },
      { key: "uncertainty-confirm", interactionId: "d-uncertainty-confirm", itemType: "UNCERTAINTY", itemId: "uncertainty-claim-1", action: "CONFIRM" },
      { key: "pdf-check-add", interactionId: "d-pdf-check-add", itemType: "EVIDENCE", action: "ADD", after: { kind: "EVIDENCE_REQUIREMENT", claim: "必须打开生成后的 PDF 检查页面内容，而不是只看退出码。", sourceEventIds: [], sourceType: "USER_REVIEW" } },
    ],
    investigation: { targetItemType: "UNCERTAINTY", targetKey: "uncertainty-confirm", resultText: "打开生成后的 PDF 后，第一页仍为空白。" },
    completion: "CANCEL",
  },
  {
    id: "E",
    label: "user adds governance rules for verification and scope",
    goal: "请修复搜索结果排序",
    corrections: ["还是不对，再改一下", "还是不对，再修一下"],
    history: [
      { eventId: "e-tool", hook: "PostToolUse", text: "integration test: ordering assertion failed for equal-ranked results", turnId: "u2" },
      { eventId: "e-agent", hook: "Stop", text: "已完成排序修改。", turnId: "u2" },
    ],
    reviews: [
      { key: "goal-confirm", interactionId: "e-goal-confirm", itemType: "GOAL", itemId: "goal-original", action: "CONFIRM" },
      { key: "claim-reject", interactionId: "e-claim-reject", itemType: "AGENT_CLAIM", itemId: "claim-1", action: "REJECT", before: "已完成排序修改。" },
      { key: "test-add", interactionId: "e-test-add", itemType: "EVIDENCE", action: "ADD", after: { kind: "EVIDENCE_REQUIREMENT", claim: "必须运行 integration test 并返回失败或通过的原始结果。", sourceEventIds: [], sourceType: "USER_REVIEW" } },
      { key: "schema-add", interactionId: "e-schema-add", itemType: "GOVERNANCE_CONSTRAINT", action: "ADD", after: { kind: "SCOPE", text: "不要修改数据库 schema；修改前先解释原因。", source: "USER_ADD", sourceReviewId: "pending", createdAt: "2026-08-29T00:00:00.000Z" } },
    ],
    editNextPrompt: "请先解释原因，再运行 integration test；不要修改数据库 schema；返回原始测试结果。",
    completion: "COPY",
  },
];

function submitPrompt(repository: Repository, sessionId: string, eventId: string, turnId: string, prompt: string) {
  const activeIssueSummary = repository.getActiveIssueChain(sessionId)?.issueSummary;
  const classification = classifyPrompt({ prompt, activeIssueSummary });
  return repository.assessUserPrompt(rawEvent("UserPromptSubmit", { prompt }, { session_id: sessionId, event_id: eventId, turn_id: turnId, received_at: `2026-08-29T00:00:${eventId.endsWith("3") ? "03" : eventId.endsWith("2") ? "02" : "01"}.000Z` }), classification);
}

function addHistory(repository: Repository, sessionId: string, history: EpisodeSpec["history"]): void {
  for (const event of history) {
    repository.ingestRawEvent(rawEvent(event.hook, event.hook === "Stop" ? { last_assistant_message: event.text } : { tool_response: event.text }, {
      session_id: sessionId,
      event_id: event.eventId,
      turn_id: event.turnId,
      received_at: `2026-08-29T00:00:${event.eventId.startsWith("a") ? "10" : event.eventId.startsWith("b") ? "20" : event.eventId.startsWith("c") ? "30" : event.eventId.startsWith("d") ? "40" : "50"}.000Z`,
    }));
  }
}

async function runEpisode(spec: EpisodeSpec): Promise<unknown> {
  const dataDir = await makeTempDataDir();
  const sessionId = `m3-audit-${spec.id.toLowerCase()}`;
  const repository = new Repository(makeConfig(dataDir));
  try {
    const first = submitPrompt(repository, sessionId, `${spec.id.toLowerCase()}-u1`, "u1", spec.goal);
    const second = submitPrompt(repository, sessionId, `${spec.id.toLowerCase()}-u2`, "u2", spec.corrections[0]);
    addHistory(repository, sessionId, spec.history);
    const third = submitPrompt(repository, sessionId, `${spec.id.toLowerCase()}-u3`, "u3", spec.corrections[1]);
    const runId = third.reentryRunId;
    if (!runId) throw new Error(`${spec.id}: expected a re-entry run`);

    const invitation = repository.getPublicState(sessionId);
    const entered = repository.recordInvitationChoice({ participantId: PARTICIPANT_ID, sessionId, reentryRunId: runId, stateVersion: 1, interactionId: `${spec.id}-enter`, choice: "ENTER_REENTRY" });
    const preSurvey = repository.submitPreSurvey({ participantId: PARTICIPANT_ID, sessionId, reentryRunId: runId, stateVersion: 2, interactionId: `${spec.id}-pre`, response: { questionSetVersion: "RETRACE-PRE-V1", responses: { system_understanding: 3, agent_actions: 3, claim_credibility: 2, next_action: 3, continuation_confidence: 2 } } });
    const reconstructed = repository.reconstructReentryContext({ participantId: PARTICIPANT_ID, sessionId, reentryRunId: runId, stateVersion: 3, interactionId: `${spec.id}-reconstruct` });

    const appliedReviews: Array<{ key: string; itemId: string; action: string }> = [];
    for (const review of spec.reviews) {
      const state = repository.recordContextReview({
        participantId: PARTICIPANT_ID,
        sessionId,
        reentryRunId: runId,
        stateVersion: 4,
        interactionId: review.interactionId,
        itemType: review.itemType,
        itemId: review.itemId,
        action: review.action,
        before: review.before,
        after: review.after,
      });
      const recorded = state.state.reviewActions?.at(-1);
      appliedReviews.push({ key: review.key, itemId: recorded?.itemId ?? review.itemId ?? "unknown", action: review.action });
    }
    const reviewedBeforeInvestigation = repository.getReviewedState(runId);

    let investigationTrace: unknown;
    let reviewedAfterInvestigation: unknown;
    if (spec.investigation) {
      const targetReview = appliedReviews.find((review) => review.key === spec.investigation?.targetKey);
      if (!targetReview) throw new Error(`${spec.id}: investigation target review not found`);
      const investigationState = repository.createInvestigation({ participantId: PARTICIPANT_ID, sessionId, reentryRunId: runId, stateVersion: 4, interactionId: `${spec.id}-investigation`, targetItemType: spec.investigation.targetItemType, targetReviewItemId: targetReview.itemId });
      const investigation = investigationState.state.investigations?.at(-1);
      if (!investigation) throw new Error(`${spec.id}: investigation not created`);
      repository.copyInvestigation({ participantId: PARTICIPANT_ID, sessionId, reentryRunId: runId, stateVersion: 4, interactionId: `${spec.id}-investigation-copy`, investigationId: investigation.investigationId });
      const resultEventId = `${spec.id.toLowerCase()}-investigation-result`;
      repository.ingestRawEvent(rawEvent("Stop", { last_assistant_message: spec.investigation.resultText }, { session_id: sessionId, event_id: resultEventId, turn_id: "u4", received_at: "2026-08-29T00:01:00.000Z" }));
      const resultState = repository.recordInvestigationResult({ participantId: PARTICIPANT_ID, sessionId, reentryRunId: runId, stateVersion: 4, interactionId: `${spec.id}-investigation-result`, investigationId: investigation.investigationId, resultEventIds: [resultEventId], evidenceCandidates: [{ claim: spec.investigation.resultText, sourceEventIds: [resultEventId] }] });
      const candidateId = resultState.state.investigations?.at(-1)?.result?.evidenceCandidateIds[0];
      if (!candidateId) throw new Error(`${spec.id}: evidence candidate not created`);
      const candidateReview = repository.recordContextReview({ participantId: PARTICIPANT_ID, sessionId, reentryRunId: runId, stateVersion: 4, interactionId: `${spec.id}-candidate-review`, itemType: "EVIDENCE", itemId: candidateId, action: "CONFIRM" });
      reviewedAfterInvestigation = repository.getReviewedState(runId);
      investigationTrace = {
        prompt: investigation,
        result: resultState.state.investigations?.at(-1)?.result,
        candidateReview: candidateReview.state.reviewActions?.at(-1),
        candidateId,
        candidateInAcceptedEvidence: Boolean(repository.getReviewedState(runId)?.acceptedEvidence.some((item) => item.id === candidateId)),
      };
    }

    const generated = repository.generateNextPrompt({ participantId: PARTICIPANT_ID, sessionId, reentryRunId: runId, stateVersion: 4, interactionId: `${spec.id}-next` });
    let edited: unknown;
    if (spec.editNextPrompt) {
      edited = repository.editNextPrompt({ participantId: PARTICIPANT_ID, sessionId, reentryRunId: runId, stateVersion: 5, interactionId: `${spec.id}-next-edit`, editedPrompt: spec.editNextPrompt });
    }
    const finalPrompt = repository.getPublicState(sessionId).nextPrompt?.editedPrompt ?? repository.getPublicState(sessionId).nextPrompt?.promptText;
    const completed = repository.completeReentry({ participantId: PARTICIPANT_ID, sessionId, reentryRunId: runId, stateVersion: 5, interactionId: `${spec.id}-complete`, action: spec.completion, ...(spec.completion === "COPY" ? { finalPrompt } : {}) });

    return {
      id: spec.id,
      label: spec.label,
      originalGoal: spec.goal,
      m2: { first, second, third, invitation, prePromptText: repository.getReentrySnapshot(runId)?.input.trigger_event_id ? repository.db.prepare("SELECT pre_prompt_text FROM reentry_runs WHERE reentry_run_id = ?").get(runId) : undefined },
      preSurveyState: entered.state,
      reentryContextState: preSurvey.state,
      reconstruction: reconstructed.state.reconstruction,
      reviews: appliedReviews,
      reviewedBeforeInvestigation,
      investigation: investigationTrace,
      reviewedAfterInvestigation,
      nextPrompt: generated.state.nextPrompt,
      editedNextPrompt: edited ? repository.getPublicState(sessionId).nextPrompt?.editedPrompt : undefined,
      completion: completed.state.completionReason,
      finalState: completed.state,
    };
  } finally {
    repository.close();
    await rm(dataDir, { recursive: true, force: true });
  }
}

const report = [];
for (const episode of episodes) report.push(await runEpisode(episode));
console.log(JSON.stringify({ generatedAt: "2026-08-29", episodeCount: report.length, episodes: report }, null, 2));
