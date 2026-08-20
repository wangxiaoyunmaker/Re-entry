from __future__ import annotations

import itertools
import random
import unittest

from retrace_selector.models import (
    CandidateEvaluation,
    DecisionBrief,
    Level,
    Primitive,
    ScoreVector,
)
from retrace_selector.scoring import NO_INTERVENTION_SCORE, score_brief
from retrace_selector.skyline import compute_skyline, dominates

from common import engine, state


def evaluated(identifier: str, vector: tuple[float, float, float, float, float]):
    brief = DecisionBrief(brief_id=identifier)
    return CandidateEvaluation(brief=brief, score=ScoreVector(*vector))


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine()

    def test_no_intervention_vector_is_frozen(self):
        self.assertEqual(NO_INTERVENTION_SCORE.vector(), (0.0, 0.0, 0.0, 1.0, 1.0))

    def test_specialized_score_increases_with_matching_need(self):
        brief = DecisionBrief.intervention(Primitive.RULE_ALIGNMENT, Level.L2)
        low = score_brief(
            brief,
            state(governance_needs={"O": 1, "S": 0, "D": 0}),
            self.engine.policy,
        )
        high = score_brief(
            brief,
            state(governance_needs={"O": 3, "S": 0, "D": 0}),
            self.engine.policy,
        )
        self.assertGreater(high.O, low.O)
        self.assertEqual(high.S, 0.0)

    def test_all_generated_scores_are_unit_finite(self):
        decision_state = state(governance_needs={"O": 3, "S": 3, "D": 3})
        for primitive in Primitive:
            for level in Level:
                score = score_brief(
                    DecisionBrief.intervention(primitive, level),
                    decision_state,
                    self.engine.policy,
                )
                for value in score.vector():
                    self.assertGreaterEqual(value, 0.0)
                    self.assertLessEqual(value, 1.0)

    def test_no_evidence_does_not_receive_perfect_evidence_score(self):
        brief = DecisionBrief.intervention(Primitive.RULE_ALIGNMENT, Level.L1)
        score = score_brief(
            brief,
            state(evidence=[], evidence_completeness="none"),
            self.engine.policy,
        )
        self.assertEqual(score.E, 0.0)


class SkylineTests(unittest.TestCase):
    def test_strict_dominance_chain(self):
        a = evaluated("A", (1, 1, 1, 1, 1))
        b = evaluated("B", (0.8, 0.8, 0.8, 0.8, 0.8))
        c = evaluated("C", (0.5, 0.5, 0.5, 0.5, 0.5))
        frontier, witnesses = compute_skyline((c, a, b), 1e-9)
        self.assertEqual([item.brief.brief_id for item in frontier], ["A"])
        self.assertEqual(witnesses["B"], ("A",))
        self.assertEqual(witnesses["C"], ("A", "B"))

    def test_orthogonal_tradeoffs_remain(self):
        a = evaluated("A", (1, 0, 0, 1, 0))
        b = evaluated("B", (0, 1, 0, 0, 1))
        frontier, witnesses = compute_skyline((a, b), 1e-9)
        self.assertEqual({item.brief.brief_id for item in frontier}, {"A", "B"})
        self.assertEqual(witnesses, {})

    def test_equal_vectors_do_not_strictly_dominate(self):
        a = evaluated("A", (0.5, 0.5, 0.5, 0.5, 0.5))
        b = evaluated("B", (0.5, 0.5, 0.5, 0.5, 0.5))
        self.assertFalse(dominates(a, b, 1e-9))

    def test_candidate_order_does_not_change_frontier(self):
        candidates = [
            evaluated("A", (1, 0, 0, 1, 0)),
            evaluated("B", (0, 1, 0, 0, 1)),
            evaluated("C", (0, 0, 0, 0, 0)),
        ]
        expected = None
        random.seed(7)
        for _ in range(20):
            random.shuffle(candidates)
            frontier, _ = compute_skyline(candidates, 1e-9)
            current = tuple(item.brief.brief_id for item in frontier)
            expected = expected or current
            self.assertEqual(current, expected)

    def test_adding_dominated_candidate_does_not_change_frontier(self):
        a = evaluated("A", (1, 0, 0, 1, 0))
        b = evaluated("B", (0, 1, 0, 0, 1))
        dominated = evaluated("C", (0, 0, 0, 0, 0))
        before, _ = compute_skyline((a, b), 1e-9)
        after, witnesses = compute_skyline((a, b, dominated), 1e-9)
        self.assertEqual(
            tuple(item.brief.brief_id for item in before),
            tuple(item.brief.brief_id for item in after),
        )
        self.assertEqual(witnesses["C"], ("A", "B"))

    def test_frontier_contains_no_dominated_member(self):
        values = [0.0, 0.5, 1.0]
        candidates = [
            evaluated(f"C{i}", vector)
            for i, vector in enumerate(itertools.islice(itertools.product(values, repeat=5), 80))
        ]
        frontier, _ = compute_skyline(candidates, 1e-9)
        for left in frontier:
            for right in candidates:
                if left is not right:
                    self.assertFalse(dominates(right, left, 1e-9))


if __name__ == "__main__":
    unittest.main()
