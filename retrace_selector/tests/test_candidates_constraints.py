from __future__ import annotations

import unittest

from retrace_selector.candidates import generate_candidates
from retrace_selector.constraints import evaluate_constraints
from retrace_selector.models import DecisionBrief, Level, Outcome, Primitive

from common import engine, state


def failed_rules(brief, decision_state, policy):
    return {
        record.rule_id
        for record in evaluate_constraints(brief, decision_state, policy)
        if not record.allowed
    }


class CandidateGenerationTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine()

    def test_progressing_generates_only_no_intervention(self):
        decision_state = state(
            process_state="DELEGATION_PROGRESSING",
            governance_needs={"O": 3, "S": 3, "D": 3},
        )
        candidates = generate_candidates(decision_state, self.engine.policy)
        self.assertEqual([item.brief_id for item in candidates], ["NO_INTERVENTION"])

    def test_early_support_generates_only_l1(self):
        decision_state = state(
            process_state="EARLY_SUPPORT_OPPORTUNITY",
            governance_needs={"O": 2, "S": 2, "D": 2},
        )
        candidates = generate_candidates(decision_state, self.engine.policy)
        self.assertTrue(
            all(item.is_no_intervention or item.level is Level.L1 for item in candidates)
        )

    def test_zero_need_does_not_generate_unrelated_candidates(self):
        decision_state = state(governance_needs={"O": 3, "S": 0, "D": 0})
        candidates = generate_candidates(decision_state, self.engine.policy)
        primitives = {item.primitive for item in candidates if item.primitive}
        self.assertEqual(primitives, {Primitive.RULE_ALIGNMENT})

    def test_high_authorization_forces_disposition_candidate_generation(self):
        decision_state = state(
            governance_needs={"O": 0, "S": 0, "D": 0},
            authorization_risk="high",
        )
        candidates = generate_candidates(decision_state, self.engine.policy)
        primitives = {item.primitive for item in candidates if item.primitive}
        self.assertEqual(primitives, {Primitive.DISPOSITION_COORDINATION})


class ConstraintTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine()
        self.policy = self.engine.policy

    def test_partial_evidence_rejects_high_causal_explanation(self):
        decision_state = state(evidence_completeness="partial")
        for level in (Level.L2, Level.L3):
            brief = DecisionBrief.intervention(Primitive.CAUSAL_EXPLANATION, level)
            self.assertIn("C030_MINIMUM_EVIDENCE", failed_rules(brief, decision_state, self.policy))

    def test_high_causal_explanation_requires_observed_source(self):
        decision_state = state(
            governance_needs={"O": 0, "S": 3, "D": 0},
            evidence=[{"evidence_id": "E1", "source": "INFERRED"}],
            evidence_completeness="sufficient",
        )
        for level in (Level.L2, Level.L3):
            brief = DecisionBrief.intervention(Primitive.CAUSAL_EXPLANATION, level)
            self.assertIn(
                "C035_CAUSAL_EXPLANATION_REQUIRES_OBSERVATION",
                failed_rules(brief, decision_state, self.policy),
            )

    def test_observed_source_allows_high_causal_explanation(self):
        decision_state = state(
            governance_needs={"O": 0, "S": 3, "D": 0},
            evidence_completeness="sufficient",
        )
        brief = DecisionBrief.intervention(Primitive.CAUSAL_EXPLANATION, Level.L2)
        self.assertNotIn(
            "C035_CAUSAL_EXPLANATION_REQUIRES_OBSERVATION",
            failed_rules(brief, decision_state, self.policy),
        )

    def test_high_authorization_rejects_b0_and_non_disposition(self):
        decision_state = state(authorization_risk="high")
        self.assertIn(
            "C020_HIGH_AUTHORIZATION_REQUIRES_DISPOSITION",
            failed_rules(DecisionBrief.no_intervention(), decision_state, self.policy),
        )
        verification = DecisionBrief.intervention(Primitive.VERIFICATION, Level.L3)
        self.assertIn(
            "C020_HIGH_AUTHORIZATION_REQUIRES_DISPOSITION",
            failed_rules(verification, decision_state, self.policy),
        )
        disposition = DecisionBrief.intervention(
            Primitive.DISPOSITION_COORDINATION, Level.L2
        )
        self.assertNotIn(
            "C020_HIGH_AUTHORIZATION_REQUIRES_DISPOSITION",
            failed_rules(disposition, decision_state, self.policy),
        )

    def test_high_consequence_low_reversibility_rejects_b0_and_l1(self):
        decision_state = state(consequence="high", reversibility="low")
        for brief in (
            DecisionBrief.no_intervention(),
            DecisionBrief.intervention(Primitive.VERIFICATION, Level.L1),
        ):
            self.assertIn(
                "C010_HIGH_CONSEQUENCE_LOW_REVERSIBILITY",
                failed_rules(brief, decision_state, self.policy),
            )

    def test_low_risk_high_reversibility_rejects_l3(self):
        decision_state = state(consequence="low", reversibility="high")
        brief = DecisionBrief.intervention(Primitive.RULE_ALIGNMENT, Level.L3)
        self.assertIn("C060_LOW_RISK_FORBIDS_L3", failed_rules(brief, decision_state, self.policy))

    def test_active_verification_rejects_verification_only(self):
        decision_state = state(active_verification=True)
        verification = DecisionBrief.intervention(Primitive.VERIFICATION, Level.L1)
        provenance = DecisionBrief.intervention(Primitive.PROVENANCE, Level.L1)
        self.assertIn("C070_AVOID_DUPLICATE_VERIFICATION", failed_rules(verification, decision_state, self.policy))
        self.assertNotIn("C070_AVOID_DUPLICATE_VERIFICATION", failed_rules(provenance, decision_state, self.policy))

    def test_cooldown_rejects_l2_but_not_l1(self):
        decision_state = state(recent_interventions=3)
        l1 = DecisionBrief.intervention(Primitive.VERIFICATION, Level.L1)
        l2 = DecisionBrief.intervention(Primitive.VERIFICATION, Level.L2)
        self.assertNotIn("C080_RECENT_INTERVENTION_COOLDOWN", failed_rules(l1, decision_state, self.policy))
        self.assertIn("C080_RECENT_INTERVENTION_COOLDOWN", failed_rules(l2, decision_state, self.policy))


if __name__ == "__main__":
    unittest.main()
