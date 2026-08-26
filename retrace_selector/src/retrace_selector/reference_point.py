"""State-specific five-dimensional reference point for v0.6."""

from __future__ import annotations

from .models import ScoreVector
from .v06_models import CoreRisk, SelectorDecisionState


def reference_point(state: SelectorDecisionState) -> ScoreVector:
    evidence_target = (
        1.0
        if state.risk_level is CoreRisk.HIGH or state.authorization_required
        else 0.5
    )
    return ScoreVector(
        criteria_basis_reconstruction=state.support_needs.normalized(
            "criteria_basis_reconstruction"
        ),
        project_state_reconstruction=state.support_needs.normalized(
            "project_state_reconstruction"
        ),
        evidence_action_governance=state.support_needs.normalized(
            "evidence_action_governance"
        ),
        evidence_quality=evidence_target,
        workflow_continuity=0.8,
    )
