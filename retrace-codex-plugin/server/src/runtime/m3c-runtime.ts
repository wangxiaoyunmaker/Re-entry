import type { ContextReview, UncertaintyItem } from "../schemas/context.js";
import { InvestigationSchema, NextPromptDraftSchema, type Investigation, type NextPromptDraft, type ReviewedReentryState } from "../schemas/m3c.js";
import { deriveReviewedReentryState, type InvestigationEvidenceCandidate } from "./reviewed-state.js";
import type { Reconstruction } from "../schemas/context.js";

export function buildReviewedState(reconstruction: Reconstruction, reviews: ContextReview[], reentryRunId: string, investigationCandidates: InvestigationEvidenceCandidate[] = []): ReviewedReentryState {
  return deriveReviewedReentryState(reconstruction, reviews, reentryRunId, investigationCandidates);
}

function section(title: string, values: string[]): string {
  return `${title}:\n${values.length > 0 ? values.map((value) => `- ${value}`).join("\n") : "- none"}`;
}

export function buildNextPromptDraft(state: ReviewedReentryState, promptId: string, generatedAt: string): NextPromptDraft {
  const objective = state.goal.map((item) => item.text).join("; ") || "在采取修改前澄清当前仍未确认的目标";
  const knownFacts = [
    ...state.acceptedEvidence.map((item) => item.claim),
    ...state.addedObservations.map((item) => `用户观察：${item.text}`),
  ];
  const openQuestions = state.unresolvedUncertainties.map((item) => item.text);
  const evidenceRequirements = state.evidenceRequirements.map((item) => item.text);
  const constraints = [
    "只处理当前目标相关范围，不做无关修改。",
    "不要把未验证的解释或 Agent 声明当作事实。",
    ...state.governanceConstraints.map((item) => item.text),
    ...state.acceptedExplanations.map((item) => `用户保留的工作解释（仍需验证）：${item.text}`),
    ...state.rejectedExplanations.map((item) => `不要直接采用未采纳的解释：${item.text}`),
  ];
  const requestedAction = evidenceRequirements.length > 0
    ? "先执行或检查用户要求的证据路径，再根据结果决定是否需要修改。"
    : "针对当前目标进行最小范围检查或修改，并返回可观察结果。";
  const verificationCriteria = evidenceRequirements.length > 0
    ? evidenceRequirements
    : ["返回测试、构建、运行时检查或用户可观察的验证结果。"];
  const promptText = [
    section("Goal", [objective]),
    section("What is currently known", knownFacts),
    section("What remains unverified", openQuestions),
    section("Evidence required", evidenceRequirements),
    section("Constraints", constraints),
    section("Requested next action", [requestedAction]),
    section("Verification condition", verificationCriteria),
  ].join("\n\n");
  return NextPromptDraftSchema.parse({
    promptId,
    reentryRunId: state.reentryRunId,
    reviewVersion: state.reviewVersion,
    objective,
    knownFacts,
    openQuestions,
    evidenceRequirements,
    constraints,
    requestedAction,
    verificationCriteria,
    promptText,
    generatedAt,
  });
}

function targetUncertainty(state: ReviewedReentryState, targetId: string): UncertaintyItem | undefined {
  return state.unresolvedUncertainties.find((item) => item.id === targetId);
}

function targetEvidence(state: ReviewedReentryState, targetId: string): { text: string } | undefined {
  const evidence = state.acceptedEvidence.find((item) => item.id === targetId);
  if (evidence) return { text: evidence.claim };
  const requirement = state.evidenceRequirements.find((item) => item.id === targetId);
  return requirement ? { text: requirement.text } : undefined;
}

export function buildInvestigation(state: ReviewedReentryState, input: {
  investigationId: string;
  targetReviewItemId?: string;
  targetItemType: "EVIDENCE" | "UNCERTAINTY";
  question?: string;
  evidenceRequirement?: string;
  createdAt: string;
}): Investigation {
  const targetId = input.targetReviewItemId;
  const uncertainty = input.targetItemType === "UNCERTAINTY" && targetId ? targetUncertainty(state, targetId) : undefined;
  const evidence = input.targetItemType === "EVIDENCE" && targetId ? targetEvidence(state, targetId) : undefined;
  if (targetId && !uncertainty && !evidence) throw new Error("INVESTIGATION_TARGET_NOT_FOUND");
  const questionToVerify = input.question?.trim() || (uncertainty
    ? `验证这项未确定内容：${uncertainty.text}`
    : evidence
      ? `验证这项证据是否足以支持当前判断：${evidence.text}`
      : "获取能够判断当前问题的可观察证据。");
  const evidenceRequirement = input.evidenceRequirement?.trim() || (uncertainty
    ? `返回能够回答该不确定性的具体测试、运行时结果或检查结果。`
    : evidence
      ? `复核该证据，并返回实际观察到的结果。`
      : "返回一个可复核的具体证据，而不是一般性说明。");
  const relevantContext = [
    ...state.goal.map((item) => item.text),
    ...state.acceptedEvidence.map((item) => item.claim),
    ...state.addedObservations.map((item) => `用户观察：${item.text}`),
  ];
  const constraints = [
    "只调查这个问题，不做无关修改。",
    "优先运行或检查现有项目，不要假设问题已解决。",
    "只返回可观察证据和未解决部分。",
  ];
  const expectedObservableResult = "返回命令输出、测试结果、运行时状态或明确的文件/代码检查结果，并附来源。";
  const generatedPrompt = [
    `Question to verify: ${questionToVerify}`,
    `Required evidence: ${evidenceRequirement}`,
    section("Relevant reviewed context", relevantContext),
    section("Constraints", constraints),
    `Expected observable result: ${expectedObservableResult}`,
  ].join("\n\n");
  return InvestigationSchema.parse({
    investigationId: input.investigationId,
    reentryRunId: state.reentryRunId,
    targetReviewItemId: targetId,
    targetItemType: input.targetItemType,
    questionToVerify,
    evidenceRequirement,
    relevantContext,
    constraints,
    expectedObservableResult,
    generatedPrompt,
    action: "GENERATE",
    status: "DRAFT",
    sourceReviewIds: state.sourceReviewIds,
    createdAt: input.createdAt,
    updatedAt: input.createdAt,
  });
}
