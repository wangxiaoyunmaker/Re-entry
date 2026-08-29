import { z } from "zod";
import { AgentClaimSchema, EvidenceItemSchema, ExplanationItemSchema, GovernanceConstraintSchema, GoalItemSchema, UncertaintyItemSchema } from "./context.js";

export const ReviewedUserItemSchema = z.object({
  id: z.string().min(1),
  text: z.string().min(1),
  kind: z.string().min(1).optional(),
  sourceEventIds: z.array(z.string().min(1)),
  sourceType: z.literal("USER_REVIEW"),
  sourceReviewId: z.string().min(1),
  timestamp: z.string().min(1),
});
export type ReviewedUserItem = z.infer<typeof ReviewedUserItemSchema>;

export const ReviewedReentryStateSchema = z.object({
  reentryRunId: z.string().min(1),
  reconstructionId: z.string().min(1),
  snapshotVersion: z.number().int().positive(),
  reviewVersion: z.number().int().nonnegative(),
  goal: z.array(GoalItemSchema),
  acceptedEvidence: z.array(EvidenceItemSchema),
  rejectedEvidence: z.array(EvidenceItemSchema),
  acceptedExplanations: z.array(ExplanationItemSchema),
  rejectedExplanations: z.array(ExplanationItemSchema),
  unresolvedUncertainties: z.array(UncertaintyItemSchema),
  addedObservations: z.array(ReviewedUserItemSchema),
  evidenceRequirements: z.array(ReviewedUserItemSchema),
  governanceConstraints: z.array(GovernanceConstraintSchema),
  rejectedClaims: z.array(AgentClaimSchema),
  sourceReviewIds: z.array(z.string().min(1)),
});
export type ReviewedReentryState = z.infer<typeof ReviewedReentryStateSchema>;

export const InvestigationActionSchema = z.enum(["GENERATE", "EDIT", "COPY", "CANCEL", "RESULT_IMPORTED"]);
export type InvestigationAction = z.infer<typeof InvestigationActionSchema>;

export const InvestigationStatusSchema = z.enum(["DRAFT", "EDITED", "COPIED", "RESULT_PENDING_REVIEW", "CANCELLED", "FAILED_OPEN"]);
export type InvestigationStatus = z.infer<typeof InvestigationStatusSchema>;

export const InvestigationSchema = z.object({
  investigationId: z.string().min(1),
  reentryRunId: z.string().min(1),
  targetReviewItemId: z.string().min(1).optional(),
  targetItemType: z.enum(["EVIDENCE", "UNCERTAINTY"]),
  questionToVerify: z.string().min(1),
  evidenceRequirement: z.string().min(1),
  relevantContext: z.array(z.string().min(1)),
  constraints: z.array(z.string().min(1)),
  expectedObservableResult: z.string().min(1),
  generatedPrompt: z.string().min(1),
  editedPrompt: z.string().min(1).optional(),
  action: InvestigationActionSchema,
  status: InvestigationStatusSchema,
  sourceReviewIds: z.array(z.string().min(1)),
  createdAt: z.string().min(1),
  updatedAt: z.string().min(1),
  result: z.lazy(() => InvestigationResultSchema).optional(),
});
export type Investigation = z.infer<typeof InvestigationSchema>;

export const InvestigationResultSchema = z.object({
  resultId: z.string().min(1),
  investigationId: z.string().min(1),
  resultEventIds: z.array(z.string().min(1)),
  evidenceCandidateIds: z.array(z.string().min(1)),
  evidenceCandidates: z.array(z.object({ id: z.string().min(1), claim: z.string().min(1), sourceEventIds: z.array(z.string().min(1)) })),
  createdAt: z.string().min(1),
});
export type InvestigationResult = z.infer<typeof InvestigationResultSchema>;

export const NextPromptDraftSchema = z.object({
  promptId: z.string().min(1),
  reentryRunId: z.string().min(1),
  reviewVersion: z.number().int().nonnegative(),
  objective: z.string().min(1),
  knownFacts: z.array(z.string().min(1)),
  openQuestions: z.array(z.string().min(1)),
  evidenceRequirements: z.array(z.string().min(1)),
  constraints: z.array(z.string().min(1)),
  requestedAction: z.string().min(1),
  verificationCriteria: z.array(z.string().min(1)),
  promptText: z.string().min(1),
  editedPrompt: z.string().min(1).optional(),
  generatedAt: z.string().min(1),
  editedAt: z.string().min(1).optional(),
});
export type NextPromptDraft = z.infer<typeof NextPromptDraftSchema>;

export const ResumeCompletionReasonSchema = z.enum(["COPIED", "SENT", "CANCELLED", "FAILED_OPEN"]);
export type ResumeCompletionReason = z.infer<typeof ResumeCompletionReasonSchema>;

export const PreSurveyQuestionSchema = z.object({
  id: z.string().min(1),
  text: z.string().min(1),
});
export type PreSurveyQuestion = z.infer<typeof PreSurveyQuestionSchema>;

export const PRE_SURVEY_QUESTION_SET_VERSION = "RETRACE-PRE-V1";
export const PRE_SURVEY_QUESTIONS: PreSurveyQuestion[] = [
  { id: "system_understanding", text: "我目前清楚系统正在发生什么。" },
  { id: "agent_actions", text: "我知道 Agent 最近做了哪些关键操作。" },
  { id: "claim_credibility", text: "我有足够依据判断 Agent 当前的说法是否可信。" },
  { id: "next_action", text: "我知道接下来应该让 Agent 做什么。" },
  { id: "continuation_confidence", text: "我对继续当前任务有信心。" },
];
