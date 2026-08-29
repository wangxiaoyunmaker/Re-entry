import { z } from "zod";
export const ContextItemTypeSchema = z.enum([
    "GOAL",
    "EVIDENCE",
    "EXPLANATION",
    "UNCERTAINTY",
    "AGENT_CLAIM",
    "GOVERNANCE_CONSTRAINT",
]);
export const ReviewActionSchema = z.enum(["CONFIRM", "EDIT", "REJECT", "ADD"]);
export const EvidenceKindSchema = z.enum([
    "TOOL_RESULT",
    "TEST_RESULT",
    "BUILD_RESULT",
    "FILE_INSPECTION",
    "ERROR",
    "COMMAND_OUTPUT",
    "USER_OBSERVATION",
    "VERIFIED_STATE",
    "ARTIFACT_CHANGE",
    "OTHER",
]);
export const ExplanationKindSchema = z.enum([
    "VERIFIED_EXPLANATION",
    "AGENT_HYPOTHESIS",
    "WORKING_INTERPRETATION",
]);
export const VerificationStatusSchema = z.enum([
    "UNVERIFIED",
    "SUPPORTED",
    "CONTRADICTED",
    "USER_CONFIRMED",
]);
const SourceEventIdsSchema = z.array(z.string().min(1));
export const GoalItemSchema = z.object({
    id: z.string().min(1),
    text: z.string().min(1),
    sourceEventIds: SourceEventIdsSchema,
    sourceType: z.enum(["CODEX_HOOK", "USER_REVIEW"]),
    timestamp: z.string().min(1),
});
export const EvidenceItemSchema = z.object({
    id: z.string().min(1),
    kind: EvidenceKindSchema,
    claim: z.string().min(1),
    sourceEventIds: SourceEventIdsSchema,
    sourceType: z.enum(["CODEX_HOOK", "USER_REVIEW"]),
    timestamp: z.string().min(1),
    verificationStatus: VerificationStatusSchema,
    candidateProvenance: z.object({
        kind: z.literal("INVESTIGATION_CANDIDATE"),
        candidateId: z.string().min(1),
        investigationId: z.string().min(1),
        resultId: z.string().min(1),
        sourceResultEventIds: z.array(z.string().min(1)),
        originalCandidateValue: z.string().min(1),
        reviewAction: z.enum(["CONFIRM", "EDIT", "REJECT"]),
        reviewedValue: z.string().min(1),
        reviewId: z.string().min(1),
        timestamp: z.string().min(1),
    }).optional(),
});
export const ExplanationItemSchema = z.object({
    id: z.string().min(1),
    text: z.string().min(1),
    kind: ExplanationKindSchema,
    sourceEventIds: SourceEventIdsSchema,
    supportingEvidenceIds: z.array(z.string().min(1)),
    timestamp: z.string().min(1),
});
export const UncertaintyItemSchema = z.object({
    id: z.string().min(1),
    text: z.string().min(1),
    sourceEventIds: SourceEventIdsSchema,
    relatedClaimIds: z.array(z.string().min(1)),
    timestamp: z.string().min(1),
});
export const AgentClaimKindSchema = z.enum(["STATE", "CAUSE", "COMPLETION", "SUCCESS", "NEXT_STEP", "OTHER"]);
export const AgentClaimSchema = z.object({
    claimId: z.string().min(1),
    kind: AgentClaimKindSchema,
    text: z.string().min(1),
    sourceEventId: z.string().min(1),
    supportingEvidenceIds: z.array(z.string().min(1)),
    verificationStatus: VerificationStatusSchema,
    timestamp: z.string().min(1),
});
export const GovernanceConstraintKindSchema = z.enum([
    "SCOPE",
    "PROCESS",
    "EVIDENCE",
    "AUTHORITY",
    "DO_NOT_ASSUME",
    "OTHER",
]);
export const GovernanceConstraintSchema = z.object({
    id: z.string().min(1),
    text: z.string().min(1),
    kind: GovernanceConstraintKindSchema,
    sourceReviewId: z.string().min(1),
    source: z.enum(["USER_ADD", "USER_EDIT"]),
    createdAt: z.string().min(1),
});
export const ReconstructionSchema = z.object({
    reconstructionId: z.string().min(1),
    snapshotVersion: z.number().int().positive(),
    generatedAt: z.string().min(1),
    goal: z.array(GoalItemSchema).min(1),
    evidenceItems: z.array(EvidenceItemSchema),
    explanations: z.array(ExplanationItemSchema),
    uncertainties: z.array(UncertaintyItemSchema).min(1),
    agentClaims: z.array(AgentClaimSchema),
});
export const ContextReviewSchema = z.object({
    reviewId: z.string().min(1),
    reentryRunId: z.string().min(1),
    reconstructionId: z.string().min(1),
    itemType: ContextItemTypeSchema,
    itemId: z.string().min(1),
    action: ReviewActionSchema,
    before: z.unknown().optional(),
    after: z.unknown().optional(),
    interactionId: z.string().min(1),
    createdAt: z.string().min(1),
});
export function validateReconstructionProvenance(reconstruction, frozenEventIds) {
    const evidenceIds = new Set(reconstruction.evidenceItems.map((item) => item.id));
    const ids = [
        ...reconstruction.goal.flatMap((item) => item.sourceEventIds),
        ...reconstruction.evidenceItems.flatMap((item) => item.sourceEventIds),
        ...reconstruction.explanations.flatMap((item) => item.sourceEventIds),
        ...reconstruction.uncertainties.flatMap((item) => item.sourceEventIds),
        ...reconstruction.agentClaims.map((claim) => claim.sourceEventId),
    ];
    if (ids.some((id) => !frozenEventIds.has(id)))
        throw new Error("INVALID_RECONSTRUCTION_PROVENANCE");
    for (const item of reconstruction.explanations) {
        if (item.supportingEvidenceIds.some((id) => !evidenceIds.has(id)))
            throw new Error("INVALID_EVIDENCE_REFERENCE");
    }
    for (const claim of reconstruction.agentClaims) {
        if (claim.supportingEvidenceIds.some((id) => !evidenceIds.has(id)))
            throw new Error("INVALID_EVIDENCE_REFERENCE");
    }
}
