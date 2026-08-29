import { join, resolve } from "node:path";
import { homedir } from "node:os";
export function loadConfig(env = process.env) {
    const pluginData = resolve(env.PLUGIN_DATA ?? join(homedir(), ".retrace-codex-plugin"));
    return {
        pluginData,
        participantId: env.RETRACE_PARTICIPANT_ID?.trim() || "unassigned",
        pluginVersion: env.RETRACE_PLUGIN_VERSION?.trim() || "0.2.0",
        dbPath: resolve(env.RETRACE_DB_PATH ?? join(pluginData, "retrace.sqlite3")),
        spoolPollMs: Number.parseInt(env.RETRACE_SPOOL_POLL_MS ?? "250", 10) || 250,
    };
}
