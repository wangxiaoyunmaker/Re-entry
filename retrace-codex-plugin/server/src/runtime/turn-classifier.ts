import { TurnClassificationSchema, type TurnClassification } from "../schemas/stall.js";

const INVESTIGATION_RE = /(先不要改|先查|查原因|为什么|日志|控制台|报错|比较.*路径|看看.*原因|检查.*原因)/u;
const EVIDENCE_RE = /(\b[245]0\d\b|\b\d{3}\b|error|exception|stack trace|console error|log output|test result|screenshot result|日志|错误|报错|控制台|测试结果|截图|运行结果)/iu;
const OBSERVATION_RE = /(我看到|实际|刷新后|点击后|每次|偶尔|只在)/u;
const REQUIREMENT_RE = /(应该|必须|希望|需要|保留|不要改变|不能改)/u;
const ACCEPTANCE_RE = /(可以了|解决了|没问题了|现在正常|符合了|就这样)/u;
const CORRECTIVE_RE = /(还是|仍然|依旧|再|又|继续|不对|不行|失败|没显示|没有|不工作|修一下|检查一下)/u;
const UNMET_RE = /(还是|仍然|依旧|没有|没|不对|不行|失败|不工作|未解决|没显示|没反应)/u;
const GENERIC_RE = /^(?:还是|仍然|依旧|又|继续|再)(?:不对|不行|没有|没显示|没反应|失败)?[\s，。,.!！?？]*(?:再)?(?:改|修|试|检查)(?:一下|一遍|看看|试试)?$/u;

function meaningfulTerms(text: string): string[] {
  const normalized = text.toLocaleLowerCase().replace(/[，。,.!?！？：:、\s]+/gu, " ").trim();
  const terms = normalized.match(/[a-z0-9_/-]{2,}|[\p{Script=Han}]{2,}/gu) ?? [];
  return terms.filter((term) => !/(还是|仍然|依旧|帮我|请|一下|看看|检查|再试|继续|没有|没|不对|不行|失败|修复|修一下|现在|问题|这个)/u.test(term));
}

function inferSameIssue(prompt: string, activeIssueSummary?: string): { sameIssue: boolean; confidence: "LOW" | "MEDIUM" | "HIGH" } {
  if (!activeIssueSummary) return { sameIssue: true, confidence: "MEDIUM" };
  const currentTerms = meaningfulTerms(prompt);
  const activeTerms = meaningfulTerms(activeIssueSummary);
  if (currentTerms.length === 0 || GENERIC_RE.test(prompt.trim())) return { sameIssue: true, confidence: "HIGH" };
  if (currentTerms.some((term) => activeTerms.includes(term))) return { sameIssue: true, confidence: "HIGH" };
  return { sameIssue: false, confidence: "HIGH" };
}

export function classifyPrompt(input: {
  prompt: string;
  activeIssueSummary?: string;
}): TurnClassification {
  const prompt = input.prompt.trim();
  const same = inferSameIssue(prompt, input.activeIssueSummary);
  const investigationDirection = INVESTIGATION_RE.test(prompt);
  const newEvidence = EVIDENCE_RE.test(prompt);
  const newObservation = OBSERVATION_RE.test(prompt) && !newEvidence;
  const newRequirement = REQUIREMENT_RE.test(prompt);
  const acceptance = ACCEPTANCE_RE.test(prompt);
  const corrective = CORRECTIVE_RE.test(prompt) && !acceptance;
  const intent = acceptance
    ? "ACCEPTANCE"
    : investigationDirection
      ? "INVESTIGATION"
      : newRequirement && !corrective
        ? "NEW_REQUIREMENT"
        : corrective
          ? "CORRECTIVE"
          : "INITIAL_REQUEST";
  const confidence = same.confidence === "HIGH" && (corrective || investigationDirection || acceptance)
    ? "HIGH"
    : same.confidence;
  return TurnClassificationSchema.parse({
    sameIssue: same.sameIssue,
    issueSummary: same.sameIssue && input.activeIssueSummary ? input.activeIssueSummary : prompt,
    intent,
    reportsTargetUnmet: UNMET_RE.test(prompt) && !acceptance,
    informationGain: {
      newObservation,
      newRequirement,
      newEvidence,
      investigationDirection,
    },
    confidence,
  });
}
