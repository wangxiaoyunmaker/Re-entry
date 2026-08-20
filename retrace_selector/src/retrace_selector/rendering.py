from __future__ import annotations

from .models import DecisionBrief, DecisionState, RenderedBrief, TemplateCatalog


def render_brief(
    brief: DecisionBrief, state: DecisionState, templates: TemplateCatalog
) -> RenderedBrief:
    if brief.is_no_intervention or brief.primitive is None or brief.level is None:
        raise ValueError("NO_INTERVENTION does not render a Decision Brief")
    entry = templates.templates[brief.primitive][brief.level]
    return RenderedBrief(
        brief_id=brief.brief_id,
        title=entry.title,
        message=entry.message,
        evidence_ids=tuple(item.evidence_id for item in state.evidence),
        next_step=entry.next_step,
    )
