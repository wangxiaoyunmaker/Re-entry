import { describe, expect, it } from "vitest";
import { evaluateStall, type TurnClassification } from "../../server/src/schemas/stall.js";
import { classifyPrompt } from "../../server/src/runtime/turn-classifier.js";

const classification = (overrides: Partial<TurnClassification> = {}): TurnClassification => ({
  sameIssue: true,
  issueSummary: "背景图没有显示",
  intent: "CORRECTIVE",
  reportsTargetUnmet: true,
  informationGain: {
    newObservation: false,
    newRequirement: false,
    newEvidence: false,
    investigationDirection: false,
  },
  confidence: "HIGH",
  ...overrides,
});

describe("M2 stall detector", () => {
  it("does not trigger on the first unmet report", () => {
    const result = evaluateStall({
      sessionId: "s1", eventId: "u1", userTurnIndex: 1,
      classification: classification(), activeReentry: false,
    });
    expect(result.issueChain.unmetReportCount).toBe(1);
    expect(result.assessment.eligible).toBe(false);
  });

  it("triggers on the second same-issue low-information corrective failure", () => {
    const first = evaluateStall({
      sessionId: "s1", eventId: "u1", userTurnIndex: 1,
      classification: classification(), activeReentry: false,
    });
    const second = evaluateStall({
      sessionId: "s1", eventId: "u2", userTurnIndex: 2,
      classification: classification(), activeIssueChain: first.issueChain, activeReentry: false,
    });
    expect(second.assessment.eligible).toBe(true);
    expect(second.assessment.reason).toEqual([]);
  });

  it.each([
    ["new observation", { newObservation: true }],
    ["new requirement", { newRequirement: true }],
    ["new evidence", { newEvidence: true }],
    ["investigation direction", { investigationDirection: true }],
  ])("does not trigger with %s", (_label, informationGain) => {
    const first = evaluateStall({ sessionId: "s1", eventId: "u1", userTurnIndex: 1, classification: classification(), activeReentry: false });
    const second = evaluateStall({
      sessionId: "s1", eventId: "u2", userTurnIndex: 2,
      classification: classification({ informationGain: { ...classification().informationGain, ...informationGain } }),
      activeIssueChain: first.issueChain, activeReentry: false,
    });
    expect(second.assessment.eligible).toBe(false);
    expect(second.assessment.reason).toContain("INFORMATION_GAIN_PRESENT");
  });

  it("does not mistake prompt length or technical vocabulary for information gain", () => {
    const result = classifyPrompt({
      prompt: "还是不对，再检查一下 webpack vite module runtime implementation architecture",
      activeIssueSummary: "背景图没有显示",
    });
    expect(result.informationGain).toEqual({
      newObservation: false,
      newRequirement: false,
      newEvidence: false,
      investigationDirection: false,
    });
  });

  it("does not merge a different issue or gate low confidence", () => {
    const first = evaluateStall({ sessionId: "s1", eventId: "u1", userTurnIndex: 1, classification: classification(), activeReentry: false });
    const different = evaluateStall({
      sessionId: "s1", eventId: "u2", userTurnIndex: 2,
      classification: classification({ sameIssue: false, issueSummary: "登录按钮没有反应" }),
      activeIssueChain: first.issueChain, activeReentry: false,
    });
    expect(different.assessment.eligible).toBe(false);
    expect(different.issueChain.issueSummary).toBe("登录按钮没有反应");

    const low = evaluateStall({
      sessionId: "s1", eventId: "u3", userTurnIndex: 2,
      classification: classification({ confidence: "LOW" }),
      activeIssueChain: first.issueChain, activeReentry: false,
    });
    expect(low.assessment.eligible).toBe(false);
    expect(low.assessment.reason).toContain("LOW_CONFIDENCE");
  });
});
