from __future__ import annotations

from .models import (
    ConstraintRecord,
    DecisionBrief,
    DecisionState,
    EvidenceSource,
    Level,
    PolicySpec,
    Primitive,
    Risk,
)


def intervention_burden(
    brief: DecisionBrief, state: DecisionState, policy: PolicySpec
) -> float:
    if brief.is_no_intervention:
        return 0.0
    assert brief.primitive is not None and brief.level is not None
    base = policy.primitive_profiles[brief.primitive].burden[brief.level]
    cooldown = min(
        1.0,
        state.recent_interventions
        * policy.thresholds.cooldown_penalty_per_intervention,
    )
    return min(1.0, base + cooldown)


def _record(
    rule_id: str, allowed: bool, reason: str, priority: int
) -> ConstraintRecord:
    return ConstraintRecord(
        rule_id=rule_id,
        allowed=allowed,
        reason=reason,
        priority=priority,
    )


def evaluate_constraints(
    brief: DecisionBrief, state: DecisionState, policy: PolicySpec
) -> tuple[ConstraintRecord, ...]:
    """Evaluate all frozen candidate-level hard constraints."""

    records: list[ConstraintRecord] = []
    level = brief.level
    primitive = brief.primitive

    high_irreversible = (
        state.consequence is Risk.HIGH and state.reversibility is Risk.LOW
    )
    irreversible_allowed = not high_irreversible or (
        not brief.is_no_intervention and level is not None and level >= Level.L2
    )
    records.append(
        _record(
            "C010_HIGH_CONSEQUENCE_LOW_REVERSIBILITY",
            irreversible_allowed,
            "high-consequence, low-reversibility states require at least L2",
            10,
        )
    )

    authorization_allowed = state.authorization_risk is not Risk.HIGH or (
        primitive is Primitive.DISPOSITION_COORDINATION
        and level is not None
        and level >= Level.L2
    )
    records.append(
        _record(
            "C020_HIGH_AUTHORIZATION_REQUIRES_DISPOSITION",
            authorization_allowed,
            "high authorization risk requires disposition coordination at L2 or L3",
            20,
        )
    )

    evidence_allowed = True
    if not brief.is_no_intervention:
        assert primitive is not None and level is not None
        minimum = policy.primitive_profiles[primitive].minimum_evidence[level]
        evidence_allowed = state.evidence_completeness.rank >= minimum.rank
    records.append(
        _record(
            "C030_MINIMUM_EVIDENCE",
            evidence_allowed,
            "candidate evidence requirement must be met",
            30,
        )
    )

    causal_source_allowed = not (
        primitive is Primitive.CAUSAL_EXPLANATION
        and level is not None
        and level >= Level.L2
    ) or any(item.source is EvidenceSource.OBSERVED for item in state.evidence)
    records.append(
        _record(
            "C035_CAUSAL_EXPLANATION_REQUIRES_OBSERVATION",
            causal_source_allowed,
            "causal explanation at L2 or L3 requires directly observed evidence",
            35,
        )
    )

    confidence_allowed = brief.is_no_intervention or (
        state.state_confidence >= policy.thresholds.low_confidence
        or (level is not None and level <= Level.L1)
    )
    records.append(
        _record(
            "C040_LOW_CONFIDENCE_INTENSITY_CAP",
            confidence_allowed,
            "low-confidence states allow only L1 interventions",
            40,
        )
    )

    process_allowed = brief.is_no_intervention or (
        level is not None and level in policy.allowed_levels[state.process_state]
    )
    records.append(
        _record(
            "C050_PROCESS_STATE_LEVEL_CAP",
            process_allowed,
            "intervention level must be enabled for the current process state",
            50,
        )
    )

    low_risk_allowed = not (
        state.consequence is Risk.LOW
        and state.reversibility is Risk.HIGH
        and level is Level.L3
    )
    records.append(
        _record(
            "C060_LOW_RISK_FORBIDS_L3",
            low_risk_allowed,
            "low-consequence, highly reversible states forbid L3",
            60,
        )
    )

    verification_allowed = not (
        state.active_verification and primitive is Primitive.VERIFICATION
    )
    records.append(
        _record(
            "C070_AVOID_DUPLICATE_VERIFICATION",
            verification_allowed,
            "do not repeat Verification while an effective verification is active",
            70,
        )
    )

    cooldown_allowed = brief.is_no_intervention or (
        state.recent_interventions < policy.thresholds.cooldown_count
        or (level is not None and level <= Level.L1)
    )
    records.append(
        _record(
            "C080_RECENT_INTERVENTION_COOLDOWN",
            cooldown_allowed,
            "cooldown permits only L1 after repeated recent interventions",
            80,
        )
    )

    burden_allowed = (
        intervention_burden(brief, state, policy) <= policy.thresholds.max_burden
    )
    records.append(
        _record(
            "C090_MAX_BURDEN",
            burden_allowed,
            "candidate burden must not exceed the frozen maximum",
            90,
        )
    )
    return tuple(sorted(records, key=lambda record: record.priority))
