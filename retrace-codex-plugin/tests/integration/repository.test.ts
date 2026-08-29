import { rm } from "node:fs/promises";
import { afterEach, describe, expect, it } from "vitest";
import { Repository } from "../../server/src/db/repository.js";
import { makeConfig, makeTempDataDir, rawEvent } from "../helpers.js";

const dataDirs: string[] = [];

afterEach(async () => {
  await Promise.all(dataDirs.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

describe("SQLite repository", () => {
  it("deduplicates the same hook delivery idempotently", async () => {
    const dataDir = await makeTempDataDir();
    dataDirs.push(dataDir);
    const repository = new Repository(makeConfig(dataDir));
    const event = rawEvent("UserPromptSubmit", { prompt: "same event" }, { event_id: "event-a" });

    const first = repository.ingestRawEvent(event);
    const second = repository.ingestRawEvent({ ...event, event_id: "event-b" });

    expect(first.inserted).toBe(true);
    expect(second).toMatchObject({ inserted: false, duplicate: true });
    expect(repository.getSessionEventCount("sess-test")).toBe(1);
    repository.close();
  });

  it("recovers persisted events after repository restart", async () => {
    const dataDir = await makeTempDataDir();
    dataDirs.push(dataDir);
    const config = makeConfig(dataDir);
    const firstRepository = new Repository(config);
    firstRepository.ingestRawEvent(rawEvent("SessionStart", { source: "startup" }));
    firstRepository.ingestRawEvent(rawEvent("UserPromptSubmit", { prompt: "persist me" }, { turn_id: "turn-1" }));
    firstRepository.close();

    const restartedRepository = new Repository(config);
    expect(restartedRepository.getRuntimeStatus()).toMatchObject({
      ok: true,
      sessionCount: 1,
      rawEventCount: 2,
    });
    expect(restartedRepository.getSessionEventCount("sess-test")).toBe(2);
    restartedRepository.close();
  });
});
