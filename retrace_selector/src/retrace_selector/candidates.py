from __future__ import annotations

from .models import DecisionBrief, DecisionState, PolicySpec, Primitive


def generate_candidates(
    state: DecisionState, policy: PolicySpec
) -> tuple[DecisionBrief, ...]:
    """Generate canonical single-component candidates plus NO_INTERVENTION."""

    candidates: list[DecisionBrief] = [DecisionBrief.no_intervention()]
    allowed_levels = policy.allowed_levels[state.process_state]
    if not allowed_levels:
        return tuple(candidates)

    # Skyline must compare the complete intervention library that is allowed
    # by the process state. Support need is a state-conditioned score input,
    # not a pre-Skyline candidate deletion rule. Safety, evidence, authority,
    # burden, and process-state restrictions remain hard constraints in
    # evaluate_constraints().
    for primitive in sorted(Primitive, key=lambda item: item.value):
        for level in allowed_levels:
            candidates.append(DecisionBrief.intervention(primitive, level))

    return tuple(sorted(candidates, key=lambda item: item.brief_id))
