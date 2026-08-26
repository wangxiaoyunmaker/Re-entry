from __future__ import annotations

from pathlib import Path
import unittest

from retrace_selector.models import Level
from retrace_selector.models import ProcessState, SupportNeeds
from retrace_selector.selector_v06 import V06SelectionEngine
from retrace_selector.strategy_registry import load_selection_policy
from retrace_selector.strategy_registry import load_strategy_registry
from retrace_selector.v06_models import CoreRisk, SelectorDecisionState, SelectorEvidenceRef


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "strategy_registry.v0.7-calibration.json"
POLICY = ROOT / "config" / "selection_policy.v0.6.json"


class StrategyCalibrationV07Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_strategy_registry(REGISTRY)

    def test_registry_is_explicitly_non_live(self):
        self.assertEqual(self.registry.registry_status, "TEST_ONLY")
        self.assertEqual(self.registry.capability_mode, "STATE_CONDITIONED")
        self.assertEqual(len(self.registry.catalog), 5)
        self.assertEqual(len(self.registry.candidates), 15)

    def test_primary_capability_is_monotonic_and_capped_below_one(self):
        primary_index = {
            "CAL_CRITERIA_ALIGNMENT": 0,
            "CAL_STATE_TRACE": 1,
            "CAL_CAUSAL_CHECK": 1,
            "CAL_VERIFICATION": 2,
            "CAL_DISPOSITION": 2,
        }
        for strategy_id, index in primary_index.items():
            candidates = sorted(
                [item for item in self.registry.candidates if item.strategy_id == strategy_id],
                key=lambda item: item.intensity,
            )
            values = [item.capability[index] for item in candidates]
            with self.subTest(strategy_id=strategy_id):
                self.assertEqual([item.intensity for item in candidates], [Level.L1, Level.L2, Level.L3])
                self.assertLess(values[0], values[1])
                self.assertLess(values[1], values[2])
                self.assertLessEqual(values[2], 0.85)

    def test_cross_loadings_never_exceed_primary_capability(self):
        primary_index = {
            "CAL_CRITERIA_ALIGNMENT": 0,
            "CAL_STATE_TRACE": 1,
            "CAL_CAUSAL_CHECK": 1,
            "CAL_VERIFICATION": 2,
            "CAL_DISPOSITION": 2,
        }
        for candidate in self.registry.candidates:
            index = primary_index[candidate.strategy_id]
            primary = candidate.capability[index]
            with self.subTest(candidate=candidate.candidate_id):
                self.assertTrue(all(value <= primary for value in candidate.capability))

    def test_state_conditioning_reduces_focused_profile_frontier(self):
        policy, _ = load_selection_policy(POLICY)
        engine = V06SelectionEngine(self.registry, policy)
        state = SelectorDecisionState(
            decision_id="state-only",
            process_state=ProcessState.REENTRY_OCCASION_OBSERVED,
            support_needs=SupportNeeds(0, 3, 0),
            risk_level=CoreRisk.MEDIUM,
            authorization_required=False,
            evidence_level=0.5,
            confidence=0.9,
            recent_intervention_count=0,
            active_verification=False,
            evidence_refs=(SelectorEvidenceRef("E1", "OBSERVED"),),
        )
        result = engine.select(state)
        self.assertLess(result.frontier_ratio, 0.6)
        self.assertTrue(result.selected_ids[0].startswith("CAL_STATE_TRACE:"))


if __name__ == "__main__":
    unittest.main()
