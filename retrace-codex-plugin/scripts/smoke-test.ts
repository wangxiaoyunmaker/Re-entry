import { spawn } from "node:child_process";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { drainOnce, ensureSpoolDirs } from "../server/src/ingest/spool.js";
import { Repository } from "../server/src/db/repository.js";
import { makeConfig } from "../tests/helpers.js";

const dataDir = await mkdtemp(join(tmpdir(), "retrace-smoke-"));
const config = makeConfig(dataDir);
const layout = await ensureSpoolDirs(dataDir);
const hookPath = join(process.cwd(), "hooks", "emit-event.mjs");
const hookPayload = {
  session_id: "smoke-session",
  cwd: "/tmp/smoke-project",
  hook_event_name: "UserPromptSubmit",
  turn_id: "smoke-turn",
  prompt: "smoke prompt",
};

try {
  await new Promise<void>((resolve, reject) => {
    const child = spawn(process.execPath, [hookPath], {
      env: { ...process.env, PLUGIN_DATA: dataDir },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code !== 0) reject(new Error(stderr || `hook exited ${code}`));
      else if (stdout.trim()) reject(new Error(`hook wrote to stdout: ${stdout}`));
      else resolve();
    });
    child.stdin.end(JSON.stringify(hookPayload));
  });

  const repository = new Repository(config);
  const drained = await drainOnce(config, repository);
  const inboxAfter = await readdir(layout.inbox);
  if (drained !== 1 || inboxAfter.length !== 0 || repository.getRuntimeStatus().rawEventCount !== 1) {
    throw new Error(`smoke mismatch: drained=${drained}, inbox=${inboxAfter.length}`);
  }
  repository.close();
  console.log("M1 smoke test passed");
} finally {
  await rm(dataDir, { recursive: true, force: true });
}
