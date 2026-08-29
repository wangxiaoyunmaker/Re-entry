import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const input = readFileSync(0, "utf8");
const mode = process.env.RETRACE_INVITATION_MODE ?? "observe_only";

if (mode !== "soft_gate") process.exit(0);

try {
  const hookDir = dirname(fileURLToPath(import.meta.url));
    const runtimePath = process.env.RETRACE_ASSESS_RUNTIME_PATH
      ? process.env.RETRACE_ASSESS_RUNTIME_PATH
      : join(hookDir, "..", "server", "dist", "runtime", "assess-submit.js");
  const result = spawnSync(process.execPath, [runtimePath], {
    input,
    encoding: "utf8",
    timeout: 1500,
    env: process.env,
  });
  if (result.error) throw result.error;
  if (result.status === 0 && result.stdout?.trim()) process.stdout.write(result.stdout.trim());
  else if (result.stderr?.trim()) process.stderr.write(result.stderr);
} catch (error) {
  // This hook is a soft gate. Runtime failure, timeout, or malformed input must fail open.
  console.error(`[retrace soft gate] ${error instanceof Error ? error.message : String(error)}`);
}
