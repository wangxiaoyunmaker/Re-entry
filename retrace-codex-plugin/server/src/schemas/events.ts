import { createHash } from "node:crypto";
import { z } from "zod";

export const HookEventNameSchema = z.enum([
  "SessionStart",
  "UserPromptSubmit",
  "PreToolUse",
  "PostToolUse",
  "Stop",
  "PreCompact",
  "PostCompact",
  "SessionEnd",
]);
export type HookEventName = z.infer<typeof HookEventNameSchema>;

export const RawHookEventSchema = z.object({
  schema_version: z.literal("retrace-raw-hook-event-v2"),
  event_id: z.string().min(1),
  received_at: z.string().min(1),
  hook_event_name: HookEventNameSchema,
  session_id: z.string().min(1),
  turn_id: z.string().nullable().optional(),
  tool_use_id: z.string().nullable().optional(),
  cwd: z.string().nullable().optional(),
  payload: z.record(z.string(), z.unknown()),
  payload_hash: z.string().regex(/^[a-f0-9]{64}$/).optional(),
});
export type RawHookEvent = z.infer<typeof RawHookEventSchema>;

export const NormalizedEventTypeSchema = z.enum([
  "SESSION_START",
  "USER_PROMPT",
  "TOOL_CALL",
  "TOOL_RESULT",
  "AGENT_FINAL",
  "COMPACTION_START",
  "COMPACTION_END",
  "SESSION_END",
  "INVITATION_SHOWN",
  "INVITATION_CHOICE",
  "SURVEY_RESPONSE",
  "GOAL_EDIT",
  "INTERPRETATION_FEEDBACK",
  "INVESTIGATION_PROMPT_CREATED",
  "INVESTIGATION_RESULT_IMPORTED",
  "NEXT_PROMPT_CREATED",
  "NEXT_PROMPT_EDITED",
  "PROMPT_COPIED",
  "REENTRY_CLOSED",
]);
export type NormalizedEventType = z.infer<typeof NormalizedEventTypeSchema>;

export const NormalizedEventSchema = z.object({
  schemaVersion: z.literal("retrace-normalized-event-v2"),
  eventId: z.string().min(1),
  sessionId: z.string().min(1),
  participantId: z.string().min(1),
  turnId: z.string().nullable(),
  toolUseId: z.string().nullable(),
  observedAt: z.string().min(1),
  collectorSeq: z.number().int().positive(),
  source: z.literal("CODEX_HOOK"),
  eventType: NormalizedEventTypeSchema,
  contentText: z.string().optional(),
  contentJson: z.unknown().optional(),
  cwd: z.string().nullable(),
  payloadHash: z.string().regex(/^[a-f0-9]{64}$/),
  dedupeKey: z.string().min(1),
});
export type NormalizedEvent = z.infer<typeof NormalizedEventSchema>;

const EVENT_TYPE_BY_HOOK: Record<HookEventName, NormalizedEventType> = {
  SessionStart: "SESSION_START",
  UserPromptSubmit: "USER_PROMPT",
  PreToolUse: "TOOL_CALL",
  PostToolUse: "TOOL_RESULT",
  Stop: "AGENT_FINAL",
  PreCompact: "COMPACTION_START",
  PostCompact: "COMPACTION_END",
  SessionEnd: "SESSION_END",
};

function stableJson(value: unknown): string {
  return JSON.stringify(value, Object.keys((value ?? {}) as object).sort());
}

function asText(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function contentTextFor(raw: RawHookEvent): string | undefined {
  const payload = raw.payload;
  if (raw.hook_event_name === "UserPromptSubmit") return asText(payload.prompt);
  if (raw.hook_event_name === "Stop") return asText(payload.last_assistant_message);
  if (raw.hook_event_name === "PreToolUse") return asText((payload.tool_input as { command?: unknown } | undefined)?.command);
  if (raw.hook_event_name === "PostToolUse") {
    const result = payload.tool_response;
    return asText(result) ?? (result === undefined ? undefined : JSON.stringify(result));
  }
  return undefined;
}

export function normalizeRawHookEvent(
  rawInput: RawHookEvent,
  participantId: string,
  collectorSeq: number,
): NormalizedEvent {
  const raw = RawHookEventSchema.parse(rawInput);
  const payloadHash = raw.payload_hash ?? createHash("sha256").update(stableJson(raw.payload)).digest("hex");
  const turnId = raw.turn_id ?? null;
  const toolUseId = raw.tool_use_id ?? null;
  const dedupeKey = [raw.session_id, raw.hook_event_name, turnId ?? "", toolUseId ?? "", payloadHash].join("\u001f");
  return NormalizedEventSchema.parse({
    schemaVersion: "retrace-normalized-event-v2",
    eventId: raw.event_id,
    sessionId: raw.session_id,
    participantId,
    turnId,
    toolUseId,
    observedAt: raw.received_at,
    collectorSeq,
    source: "CODEX_HOOK",
    eventType: EVENT_TYPE_BY_HOOK[raw.hook_event_name],
    contentText: contentTextFor(raw),
    contentJson: raw.payload,
    cwd: raw.cwd ?? (typeof raw.payload.cwd === "string" ? raw.payload.cwd : null),
    payloadHash,
    dedupeKey,
  });
}
