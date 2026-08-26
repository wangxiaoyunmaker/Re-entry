from __future__ import annotations

import unittest

from retrace_selector.models import Outcome

from common import engine, state


class SelectorScenarioTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine()

    def test_progressing_returns_no_intervention(self):
        result = self.engine.select(
            state(
                process_state="DELEGATION_PROGRESSING",
                support_opportunity="NONE",
                support_needs={"criteria_basis_reconstruction": 0, "project_state_reconstruction": 0, "evidence_action_governance": 0},
                consequence="low",
                reversibility="high",
            )
        )
        self.assertEqual(result.outcome, Outcome.NO_INTERVENTION)

    def test_early_support_can_select_low_burden_l1_below_normal_gain(self):
        result = self.engine.select(
            state(
                process_state="EARLY_SUPPORT_OPPORTUNITY",
                support_opportunity="EARLY_SUPPORT",
                support_needs={
                    "criteria_basis_reconstruction": 0,
                    "project_state_reconstruction": 1,
                    "evidence_action_governance": 1,
                },
                evidence_completeness="partial",
                state_confidence=0.72,
            )
        )
        self.assertEqual(result.outcome, Outcome.PRESENT_CHOICES)
        self.assertIn("VERIFICATION-L1", result.selected_ids)
        self.assertTrue(all(item.endswith("-L1") for item in result.selected_ids))

    def test_low_confidence_high_risk_requests_clarification(self):
        result = self.engine.select(
            state(
                state_confidence=0.4,
                authorization_risk="high",
                consequence="high",
                reversibility="low",
            )
        )
        self.assertEqual(result.outcome, Outcome.REQUEST_CLARIFICATION)
        self.assertIn("T001_LOW_CONFIDENCE_HIGH_RISK_CONFLICT", result.reason_codes)

    def test_high_authorization_selects_only_disposition(self):
        result = self.engine.select(state(authorization_risk="high"))
        self.assertIn(result.outcome, (Outcome.INTERVENE, Outcome.PRESENT_CHOICES))
        self.assertTrue(result.selected_ids)
        self.assertTrue(
            all(item.startswith("DISPOSITION_COORDINATION-") for item in result.selected_ids)
        )

    def test_empty_feasible_set_returns_safe_hold(self):
        result = self.engine.select(
            state(
                authorization_risk="high",
                evidence_completeness="none",
                evidence=[],
                state_confidence=0.9,
            )
        )
        self.assertEqual(result.outcome, Outcome.SAFE_HOLD)
        self.assertIn("T002_EMPTY_FEASIBLE_SET", result.reason_codes)

    def test_active_verification_never_selects_verification(self):
        result = self.engine.select(state(active_verification=True))
        self.assertTrue(
            all(not item.startswith("VERIFICATION-") for item in result.selected_ids)
        )

    def test_near_tie_different_paths_presents_choices(self):
        result = self.engine.select(
            state(
                process_state="EARLY_SUPPORT_OPPORTUNITY",
                support_needs={"criteria_basis_reconstruction": 0, "project_state_reconstruction": 2, "evidence_action_governance": 2},
                evidence_completeness="sufficient",
            )
        )
        self.assertEqual(result.outcome, Outcome.PRESENT_CHOICES)
        self.assertEqual(len(result.selected_ids), 2)

    def test_near_tie_same_path_chooses_lower_burden(self):
        result = self.engine.select(
            state(
                support_needs={"criteria_basis_reconstruction": 0, "project_state_reconstruction": 3, "evidence_action_governance": 0},
                evidence_completeness="sufficient",
            )
        )
        self.assertEqual(result.outcome, Outcome.INTERVENE)
        self.assertEqual(result.selected_ids, ("PROVENANCE-L2",))
        self.assertIn("S006_NEAR_TIE_LOWER_BURDEN", result.reason_codes)

    def test_same_input_is_deterministic(self):
        decision_state = state()
        first = self.engine.select(decision_state).to_dict()
        second = self.engine.select(decision_state).to_dict()
        self.assertEqual(first, second)

    def test_every_selected_candidate_is_feasible_and_skyline(self):
        result = self.engine.select(state())
        self.assertTrue(set(result.selected_ids) <= set(result.feasible_ids))
        self.assertTrue(set(result.selected_ids) <= set(result.skyline_ids))

    def test_rejected_candidates_are_not_scored(self):
        result = self.engine.select(state(evidence_completeness="partial"))
        rejected = [item for item in result.generated if not item.allowed]
        self.assertTrue(rejected)
        self.assertTrue(all(item.score is None for item in rejected))


if __name__ == "__main__":
    unittest.main()
