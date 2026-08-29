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

function input(sessionId: string, turnId: string, prompt: string) {
  return { session_id: sessionId, turn_id: turnId, cwd: "/tmp/m3-project", hook_event_name: "UserPromptSubmit", prompt };
}

describe("M3-A Re-entry state machine", () => {
  it("freezes M2 input at Enter, enforces the PRE barrier, and permits only ordered transitions", async () => {
    const dataDir = await makeTempDataDir();
    dataDirs.push(dataDir);
    const config = makeConfig(dataDir);
    process.env.RETRACE_INVITATION_MODE = "soft_gate";

    assessSubmit(input("m3-session", "u1", "请实现背景图显示"), config);
    assessSubmit(input("m3-session", "u2", "背景图还是没显示，再检查一下"), config);
    const gated = assessSubmit(input("m3-session", "u3", "还是不对，再改一下"), config);
    expect(gated.reentryRunId).toBeTruthy();

    const repository = new Repository(config);
    const entered = repository.recordInvitationChoice({
      participantId: "p-test",
      sessionId: "m3-session",
      reentryRunId: gated.reentryRunId ?? "",
      stateVersion: 1,
      interactionId: "m3-enter",
      choice: "ENTER_REENTRY",
    });
    expect(entered.state).toMatchObject({ uiState: "PRE_SURVEY", stateVersion: 2 });
    expect(entered.state.context).toBeUndefined();

    const snapshot = repository.getReentrySnapshot(gated.reentryRunId ?? "");
    expect(snapshot?.input).toMatchObject({
      reentry_run_id: gated.reentryRunId,
      pre_prompt_text: "还是不对，再改一下",
      trigger_event_id: gated.stallAssessment.asOfEventId,
    });
    const frozenSnapshot = JSON.stringify(snapshot);

    expect(() => repository.transitionReentryState({
      participantId: "p-test", sessionId: "m3-session", reentryRunId: gated.reentryRunId ?? "",
      stateVersion: 2, interactionId: "m3-skip", nextState: "USER_REVIEW",
    })).toThrow("INVALID_TRANSITION");

    const afterSurvey = repository.submitPreSurvey({
      participantId: "p-test",
      sessionId: "m3-session",
      reentryRunId: gated.reentryRunId ?? "",
      stateVersion: 2,
      interactionId: "m3-pre",
      response: { questionSetVersion: "RETRACE-LIKERT-V2", responses: { perceivedUnderstanding: 4, confidence: 3 } },
    });
    expect(afterSurvey.state).toMatchObject({ uiState: "REENTRY_CONTEXT", stateVersion: 3 });
    expect(afterSurvey.state.context).toBeDefined();
    expect(repository.db.prepare("SELECT COUNT(*) AS count FROM survey_responses WHERE reentry_run_id = ?").get(gated.reentryRunId) as { count: number }).toEqual({ count: 1 });

    expect(() => repository.transitionReentryState({
      participantId: "p-test", sessionId: "m3-session", reentryRunId: gated.reentryRunId ?? "",
      stateVersion: 3, interactionId: "m3-skip-next", nextState: "NEXT_PROMPT_READY",
    })).toThrow("INVALID_TRANSITION");

    const review = repository.transitionReentryState({
      participantId: "p-test", sessionId: "m3-session", reentryRunId: gated.reentryRunId ?? "",
      stateVersion: 3, interactionId: "m3-review", nextState: "USER_REVIEW",
    });
    expect(review.state.uiState).toBe("USER_REVIEW");
    const draft = repository.transitionReentryState({
      participantId: "p-test", sessionId: "m3-session", reentryRunId: gated.reentryRunId ?? "",
      stateVersion: 4, interactionId: "m3-draft", nextState: "NEXT_PROMPT_READY",
    });
    expect(draft.state.uiState).toBe("NEXT_PROMPT_READY");
    const edited = repository.transitionReentryState({
      participantId: "p-test", sessionId: "m3-session", reentryRunId: gated.reentryRunId ?? "",
      stateVersion: 5, interactionId: "m3-edit", nextState: "NEXT_PROMPT_READY",
    });
    expect(edited.state.uiState).toBe("NEXT_PROMPT_READY");
    const resumable = repository.transitionReentryState({
      participantId: "p-test", sessionId: "m3-session", reentryRunId: gated.reentryRunId ?? "",
      stateVersion: 6, interactionId: "m3-resumable", nextState: "RESUMABLE",
    });
    expect(resumable.state.uiState).toBe("RESUMABLE");

    expect(() => repository.transitionReentryState({
      participantId: "p-test", sessionId: "m3-session", reentryRunId: gated.reentryRunId ?? "",
      stateVersion: 6, interactionId: "m3-stale", nextState: "RESUMABLE",
    })).toThrow("STALE_STATE");

    repository.ingestRawEvent(rawEvent("Stop", { session_id: "m3-session", last_assistant_message: "完成了一次尝试" }, {
      session_id: "m3-session", event_id: "m3-agent-final", turn_id: "u3",
    }));
    expect(JSON.stringify(repository.getReentrySnapshot(gated.reentryRunId ?? ""))).toBe(frozenSnapshot);
    repository.close();
  });

  it("is idempotent for repeated PRE submission and rejects invalid Likert values", async () => {
    const dataDir = await makeTempDataDir();
    dataDirs.push(dataDir);
    const config = makeConfig(dataDir);
    process.env.RETRACE_INVITATION_MODE = "soft_gate";
    assessSubmit(input("m3-idempotent", "u1", "请实现背景图显示"), config);
    assessSubmit(input("m3-idempotent", "u2", "还是没有，再检查一下"), config);
    const gated = assessSubmit(input("m3-idempotent", "u3", "还是不对，再改一下"), config);
    const repository = new Repository(config);
    repository.recordInvitationChoice({ participantId: "p-test", sessionId: "m3-idempotent", reentryRunId: gated.reentryRunId ?? "", stateVersion: 1, interactionId: "enter", choice: "ENTER_REENTRY" });
    expect(() => repository.submitPreSurvey({ participantId: "p-test", sessionId: "m3-idempotent", reentryRunId: gated.reentryRunId ?? "", stateVersion: 2, interactionId: "bad", response: { questionSetVersion: "RETRACE-LIKERT-V2", responses: { confidence: 8 } } })).toThrow();
    const first = repository.submitPreSurvey({ participantId: "p-test", sessionId: "m3-idempotent", reentryRunId: gated.reentryRunId ?? "", stateVersion: 2, interactionId: "same", response: { questionSetVersion: "RETRACE-LIKERT-V2", responses: { confidence: 4 } } });
    const repeated = repository.submitPreSurvey({ participantId: "p-test", sessionId: "m3-idempotent", reentryRunId: gated.reentryRunId ?? "", stateVersion: 2, interactionId: "same", response: { questionSetVersion: "RETRACE-LIKERT-V2", responses: { confidence: 1 } } });
    expect(repeated.state).toEqual(first.state);
    expect(repository.db.prepare("SELECT COUNT(*) AS count FROM survey_responses WHERE reentry_run_id = ?").get(gated.reentryRunId) as { count: number }).toEqual({ count: 1 });
    repository.close();
  });
});
