import { describe, expect, it } from "vitest";
import { ReconstructionSchema, validateReconstructionProvenance } from "../../server/src/schemas/context.js";

function reconstruction() {
  return ReconstructionSchema.parse({
    reconstructionId: "r1",
    snapshotVersion: 1,
    generatedAt: "2026-08-29T00:00:00.000Z",
    goal: [{ id: "g1", text: "原始用户目标：显示背景图", sourceEventIds: ["u1"], sourceType: "CODEX_HOOK", timestamp: "2026-08-29T00:00:00.000Z" }],
    evidenceItems: [{ id: "e1", kind: "TEST_RESULT", claim: "测试失败", sourceEventIds: ["tool1"], sourceType: "CODEX_HOOK", timestamp: "2026-08-29T00:00:01.000Z", verificationStatus: "UNVERIFIED" }],
    explanations: [{ id: "x1", text: "可能是路径问题", kind: "AGENT_HYPOTHESIS", sourceEventIds: ["a1"], supportingEvidenceIds: ["e1"], timestamp: "2026-08-29T00:00:02.000Z" }],
    uncertainties: [{ id: "u?", text: "尚未验证", sourceEventIds: ["a1"], relatedClaimIds: [], timestamp: "2026-08-29T00:00:02.000Z" }],
    agentClaims: [{ claimId: "c1", kind: "CAUSE", text: "可能是路径问题", sourceEventId: "a1", supportingEvidenceIds: ["e1"], verificationStatus: "UNVERIFIED", timestamp: "2026-08-29T00:00:02.000Z" }],
  });
}

describe("M3-B provenance contract", () => {
  it("rejects invented event provenance", () => {
    expect(() => validateReconstructionProvenance(reconstruction(), new Set(["u1", "tool1"]))).toThrow("INVALID_RECONSTRUCTION_PROVENANCE");
  });

  it("rejects unsupported evidence references", () => {
    const value = reconstruction();
    value.agentClaims[0].supportingEvidenceIds = ["missing-evidence"];
    expect(() => validateReconstructionProvenance(value, new Set(["u1", "tool1", "a1"]))).toThrow("INVALID_EVIDENCE_REFERENCE");
  });
});
