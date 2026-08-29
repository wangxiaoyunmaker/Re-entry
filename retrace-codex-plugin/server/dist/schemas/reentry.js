import { z } from "zod";
import { IssueChainSchema, StallAssessmentSchema } from "./stall.js";
export const ReentryStateSchema = z.enum([
    "PRE_SURVEY",
    "REENTRY_CONTEXT",
    "USER_REVIEW",
    "NEXT_PROMPT_READY",
    "RESUMABLE",
]);
export const M3InputSchema = z.object({
    reentry_run_id: z.string().min(1),
    issue_chain: IssueChainSchema,
    stall_assessment: StallAssessmentSchema,
    pre_prompt_text: z.string().min(1),
    trigger_event_id: z.string().min(1),
});
export const ReentrySnapshotSchema = z.object({
    snapshot_id: z.string().min(1),
    reentry_run_id: z.string().min(1),
    snapshot_version: z.number().int().positive(),
    as_of_event_id: z.string().min(1),
    input: M3InputSchema,
    created_at: z.string().min(1),
});
export const PreSurveyResponseSchema = z.object({
    questionSetVersion: z.string().min(1),
    responses: z.record(z.string().min(1), z.number().int().min(1).max(7)).refine((responses) => Object.keys(responses).length > 0, "at least one PRE response is required"),
});
export const PublicReentryContextSchema = z.object({
    snapshotVersion: z.number().int().positive(),
    issueSummary: z.string().min(1),
    unmetReportCount: z.number().int().nonnegative(),
    triggerEventId: z.string().min(1),
});
