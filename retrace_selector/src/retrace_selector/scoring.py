from __future__ import annotations

from typing import Mapping

from .constraints import intervention_burden
from .evidence import candidate_evidence_score
from .models import (
    SCORE_DIMENSIONS,
    SUPPORT_DIMENSIONS,
    DecisionBrief,
    DecisionState,
    PolicySpec,
    Risk,
    ScoreVector,
)


NO_INTERVENTION_SCORE = ScoreVector(
    criteria_basis_reconstruction=0.0,
    project_state_reconstruction=0.0,
    evidence_action_governance=0.0,
    evidence_quality=1.0,
    workflow_continuity=1.0,
)


def state_compatibility(
    brief: DecisionBrief,
    state: DecisionState,
    dimension: str,
) -> float:
    """Return candidate-specific compatibility for one support dimension."""

    assert brief.primitive is not None
    compatibility = state.support_needs.normalized(dimension)
    if (
        dimension == "evidence_action_governance"
        and brief.primitive is not None
        and brief.primitive.value == "DISPOSITION_COORDINATION"
        and state.authorization_risk is Risk.HIGH
    ):
        compatibility = max(compatibility, 1.0)
    return compatibility


def score_brief(
    brief: DecisionBrief, state: DecisionState, policy: PolicySpec
) -> ScoreVector:
    if brief.is_no_intervention:
        return NO_INTERVENTION_SCORE

    assert brief.primitive is not None and brief.level is not None
    profile = policy.primitive_profiles[brief.primitive]
    multiplier = policy.level_multipliers[brief.level]
    benefits = {
        key: profile.capabilities[key]
        * multiplier
        * state_compatibility(brief, state, key)
        for key in SUPPORT_DIMENSIONS
    }
    evidence = candidate_evidence_score(brief, state, policy)
    workflow = max(0.0, 1.0 - intervention_burden(brief, state, policy))
    return ScoreVector(
        criteria_basis_reconstruction=benefits["criteria_basis_reconstruction"],
        project_state_reconstruction=benefits["project_state_reconstruction"],
        evidence_action_governance=benefits["evidence_action_governance"],
        evidence_quality=evidence,
        workflow_continuity=workflow,
    )


def contextual_weights(
    state: DecisionState,
    policy: PolicySpec,
) -> tuple[Mapping[str, float], dict[str, object]]:
    """Apply context adjustments only to the post-Skyline ranking weights.

    Hard constraints remain in ``constraints.py``. This layer cannot make an
    unsafe candidate feasible; it only changes the preference among feasible
    non-dominated candidates and records which rules were applied.
    """

    active_rules: list[str] = []
    active_dimensions: list[str] = []
    low_profile_confidence = False
    if state.support_profile:
        need_rank = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        active = {
            dimension: item
            for dimension, item in state.support_profile.items()
            if item["observed_work"] != "NONE"
            and need_rank[item["support_need"]] > 0
        }
        if active:
            active_dimensions = list(active)
            top_rank = max(need_rank[item["support_need"]] for item in active.values())
            top_dimensions = [
                dimension
                for dimension, item in active.items()
                if need_rank[item["support_need"]] == top_rank
            ]
            if len(top_dimensions) > 1:
                active_rules.append("MULTI_BASIS")
            else:
                focus_rule = {
                    "criteria_basis_reconstruction": "FOCUS_CRITERIA_BASIS",
                    "project_state_reconstruction": "FOCUS_PROJECT_STATE",
                    "evidence_action_governance": "FOCUS_EVIDENCE_ACTION",
                }[top_dimensions[0]]
                active_rules.append(focus_rule)
            low_profile_confidence = any(
                item["confidence"] == "LOW" for item in active.values()
            )
            if low_profile_confidence:
                active_rules.append("LOW_PROFILE_CONFIDENCE")

    deltas = {key: 0.0 for key in SCORE_DIMENSIONS}
    configured_rules = policy.contextual_weight_adjustment["rules"]
    for rule_id in active_rules:
        rule = configured_rules.get(rule_id)
        if rule is None:
            continue
        for key in SCORE_DIMENSIONS:
            deltas[key] += float(rule[key])

    raw = {
        key: max(0.0, policy.weights[key] + deltas[key])
        for key in SCORE_DIMENSIONS
    }
    total = sum(raw.values())
    effective = {key: raw[key] / total for key in SCORE_DIMENSIONS}
    return effective, {
        "base_weights": dict(policy.weights),
        "applied_rules": active_rules,
        "raw_deltas": deltas,
        "effective_weights": effective,
        "support_profile_dimensions": active_dimensions,
        "support_profile_fallback_used": state.support_profile is None,
        "low_profile_confidence": low_profile_confidence,
    }


def utility(
    score: ScoreVector,
    policy: PolicySpec,
    weights: Mapping[str, float] | None = None,
) -> float:
    """Legacy score aggregation retained for old callers and audit replay.

    The selector never calls this function. Final decisions use
    :func:`retrace_selector.objective.objective_value` (J(c)).
    """
    ranking_weights = weights or policy.weights
    return sum(
        ranking_weights[key] * getattr(score, key) for key in SCORE_DIMENSIONS
    )
