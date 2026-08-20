from __future__ import annotations

from .models import (
    DecisionBrief,
    DecisionState,
    EvidenceCompleteness,
    EvidenceRef,
    EvidenceSource,
    PolicySpec,
)


def evidence_supports_candidate(
    evidence: EvidenceRef,
    brief: DecisionBrief,
    policy: PolicySpec,
) -> bool:
    """Return whether an evidence reference is linked to this candidate.

    Primitive bindings are more specific than need bindings. Legacy v1 evidence
    with no bindings remains globally applicable for backward compatibility.
    """

    if brief.is_no_intervention or brief.primitive is None:
        return False
    if evidence.supports_primitives:
        return brief.primitive in evidence.supports_primitives
    if evidence.supports_needs:
        primary_need = policy.primitive_profiles[brief.primitive].primary_need
        return primary_need in evidence.supports_needs
    return True


def supporting_evidence(
    brief: DecisionBrief,
    state: DecisionState,
    policy: PolicySpec,
    *,
    empirical_only: bool = True,
) -> tuple[EvidenceRef, ...]:
    result = []
    for item in state.evidence:
        if empirical_only and item.source is EvidenceSource.DESIGN_ASSUMPTION:
            continue
        if evidence_supports_candidate(item, brief, policy):
            result.append(item)
    return tuple(result)


def candidate_evidence_completeness(
    brief: DecisionBrief,
    state: DecisionState,
    policy: PolicySpec,
) -> EvidenceCompleteness:
    linked = supporting_evidence(brief, state, policy)
    if not linked:
        return EvidenceCompleteness.NONE
    if state.evidence_completeness is EvidenceCompleteness.NONE:
        return EvidenceCompleteness.NONE
    if state.evidence_completeness is EvidenceCompleteness.PARTIAL:
        return EvidenceCompleteness.PARTIAL
    all_empirical = supporting_evidence(
        brief,
        state,
        policy,
        empirical_only=False,
    )
    empirical_state_evidence = tuple(
        item
        for item in state.evidence
        if item.source is not EvidenceSource.DESIGN_ASSUMPTION
    )
    linked_empirical = tuple(
        item for item in all_empirical if item.source is not EvidenceSource.DESIGN_ASSUMPTION
    )
    if len(linked_empirical) < len(empirical_state_evidence):
        return EvidenceCompleteness.PARTIAL
    return state.evidence_completeness


def candidate_evidence_score(
    brief: DecisionBrief,
    state: DecisionState,
    policy: PolicySpec,
) -> float:
    linked = supporting_evidence(brief, state, policy)
    if not linked:
        return 0.0
    source_strength = max(
        1.0 if item.source is EvidenceSource.OBSERVED else 0.5 for item in linked
    )
    return min(state.evidence_completeness.score, source_strength)
