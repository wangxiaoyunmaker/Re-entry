import { z } from "zod";

export const IntentSchema = z.enum([
  "INITIAL_REQUEST",
  "CORRECTIVE",
  "INVESTIGATION",
  "NEW_REQUIREMENT",
  "ACCEPTANCE",
  "OTHER",
]);
export type Intent = z.infer<typeof IntentSchema>;

export const InformationGainSchema = z.object({
  newObservation: z.boolean(),
  newRequirement: z.boolean(),
  newEvidence: z.boolean(),
  investigationDirection: z.boolean(),
});
export type InformationGain = z.infer<typeof InformationGainSchema>;

export const TurnClassificationSchema = z.object({
  sameIssue: z.boolean(),
  issueSummary: z.string().min(1),
  intent: IntentSchema,
  reportsTargetUnmet: z.boolean(),
  informationGain: InformationGainSchema,
  confidence: z.enum(["LOW", "MEDIUM", "HIGH"]),
});
export type TurnClassification = z.infer<typeof TurnClassificationSchema>;

export const IssueChainSchema = z.object({
  issueChainId: z.string().min(1),
  sessionId: z.string().min(1),
  issueKey: z.string().min(1),
  issueSummary: z.string().min(1),
  status: z.enum(["ACTIVE", "CLOSED"]),
  unmetReportCount: z.number().int().nonnegative(),
  firstEventId: z.string().min(1),
  lastEventId: z.string().min(1),
  cooldownUntilUserTurn: z.number().int().nonnegative(),
});
export type IssueChain = z.infer<typeof IssueChainSchema>;

export const StallAssessmentSchema = z.object({
  stallAssessmentId: z.string().min(1),
  issueChainId: z.string().min(1),
  asOfEventId: z.string().min(1),
  eligible: z.boolean(),
  sameIssue: z.boolean(),
  reportsTargetUnmet: z.boolean(),
  informationGain: InformationGainSchema,
  unmetReportCount: z.number().int().nonnegative(),
  confidence: z.enum(["LOW", "MEDIUM", "HIGH"]),
  reason: z.array(z.string()),
});
export type StallAssessment = z.infer<typeof StallAssessmentSchema>;

export type DetectorInput = {
  sessionId: string;
  eventId: string;
  userTurnIndex: number;
  classification: TurnClassification;
  activeIssueChain?: IssueChain;
  activeReentry: boolean;
};

export type DetectorOutput = {
  issueChain: IssueChain;
  previousIssueChainId?: string;
  assessment: StallAssessment;
};

function issueKey(summary: string): string {
  return summary
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
    .split(/\s+/u)
    .slice(0, 6)
    .join("-") || "unknown-issue";
}

function hasInfoGain(informationGain: InformationGain): boolean {
  return Object.values(informationGain).some(Boolean);
}

export function evaluateStall(input: DetectorInput): DetectorOutput {
  const { classification: current } = input;
  const existing = input.activeIssueChain;
  const sameChain = Boolean(existing && existing.status === "ACTIVE" && current.sameIssue);
  const chain: IssueChain = sameChain && existing
    ? {
        ...existing,
        issueSummary: current.issueSummary,
        lastEventId: input.eventId,
        unmetReportCount: existing.unmetReportCount + (current.reportsTargetUnmet ? 1 : 0),
      }
    : {
        issueChainId: `${input.sessionId}:${issueKey(current.issueSummary)}:${input.eventId}`,
        sessionId: input.sessionId,
        issueKey: issueKey(current.issueSummary),
        issueSummary: current.issueSummary,
        status: "ACTIVE",
        unmetReportCount: current.reportsTargetUnmet ? 1 : 0,
        firstEventId: input.eventId,
        lastEventId: input.eventId,
        cooldownUntilUserTurn: 0,
      };

  const reasons: string[] = [];
  if (current.intent === "ACCEPTANCE") chain.status = "CLOSED";
  if (current.intent !== "CORRECTIVE") reasons.push("CURRENT_TURN_NOT_CORRECTIVE");
  if (!current.reportsTargetUnmet) reasons.push("TARGET_NOT_REPORTED_UNMET");
  if (!sameChain) reasons.push("NEW_OR_DIFFERENT_ISSUE");
  if (chain.unmetReportCount < 2) reasons.push("INSUFFICIENT_UNMET_REPORTS");
  if (hasInfoGain(current.informationGain)) reasons.push("INFORMATION_GAIN_PRESENT");
  if (input.activeReentry) reasons.push("ACTIVE_REENTRY");
  if (current.confidence === "LOW") reasons.push("LOW_CONFIDENCE");
  if (input.userTurnIndex <= chain.cooldownUntilUserTurn) reasons.push("COOLDOWN");

  const assessment = StallAssessmentSchema.parse({
    stallAssessmentId: `${chain.issueChainId}:${input.eventId}`,
    issueChainId: chain.issueChainId,
    asOfEventId: input.eventId,
    eligible: reasons.length === 0,
    sameIssue: sameChain,
    reportsTargetUnmet: current.reportsTargetUnmet,
    informationGain: current.informationGain,
    unmetReportCount: chain.unmetReportCount,
    confidence: current.confidence,
    reason: reasons,
  });

  return {
    issueChain: IssueChainSchema.parse(chain),
    previousIssueChainId: sameChain ? undefined : existing?.issueChainId,
    assessment,
  };
}
