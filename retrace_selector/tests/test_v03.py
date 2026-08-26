from __future__ import annotations

import unittest

from retrace_selector.v03 import adapt_v03_state, select_v03
from retrace_selector.models import Outcome, ValidationError

from common import engine


def v03_state(**overrides):
    evidence_id = "E-V03"
    profile = {
        "criteria_basis_reconstruction": {
            "observed_work": "NONE",
            "support_need": "NONE",
            "confidence": "LOW",
            "evidence_ids": [],
            "evidence_basis": [],
        },
        "project_state_reconstruction": {
            "observed_work": "POSSIBLE",
            "support_need": "MEDIUM",
            "confidence": "HIGH",
            "evidence_ids": [evidence_id],
            "evidence_basis": [
                {
                    "signal": "user_reconstructed_project_state",
                    "actor": "USER",
                    "temporal_position": "BEFORE_OR_AT_TRIGGER",
                    "uptake_status": "POSSIBLE",
                }
            ],
        },
        "evidence_action_governance": {
            "observed_work": "POSSIBLE",
            "support_need": "MEDIUM",
            "confidence": "HIGH",
            "evidence_ids": [evidence_id],
            "evidence_basis": [
                {
                    "signal": "user_defined_evidence_or_action_boundary",
                    "actor": "USER",
                    "temporal_position": "BEFORE_OR_AT_TRIGGER",
                    "uptake_status": "POSSIBLE",
                }
            ],
        },
    }
    data = {
        "schema_version": "retrace-state-v3",
        "decision_id": "v03-test",
        "process_state": "REENTRY_SUPPORT",
        "support_profile": profile,
        "trace_coverage": "ADEQUATE",
        "uncertainties": [],
        "consequence": "MEDIUM",
        "reversibility": "MEDIUM",
        "authorization_risk": "LOW",
        "evidence_quality": 0.9,
        "workflow_continuity": 0.8,
        "evidence": [
            {
                "evidence_id": evidence_id,
                "source": "OBSERVED",
                "supports_dimensions": [
                    "project_state_reconstruction",
                    "evidence_action_governance",
                ],
                "supports_primitives": [
                    "PROVENANCE",
                    "CAUSAL_EXPLANATION",
                    "VERIFICATION",
                    "DISPOSITION_COORDINATION",
                ],
                "available_at_decision": True,
            }
        ],
        "recent_interventions": 0,
        "active_verification": False,
    }
    data.update(overrides)
    return data


class V03BoundaryTests(unittest.TestCase):
    def test_abstain_opportunity_stops_automatic_selection(self):
        result = select_v03(
            v03_state(support_opportunity="ABSTAIN"),
            engine(),
        )
        self.assertEqual(result["outcome"], Outcome.REQUEST_CLARIFICATION.value)
        self.assertIn("T000_SUPPORT_OPPORTUNITY_ABSTAIN", result["reason_codes"])

    def test_support_need_audit_is_exposed_without_overwriting_request(self):
        raw = v03_state()
        state, metadata = adapt_v03_state(raw)
        self.assertEqual(
            metadata["support_need_audit"]["project_state_reconstruction"]["requested"],
            "MEDIUM",
        )
        self.assertIn(
            "recommended",
            metadata["support_need_audit"]["project_state_reconstruction"],
        )

    def test_full_name_profile_maps_at_explicit_boundary(self):
        state, metadata = adapt_v03_state(v03_state())
        self.assertEqual(
            state.support_needs.to_dict(),
            {
                "criteria_basis_reconstruction": 0,
                "project_state_reconstruction": 2,
                "evidence_action_governance": 2,
            },
        )
        self.assertEqual(metadata["trace_coverage"], "ADEQUATE")
        self.assertEqual(state.process_state.value, "REENTRY_OCCASION_OBSERVED")

    def test_trace_coverage_inadequate_reduces_evidence_completeness(self):
        raw = v03_state(trace_coverage="INADEQUATE", evidence_quality=0.9)
        state, _ = adapt_v03_state(raw)
        self.assertEqual(state.evidence_completeness.value, "none")
        self.assertLessEqual(state.state_confidence, 0.5)

    def test_unknown_evidence_reference_fails_closed(self):
        raw = v03_state()
        raw["support_profile"]["project_state_reconstruction"]["evidence_ids"] = ["MISSING"]
        with self.assertRaisesRegex(ValidationError, "unknown evidence"):
            adapt_v03_state(raw)

    def test_selector_result_keeps_v03_input_metadata(self):
        result = select_v03(v03_state(), engine())
        self.assertEqual(result["v03_input"]["schema_version"], "retrace-state-v3")
        self.assertIn("frontier_ratio", result)
        self.assertEqual(
            set(result["generated_candidates"][0]["score"]),
            {
                "criteria_basis_reconstruction",
                "project_state_reconstruction",
                "evidence_action_governance",
                "evidence_quality",
                "workflow_continuity",
            },
        )
        self.assertEqual(result["metadata"]["state"]["schema_version"], "retrace-state-v3")
        self.assertEqual(
            result["metadata"]["objective"]["name"],
            "reference_point_target_gap",
        )
        self.assertIn("target_vector", result["metadata"]["objective"])

    def test_evidence_first_packet_must_match_selector_profile(self):
        raw = v03_state()
        raw["behavior_evidence"] = [{
            "evidence_id": "E-V03",
            "actor": "USER",
            "text_span": "请先核对当前数据关系。",
            "dialogue_act": ["IT-Q"],
            "task_intent": ["CODE.EXPLAIN"],
            "target_object": ["TO05"],
            "input_type": ["IN00"],
            "validation_strategy": ["VS00"],
            "temporal_position": "BEFORE_OR_AT_TRIGGER",
            "source": "OBSERVED",
            "behavior_change_from_prior": "NOT_APPLICABLE",
        }]
        raw["basis_assessment"] = {
            "criteria_basis_reconstruction": {
                "basis_status": "NOT_OBSERVED",
                "formation_evidence_ids": [],
                "use_evidence_ids": [],
                "support_need": "NONE",
                "need_evidence_ids": [],
                "confidence": "LOW",
                "rationale": "No criteria evidence.",
                "need_rationale": "No support need was assigned.",
            },
            "project_state_reconstruction": {
                "basis_status": "POSSIBLE",
                "formation_evidence_ids": [],
                "use_evidence_ids": [],
                "support_need": "MEDIUM",
                "need_evidence_ids": ["E-V03"],
                "confidence": "HIGH",
                "rationale": "The user asks to inspect current state.",
                "need_rationale": "The same request identifies why state support is needed.",
            },
            "evidence_action_governance": {
                "basis_status": "POSSIBLE",
                "formation_evidence_ids": [],
                "use_evidence_ids": [],
                "support_need": "MEDIUM",
                "need_evidence_ids": ["E-V03"],
                "confidence": "HIGH",
                "rationale": "The user asks for a state check before proceeding.",
                "need_rationale": "The same request identifies why evidence-action support is needed.",
            },
        }
        state, metadata = adapt_v03_state(raw)
        self.assertIn("basis_assessment", metadata)
        self.assertEqual(
            state.support_needs.to_dict(),
            {
                "criteria_basis_reconstruction": 0,
                "project_state_reconstruction": 2,
                "evidence_action_governance": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
