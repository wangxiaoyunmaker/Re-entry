import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { registerAppResource, RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import { z } from "zod";
import { loadConfig } from "./config.js";
import { Repository } from "./db/repository.js";
import { countInboxFiles, ensureSpoolDirs, startSpoolDrainer } from "./ingest/spool.js";
import { ReentryStateSchema } from "./schemas/reentry.js";
import { ContextItemTypeSchema, ReviewActionSchema } from "./schemas/context.js";

const config = loadConfig();
await ensureSpoolDirs(config.pluginData);
const repository = new Repository(config);
const stopDrainer = startSpoolDrainer(config, repository);
const server = new McpServer({ name: "retrace", version: config.pluginVersion });
const RESOURCE_URI = "ui://retrace/retrace-panel.html";
const sessionId = process.env.RETRACE_SESSION_ID ?? "unassigned";

async function loadWidgetHtml(): Promise<string> {
  const serverDir = dirname(fileURLToPath(import.meta.url));
  const widgetPath = resolve(serverDir, "../../web/dist/index.html");
  return readFile(widgetPath, "utf8");
}

registerAppResource(server, "retrace-panel", RESOURCE_URI, { _meta: { ui: { prefersBorder: true } } }, async () => ({
  contents: [{
    uri: RESOURCE_URI,
    mimeType: RESOURCE_MIME_TYPE,
    text: await loadWidgetHtml(),
  }],
}));

server.registerTool(
  "open_retrace_panel",
  {
    title: "Open ReTrace panel",
    description: "Open the ReTrace runtime status panel. This tool only reads local plugin status.",
    inputSchema: z.object({ sessionId: z.string().min(1).optional() }),
    _meta: {
      ui: { resourceUri: RESOURCE_URI },
      "openai/toolInvocation/invoking": "Opening ReTrace…",
      "openai/toolInvocation/invoked": "ReTrace opened.",
    },
  },
  async ({ sessionId: requestedSessionId }) => ({
    structuredContent: {
      resourceUri: RESOURCE_URI,
      initialState: repository.getPublicState(requestedSessionId ?? repository.getLatestSessionId() ?? sessionId),
    },
    content: [{ type: "text", text: "ReTrace runtime panel is ready." }],
  }),
);

server.registerTool(
  "get_retrace_state",
  {
    title: "Get ReTrace state",
    description: "Read the current public ReTrace state for a Codex session.",
    inputSchema: z.object({
      sessionId: z.string().min(1),
      afterVersion: z.number().int().nonnegative().optional(),
    }),
  },
  async ({ sessionId: requestedSessionId, afterVersion }) => {
    const state = repository.getPublicState(requestedSessionId);
    const changed = afterVersion === undefined || afterVersion < state.stateVersion;
    return {
      structuredContent: changed
        ? { changed: true, stateVersion: state.stateVersion, state }
        : { changed: false, stateVersion: state.stateVersion },
      content: [{ type: "text", text: changed ? "ReTrace state updated." : "ReTrace state unchanged." }],
    };
  },
);

server.registerTool(
  "get_runtime_status",
  {
    title: "Get ReTrace runtime status",
    description: "Read local event ingestion and SQLite health without exposing raw event contents.",
    inputSchema: z.object({}),
  },
  async () => ({
    structuredContent: {
      ...repository.getRuntimeStatus(),
      participantId: config.participantId,
      activeSessionId: repository.getLatestSessionId() ?? sessionId,
      inboxCount: await countInboxFiles(config.pluginData),
      llmMode: process.env.RETRACE_LLM_MODE ?? "api",
      uiSyncMode: "manual-or-polling-pending-m0",
    },
    content: [{ type: "text", text: "ReTrace runtime status returned." }],
  }),
);

server.registerTool(
  "record_invitation_choice",
  {
    title: "Record ReTrace invitation choice",
    description: "Record whether the participant enters Re-entry, continues the original delegation, or pauses.",
    inputSchema: z.object({
      participantId: z.string().min(1),
      sessionId: z.string().min(1),
      reentryRunId: z.string().min(1),
      stateVersion: z.number().int().positive(),
      interactionId: z.string().min(1),
      choice: z.enum(["ENTER_REENTRY", "CONTINUE_DIRECT", "PAUSE"]),
    }),
  },
  async (input) => {
    try {
      const result = repository.recordInvitationChoice(input);
      return {
        structuredContent: result,
        content: [{ type: "text", text: `Invitation choice recorded: ${input.choice}.` }],
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      repository.recordRuntimeError({
        sessionId: input.sessionId,
        reentryRunId: input.reentryRunId,
        component: "mcp",
        code: message,
        message,
        recoverable: message === "STALE_STATE" || message === "INVALID_TRANSITION",
      });
      throw error;
    }
  },
);

server.registerTool(
  "submit_pre_survey",
  {
    title: "Submit ReTrace PRE survey",
    description: "Persist PRE intervention responses and advance to the frozen Re-entry context boundary.",
    inputSchema: z.object({
      participantId: z.string().min(1),
      sessionId: z.string().min(1),
      reentryRunId: z.string().min(1),
      stateVersion: z.number().int().positive(),
      interactionId: z.string().min(1),
      response: z.object({
        questionSetVersion: z.string().min(1),
        responses: z.record(z.string().min(1), z.number().int().min(1).max(7)),
      }),
    }),
  },
  async (input) => {
    try {
      const result = repository.submitPreSurvey(input);
      return {
        structuredContent: result,
        content: [{ type: "text", text: "PRE survey recorded; Re-entry context is now available." }],
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      repository.recordRuntimeError({ sessionId: input.sessionId, reentryRunId: input.reentryRunId, component: "mcp", code: message, message, recoverable: message === "STALE_STATE" || message === "INVALID_TRANSITION" });
      throw error;
    }
  },
);

server.registerTool(
  "transition_reentry_state",
  {
    title: "Advance ReTrace state",
    description: "Advance the M3 state machine without generating content or sending a Codex prompt.",
    inputSchema: z.object({
      participantId: z.string().min(1),
      sessionId: z.string().min(1),
      reentryRunId: z.string().min(1),
      stateVersion: z.number().int().positive(),
      interactionId: z.string().min(1),
      nextState: ReentryStateSchema,
    }),
  },
  async (input) => {
    try {
      const result = repository.transitionReentryState(input);
      return {
        structuredContent: result,
        content: [{ type: "text", text: `ReTrace state advanced to ${input.nextState}.` }],
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      repository.recordRuntimeError({ sessionId: input.sessionId, reentryRunId: input.reentryRunId, component: "mcp", code: message, message, recoverable: message === "STALE_STATE" || message === "INVALID_TRANSITION" });
      throw error;
    }
  },
);

server.registerTool(
  "reconstruct_reentry_context",
  {
    title: "Reconstruct Re-entry context",
    description: "Build a structured, provenance-linked context from the frozen M3 snapshot and advance to USER_REVIEW. It never sends a prompt.",
    inputSchema: z.object({
      participantId: z.string().min(1),
      sessionId: z.string().min(1),
      reentryRunId: z.string().min(1),
      stateVersion: z.number().int().positive(),
      interactionId: z.string().min(1),
    }),
  },
  async (input) => {
    try {
      const result = repository.reconstructReentryContext(input);
      return {
        structuredContent: result,
        content: [{ type: "text", text: result.state.reconstructionError ? "Re-entry context reconstruction failed open; no prompt was sent." : "Re-entry context reconstructed for review." }],
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      repository.recordRuntimeError({ sessionId: input.sessionId, reentryRunId: input.reentryRunId, component: "mcp", code: message, message, recoverable: message === "STALE_STATE" || message === "INVALID_TRANSITION" });
      throw error;
    }
  },
);

server.registerTool(
  "record_context_review",
  {
    title: "Review Re-entry context",
    description: "Record a participant confirmation, edit, rejection, or addition for structured Re-entry context. This does not generate or send a next prompt.",
    inputSchema: z.object({
      participantId: z.string().min(1),
      sessionId: z.string().min(1),
      reentryRunId: z.string().min(1),
      stateVersion: z.number().int().positive(),
      interactionId: z.string().min(1),
      itemType: ContextItemTypeSchema,
      itemId: z.string().min(1).optional(),
      action: ReviewActionSchema,
      before: z.unknown().optional(),
      after: z.unknown().optional(),
    }),
  },
  async (input) => {
    try {
      const result = repository.recordContextReview(input);
      return {
        structuredContent: result,
        content: [{ type: "text", text: `Context review recorded: ${input.action}.` }],
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      repository.recordRuntimeError({ sessionId: input.sessionId, reentryRunId: input.reentryRunId, component: "mcp", code: message, message, recoverable: message === "STALE_STATE" || message === "INVALID_TRANSITION" });
      throw error;
    }
  },
);

server.registerTool(
  "create_investigation",
  {
    title: "Create bounded investigation",
    description: "Create a narrow evidence-gathering delegation from a reviewed uncertainty or evidence requirement. It does not change the main Re-entry state.",
    inputSchema: z.object({
      participantId: z.string().min(1), sessionId: z.string().min(1), reentryRunId: z.string().min(1),
      stateVersion: z.number().int().positive(), interactionId: z.string().min(1),
      targetReviewItemId: z.string().min(1).optional(), targetItemType: z.enum(["EVIDENCE", "UNCERTAINTY"]),
      question: z.string().min(1).optional(), evidenceRequirement: z.string().min(1).optional(),
    }),
  },
  async (input) => {
    try {
      const result = repository.createInvestigation(input);
      return { structuredContent: result, content: [{ type: "text", text: "Bounded investigation created in USER_REVIEW." }] };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      repository.recordRuntimeError({ sessionId: input.sessionId, reentryRunId: input.reentryRunId, component: "mcp", code: message, message, recoverable: true });
      throw error;
    }
  },
);

server.registerTool(
  "edit_investigation",
  {
    title: "Edit investigation prompt",
    description: "Edit a bounded investigation prompt without sending it or changing the main Re-entry state.",
    inputSchema: z.object({
      participantId: z.string().min(1), sessionId: z.string().min(1), reentryRunId: z.string().min(1),
      stateVersion: z.number().int().positive(), interactionId: z.string().min(1), investigationId: z.string().min(1), editedPrompt: z.string().min(1),
    }),
  },
  async (input) => {
    const result = repository.editInvestigation(input);
    return { structuredContent: result, content: [{ type: "text", text: "Investigation prompt edited; it has not been sent." }] };
  },
);

server.registerTool(
  "copy_investigation",
  {
    title: "Copy investigation prompt",
    description: "Record explicit copy authorization for a bounded investigation prompt. This does not release the main Re-entry flow.",
    inputSchema: z.object({
      participantId: z.string().min(1), sessionId: z.string().min(1), reentryRunId: z.string().min(1),
      stateVersion: z.number().int().positive(), interactionId: z.string().min(1), investigationId: z.string().min(1),
    }),
  },
  async (input) => {
    const result = repository.copyInvestigation(input);
    return { structuredContent: result, content: [{ type: "text", text: "Investigation prompt copy recorded." }] };
  },
);

server.registerTool(
  "record_investigation_result",
  {
    title: "Record investigation result",
    description: "Associate returned event IDs and evidence candidates with an investigation. Candidates remain pending user review and are not accepted automatically.",
    inputSchema: z.object({
      participantId: z.string().min(1), sessionId: z.string().min(1), reentryRunId: z.string().min(1),
      stateVersion: z.number().int().positive(), interactionId: z.string().min(1), investigationId: z.string().min(1),
      resultEventIds: z.array(z.string().min(1)),
      evidenceCandidates: z.array(z.object({ claim: z.string().min(1), sourceEventIds: z.array(z.string().min(1)) })),
    }),
  },
  async (input) => {
    const result = repository.recordInvestigationResult(input);
    return { structuredContent: result, content: [{ type: "text", text: "Investigation result recorded as pending review." }] };
  },
);

server.registerTool(
  "generate_next_prompt",
  {
    title: "Generate reviewed next prompt",
    description: "Generate a structured candidate delegation from the reviewed Re-entry state. It never sends the prompt.",
    inputSchema: z.object({
      participantId: z.string().min(1), sessionId: z.string().min(1), reentryRunId: z.string().min(1),
      stateVersion: z.number().int().positive(), interactionId: z.string().min(1),
    }),
  },
  async (input) => {
    try {
      const result = repository.generateNextPrompt(input);
      return { structuredContent: result, content: [{ type: "text", text: "Candidate next prompt generated; explicit copy or send is still required." }] };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      repository.recordRuntimeError({ sessionId: input.sessionId, reentryRunId: input.reentryRunId, component: "mcp", code: message, message, recoverable: true });
      throw error;
    }
  },
);

server.registerTool(
  "edit_next_prompt",
  {
    title: "Edit next prompt",
    description: "Persist a user-edited candidate next prompt while keeping the generated version intact.",
    inputSchema: z.object({
      participantId: z.string().min(1), sessionId: z.string().min(1), reentryRunId: z.string().min(1),
      stateVersion: z.number().int().positive(), interactionId: z.string().min(1), editedPrompt: z.string().min(1),
    }),
  },
  async (input) => {
    const result = repository.editNextPrompt(input);
    return { structuredContent: result, content: [{ type: "text", text: "Next prompt edited; it has not been sent." }] };
  },
);

server.registerTool(
  "complete_reentry",
  {
    title: "Complete Re-entry authorization",
    description: "Release Re-entry control only after explicit COPY, SENT, CANCEL, or fail-open completion. SENT is a confirmation after the host follow-up succeeds.",
    inputSchema: z.object({
      participantId: z.string().min(1), sessionId: z.string().min(1), reentryRunId: z.string().min(1),
      stateVersion: z.number().int().positive(), interactionId: z.string().min(1),
      action: z.enum(["COPY", "SENT", "CANCEL", "FAILED_OPEN"]), finalPrompt: z.string().optional(),
    }),
  },
  async (input) => {
    try {
      const result = repository.completeReentry(input);
      return { structuredContent: result, content: [{ type: "text", text: `Re-entry completed: ${input.action}.` }] };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      repository.recordRuntimeError({ sessionId: input.sessionId, reentryRunId: input.reentryRunId, component: "mcp", code: message, message, recoverable: true });
      throw error;
    }
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);

function shutdown(): void {
  stopDrainer();
  repository.close();
}

process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
process.once("exit", () => {
  stopDrainer();
  if (repository.db.open) repository.close();
});
