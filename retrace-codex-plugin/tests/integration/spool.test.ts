import { readFile, readdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { Repository } from "../../server/src/db/repository.js";
import { makeConfig, makeTempDataDir, rawEvent } from "../helpers.js";
import { drainOnce, ensureSpoolDirs, spoolLayout } from "../../server/src/ingest/spool.js";

const dataDirs: string[] = [];

afterEach(async () => {
  await Promise.all(dataDirs.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

describe("atomic spool ingestion", () => {
  it("drains complete json files and ignores temporary files", async () => {
    const dataDir = await makeTempDataDir();
    dataDirs.push(dataDir);
    const config = makeConfig(dataDir);
    const layout = await ensureSpoolDirs(dataDir);
    await writeFile(join(layout.inbox, "event.tmp"), "partial", "utf8");
    await writeFile(join(layout.inbox, "event.json"), `${JSON.stringify(rawEvent("SessionStart", {}))}\n`, "utf8");

    const repository = new Repository(config);
    expect(await drainOnce(config, repository)).toBe(1);
    expect(repository.getRuntimeStatus().rawEventCount).toBe(1);
    expect((await readdir(layout.inbox)).sort()).toEqual(["event.tmp"]);
    expect((await readdir(layout.done)).sort()).toEqual(["event.json"]);
    expect(await readFile(join(layout.done, "event.json"), "utf8")).toContain("retrace-raw-hook-event-v2");
    repository.close();
    expect(spoolLayout(dataDir).processing).toBe(layout.processing);
  });
});
