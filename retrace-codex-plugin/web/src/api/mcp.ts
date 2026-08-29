export type RuntimeStatus = {
  ok: boolean;
  participantId: string;
  activeSessionId: string;
  dbPath: string;
  sessionCount: number;
  rawEventCount: number;
  lastEventAt: string | null;
  schema: string;
  inboxCount: number;
  llmMode: string;
  uiSyncMode: string;
};

type ToolResponse<T> = { structuredContent?: T; isError?: boolean };

type McpBridge = {
  callTool?: (name: string, args: Record<string, unknown>) => Promise<ToolResponse<unknown>>;
  sendFollowUpMessage?: (text: string) => Promise<unknown>;
};

let initialization: Promise<void> | undefined;

declare global {
  interface Window {
    openai?: McpBridge;
  }
}

function request(method: string, params: Record<string, unknown>): Promise<unknown> {
  const requestId = crypto.randomUUID();
  window.parent.postMessage({ jsonrpc: "2.0", id: requestId, method, params }, "*");
  return new Promise((resolve, reject) => {
    const handler = (event: MessageEvent) => {
      if (event.source !== window.parent || event.data?.id !== requestId) return;
      window.removeEventListener("message", handler);
      if (event.data.error) reject(new Error(event.data.error.message ?? "MCP request failed"));
      else resolve(event.data.result);
    };
    window.addEventListener("message", handler);
  });
}

export function initializeMcpBridge(): Promise<void> {
  if (window.openai?.callTool || window.parent === window) return Promise.resolve();
  initialization ??= request("ui/initialize", {
    protocolVersion: "2026-01-26",
    appInfo: { name: "retrace", version: "0.2.0" },
    appCapabilities: {},
  }).then(() => {
    window.parent.postMessage({
      jsonrpc: "2.0",
      method: "ui/notifications/initialized",
      params: {},
    }, "*");
  });
  return initialization;
}

export async function callTool<T>(name: string, args: Record<string, unknown>): Promise<T> {
  if (window.openai?.callTool) {
    const result = await window.openai.callTool(name, args);
    if (result.isError) throw new Error(`MCP tool ${name} failed`);
    return result.structuredContent as T;
  }
  const result = await request("tools/call", { name, arguments: args }) as ToolResponse<T>;
  if (result.isError || !result.structuredContent) throw new Error(`MCP tool ${name} failed`);
  return result.structuredContent;
}

export async function sendFollowUpMessage(text: string): Promise<void> {
  if (window.openai?.sendFollowUpMessage) {
    await window.openai.sendFollowUpMessage(text);
    return;
  }
  await request("ui/message", followUpMessage(text));
}

export function followUpMessage(text: string): { role: "user"; content: [{ type: "text"; text: string }] } {
  return {
    role: "user",
    content: [{ type: "text", text }],
  };
}

export function isMcpBridgeAvailable(): boolean {
  return Boolean(window.openai?.callTool) || window.parent !== window;
}
