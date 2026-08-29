import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Repository } from "../server/src/db/repository.js";
import { makeConfig } from "../tests/helpers.js";

const dataDir = await mkdtemp(join(tmpdir(), "retrace-soft-gate-smoke-"));
const hookPath = join(process.cwd(), "hooks", "assess-submit.mjs");
const prompts = [
  ["u1", "请实现背景图显示"],
  ["u2", "背景图还是没显示，再检查一下"],
  ["u3", "还是不对，再改一下"],
] as const;

function runHook(turnId: string, prompt: string): Promise<{ code: number | null; stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [hookPath], {
      env: { ...process.env, PLUGIN_DATA: dataDir, RETRACE_INVITATION_MODE: "soft_gate" },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("exit", (code) => resolve({ code, stdout, stderr }));
    child.stdin.end(JSON.stringify({
      session_id: "soft-gate-smoke-session",
      turn_id: turnId,
      cwd: "/tmp/soft-gate-smoke",
      hook_event_name: "UserPromptSubmit",
      prompt,
    }));
  });
}

try {
  const results = [];
  for (const [turnId, prompt] of prompts) results.push(await runHook(turnId, prompt));
  if (results[0]?.stdout || results[1]?.stdout) throw new Error("early prompt was blocked");
  if (JSON.parse(results[2]?.stdout ?? "{}").decision !== "block") throw new Error("third prompt was not blocked");
  const repository = new Repository(makeConfig(dataDir));
  const run = repository.db.prepare("SELECT pre_prompt_text FROM reentry_runs WHERE session_id = ?").get("soft-gate-smoke-session") as { pre_prompt_text: string } | undefined;
  repository.close();
  if (run?.pre_prompt_text !== prompts[2][1]) throw new Error("pre_prompt_text was not preserved verbatim");
  console.log("M2 soft-gate hook smoke passed");
} finally {
  await rm(dataDir, { recursive: true, force: true });
}
