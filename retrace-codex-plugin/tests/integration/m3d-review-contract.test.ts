import { rm } from "node:fs/promises";
import { afterEach, describe, expect, it } from "vitest";
import { assessSubmit } from "../../server/src/runtime/assess-submit.js";
import { Repository } from "../../server/src/db/repository.js";
import { rawEvent, makeConfig, makeTempDataDir } from "../helpers.js";

const dataDirs: string[] = [];

afterEach(async () => {
  await Promise.all(dataDirs.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

function prompt(sessionId: string, turnId: string, value: string) {
  return { session_id: sessionId, turn_id: turnId, cwd: "/tmp/m3d-project", hook_event_name: "UserPromptSubmit" as const, prompt: value };
}

async function openedRun(sessionId: string) {
  const dataDir = await makeTempDataDir();
  dataDirs.push(dataDir);
  const config = makeConfig(dataDir);
  assessSubmit(prompt(sessionId, "u1", "请修复导出 PDF 为空白"), config);
  assessSubmit(prompt(sessionId, "u2", "还是不对，再改一下"), config);
  const repository = new Repository(config);
  repository.ingestRawEvent(rawEvent("PostToolUse", { tool_response: "export exits 0 but PDF is blank" }, { session_id: sessionId, event_id: `${sessionId}-tool`, turn_id: "u2" }));
  repository.ingestRawEvent(rawEvent("Stop", { last_assistant_message: "导出已经成功。" }, { session_id: sessionId, event_id: `${sessionId}-agent`, turn_id: "u2" }));
  const gated = assessSubmit(prompt(sessionId, "u3", "还是不对，再修一下"), config);
  const runId = gated.reentryRunId ?? "";
  repository.recordInvitationChoice({ participantId: "p-test", sessionId, reentryRunId: runId, stateVersion: 1, interactionId: `${sessionId}-enter`, choice: "ENTER_REENTRY" });
  repository.submitPreSurvey({ participantId: "p-test", sessionId, reentryRunId: runId, stateVersion: 2, interactionId: `${sessionId}-pre`, response: { questionSetVersion: "RETRACE-PRE-V1", responses: { system_understanding: 3 } } });
  repository.reconstructReentryContext({ participantId: "p-test", sessionId, reentryRunId: runId, stateVersion: 3, interactionId: `${sessionId}-reconstruct` });
  return { repository, runId };
}

describe("M3-D review contract hardening", () => {
  it("promotes only reviewed investigation candidates and preserves provenance", async () => {
    const { repository, runId } = await openedRun("m3d-candidates");
    const beforeInvalid = repository.db.prepare("SELECT COUNT(*) AS count FROM context_reviews WHERE reentry_run_id = ?").get(runId) as { count: number };
    expect(() => repository.recordContextReview({ participantId: "p-test", sessionId: "m3d-candidates", reentryRunId: runId, stateVersion: 4, interactionId: "invalid-target", itemType: "EVIDENCE", itemId: "does-not-exist", action: "CONFIRM" })).toThrow("REVIEW_TARGET_NOT_FOUND");
    const afterInvalid = repository.db.prepare("SELECT COUNT(*) AS count FROM context_reviews WHERE reentry_run_id = ?").get(runId) as { count: number };
    expect(afterInvalid.count).toBe(beforeInvalid.count);

    repository.recordContextReview({ participantId: "p-test", sessionId: "m3d-candidates", reentryRunId: runId, stateVersion: 4, interactionId: "goal", itemType: "GOAL", itemId: "goal-original", action: "CONFIRM" });
    repository.recordContextReview({ participantId: "p-test", sessionId: "m3d-candidates", reentryRunId: runId, stateVersion: 4, interactionId: "uncertainty", itemType: "UNCERTAINTY", itemId: "uncertainty-claim-1", action: "CONFIRM" });
    const investigation = repository.createInvestigation({ participantId: "p-test", sessionId: "m3d-candidates", reentryRunId: runId, stateVersion: 4, interactionId: "investigation", targetItemType: "UNCERTAINTY", targetReviewItemId: "uncertainty-claim-1" });
    const investigationId = investigation.state.investigations?.[0]?.investigationId ?? "";
    repository.ingestRawEvent(rawEvent("Stop", { last_assistant_message: "打开生成后的 PDF 后，第一页仍为空白。" }, { session_id: "m3d-candidates", event_id: "m3d-result-event", turn_id: "u4" }));
    const imported = repository.recordInvestigationResult({ participantId: "p-test", sessionId: "m3d-candidates", reentryRunId: runId, stateVersion: 4, interactionId: "result", investigationId, resultEventIds: ["m3d-result-event"], evidenceCandidates: [
      { claim: "打开生成后的 PDF 后，第一页仍为空白。", sourceEventIds: ["m3d-result-event"] },
      { claim: "候选结果需要编辑", sourceEventIds: ["m3d-result-event"] },
      { claim: "不应进入事实的候选", sourceEventIds: ["m3d-result-event"] },
    ] });
    const candidateIds = imported.state.investigations?.[0]?.result?.evidenceCandidateIds ?? [];
    expect(candidateIds).toHaveLength(3);
    repository.recordContextReview({ participantId: "p-test", sessionId: "m3d-candidates", reentryRunId: runId, stateVersion: 4, interactionId: "candidate-confirm", itemType: "EVIDENCE", itemId: candidateIds[0], action: "CONFIRM" });
    repository.recordContextReview({ participantId: "p-test", sessionId: "m3d-candidates", reentryRunId: runId, stateVersion: 4, interactionId: "candidate-edit", itemType: "EVIDENCE", itemId: candidateIds[1], action: "EDIT", before: "候选结果需要编辑", after: "编辑后的候选结果" });
    repository.recordContextReview({ participantId: "p-test", sessionId: "m3d-candidates", reentryRunId: runId, stateVersion: 4, interactionId: "candidate-reject", itemType: "EVIDENCE", itemId: candidateIds[2], action: "REJECT" });

    const reviewed = repository.getReviewedState(runId);
    expect(reviewed?.acceptedEvidence.map((item) => item.claim)).toEqual(["打开生成后的 PDF 后，第一页仍为空白。", "编辑后的候选结果"]);
    expect(reviewed?.rejectedEvidence.map((item) => item.claim)).toEqual(["不应进入事实的候选"]);
    const promoted = reviewed?.acceptedEvidence[0];
    expect(promoted?.candidateProvenance).toMatchObject({
      kind: "INVESTIGATION_CANDIDATE",
      candidateId: candidateIds[0],
      investigationId,
      sourceResultEventIds: ["m3d-result-event"],
      originalCandidateValue: "打开生成后的 PDF 后，第一页仍为空白。",
      reviewAction: "CONFIRM",
      reviewedValue: "打开生成后的 PDF 后，第一页仍为空白。",
    });
    expect(reviewed?.acceptedEvidence[1]?.candidateProvenance).toMatchObject({
      candidateId: candidateIds[1],
      originalCandidateValue: "候选结果需要编辑",
      reviewAction: "EDIT",
      reviewedValue: "编辑后的候选结果",
    });
    expect(reviewed?.rejectedEvidence[0]?.candidateProvenance).toMatchObject({
      candidateId: candidateIds[2],
      reviewAction: "REJECT",
      originalCandidateValue: "不应进入事实的候选",
    });
    const generated = repository.generateNextPrompt({ participantId: "p-test", sessionId: "m3d-candidates", reentryRunId: runId, stateVersion: 4, interactionId: "next" });
    expect(generated.state.nextPrompt?.knownFacts).toContain("打开生成后的 PDF 后，第一页仍为空白。");
    expect(generated.state.nextPrompt?.knownFacts).toContain("编辑后的候选结果");
    expect(generated.state.nextPrompt?.knownFacts).not.toContain("不应进入事实的候选");
    expect(JSON.stringify(repository.getReviewedState(runId))).toBe(JSON.stringify(repository.getReviewedState(runId)));
    repository.close();
  });

  it("keeps governance constraints distinct from uncertainty and maps them to Constraints", async () => {
    const { repository, runId } = await openedRun("m3d-governance");
    expect(() => repository.recordContextReview({ participantId: "p-test", sessionId: "m3d-governance", reentryRunId: runId, stateVersion: 4, interactionId: "invalid-governance", itemType: "GOVERNANCE_CONSTRAINT", itemId: "missing", action: "CONFIRM" })).toThrow("REVIEW_TARGET_NOT_FOUND");
    const added = repository.recordContextReview({ participantId: "p-test", sessionId: "m3d-governance", reentryRunId: runId, stateVersion: 4, interactionId: "governance-add", itemType: "GOVERNANCE_CONSTRAINT", action: "ADD", after: { kind: "SCOPE", text: "不要修改数据库 schema；修改前先解释原因。", source: "USER_ADD" } });
    const constraintId = added.state.reviewActions?.at(-1)?.itemId ?? "";
    const initial = repository.getReviewedState(runId);
    expect(initial?.governanceConstraints).toMatchObject([{ id: constraintId, text: "不要修改数据库 schema；修改前先解释原因。", kind: "SCOPE", source: "USER_ADD" }]);
    expect(initial?.unresolvedUncertainties).toHaveLength(1);
    repository.recordContextReview({ participantId: "p-test", sessionId: "m3d-governance", reentryRunId: runId, stateVersion: 4, interactionId: "governance-edit", itemType: "GOVERNANCE_CONSTRAINT", itemId: constraintId, action: "EDIT", before: "不要修改数据库 schema；修改前先解释原因。", after: "不要修改数据库 schema；先解释原因并等待授权。" });
    const edited = repository.getReviewedState(runId);
    expect(edited?.governanceConstraints).toMatchObject([{ id: constraintId, text: "不要修改数据库 schema；先解释原因并等待授权。", kind: "SCOPE", source: "USER_EDIT" }]);
    expect(edited?.unresolvedUncertainties).toHaveLength(1);
    const generated = repository.generateNextPrompt({ participantId: "p-test", sessionId: "m3d-governance", reentryRunId: runId, stateVersion: 4, interactionId: "next" });
    expect(generated.state.nextPrompt?.constraints).toContain("不要修改数据库 schema；先解释原因并等待授权。");
    expect(generated.state.nextPrompt?.openQuestions).not.toContain("不要修改数据库 schema；先解释原因并等待授权。");
    repository.close();
  });
});
