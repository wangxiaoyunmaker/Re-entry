import { randomUUID, createHash } from "node:crypto";
import { mkdir, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);

try {
  const inputText = Buffer.concat(chunks).toString("utf8").trim();
  if (!inputText) throw new Error("hook stdin was empty");

  const payload = JSON.parse(inputText);
  const hookEventName = payload?.hook_event_name;
  const sessionId = payload?.session_id;
  if (typeof hookEventName !== "string" || typeof sessionId !== "string") {
    throw new Error("hook payload is missing hook_event_name or session_id");
  }

  const pluginData = process.env.PLUGIN_DATA;
  if (!pluginData) throw new Error("PLUGIN_DATA is not set");

  const inbox = join(pluginData, "spool", "inbox");
  await mkdir(inbox, { recursive: true });
  const rawPayloadJson = JSON.stringify(payload);
  const event = {
    schema_version: "retrace-raw-hook-event-v2",
    event_id: randomUUID(),
    received_at: new Date().toISOString(),
    hook_event_name: hookEventName,
    session_id: sessionId,
    turn_id: typeof payload.turn_id === "string" ? payload.turn_id : null,
    tool_use_id: typeof payload.tool_use_id === "string" ? payload.tool_use_id : null,
    cwd: typeof payload.cwd === "string" ? payload.cwd : null,
    payload,
    payload_hash: createHash("sha256").update(rawPayloadJson).digest("hex")
  };
  const suffix = `${Date.now()}-${process.pid}-${randomUUID()}`;
  const tempPath = join(inbox, `event-${suffix}.tmp`);
  const finalPath = join(inbox, `event-${suffix}.json`);
  await writeFile(tempPath, `${JSON.stringify(event)}\n`, { encoding: "utf8", flag: "wx" });
  await rename(tempPath, finalPath);
} catch (error) {
  // Hook failures must be observable without writing anything to stdout.
  console.error(`[retrace hook] ${error instanceof Error ? error.message : String(error)}`);
}
