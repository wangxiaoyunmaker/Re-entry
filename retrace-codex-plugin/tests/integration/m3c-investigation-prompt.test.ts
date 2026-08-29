import { rm } from "node:fs/promises";
import { afterEach, describe, expect, it } from "vitest";
import { assessSubmit } from "../../server/src/runtime/assess-submit.js";
import { Repository } from "../../server/src/db/repository.js";
import { rawEvent, makeConfig, makeTempDataDir } from "../helpers.js";

const dataDirs: string[] = [];
const oldMode = process.env.RETRACE_INVITATION_MODE;

afterEach(async () => {
  if (oldMode === undefined) delete process.env.RETRACE_INVITATION_MODE;
  else process.env.RETRACE_INVITATION_MODE = oldMode;
  await Promise.all(dataDirs.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

function prompt(sessionId: string, turnId: string, value: string) {
  return { session_id: sessionId, turn_id: turnId, cwd: "/tmp/m3c-project", hook_event_name: "UserPromptSubmit" as const, prompt: value };
}

async function openedRun(sessionId: string) {
  const dataDir = await makeTempDataDir();
  dataDirs.push(dataDir);
  const config = makeConfig(dataDir);
  process.env.RETRACE_INVITATION_MODE = "soft_gate";
  assessSubmit(prompt(sessionId, "u1", "请让背景图显示"), config);
  assessSubmit(prompt(sessionId, "u2", "背景图还是没显示，再检查一下"), config);
  const repository = new Repository(config);
  repository.ingestRawEvent(rawEvent("PostToolUse", { tool_response: "integration test failed: image element missing" }, { session_id: sessionId, event_id: `${sessionId}-tool`, turn_id: "u2" }));
  repository.ingestRawEvent(rawEvent("Stop", { last_assistant_message: "原因可能是资源路径问题，但 bug fixed 尚未验证。" }, { session_id: sessionId, event_id: `${sessionId}-agent`, turn_id: "u2" }));
  const gated = assessSubmit(prompt(sessionId, "u3", "还是不对，再改一下"), config);
  const runId = gated.reentryRunId ?? "";
  repository.recordInvitationChoice({ participantId: "p-test", sessionId, reentryRunId: runId, stateVersion: 1, interactionId: `${sessionId}-enter`, choice: "ENTER_REENTRY" });
  repository.submitPreSurvey({ participantId: "p-test", sessionId, reentryRunId: runId, stateVersion: 2, interactionId: `${sessionId}-pre`, response: { questionSetVersion: "RETRACE-PRE-V1", responses: { system_understanding: 3 } } });
  repository.reconstructReentryContext({ participantId: "p-test", sessionId, reentryRunId: runId, stateVersion: 3, interactionId: `${sessionId}-reconstruct` });
  return { repository, runId };
}

describe("M3-C investigation, reviewed prompt, and explicit resume", () => {
  it("derives the prompt from reviewed state and preserves the authorization boundary", async () => {
    const { repository, runId } = await openedRun("m3c-main");
    repository.recordContextReview({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 4, interactionId: "goal-edit", itemType: "GOAL", itemId: "goal-original", action: "EDIT", before: "原始用户目标：请让背景图显示", after: "用户重新界定目标：先确认背景图资源是否被加载" });
    repository.recordContextReview({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 4, interactionId: "evidence-confirm", itemType: "EVIDENCE", itemId: "evidence-1", action: "CONFIRM" });
    repository.recordContextReview({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 4, interactionId: "claim-reject", itemType: "AGENT_CLAIM", itemId: "claim-1", action: "REJECT", before: "原因可能是资源路径问题，但 bug fixed 尚未验证" });
    repository.recordContextReview({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 4, interactionId: "requirement-add", itemType: "EVIDENCE", action: "ADD", after: { kind: "EVIDENCE_REQUIREMENT", claim: "必须运行 integration test", sourceEventIds: [], sourceType: "USER_REVIEW" } });
    const reviewed = repository.getReviewedState(runId);
    expect(reviewed?.goal[0]?.text).toContain("用户重新界定目标");
    expect(reviewed?.acceptedEvidence).toHaveLength(1);
    expect(reviewed?.evidenceRequirements[0]?.text).toBe("必须运行 integration test");
    expect(reviewed?.rejectedClaims[0]?.text).toContain("bug fixed");
    expect(() => repository.transitionReentryState({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 4, interactionId: "bypass-next", nextState: "NEXT_PROMPT_READY" })).toThrow("EXPLICIT_AUTHORIZATION_REQUIRED");

    const investigation = repository.createInvestigation({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 4, interactionId: "investigate", targetItemType: "UNCERTAINTY", targetReviewItemId: "uncertainty-claim-1" });
    expect(investigation.state.uiState).toBe("USER_REVIEW");
    expect(investigation.state.investigations?.[0]?.generatedPrompt).toContain("只调查这个问题");
    expect(investigation.state.investigations?.[0]?.generatedPrompt).toContain("Required evidence");
    const requirementInvestigation = repository.createInvestigation({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 4, interactionId: "investigate-requirement", targetItemType: "EVIDENCE", targetReviewItemId: reviewed?.evidenceRequirements[0]?.id });
    expect(requirementInvestigation.state.uiState).toBe("USER_REVIEW");
    expect(requirementInvestigation.state.investigations?.[1]?.questionToVerify).toContain("必须运行 integration test");
    repository.editInvestigation({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 4, interactionId: "investigate-edit", investigationId: investigation.state.investigations?.[0]?.investigationId ?? "", editedPrompt: "只运行 integration test，并返回原始输出。" });
    const copiedInvestigation = repository.copyInvestigation({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 4, interactionId: "investigate-copy", investigationId: investigation.state.investigations?.[0]?.investigationId ?? "" });
    expect(copiedInvestigation.prompt).toBe("只运行 integration test，并返回原始输出。");
    expect(copiedInvestigation.state.uiState).toBe("USER_REVIEW");

    repository.ingestRawEvent(rawEvent("Stop", { last_assistant_message: "integration test output: still failing" }, { session_id: "m3c-main", event_id: "m3c-result", turn_id: "u4" }));
    const result = repository.recordInvestigationResult({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 4, interactionId: "investigate-result", investigationId: investigation.state.investigations?.[0]?.investigationId ?? "", resultEventIds: ["m3c-result"], evidenceCandidates: [{ claim: "integration test still fails", sourceEventIds: ["m3c-result"] }] });
    expect(result.state.uiState).toBe("USER_REVIEW");
    expect(result.state.investigations?.[0]?.status).toBe("RESULT_PENDING_REVIEW");
    expect(result.state.reconstruction?.evidenceItems).toHaveLength(1);

    const next = repository.generateNextPrompt({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 4, interactionId: "next" });
    expect(next.state.uiState).toBe("NEXT_PROMPT_READY");
    expect(next.state.nextPrompt?.evidenceRequirements).toContain("必须运行 integration test");
    expect(next.state.nextPrompt?.knownFacts.join(" ")).toContain("integration test failed");
    expect(next.state.nextPrompt?.knownFacts.join(" ")).not.toContain("bug fixed");
    const generatedText = next.state.nextPrompt?.promptText ?? "";
    expect(() => repository.transitionReentryState({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 5, interactionId: "bypass-resume", nextState: "RESUMABLE" })).toThrow("EXPLICIT_AUTHORIZATION_REQUIRED");
    const edited = repository.editNextPrompt({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 5, interactionId: "next-edit", editedPrompt: "请只验证 integration test，不做其他修改。" });
    expect(edited.state.nextPrompt?.promptText).toBe(generatedText);
    expect(edited.state.nextPrompt?.editedPrompt).toBe("请只验证 integration test，不做其他修改。");
    expect(() => repository.completeReentry({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 5, interactionId: "bad-send", action: "SENT", finalPrompt: "hidden generated prompt" })).toThrow("FINAL_PROMPT_MISMATCH");
    const resumed = repository.completeReentry({ participantId: "p-test", sessionId: "m3c-main", reentryRunId: runId, stateVersion: 5, interactionId: "copy", action: "COPY", finalPrompt: "请只验证 integration test，不做其他修改。" });
    expect(resumed.state.uiState).toBe("RESUMABLE");
    expect(resumed.state.completionReason).toBe("COPIED");
    expect(repository.db.prepare("SELECT generated_text, user_edited_text, status FROM prompt_drafts WHERE reentry_run_id = ?").get(runId) as { generated_text: string; user_edited_text: string; status: string }).toMatchObject({ generated_text: generatedText, user_edited_text: "请只验证 integration test，不做其他修改。", status: "COPIED" });
    repository.close();
  });

  it("fails open on investigation generation and records FAILED_OPEN release", async () => {
    const { repository, runId } = await openedRun("m3c-failure");
    const failedInvestigation = repository.createInvestigation({ participantId: "p-test", sessionId: "m3c-failure", reentryRunId: runId, stateVersion: 4, interactionId: "bad-investigation", targetItemType: "UNCERTAINTY", targetReviewItemId: "missing" });
    expect(failedInvestigation.state.uiState).toBe("RESUMABLE");
    expect(failedInvestigation.state.completionReason).toBe("FAILED_OPEN");
    expect(repository.db.prepare("SELECT code FROM runtime_errors WHERE reentry_run_id = ? ORDER BY created_at DESC LIMIT 1").get(runId) as { code: string }).toEqual({ code: "INVESTIGATION_TARGET_NOT_FOUND" });
    expect(repository.db.prepare("SELECT COUNT(*) AS count FROM ui_actions WHERE reentry_run_id = ? AND action_type = 'REENTRY_COMPLETED'").get(runId) as { count: number }).toEqual({ count: 1 });
    repository.close();
  });
});
