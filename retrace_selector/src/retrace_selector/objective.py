"""Reference-point objective used after the single Skyline pass.

The selector is a finite discrete optimization problem: the candidate library
is small, so evaluating every feasible non-dominated candidate is exact and
more transparent than using a numerical optimizer.
"""

from __future__ import annotations

from typing import Mapping

from .models import (
    SCORE_DIMENSIONS,
    ScoreVector,
    DecisionState,
    PolicySpec,
    Risk,
)


def _state_risk(state: DecisionState) -> Risk:
    if state.authorization_risk is Risk.HIGH or (
        state.consequence is Risk.HIGH and state.reversibility is Risk.LOW
    ):
        return Risk.HIGH
    if Risk.HIGH in {state.consequence, state.reversibility, state.authorization_risk}:
        return Risk.MEDIUM
    if Risk.MEDIUM in {state.consequence, state.reversibility, state.authorization_risk}:
        return Risk.MEDIUM
    return Risk.LOW


def target_vector(state: DecisionState, policy: PolicySpec) -> Mapping[str, float]:
    """Build the state-conditioned aspiration point for the five score axes."""

    return {
        "criteria_basis_reconstruction": state.support_needs.normalized(
            "criteria_basis_reconstruction"
        ),
        "project_state_reconstruction": state.support_needs.normalized(
            "project_state_reconstruction"
        ),
        "evidence_action_governance": state.support_needs.normalized(
            "evidence_action_governance"
        ),
        "evidence_quality": policy.objective["evidence_target_by_risk"][
            _state_risk(state).value
        ],
        "workflow_continuity": float(policy.objective["workflow_target"]),
    }


def gap_vector(
    score: ScoreVector,
    target: Mapping[str, float],
) -> Mapping[str, float]:
    """Return only the shortfall below the state-conditioned target."""

    return {
        key: max(0.0, float(target[key]) - getattr(score, key))
        for key in SCORE_DIMENSIONS
    }


def objective_value(
    score: ScoreVector,
    state: DecisionState,
    policy: PolicySpec,
) -> tuple[float, Mapping[str, float], Mapping[str, float]]:
    """Compute J(c), its target point, and its per-axis gaps.

    Lower is better. The first term is the weighted total shortfall; the
    second term prevents a single severe shortfall from being hidden by good
    performance on other dimensions.
    """

    target = target_vector(state, policy)
    gaps = gap_vector(score, target)
    weighted_gap = sum(policy.weights[key] * gaps[key] for key in SCORE_DIMENSIONS)
    worst_gap = max(gaps.values()) if gaps else 0.0
    value = weighted_gap + float(policy.objective["max_gap_penalty"]) * worst_gap
    return value, target, gaps


def objective_improvement(
    candidate_value: float,
    baseline_value: float,
) -> float:
    """Positive values mean the candidate reduces target shortfall."""

    return baseline_value - candidate_value
