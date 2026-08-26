from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from retrace_selector.config import load_json
from retrace_selector.models import (
    EvidenceCompleteness,
    Level,
    Outcome,
    ProcessState,
    SupportNeeds,
    ValidationError,
)
from retrace_selector.selector_v06 import V06SelectionEngine, score_candidate
from retrace_selector.state_adapter import ClarificationRequired, adapt_state
from retrace_selector.strategy_registry import (
    load_selection_policy,
    load_strategy_registry,
)
from retrace_selector.v06_models import (
    CoreRisk,
    SelectorDecisionState,
    SelectorEvidenceRef,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "selection_policy.v0.6.json"
REGISTRY_PATH = ROOT / "config" / "strategy_registry.v0.6.json"
PILOT_DIR = ROOT / "artifacts" / "pilot_annotation_20260820"


def selector_state(**overrides) -> SelectorDecisionState:
    values = {
        "decision_id": "v06-test",
        "process_state": ProcessState.REENTRY_OCCASION_OBSERVED,
        "support_needs": SupportNeeds(3, 2, 1),
        "risk_level": CoreRisk.MEDIUM,
        "authorization_required": False,
        "evidence_level": 0.5,
        "confidence": 0.9,
        "recent_intervention_count": 0,
        "active_verification": False,
        "evidence_refs": (SelectorEvidenceRef("E1", "OBSERVED"),),
    }
    values.update(overrides)
    return SelectorDecisionState(**values)


def engine(*, policy_override=None, registry_override=None) -> V06SelectionEngine:
    policy, _ = load_selection_policy(POLICY_PATH)
    registry = load_strategy_registry(REGISTRY_PATH)
    return V06SelectionEngine(
        registry_override or registry,
        policy_override or policy,
    )


class V06ContractTests(unittest.TestCase):
    def test_policy_and_test_registry_load_strictly(self):
        policy, policy_hash = load_selection_policy(POLICY_PATH)
        registry = load_strategy_registry(REGISTRY_PATH)
        self.assertEqual(policy.weights, (0.2, 0.2, 0.25, 0.15, 0.2))
        self.assertEqual(policy.tau, 0.05)
        self.assertEqual(len(policy_hash), 64)
        self.assertEqual(registry.registry_status, "TEST_ONLY")
        self.assertEqual(len(registry.catalog), 5)
        self.assertEqual(len(registry.candidates), 15)
        self.assertEqual(len({item.candidate_id for item in registry.candidates}), 15)

    def test_policy_rejects_extra_online_fields(self):
        raw = load_json(POLICY_PATH)
        raw["risk_weight"] = 0.4
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad-policy.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "unknown fields"):
                load_selection_policy(path)

    def test_registry_rejects_unregistered_template(self):
        raw = load_json(REGISTRY_PATH)
        raw["candidates"][0]["template_id"] = "MISSING"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad-registry.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "unknown template_id"):
                load_strategy_registry(path)

    def test_test_fixture_registry_cannot_be_marked_approved(self):
        raw = load_json(REGISTRY_PATH)
        raw["registry_status"] = "APPROVED"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe-registry.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "cannot contain TEST_"):
                load_strategy_registry(path)

    def test_compact_state_has_exactly_ten_input_fields(self):
        raw = selector_state().to_dict()
        self.assertEqual(
            set(raw),
            {
                "decision_id",
                "process_state",
                "support_needs",
                "risk_level",
                "authorization_required",
                "evidence_level",
                "confidence",
                "recent_intervention_count",
                "active_verification",
                "evidence_refs",
            },
        )
        self.assertEqual(SelectorDecisionState.from_dict(raw), selector_state())


class V06AdapterTests(unittest.TestCase):
    def test_real_pilot_governance_needs_are_projected(self):
        state = adapt_state(load_json(PILOT_DIR / "SRE-0017.state.json"))
        self.assertEqual(state.decision_id, "SRE-0017")
        self.assertEqual(state.support_needs, SupportNeeds(3, 2, 1))
        self.assertEqual(state.risk_level, CoreRisk.LOW)
        self.assertEqual(state.evidence_level, 0.5)
        self.assertTrue(state.has_observed_evidence)
        self.assertEqual(
            state.evidence_refs[0].evidence_id,
            "SRE-0017:context_0018:R23",
        )

    def test_all_four_real_pilot_states_project_without_llm(self):
        expected = {
            "SRE-0012": SupportNeeds(1, 2, 3),
            "SRE-0017": SupportNeeds(3, 2, 1),
            "SRE-0061": SupportNeeds(2, 3, 3),
            "SRE-0112": SupportNeeds(2, 3, 2),
        }
        for decision_id, needs in expected.items():
            with self.subTest(decision_id=decision_id):
                state = adapt_state(load_json(PILOT_DIR / f"{decision_id}.state.json"))
                self.assertEqual(state.support_needs, needs)

    def test_abstain_is_routed_to_clarification(self):
        raw = load_json(PILOT_DIR / "SRE-0017.state.json")
        raw["support_opportunity"] = "ABSTAIN"
        with self.assertRaises(ClarificationRequired):
            adapt_state(raw)


class V06ScoringAndDecisionTests(unittest.TestCase):
    def test_first_three_scores_are_registered_capabilities_not_need_weighted(self):
        registry = load_strategy_registry(REGISTRY_PATH)
        candidate = registry.candidates[0]
        low_needs = selector_state(support_needs=SupportNeeds(0, 0, 0))
        high_needs = selector_state(support_needs=SupportNeeds(3, 3, 3))
        low_score = score_candidate(candidate, low_needs)
        high_score = score_candidate(candidate, high_needs)
        self.assertEqual(low_score.vector()[:3], candidate.capability)
        self.assertEqual(high_score.vector()[:3], candidate.capability)

    def test_no_support_and_safe_continuation_returns_no_intervention(self):
        result = engine().select(
            selector_state(support_needs=SupportNeeds(0, 0, 0))
        )
        self.assertEqual(result.outcome, Outcome.NO_INTERVENTION)
        self.assertEqual(result.reason_codes, ("NO_SUPPORT_SIGNAL",))
        self.assertEqual(result.selected_ids, ())

    def test_no_support_but_authorization_required_forces_safe_strategy(self):
        result = engine().select(
            selector_state(
                support_needs=SupportNeeds(0, 0, 0),
                authorization_required=True,
            )
        )
        self.assertEqual(result.outcome, Outcome.INTERVENE)
        self.assertEqual(result.reason_codes, ("FORCED_GOVERNANCE",))
        self.assertTrue(result.metadata["forced_governance"])
        self.assertTrue(result.selected_ids[0].startswith("TEST_AUTHORIZATION_SUPPORT:"))

    def test_unsafe_state_without_compatible_intensity_returns_safe_hold(self):
        result = engine().select(
            selector_state(
                risk_level=CoreRisk.HIGH,
                confidence=0.4,
            )
        )
        self.assertEqual(result.outcome, Outcome.SAFE_HOLD)
        self.assertEqual(result.reason_codes, ("NO_SAFE_CANDIDATE",))

    def test_hard_constraint_removal_happens_before_scoring(self):
        registry = load_strategy_registry(REGISTRY_PATH)
        sufficient_candidate = next(
            item
            for item in registry.candidates
            if item.minimum_evidence is EvidenceCompleteness.SUFFICIENT
        )
        result = engine().select(selector_state(evidence_level=0.5))
        evaluation = next(
            item for item in result.generated if item.candidate_id == sufficient_candidate.candidate_id
        )
        self.assertFalse(evaluation.allowed)
        self.assertIsNone(evaluation.score)

    def test_gain_equal_to_tau_is_eligible(self):
        policy, _ = load_selection_policy(POLICY_PATH)
        registry = load_strategy_registry(REGISTRY_PATH)
        candidate = next(
            item
            for item in registry.candidates
            if item.strategy_id == "TEST_CRITERIA_SUPPORT" and item.intensity is Level.L1
        )
        one_candidate_registry = replace(registry, candidates=(candidate,))
        state = selector_state(support_needs=SupportNeeds(3, 0, 0))
        first = engine(
            policy_override=replace(policy, tau=0.0),
            registry_override=one_candidate_registry,
        ).select(state)
        gain = first.metadata["Gain"]
        self.assertIsNotNone(gain)
        self.assertGreater(gain, 0.0)
        at_boundary = engine(
            policy_override=replace(policy, tau=gain),
            registry_override=one_candidate_registry,
        ).select(state)
        self.assertEqual(at_boundary.outcome, Outcome.INTERVENE)

    def test_different_strategy_near_tie_presents_at_most_two_choices(self):
        policy, _ = load_selection_policy(POLICY_PATH)
        registry = load_strategy_registry(REGISTRY_PATH)
        criteria = next(
            item
            for item in registry.candidates
            if item.strategy_id == "TEST_CRITERIA_SUPPORT" and item.intensity is Level.L1
        )
        state_candidate = next(
            item
            for item in registry.candidates
            if item.strategy_id == "TEST_STATE_SUPPORT" and item.intensity is Level.L1
        )
        state_candidate = replace(
            state_candidate,
            capability=criteria.capability,
            evidence_quality=criteria.evidence_quality,
            workflow_cost=criteria.workflow_cost,
            minimum_evidence=criteria.minimum_evidence,
        )
        tie_registry = replace(registry, candidates=(criteria, state_candidate))
        result = engine(
            policy_override=replace(policy, tau=0.0),
            registry_override=tie_registry,
        ).select(selector_state(support_needs=SupportNeeds(3, 3, 0)))
        self.assertEqual(result.outcome, Outcome.PRESENT_CHOICES)
        self.assertEqual(len(result.selected_ids), 2)
        self.assertNotEqual(
            result.selected_ids[0].split(":")[0],
            result.selected_ids[1].split(":")[0],
        )

    def test_tau_gates_best_path_but_does_not_remove_near_tie_second_path(self):
        policy, _ = load_selection_policy(POLICY_PATH)
        registry = load_strategy_registry(REGISTRY_PATH)
        criteria = next(
            item
            for item in registry.candidates
            if item.strategy_id == "TEST_CRITERIA_SUPPORT" and item.intensity is Level.L1
        )
        second_path = next(
            item
            for item in registry.candidates
            if item.strategy_id == "TEST_STATE_SUPPORT" and item.intensity is Level.L1
        )
        second_path = replace(
            second_path,
            capability=(criteria.capability[0] + 0.01, *criteria.capability[1:]),
            evidence_quality=criteria.evidence_quality,
            workflow_cost=criteria.workflow_cost + 0.05,
            minimum_evidence=criteria.minimum_evidence,
        )
        tie_registry = replace(registry, candidates=(criteria, second_path))
        state = selector_state(support_needs=SupportNeeds(3, 3, 0))
        baseline = engine(
            policy_override=replace(policy, tau=0.0),
            registry_override=tie_registry,
        ).select(state)
        evaluations = sorted(
            (
                item
                for item in baseline.generated
                if item.candidate is not None and item.objective_value is not None
            ),
            key=lambda item: item.objective_value,
        )
        self.assertLessEqual(
            evaluations[1].objective_value - evaluations[0].objective_value,
            policy.epsilon_tie,
        )
        boundary_between_gains = (
            evaluations[0].gain + evaluations[1].gain
        ) / 2
        self.assertGreater(evaluations[0].gain, boundary_between_gains)
        self.assertLess(evaluations[1].gain, boundary_between_gains)
        result = engine(
            policy_override=replace(policy, tau=boundary_between_gains),
            registry_override=tie_registry,
        ).select(state)
        self.assertEqual(result.outcome, Outcome.PRESENT_CHOICES)
        self.assertEqual(len(result.selected_ids), 2)

    def test_same_strategy_near_tie_prefers_lower_cost_and_intensity(self):
        policy, _ = load_selection_policy(POLICY_PATH)
        registry = load_strategy_registry(REGISTRY_PATH)
        level1 = next(
            item
            for item in registry.candidates
            if item.strategy_id == "TEST_CRITERIA_SUPPORT" and item.intensity is Level.L1
        )
        level2 = next(
            item
            for item in registry.candidates
            if item.strategy_id == "TEST_CRITERIA_SUPPORT" and item.intensity is Level.L2
        )
        level2 = replace(
            level2,
            capability=level1.capability,
            evidence_quality=level1.evidence_quality,
            workflow_cost=level1.workflow_cost,
            minimum_evidence=level1.minimum_evidence,
        )
        tie_registry = replace(registry, candidates=(level2, level1))
        result = engine(
            policy_override=replace(policy, tau=0.0),
            registry_override=tie_registry,
        ).select(selector_state(support_needs=SupportNeeds(3, 0, 0)))
        self.assertEqual(result.outcome, Outcome.INTERVENE)
        self.assertEqual(result.selected_ids, ("TEST_CRITERIA_SUPPORT:L1",))

    def test_audit_has_state_hashes_objectives_and_evidence(self):
        result = engine().select(selector_state()).to_dict()
        metadata = result["metadata"]
        self.assertEqual(result["contract_version"], "retrace-selector-v0.6")
        self.assertEqual(len(result["decision_digest"]), 64)
        self.assertEqual(len(metadata["registry_hash"]), 64)
        self.assertEqual(len(metadata["policy_hash"]), 64)
        self.assertEqual(len(metadata["decision_state_hash"]), 64)
        self.assertEqual(metadata["policy_version"], "selection-policy-v0.6")
        self.assertEqual(metadata["engine_version"], "0.6.0")
        self.assertIn("J_no_intervention", metadata)
        self.assertIn("Gain", metadata)
        self.assertEqual(metadata["state"]["evidence_refs"][0]["evidence_id"], "E1")


class V06CliTests(unittest.TestCase):
    def test_select_v06_cli_accepts_real_pilot_state(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        command = [
            sys.executable,
            "-m",
            "retrace_selector.cli",
            "select-v06",
            "--state",
            str(PILOT_DIR / "SRE-0017.state.json"),
            "--policy",
            str(POLICY_PATH),
            "--registry",
            str(REGISTRY_PATH),
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["contract_version"], "retrace-selector-v0.6")
        self.assertEqual(result["metadata"]["state"]["decision_id"], "SRE-0017")


if __name__ == "__main__":
    unittest.main()
