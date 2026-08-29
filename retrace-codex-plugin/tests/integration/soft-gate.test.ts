import { spawn } from "node:child_process";
import { join } from "node:path";
import { rm } from "node:fs/promises";
import { afterEach, describe, expect, it } from "vitest";
import { assessSubmit, shouldSoftGate } from "../../server/src/runtime/assess-submit.js";
import { makeConfig, makeTempDataDir } from "../helpers.js";
import { Repository } from "../../server/src/db/repository.js";
import { evaluateStall, type TurnClassification } from "../../server/src/schemas/stall.js";

const dataDirs: string[] = [];
const oldMode = process.env.RETRACE_INVITATION_MODE;

afterEach(async () => {
  if (oldMode === undefined) delete process.env.RETRACE_INVITATION_MODE;
  else process.env.RETRACE_INVITATION_MODE = oldMode;
  await Promise.all(dataDirs.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

function hookInput(sessionId: string, turnId: string, prompt: string) {
  return {
    session_id: sessionId,
    turn_id: turnId,
    cwd: "/tmp/study-project",
    hook_event_name: "UserPromptSubmit",
    prompt,
  };
}

describe("M2 soft gate", () => {
  it("blocks only a confirmed second unmet low-information corrective turn and preserves pre_prompt_text", async () => {
    const dataDir = await makeTempDataDir();
    dataDirs.push(dataDir);
    const config = makeConfig(dataDir);
    process.env.RETRACE_INVITATION_MODE = "soft_gate";

    const first = assessSubmit(hookInput("soft-session", "u1", "请实现背景图显示"), config);
    const second = assessSubmit(hookInput("soft-session", "u2", "背景图还是没显示，再检查一下"), config);
    const thirdPrompt = "还是不对，再改一下";
    const third = assessSubmit(hookInput("soft-session", "u3", thirdPrompt), config);

    expect(first.shouldBlock).toBe(false);
    expect(second.shouldBlock).toBe(false);
    expect(third.shouldBlock).toBe(true);
    expect(third.reentryRunId).toBeTruthy();
    const repository = new Repository(config);
    const stored = repository.db.prepare("SELECT pre_prompt_text FROM reentry_runs WHERE reentry_run_id = ?").get(third.reentryRunId) as { pre_prompt_text: string };
    expect(stored.pre_prompt_text).toBe(thirdPrompt);
    expect(repository.getPublicState("soft-session").uiState).toBe("INVITATION");
    repository.close();
  });

  it("fails open for observe_only and never emits a blocking response", async () => {
    const dataDir = await makeTempDataDir();
    dataDirs.push(dataDir);
    process.env.RETRACE_INVITATION_MODE = "observe_only";
    const result = assessSubmit(hookInput("observe-session", "u1", "还是不对，再改一下"), makeConfig(dataDir));
    expect(result.mode).toBe("observe_only");
    expect(result.shouldBlock).toBe(false);
  });

  it("fails open when confidence is LOW", () => {
    const low: TurnClassification = {
      sameIssue: true,
      issueSummary: "背景图没有显示",
      intent: "CORRECTIVE",
      reportsTargetUnmet: true,
      informationGain: { newObservation: false, newRequirement: false, newEvidence: false, investigationDirection: false },
      confidence: "LOW",
    };
    const first = evaluateStall({ sessionId: "low", eventId: "u1", userTurnIndex: 1, classification: low, activeReentry: false });
    const second = evaluateStall({ sessionId: "low", eventId: "u2", userTurnIndex: 2, classification: low, activeIssueChain: first.issueChain, activeReentry: false });
    expect(second.assessment.eligible).toBe(false);
    expect(shouldSoftGate({ stallAssessment: second.assessment, classification: low })).toBe(false);
  });

  it("fails open on assessor runtime error and timeout", async () => {
    const dataDir = await makeTempDataDir();
    dataDirs.push(dataDir);
    const hookPath = join(process.cwd(), "hooks", "assess-submit.mjs");
    const runHook = (runtimePath: string) => new Promise<{ code: number | null; stdout: string }>((resolve, reject) => {
      const child = spawn(process.execPath, [hookPath], {
        env: { ...process.env, PLUGIN_DATA: dataDir, RETRACE_INVITATION_MODE: "soft_gate", RETRACE_ASSESS_RUNTIME_PATH: runtimePath },
        stdio: ["pipe", "pipe", "ignore"],
      });
      let stdout = "";
      child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
      child.on("error", reject);
      child.on("exit", (code) => resolve({ code, stdout }));
      child.stdin.end(JSON.stringify(hookInput("fail-open", "u1", "还是不对，再改一下")));
    });
    const runtimeError = await runHook(join(dataDir, "missing-assessor.mjs"));
    const timeout = await runHook(join(process.cwd(), "tests", "fixtures", "slow-assessor.mjs"));
    expect(runtimeError.code).toBe(0);
    expect(runtimeError.stdout).toBe("");
    expect(timeout.code).toBe(0);
    expect(timeout.stdout).toBe("");
  });

  it("dismisses on PAUSE without returning a prompt to send", async () => {
    const dataDir = await makeTempDataDir();
    dataDirs.push(dataDir);
    process.env.RETRACE_INVITATION_MODE = "soft_gate";
    assessSubmit(hookInput("pause-session", "u1", "请实现背景图显示"), makeConfig(dataDir));
    assessSubmit(hookInput("pause-session", "u2", "还是没有，再检查一下"), makeConfig(dataDir));
    const gated = assessSubmit(hookInput("pause-session", "u3", "还是不对，再改一下"), makeConfig(dataDir));
    const repository = new Repository(makeConfig(dataDir));
    const result = repository.recordInvitationChoice({
      participantId: "p-test",
      sessionId: "pause-session",
      reentryRunId: gated.reentryRunId ?? "",
      stateVersion: 1,
      interactionId: "pause-choice",
      choice: "PAUSE",
    });
    expect(result.state.uiState).toBe("IDLE");
    expect(result.shouldSendDirect).toBe(false);
    expect(result.originalPrompt).toBeUndefined();
    repository.close();
  });
});
