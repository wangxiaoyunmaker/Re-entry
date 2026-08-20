from __future__ import annotations

from .constraints import intervention_burden
from .evidence import candidate_evidence_score
from .models import (
    CRITERIA,
    DecisionBrief,
    DecisionState,
    PolicySpec,
    ScoreVector,
)


NO_INTERVENTION_SCORE = ScoreVector(O=0.0, S=0.0, D=0.0, E=1.0, W=1.0)


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
        * state.governance_needs.normalized(key)
        for key in ("O", "S", "D")
    }
    evidence = candidate_evidence_score(brief, state, policy)
    workflow = max(0.0, 1.0 - intervention_burden(brief, state, policy))
    return ScoreVector(
        O=benefits["O"],
        S=benefits["S"],
        D=benefits["D"],
        E=evidence,
        W=workflow,
    )


def utility(score: ScoreVector, policy: PolicySpec) -> float:
    return sum(policy.weights[key] * getattr(score, key) for key in CRITERIA)
