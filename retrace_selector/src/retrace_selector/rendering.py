from __future__ import annotations

from .evidence import supporting_evidence
from .models import (
    DecisionBrief,
    DecisionState,
    PolicySpec,
    RenderedBrief,
    TemplateCatalog,
)


def render_brief(
    brief: DecisionBrief,
    state: DecisionState,
    policy: PolicySpec,
    templates: TemplateCatalog,
) -> RenderedBrief:
    if brief.is_no_intervention or brief.primitive is None or brief.level is None:
        raise ValueError("NO_INTERVENTION does not render a Decision Brief")
    entry = templates.templates[brief.primitive][brief.level]
    return RenderedBrief(
        brief_id=brief.brief_id,
        title=entry.title,
        message=entry.message,
        evidence_ids=tuple(
            item.evidence_id for item in supporting_evidence(brief, state, policy)
        ),
        next_step=entry.next_step,
    )
