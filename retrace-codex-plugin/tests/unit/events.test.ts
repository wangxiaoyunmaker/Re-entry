import { describe, expect, it } from "vitest";
import { normalizeRawHookEvent } from "../../server/src/schemas/events.js";
import { rawEvent } from "../helpers.js";

describe("event normalization", () => {
  it("maps Codex lifecycle events and preserves prompt content", () => {
    const normalized = normalizeRawHookEvent(
      rawEvent("UserPromptSubmit", { prompt: "背景图还是没显示，帮我修一下。", turn_id: "turn-1" }, { turn_id: "turn-1" }),
      "p-001",
      1,
    );

    expect(normalized).toMatchObject({
      schemaVersion: "retrace-normalized-event-v2",
      eventType: "USER_PROMPT",
      participantId: "p-001",
      sessionId: "sess-test",
      turnId: "turn-1",
      contentText: "背景图还是没显示，帮我修一下。",
      source: "CODEX_HOOK",
      collectorSeq: 1,
    });
    expect(normalized.payloadHash).toHaveLength(64);
  });

  it("normalizes tool calls and results without relying on transcript format", () => {
    const call = normalizeRawHookEvent(
      rawEvent("PreToolUse", { tool_name: "Bash", tool_input: { command: "npm test" } }, { tool_use_id: "tool-1" }),
      "p-001",
      2,
    );
    const result = normalizeRawHookEvent(
      rawEvent("PostToolUse", { tool_name: "Bash", tool_response: { exit_code: 1, output: "failed" } }, { tool_use_id: "tool-1" }),
      "p-001",
      3,
    );

    expect(call.eventType).toBe("TOOL_CALL");
    expect(call.contentText).toBe("npm test");
    expect(result.eventType).toBe("TOOL_RESULT");
    expect(result.contentText).toContain("failed");
    expect(result.toolUseId).toBe("tool-1");
  });
});
