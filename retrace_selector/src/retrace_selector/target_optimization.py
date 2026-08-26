"""Five-dimensional target-gap objective for the v0.6 selector."""

from __future__ import annotations

from .models import SCORE_DIMENSIONS, ScoreVector
from .reference_point import reference_point
from .v06_models import SelectionPolicy, SelectorDecisionState


def gap_vector(score: ScoreVector, target: ScoreVector) -> tuple[float, ...]:
    return tuple(
        max(0.0, target_value - score_value)
        for score_value, target_value in zip(score.vector(), target.vector())
    )


def objective_value(
    score: ScoreVector,
    state: SelectorDecisionState,
    policy: SelectionPolicy,
) -> tuple[float, ScoreVector, dict[str, float]]:
    target = reference_point(state)
    gaps = gap_vector(score, target)
    weighted_gap = sum(weight * gap for weight, gap in zip(policy.weights, gaps))
    value = weighted_gap + policy.lambda_value * max(gaps, default=0.0)
    return value, target, dict(zip(SCORE_DIMENSIONS, gaps))


def gain_vs_no_intervention(candidate_value: float, baseline_value: float) -> float:
    return baseline_value - candidate_value
