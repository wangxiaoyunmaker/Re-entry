import { mkdir, readdir, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { RuntimeConfig } from "../config.js";
import { RawHookEventSchema } from "../schemas/events.js";
import { Repository } from "../db/repository.js";

export type SpoolLayout = {
  root: string;
  inbox: string;
  processing: string;
  done: string;
  failed: string;
};

export function spoolLayout(pluginData: string): SpoolLayout {
  const root = join(pluginData, "spool");
  return {
    root,
    inbox: join(root, "inbox"),
    processing: join(root, "processing"),
    done: join(root, "done"),
    failed: join(root, "failed"),
  };
}

export async function ensureSpoolDirs(pluginData: string): Promise<SpoolLayout> {
  const layout = spoolLayout(pluginData);
  await Promise.all(Object.values(layout).map((path) => mkdir(path, { recursive: true })));
  return layout;
}

export async function countInboxFiles(pluginData: string): Promise<number> {
  const layout = await ensureSpoolDirs(pluginData);
  const files = await readdir(layout.inbox, { withFileTypes: true });
  return files.filter((file) => file.isFile() && file.name.endsWith(".json")).length;
}

export async function drainOnce(config: RuntimeConfig, repository: Repository): Promise<number> {
  const layout = await ensureSpoolDirs(config.pluginData);
  const files = await readdir(layout.inbox, { withFileTypes: true });
  const candidates = files
    .filter((file) => file.isFile() && file.name.endsWith(".json"))
    .map((file) => file.name)
    .sort();
  let processed = 0;

  for (const name of candidates) {
    const processingPath = join(layout.processing, name);
    try {
      await rename(join(layout.inbox, name), processingPath);
    } catch {
      // Another drain invocation may have claimed the file.
      continue;
    }

    try {
      const content = await BunCompatibleReadFile(processingPath);
      const raw = RawHookEventSchema.parse(JSON.parse(content));
      repository.ingestRawEvent(raw);
      await rename(processingPath, join(layout.done, name));
      processed += 1;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      repository.recordRuntimeError({
        component: "spool",
        code: "HOOK_SPOOL_ERROR",
        message,
        recoverable: true,
      });
      await writeFile(join(layout.failed, `${name}.error.txt`), `${message}\n`, "utf8");
      await rename(processingPath, join(layout.failed, name));
    }
  }
  return processed;
}

// Kept as a small wrapper so the ingestion path remains easy to exercise in tests.
async function BunCompatibleReadFile(path: string): Promise<string> {
  const { readFile } = await import("node:fs/promises");
  return readFile(path, "utf8");
}

export function startSpoolDrainer(config: RuntimeConfig, repository: Repository): () => void {
  let stopped = false;
  let draining = false;
  const timer = setInterval(() => {
    if (stopped || draining) return;
    draining = true;
    void drainOnce(config, repository)
      .catch((error: unknown) => {
        repository.recordRuntimeError({
          component: "spool",
          code: "HOOK_SPOOL_ERROR",
          message: error instanceof Error ? error.message : String(error),
          recoverable: true,
        });
      })
      .finally(() => {
        draining = false;
      });
  }, config.spoolPollMs);
  timer.unref();
  return () => {
    stopped = true;
    clearInterval(timer);
  };
}
