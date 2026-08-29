import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { loadConfig } from "../config.js";
import { Repository } from "../db/repository.js";
import { RawHookEventSchema } from "../schemas/events.js";
import { classifyPrompt } from "./turn-classifier.js";
export function shouldSoftGate(result) {
    return result.stallAssessment.eligible && result.classification.confidence === "HIGH";
}
function rawEventFromHookInput(input) {
    const payloadJson = JSON.stringify(input);
    return RawHookEventSchema.parse({
        schema_version: "retrace-raw-hook-event-v2",
        event_id: randomUUID(),
        received_at: new Date().toISOString(),
        hook_event_name: "UserPromptSubmit",
        session_id: input.session_id,
        turn_id: typeof input.turn_id === "string" ? input.turn_id : null,
        tool_use_id: null,
        cwd: typeof input.cwd === "string" ? input.cwd : null,
        payload: input,
        payload_hash: createHash("sha256").update(payloadJson).digest("hex"),
    });
}
export function assessSubmit(input, config = loadConfig()) {
    const repository = new Repository(config);
    try {
        const sessionId = typeof input.session_id === "string" ? input.session_id : "";
        const prompt = typeof input.prompt === "string" ? input.prompt : "";
        const active = repository.getActiveIssueChain(sessionId);
        const classification = classifyPrompt({ prompt, activeIssueSummary: active?.issueSummary });
        const result = repository.assessUserPrompt(rawEventFromHookInput(input), classification);
        const mode = process.env.RETRACE_INVITATION_MODE === "soft_gate" ? "soft_gate" : "observe_only";
        return { ...result, mode, shouldBlock: mode === "soft_gate" && shouldSoftGate(result) };
    }
    finally {
        repository.close();
    }
}
if (process.argv[1] && process.argv[1].endsWith("assess-submit.js")) {
    try {
        const input = JSON.parse(readFileSync(0, "utf8"));
        const result = assessSubmit(input);
        if (result.shouldBlock)
            process.stdout.write(JSON.stringify({ decision: "block", reason: "ReTrace invitation pending." }));
    }
    catch (error) {
        console.error(`[retrace assessor] ${error instanceof Error ? error.message : String(error)}`);
        // Fail open: no stdout means Codex continues with the original prompt.
    }
}
