import { createHash, randomUUID } from "node:crypto";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { RuntimeConfig } from "../server/src/config.js";
import type { RawHookEvent } from "../server/src/schemas/events.js";

export async function makeTempDataDir(): Promise<string> {
  return mkdtemp(join(tmpdir(), "retrace-test-"));
}

export function makeConfig(pluginData: string): RuntimeConfig {
  return {
    pluginData,
    participantId: "p-test",
    pluginVersion: "0.2.0-test",
    dbPath: join(pluginData, "retrace.sqlite3"),
    spoolPollMs: 20,
  };
}

export function rawEvent(
  hookEventName: RawHookEvent["hook_event_name"],
  payload: Record<string, unknown>,
  overrides: Partial<RawHookEvent> = {},
): RawHookEvent {
  const serialized = JSON.stringify(payload);
  return {
    schema_version: "retrace-raw-hook-event-v2",
    event_id: overrides.event_id ?? randomUUID(),
    received_at: overrides.received_at ?? "2026-08-28T00:00:00.000Z",
    hook_event_name: hookEventName,
    session_id: overrides.session_id ?? "sess-test",
    turn_id: overrides.turn_id ?? null,
    tool_use_id: overrides.tool_use_id ?? null,
    cwd: overrides.cwd ?? "/tmp/project",
    payload,
    payload_hash: overrides.payload_hash ?? createHash("sha256").update(serialized).digest("hex"),
  };
}
