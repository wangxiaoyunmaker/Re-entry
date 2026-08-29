import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const dataDir = await mkdtemp(join(tmpdir(), "retrace-mcp-smoke-"));
const transport = new StdioClientTransport({
  command: process.execPath,
  args: [join(process.cwd(), "server", "dist", "index.js")],
  env: { ...process.env, PLUGIN_DATA: dataDir, RETRACE_SESSION_ID: "mcp-smoke-session" },
});
const client = new Client({ name: "retrace-mcp-smoke", version: "0.0.0" });

try {
  await client.connect(transport);
  const tools = await client.listTools();
  const names = tools.tools.map((tool) => tool.name).sort();
  if (JSON.stringify(names) !== JSON.stringify(["complete_reentry", "copy_investigation", "create_investigation", "edit_investigation", "edit_next_prompt", "generate_next_prompt", "get_retrace_state", "get_runtime_status", "open_retrace_panel", "reconstruct_reentry_context", "record_context_review", "record_investigation_result", "record_invitation_choice", "submit_pre_survey", "transition_reentry_state"])) {
    throw new Error(`unexpected tools: ${names.join(", ")}`);
  }
  const status = await client.callTool({ name: "get_runtime_status", arguments: {} });
  const structured = status.structuredContent as { ok?: boolean; rawEventCount?: number } | undefined;
  if (!structured?.ok || structured.rawEventCount !== 0) throw new Error("runtime status smoke failed");
  const resource = await client.readResource({ uri: "ui://retrace/retrace-panel.html" });
  const first = resource.contents[0];
  if (!first || first.mimeType !== "text/html;profile=mcp-app") throw new Error("MCP UI resource smoke failed");
  console.log("MCP server/UI smoke test passed");
} finally {
  await client.close();
  await rm(dataDir, { recursive: true, force: true });
}
