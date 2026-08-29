import { AgentClaimSchema, EvidenceItemSchema, ExplanationItemSchema, GovernanceConstraintKindSchema, GovernanceConstraintSchema, GoalItemSchema, UncertaintyItemSchema } from "../schemas/context.js";
import { ReviewedReentryStateSchema } from "../schemas/m3c.js";
function latestReviews(reviews) {
    return new Map(reviews.map((review) => [`${review.itemType}:${review.itemId}`, review]));
}
function editedValue(item, review, textKey) {
    if (!review || review.action !== "EDIT")
        return item;
    if (typeof review.after === "string" && review.after.trim())
        return { ...item, [textKey]: review.after.trim() };
    if (typeof review.after === "object" && review.after !== null)
        return { ...item, ...review.after };
    return item;
}
function reviewedItem(item, review, textKey) {
    return editedValue(item, review, textKey);
}
function isAccepted(review) {
    return review?.action === "CONFIRM" || review?.action === "EDIT";
}
function addedText(review) {
    if (typeof review.after === "string" && review.after.trim())
        return review.after.trim();
    if (typeof review.after === "object" && review.after !== null && typeof review.after.text === "string")
        return review.after.text.trim();
    if (typeof review.after === "object" && review.after !== null && typeof review.after.claim === "string")
        return review.after.claim.trim();
    return undefined;
}
function userItem(review, text, kind) {
    const payload = typeof review.after === "object" && review.after !== null ? review.after : {};
    return {
        id: review.itemId,
        text,
        ...(kind ? { kind } : {}),
        sourceEventIds: Array.isArray(payload.sourceEventIds) ? payload.sourceEventIds.filter((id) => typeof id === "string") : [],
        sourceType: "USER_REVIEW",
        sourceReviewId: review.reviewId,
        timestamp: typeof payload.timestamp === "string" ? payload.timestamp : review.createdAt,
    };
}
function reviewText(review, fallback) {
    return addedText(review) ?? fallback;
}
function governanceConstraint(review, fallback) {
    const payload = typeof review.after === "object" && review.after !== null ? review.after : {};
    const text = reviewText(review, fallback?.text ?? "");
    if (!text)
        return undefined;
    const kindValue = typeof payload.kind === "string" ? payload.kind : fallback?.kind ?? "OTHER";
    const kind = GovernanceConstraintKindSchema.parse(kindValue);
    return GovernanceConstraintSchema.parse({
        id: review.itemId,
        text,
        kind,
        sourceReviewId: review.reviewId,
        source: review.action === "EDIT" ? "USER_EDIT" : fallback?.source ?? "USER_ADD",
        createdAt: review.createdAt,
    });
}
function candidateEvidence(candidate, review) {
    const originalCandidateValue = candidate.claim;
    const reviewedValue = reviewText(review, originalCandidateValue);
    return EvidenceItemSchema.parse({
        id: candidate.id,
        kind: "OTHER",
        claim: reviewedValue,
        sourceEventIds: candidate.sourceEventIds,
        sourceType: "CODEX_HOOK",
        timestamp: candidate.createdAt,
        verificationStatus: review.action === "REJECT" ? "CONTRADICTED" : "USER_CONFIRMED",
        candidateProvenance: {
            kind: "INVESTIGATION_CANDIDATE",
            candidateId: candidate.id,
            investigationId: candidate.investigationId,
            resultId: candidate.resultId,
            sourceResultEventIds: candidate.resultEventIds,
            originalCandidateValue,
            reviewAction: review.action,
            reviewedValue,
            reviewId: review.reviewId,
            timestamp: review.createdAt,
        },
    });
}
export function deriveReviewedReentryState(reconstruction, reviews, reentryRunId, investigationCandidates = []) {
    const latest = latestReviews(reviews);
    const goal = reconstruction.goal.flatMap((item) => {
        const review = latest.get(`GOAL:${item.id}`);
        return isAccepted(review) ? [GoalItemSchema.parse(reviewedItem(item, review, "text"))] : [];
    });
    const acceptedEvidence = [];
    const rejectedEvidence = [];
    for (const item of reconstruction.evidenceItems) {
        const review = latest.get(`EVIDENCE:${item.id}`);
        if (isAccepted(review))
            acceptedEvidence.push(EvidenceItemSchema.parse(reviewedItem(item, review, "claim")));
        if (review?.action === "REJECT")
            rejectedEvidence.push(item);
    }
    for (const candidate of investigationCandidates) {
        const review = latest.get(`EVIDENCE:${candidate.id}`);
        if (!review || !isAccepted(review) && review.action !== "REJECT")
            continue;
        const evidence = candidateEvidence(candidate, review);
        if (isAccepted(review))
            acceptedEvidence.push(evidence);
        if (review.action === "REJECT")
            rejectedEvidence.push(evidence);
    }
    const acceptedExplanations = [];
    const rejectedExplanations = [];
    for (const item of reconstruction.explanations) {
        const review = latest.get(`EXPLANATION:${item.id}`);
        if (isAccepted(review))
            acceptedExplanations.push(ExplanationItemSchema.parse(reviewedItem(item, review, "text")));
        if (review?.action === "REJECT")
            rejectedExplanations.push(item);
    }
    const unresolvedUncertainties = reconstruction.uncertainties.flatMap((item) => {
        const review = latest.get(`UNCERTAINTY:${item.id}`);
        if (review?.action === "REJECT")
            return [];
        return [UncertaintyItemSchema.parse(reviewedItem(item, review, "text"))];
    });
    const rejectedClaims = reconstruction.agentClaims.flatMap((claim) => {
        const review = latest.get(`AGENT_CLAIM:${claim.claimId}`);
        return review?.action === "REJECT" ? [AgentClaimSchema.parse(claim)] : [];
    });
    const addedObservations = [];
    const evidenceRequirements = [];
    const governanceAdds = new Map();
    for (const review of reviews.filter((candidate) => candidate.action === "ADD")) {
        const text = addedText(review);
        if (!text)
            continue;
        const payload = typeof review.after === "object" && review.after !== null ? review.after : {};
        if (review.itemType === "EVIDENCE" && payload.kind === "EVIDENCE_REQUIREMENT")
            evidenceRequirements.push(userItem(review, text, "EVIDENCE_REQUIREMENT"));
        else if (review.itemType === "EVIDENCE")
            addedObservations.push(userItem(review, text, typeof payload.kind === "string" ? payload.kind : "USER_OBSERVATION"));
        else if (review.itemType === "UNCERTAINTY")
            unresolvedUncertainties.push(UncertaintyItemSchema.parse({ id: review.itemId, text, sourceEventIds: [], relatedClaimIds: [], timestamp: review.createdAt }));
        else if (review.itemType === "GOVERNANCE_CONSTRAINT") {
            const constraint = governanceConstraint(review);
            if (constraint)
                governanceAdds.set(review.itemId, constraint);
        }
    }
    for (const [itemId, added] of governanceAdds) {
        const latestReview = latest.get(`GOVERNANCE_CONSTRAINT:${itemId}`);
        if (!latestReview || latestReview.action === "REJECT")
            continue;
        const constraint = latestReview.action === "EDIT" ? governanceConstraint(latestReview, added) : added;
        if (constraint)
            governanceAdds.set(itemId, constraint);
    }
    return ReviewedReentryStateSchema.parse({
        reentryRunId,
        reconstructionId: reconstruction.reconstructionId,
        snapshotVersion: reconstruction.snapshotVersion,
        reviewVersion: reviews.length,
        goal,
        acceptedEvidence,
        rejectedEvidence,
        acceptedExplanations,
        rejectedExplanations,
        unresolvedUncertainties,
        addedObservations,
        evidenceRequirements,
        governanceConstraints: [...governanceAdds.values()],
        rejectedClaims,
        sourceReviewIds: reviews.map((review) => review.reviewId),
    });
}
