from __future__ import annotations

from .models import DecisionBrief, DecisionState, PolicySpec, Primitive, Risk


def generate_candidates(
    state: DecisionState, policy: PolicySpec
) -> tuple[DecisionBrief, ...]:
    """Generate canonical single-component candidates plus NO_INTERVENTION."""

    candidates: list[DecisionBrief] = [DecisionBrief.no_intervention()]
    allowed_levels = policy.allowed_levels[state.process_state]
    if not allowed_levels:
        return tuple(candidates)

    for primitive in sorted(Primitive, key=lambda item: item.value):
        profile = policy.primitive_profiles[primitive]
        need_level = getattr(state.governance_needs, profile.primary_need)
        authorization_override = (
            primitive is Primitive.DISPOSITION_COORDINATION
            and state.authorization_risk is Risk.HIGH
        )
        if need_level == 0 and not authorization_override:
            continue
        for level in allowed_levels:
            candidates.append(DecisionBrief.intervention(primitive, level))

    return tuple(sorted(candidates, key=lambda item: item.brief_id))
