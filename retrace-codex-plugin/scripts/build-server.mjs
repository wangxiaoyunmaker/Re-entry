import { copyFileSync, mkdirSync } from "node:fs";
import { spawnSync } from "node:child_process";

const result = spawnSync("tsc", ["-p", "server/tsconfig.json"], {
  stdio: "inherit",
  shell: process.platform === "win32",
});
if (result.status !== 0) process.exit(result.status ?? 1);
mkdirSync("server/dist/db", { recursive: true });
copyFileSync("server/src/db/schema.sql", "server/dist/db/schema.sql");
