from __future__ import annotations

import unittest

from retrace_selector.models import ValidationError
from retrace_selector.support_profile import aggregate_support_profile


def behavior(evidence_id: str, actor: str, text: str, behavior_change: str = "NOT_APPLICABLE", behavior_change_basis: str = "") -> dict:
    return {
        "evidence_id": evidence_id,
        "actor": actor,
        "text_span": text,
        "dialogue_act": ["IT-Q"],
        "task_intent": ["CODE.EXPLAIN"],
        "target_object": ["TO05"],
        "input_type": ["IN00"],
        "validation_strategy": ["VS00"],
        "temporal_position": "BEFORE_OR_AT_TRIGGER",
        "source": "OBSERVED",
        "behavior_change_from_prior": behavior_change,
        "behavior_change_basis": behavior_change_basis,
    }


def packet() -> dict:
    return {
        "behavior_evidence": [
            behavior("U1", "USER", "为什么不同账号还能看到同一条数据？"),
            behavior("U2", "USER", "请先输出每个账号的隔离测试，再修改数据库。", "CHANGED", "用户从询问状态转为指定隔离测试和修改边界。"),
            behavior("U3", "USER", "隔离测试通过后，只修改账号过滤条件，不要改动已有数据。", "CHANGED", "用户在验证条件基础上进一步限定修改范围。"),
            behavior("A1", "AGENT", "可能是缓存没有按账号区分。"),
        ],
        "basis_assessment": {
            "criteria_basis_reconstruction": {
                "basis_status": "NOT_OBSERVED",
                "formation_evidence_ids": [],
                "use_evidence_ids": [],
                "support_need": "NONE",
                "need_evidence_ids": [],
                "confidence": "HIGH",
                "rationale": "No explicit acceptance rule was observed in this packet.",
                "need_rationale": "No support need was assigned.",
            },
            "project_state_reconstruction": {
                "basis_status": "POSSIBLE",
                "formation_evidence_ids": [],
                "use_evidence_ids": [],
                "support_need": "MEDIUM",
                "need_evidence_ids": ["U1"],
                "confidence": "MEDIUM",
                "rationale": "The user asks about a state relation but has not yet confirmed its cause.",
                "need_rationale": "U1 both exposes the unresolved state relation and explains why state reconstruction support is needed.",
            },
            "evidence_action_governance": {
                "basis_status": "USED",
                "formation_evidence_ids": ["U2"],
                "use_evidence_ids": ["U3"],
                "support_need": "LOW",
                "need_evidence_ids": ["U2"],
                "confidence": "HIGH",
                "rationale": "The user specifies a verification condition and a modification boundary.",
                "need_rationale": "U2 simultaneously forms the action boundary and shows why a verification support is needed before modification.",
            },
        },
    }


class SupportProfileExtractionTests(unittest.TestCase):
    def test_aggregation_requires_explicit_basis_assessment(self):
        result = aggregate_support_profile(packet())
        profile = result["support_profile"]
        self.assertEqual(profile["criteria_basis_reconstruction"]["observed_work"], "NONE")
        self.assertEqual(profile["project_state_reconstruction"]["observed_work"], "POSSIBLE")
        self.assertEqual(profile["evidence_action_governance"]["observed_work"], "OBSERVED")
        self.assertEqual(
            profile["evidence_action_governance"]["evidence_ids"], ["U2", "U3"]
        )

    def test_agent_explanation_cannot_prove_user_basis_use(self):
        raw = packet()
        raw["basis_assessment"]["project_state_reconstruction"]["basis_status"] = "USED"
        raw["basis_assessment"]["project_state_reconstruction"]["formation_evidence_ids"] = ["A1"]
        raw["basis_assessment"]["project_state_reconstruction"]["use_evidence_ids"] = ["A1"]
        with self.assertRaisesRegex(ValidationError, "use evidence must include a USER event"):
            aggregate_support_profile(raw)

    def test_nonzero_need_requires_evidence(self):
        raw = packet()
        raw["basis_assessment"]["project_state_reconstruction"]["need_evidence_ids"] = []
        with self.assertRaisesRegex(ValidationError, "support_need requires need_evidence_ids"):
            aggregate_support_profile(raw)

    def test_used_requires_formation_and_use(self):
        raw = packet()
        raw["basis_assessment"]["project_state_reconstruction"]["basis_status"] = "USED"
        raw["basis_assessment"]["project_state_reconstruction"]["formation_evidence_ids"] = ["U1"]
        with self.assertRaisesRegex(ValidationError, "USED requires both formation and use evidence"):
            aggregate_support_profile(raw)


if __name__ == "__main__":
    unittest.main()
