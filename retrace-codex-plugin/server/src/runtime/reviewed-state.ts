import { AgentClaimSchema, EvidenceItemSchema, ExplanationItemSchema, GovernanceConstraintKindSchema, GovernanceConstraintSchema, GoalItemSchema, UncertaintyItemSchema, type AgentClaim, type ContextReview, type EvidenceItem, type ExplanationItem, type GovernanceConstraint, type GoalItem, type UncertaintyItem } from "../schemas/context.js";
import { ReviewedReentryStateSchema, type ReviewedReentryState, type ReviewedUserItem } from "../schemas/m3c.js";
import type { Reconstruction } from "../schemas/context.js";

type MutableRecord = Record<string, unknown>;

function latestReviews(reviews: ContextReview[]): Map<string, ContextReview> {
  return new Map(reviews.map((review) => [`${review.itemType}:${review.itemId}`, review]));
}

function editedValue<T extends MutableRecord>(item: T, review: ContextReview | undefined, textKey: "text" | "claim"): T {
  if (!review || review.action !== "EDIT") return item;
  if (typeof review.after === "string" && review.after.trim()) return { ...item, [textKey]: review.after.trim() } as T;
  if (typeof review.after === "object" && review.after !== null) return { ...item, ...(review.after as MutableRecord) } as T;
  return item;
}

function reviewedItem<T extends MutableRecord>(item: T, review: ContextReview | undefined, textKey: "text" | "claim"): T {
  return editedValue(item, review, textKey);
}

function isAccepted(review: ContextReview | undefined): boolean {
  return review?.action === "CONFIRM" || review?.action === "EDIT";
}

function addedText(review: ContextReview): string | undefined {
  if (typeof review.after === "string" && review.after.trim()) return review.after.trim();
  if (typeof review.after === "object" && review.after !== null && typeof (review.after as MutableRecord).text === "string") return ((review.after as MutableRecord).text as string).trim();
  if (typeof review.after === "object" && review.after !== null && typeof (review.after as MutableRecord).claim === "string") return ((review.after as MutableRecord).claim as string).trim();
  return undefined;
}

function userItem(review: ContextReview, text: string, kind?: string): ReviewedUserItem {
  const payload = typeof review.after === "object" && review.after !== null ? review.after as MutableRecord : {};
  return {
    id: review.itemId,
    text,
    ...(kind ? { kind } : {}),
    sourceEventIds: Array.isArray(payload.sourceEventIds) ? payload.sourceEventIds.filter((id): id is string => typeof id === "string") : [],
    sourceType: "USER_REVIEW",
    sourceReviewId: review.reviewId,
    timestamp: typeof payload.timestamp === "string" ? payload.timestamp : review.createdAt,
  };
}

export type InvestigationEvidenceCandidate = {
  id: string;
  claim: string;
  sourceEventIds: string[];
  investigationId: string;
  resultId: string;
  resultEventIds: string[];
  createdAt: string;
};

function reviewText(review: ContextReview, fallback: string): string {
  return addedText(review) ?? fallback;
}

function governanceConstraint(review: ContextReview, fallback?: GovernanceConstraint): GovernanceConstraint | undefined {
  const payload = typeof review.after === "object" && review.after !== null ? review.after as MutableRecord : {};
  const text = reviewText(review, fallback?.text ?? "");
  if (!text) return undefined;
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

function candidateEvidence(candidate: InvestigationEvidenceCandidate, review: ContextReview): EvidenceItem {
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

export function deriveReviewedReentryState(
  reconstruction: Reconstruction,
  reviews: ContextReview[],
  reentryRunId: string,
  investigationCandidates: InvestigationEvidenceCandidate[] = [],
): ReviewedReentryState {
  const latest = latestReviews(reviews);
  const goal: GoalItem[] = reconstruction.goal.flatMap((item) => {
    const review = latest.get(`GOAL:${item.id}`);
    return isAccepted(review) ? [GoalItemSchema.parse(reviewedItem(item, review, "text"))] : [];
  });
  const acceptedEvidence: EvidenceItem[] = [];
  const rejectedEvidence: EvidenceItem[] = [];
  for (const item of reconstruction.evidenceItems) {
    const review = latest.get(`EVIDENCE:${item.id}`);
    if (isAccepted(review)) acceptedEvidence.push(EvidenceItemSchema.parse(reviewedItem(item, review, "claim")));
    if (review?.action === "REJECT") rejectedEvidence.push(item);
  }
  for (const candidate of investigationCandidates) {
    const review = latest.get(`EVIDENCE:${candidate.id}`);
    if (!review || !isAccepted(review) && review.action !== "REJECT") continue;
    const evidence = candidateEvidence(candidate, review);
    if (isAccepted(review)) acceptedEvidence.push(evidence);
    if (review.action === "REJECT") rejectedEvidence.push(evidence);
  }
  const acceptedExplanations: ExplanationItem[] = [];
  const rejectedExplanations: ExplanationItem[] = [];
  for (const item of reconstruction.explanations) {
    const review = latest.get(`EXPLANATION:${item.id}`);
    if (isAccepted(review)) acceptedExplanations.push(ExplanationItemSchema.parse(reviewedItem(item, review, "text")));
    if (review?.action === "REJECT") rejectedExplanations.push(item);
  }
  const unresolvedUncertainties: UncertaintyItem[] = reconstruction.uncertainties.flatMap((item) => {
    const review = latest.get(`UNCERTAINTY:${item.id}`);
    if (review?.action === "REJECT") return [];
    return [UncertaintyItemSchema.parse(reviewedItem(item, review, "text"))];
  });
  const rejectedClaims: AgentClaim[] = reconstruction.agentClaims.flatMap((claim) => {
    const review = latest.get(`AGENT_CLAIM:${claim.claimId}`);
    return review?.action === "REJECT" ? [AgentClaimSchema.parse(claim)] : [];
  });
  const addedObservations: ReviewedUserItem[] = [];
  const evidenceRequirements: ReviewedUserItem[] = [];
  const governanceAdds = new Map<string, GovernanceConstraint>();
  for (const review of reviews.filter((candidate) => candidate.action === "ADD")) {
    const text = addedText(review);
    if (!text) continue;
    const payload = typeof review.after === "object" && review.after !== null ? review.after as MutableRecord : {};
    if (review.itemType === "EVIDENCE" && payload.kind === "EVIDENCE_REQUIREMENT") evidenceRequirements.push(userItem(review, text, "EVIDENCE_REQUIREMENT"));
    else if (review.itemType === "EVIDENCE") addedObservations.push(userItem(review, text, typeof payload.kind === "string" ? payload.kind : "USER_OBSERVATION"));
    else if (review.itemType === "UNCERTAINTY") unresolvedUncertainties.push(UncertaintyItemSchema.parse({ id: review.itemId, text, sourceEventIds: [], relatedClaimIds: [], timestamp: review.createdAt }));
    else if (review.itemType === "GOVERNANCE_CONSTRAINT") {
      const constraint = governanceConstraint(review);
      if (constraint) governanceAdds.set(review.itemId, constraint);
    }
  }
  for (const [itemId, added] of governanceAdds) {
    const latestReview = latest.get(`GOVERNANCE_CONSTRAINT:${itemId}`);
    if (!latestReview || latestReview.action === "REJECT") continue;
    const constraint = latestReview.action === "EDIT" ? governanceConstraint(latestReview, added) : added;
    if (constraint) governanceAdds.set(itemId, constraint);
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
