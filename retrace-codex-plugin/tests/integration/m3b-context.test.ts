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
  return { session_id: sessionId, turn_id: turnId, cwd: "/tmp/m3b-project", hook_event_name: "UserPromptSubmit" as const, prompt: value };
}

describe("M3-B Re-entry context reconstruction and review", () => {
  it("uses only the frozen history, separates claims from evidence, and persists all review actions", async () => {
    const dataDir = await makeTempDataDir();
    dataDirs.push(dataDir);
    const config = makeConfig(dataDir);
    process.env.RETRACE_INVITATION_MODE = "soft_gate";
    assessSubmit(prompt("m3b-session", "u1", "请实现背景图显示"), config);
    assessSubmit(prompt("m3b-session", "u2", "背景图还是没显示，再检查一下"), config);
    const repository = new Repository(config);
    repository.ingestRawEvent(rawEvent("PostToolUse", { tool_response: "test failed: background image is missing" }, { session_id: "m3b-session", event_id: "m3b-tool", turn_id: "u2" }));
    repository.ingestRawEvent(rawEvent("Stop", { last_assistant_message: "原因是资源路径没有被加载，已经修复。" }, { session_id: "m3b-session", event_id: "m3b-agent-before", turn_id: "u2" }));
    const gated = assessSubmit(prompt("m3b-session", "u3", "还是不对，再改一下"), config);
    expect(gated.reentryRunId).toBeTruthy();

    repository.recordInvitationChoice({ participantId: "p-test", sessionId: "m3b-session", reentryRunId: gated.reentryRunId ?? "", stateVersion: 1, interactionId: "enter", choice: "ENTER_REENTRY" });
    repository.submitPreSurvey({ participantId: "p-test", sessionId: "m3b-session", reentryRunId: gated.reentryRunId ?? "", stateVersion: 2, interactionId: "pre", response: { questionSetVersion: "RETRACE-LIKERT-V2", responses: { confidence: 4 } } });
    repository.ingestRawEvent(rawEvent("Stop", { last_assistant_message: "已经成功修复，所有问题都解决了。" }, { session_id: "m3b-session", event_id: "m3b-agent-after", turn_id: "u3" }));

    const result = repository.reconstructReentryContext({ participantId: "p-test", sessionId: "m3b-session", reentryRunId: gated.reentryRunId ?? "", stateVersion: 3, interactionId: "reconstruct" });
    expect(result.state.uiState).toBe("USER_REVIEW");
    expect(result.state.reconstruction?.evidenceItems[0]).toMatchObject({ id: "evidence-1", sourceEventIds: ["m3b-tool"] });
    expect(result.state.reconstruction?.agentClaims.map((claim) => claim.text)).toContain("原因是资源路径没有被加载，已经修复");
    expect(result.state.reconstruction?.agentClaims.map((claim) => claim.text).join(" ")).not.toContain("所有问题都解决了");
    expect(result.state.reconstruction?.explanations[0].kind).toBe("AGENT_HYPOTHESIS");
    expect(result.state.reconstruction?.uncertainties.length).toBeGreaterThan(0);
    expect(repository.db.prepare("SELECT COUNT(*) AS count FROM agent_claims WHERE reentry_run_id = ?").get(gated.reentryRunId) as { count: number }).toEqual({ count: 1 });

    const reviewInput = { participantId: "p-test", sessionId: "m3b-session", reentryRunId: gated.reentryRunId ?? "", stateVersion: 4 };
    repository.recordContextReview({ ...reviewInput, interactionId: "confirm", itemType: "GOAL", itemId: "goal-original", action: "CONFIRM" });
    repository.recordContextReview({ ...reviewInput, interactionId: "edit", itemType: "EXPLANATION", itemId: "explanation-claim-1", action: "EDIT", before: "原因是资源路径没有被加载，已经修复", after: "Agent 推测资源路径可能没有被加载" });
    repository.recordContextReview({ ...reviewInput, interactionId: "reject", itemType: "AGENT_CLAIM", itemId: "claim-1", action: "REJECT", before: "原因是资源路径没有被加载，已经修复" });
    repository.recordContextReview({ ...reviewInput, interactionId: "add-evidence", itemType: "EVIDENCE", action: "ADD", after: { kind: "USER_OBSERVATION", claim: "我在浏览器中仍看到空白区域", sourceEventIds: [], sourceType: "USER_REVIEW" } });
    repository.recordContextReview({ ...reviewInput, interactionId: "add-uncertainty", itemType: "UNCERTAINTY", action: "ADD", after: { text: "需要确认刷新后资源是否仍然存在", sourceEventIds: [], relatedClaimIds: [], sourceType: "USER_REVIEW" } });
    const repeated = repository.recordContextReview({ ...reviewInput, interactionId: "confirm", itemType: "GOAL", itemId: "goal-original", action: "REJECT" });
    expect(repeated.state.uiState).toBe("USER_REVIEW");
    expect(repeated.state.stateVersion).toBe(4);
    expect(repository.db.prepare("SELECT COUNT(*) AS count FROM context_reviews WHERE reentry_run_id = ?").get(gated.reentryRunId) as { count: number }).toEqual({ count: 5 });
    expect(repository.db.prepare("SELECT COUNT(*) AS count FROM prompt_drafts WHERE reentry_run_id = ?").get(gated.reentryRunId) as { count: number }).toEqual({ count: 0 });
    repository.close();
  });

  it("fails open on reconstruction errors and records the failure without advancing state", async () => {
    const dataDir = await makeTempDataDir();
    dataDirs.push(dataDir);
    const config = makeConfig(dataDir);
    process.env.RETRACE_INVITATION_MODE = "soft_gate";
    assessSubmit(prompt("m3b-failure", "u1", "请实现背景图显示"), config);
    assessSubmit(prompt("m3b-failure", "u2", "还是没有，再检查一下"), config);
    const gated = assessSubmit(prompt("m3b-failure", "u3", "还是不对，再改一下"), config);
    const repository = new Repository(config);
    repository.recordInvitationChoice({ participantId: "p-test", sessionId: "m3b-failure", reentryRunId: gated.reentryRunId ?? "", stateVersion: 1, interactionId: "enter", choice: "ENTER_REENTRY" });
    repository.submitPreSurvey({ participantId: "p-test", sessionId: "m3b-failure", reentryRunId: gated.reentryRunId ?? "", stateVersion: 2, interactionId: "pre", response: { questionSetVersion: "RETRACE-LIKERT-V2", responses: { confidence: 4 } } });
    repository.db.prepare("DELETE FROM raw_events WHERE event_id = ?").run(gated.stallAssessment.asOfEventId);
    const result = repository.reconstructReentryContext({ participantId: "p-test", sessionId: "m3b-failure", reentryRunId: gated.reentryRunId ?? "", stateVersion: 3, interactionId: "reconstruct-fail" });
    expect(result.state.uiState).toBe("REENTRY_CONTEXT");
    expect(result.state.reconstruction).toBeUndefined();
    expect(result.state.reconstructionError?.code).toBe("RECONSTRUCTION_FAILED");
    expect(repository.db.prepare("SELECT status FROM reentry_reconstructions WHERE reentry_run_id = ?").get(gated.reentryRunId) as { status: string }).toEqual({ status: "FAILED" });
    repository.close();
  });
});
