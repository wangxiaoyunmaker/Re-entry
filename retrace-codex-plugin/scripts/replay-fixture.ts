import { readFile, readdir } from "node:fs/promises";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Repository } from "../server/src/db/repository.js";
import { makeConfig } from "../tests/helpers.js";
import { RawHookEventSchema } from "../server/src/schemas/events.js";
import { TurnClassificationSchema } from "../server/src/schemas/stall.js";

const fixtureRoot = join(process.cwd(), "fixtures");

type Expected = {
  sessionCount?: number;
  rawEventCount?: number;
  eventTypes?: string[];
  unmetReportCount?: number;
  stallEligible?: boolean;
  uiState?: "IDLE" | "INVITATION" | "PRE_SURVEY";
  sameIssue?: boolean;
  newIssueChain?: boolean;
  action?: "ENTER_REENTRY" | "CONTINUE_DIRECT" | "PAUSE";
  originalPrompt?: string;
  cooldownUntilUserTurn?: number;
  preSurveyShown?: boolean;
  noReentryContentShown?: boolean;
  shouldBlock?: boolean;
};

async function replayM1(): Promise<void> {
  const tracePath = join(fixtureRoot, "traces", "m1-basic", "events.json");
  const expectedPath = join(fixtureRoot, "expected", "m1-basic.json");
  const events = JSON.parse(await readFile(tracePath, "utf8")) as unknown[];
  const expected = JSON.parse(await readFile(expectedPath, "utf8")) as Expected;
  const dataDir = await mkdtemp(join(tmpdir(), "retrace-fixture-"));
  const repository = new Repository(makeConfig(dataDir));
  try {
    for (const event of events) repository.ingestRawEvent(RawHookEventSchema.parse(event));
    const status = repository.getRuntimeStatus();
    const eventTypes = (repository.db.prepare("SELECT event_type FROM raw_events ORDER BY collector_seq").all() as Array<{ event_type: string }>).map((row) => row.event_type);
    assertEqual(status.sessionCount, expected.sessionCount, "M1 sessionCount");
    assertEqual(status.rawEventCount, expected.rawEventCount, "M1 rawEventCount");
    assertEqual(JSON.stringify(eventTypes), JSON.stringify(expected.eventTypes), "M1 eventTypes");
  } finally {
    repository.close();
    await rm(dataDir, { recursive: true, force: true });
  }
}

async function replayM2(name: string): Promise<void> {
  const tracePath = join(fixtureRoot, "traces", name, "events.json");
  const classifierPath = join(fixtureRoot, "traces", name, "classifier_outputs.json");
  const expectedPath = join(fixtureRoot, "expected", `${name}.json`);
  const events = JSON.parse(await readFile(tracePath, "utf8")) as unknown[];
  const classifierOutputs = JSON.parse(await readFile(classifierPath, "utf8")) as Record<string, unknown>;
  const expected = JSON.parse(await readFile(expectedPath, "utf8")) as Expected;
  const dataDir = await mkdtemp(join(tmpdir(), "retrace-fixture-"));
  const repository = new Repository(makeConfig(dataDir));
  let lastAssessment: ReturnType<Repository["assessUserPrompt"]> | undefined;
  try {
    for (const event of events) {
      const raw = RawHookEventSchema.parse(event);
      if (raw.hook_event_name === "UserPromptSubmit") {
        const output = classifierOutputs[raw.event_id];
        if (!output) throw new Error(`${name}: missing classifier output for ${raw.event_id}`);
        lastAssessment = repository.assessUserPrompt(raw, TurnClassificationSchema.parse(output));
      } else {
        repository.ingestRawEvent(raw);
      }
    }

    if (expected.action) {
      if (!lastAssessment?.reentryRunId) throw new Error(`${name}: expected an invitation run`);
      const choice = repository.recordInvitationChoice({
        participantId: "p-test",
        sessionId: events[0] && typeof events[0] === "object" && events[0] !== null && "session_id" in events[0] ? String(events[0].session_id) : "fixture-session",
        reentryRunId: lastAssessment.reentryRunId,
        stateVersion: 1,
        interactionId: `${name}:choice`,
        choice: expected.action,
      });
      assertEqual(choice.shouldSendDirect, expected.action === "CONTINUE_DIRECT", `${name} shouldSendDirect`);
      if (expected.originalPrompt !== undefined) assertEqual(choice.originalPrompt, expected.originalPrompt, `${name} originalPrompt`);
    }

    const status = repository.getRuntimeStatus();
    const eventTypes = (repository.db.prepare("SELECT event_type FROM raw_events ORDER BY collector_seq").all() as Array<{ event_type: string }>).map((row) => row.event_type);
    const sessionId = events[0] && typeof events[0] === "object" && events[0] !== null && "session_id" in events[0] ? String(events[0].session_id) : "fixture-session";
    const state = repository.getPublicState(sessionId);
    const latestChain = repository.getActiveIssueChain(sessionId);
    if (expected.sessionCount !== undefined) assertEqual(status.sessionCount, expected.sessionCount, `${name} sessionCount`);
    if (expected.rawEventCount !== undefined) assertEqual(status.rawEventCount, expected.rawEventCount, `${name} rawEventCount`);
    if (expected.eventTypes !== undefined) assertEqual(JSON.stringify(eventTypes), JSON.stringify(expected.eventTypes), `${name} eventTypes`);
    if (expected.unmetReportCount !== undefined) assertEqual(lastAssessment?.stallAssessment.unmetReportCount ?? latestChain?.unmetReportCount, expected.unmetReportCount, `${name} unmetReportCount`);
    if (expected.stallEligible !== undefined) assertEqual(lastAssessment?.stallAssessment.eligible, expected.stallEligible, `${name} stallEligible`);
    if (expected.shouldBlock !== undefined) assertEqual(lastAssessment?.shouldBlock, expected.shouldBlock, `${name} shouldBlock`);
    if (expected.uiState !== undefined) assertEqual(state.uiState, expected.uiState, `${name} uiState`);
    if (expected.sameIssue !== undefined) assertEqual(lastAssessment?.stallAssessment.sameIssue, expected.sameIssue, `${name} sameIssue`);
    if (expected.newIssueChain) {
      assertEqual(lastAssessment?.stallAssessment.sameIssue, false, `${name} new issue classification`);
      const closed = repository.db.prepare("SELECT COUNT(*) AS count FROM issue_chains WHERE status = 'CLOSED'").get() as { count: number };
      if (closed.count < 1) throw new Error(`${name}: previous issue chain was not closed`);
    }
    if (expected.cooldownUntilUserTurn !== undefined) {
      const row = repository.db.prepare("SELECT cooldown_until_user_turn FROM issue_chains ORDER BY updated_at DESC LIMIT 1").get() as { cooldown_until_user_turn: number };
      assertEqual(row.cooldown_until_user_turn, expected.cooldownUntilUserTurn, `${name} cooldownUntilUserTurn`);
    }
    if (expected.preSurveyShown !== undefined) assertEqual(state.uiState === "PRE_SURVEY", expected.preSurveyShown, `${name} preSurveyShown`);
    if (expected.noReentryContentShown) {
      const counts = repository.db.prepare("SELECT (SELECT COUNT(*) FROM reentry_snapshots) AS snapshots, (SELECT COUNT(*) FROM survey_responses) AS surveys").get() as { snapshots: number; surveys: number };
      assertEqual(counts.snapshots, 1, `${name} frozen snapshot`);
      assertEqual(counts.surveys, 0, `${name} reentry content`);
    }
  } finally {
    repository.close();
    await rm(dataDir, { recursive: true, force: true });
  }
}

function assertEqual(actual: unknown, expected: unknown, label: string): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new Error(`${label} mismatch: ${JSON.stringify({ actual, expected })}`);
}

await replayM1();
const fixtureNames = (await readdir(join(fixtureRoot, "traces")))
  .filter((name) => name.startsWith("m2-"))
  .sort();
for (const name of fixtureNames) await replayM2(name);
console.log(`M1 fixture replay passed; ${fixtureNames.length} M2 fixtures passed`);
