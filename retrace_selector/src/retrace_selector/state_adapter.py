"""Projection from evidence-rich ReTrace states to the v0.6 core view."""

from __future__ import annotations

from typing import Any, Mapping

from .models import (
    DecisionState as LegacyDecisionState,
    Risk,
    SupportOpportunity,
    ValidationError,
)
from .v06_models import CoreRisk, SelectorDecisionState, SelectorEvidenceRef


class ClarificationRequired(ValidationError):
    """Raised when the upstream state explicitly abstains."""


_MINIMAL_FIELDS = {
    "decision_id",
    "process_state",
    "support_needs",
    "risk_level",
    "authorization_required",
    "evidence_level",
    "confidence",
    "recent_intervention_count",
    "active_verification",
    "evidence_refs",
}


def _opportunity_for_process_state(process_state: Any) -> str:
    return {
        "DELEGATION_PROGRESSING": "NONE",
        "EARLY_SUPPORT_OPPORTUNITY": "EARLY_SUPPORT",
        "REENTRY_OCCASION_OBSERVED": "REENTRY_SUPPORT",
        "GOVERNANCE_RECOVERING": "REENTRY_SUPPORT",
    }.get(str(process_state), "ABSTAIN")


def _normalize_legacy_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(raw)
    if "support_needs" not in data and "governance_needs" in data:
        governance = data.pop("governance_needs")
        if not isinstance(governance, Mapping):
            raise ValidationError("governance_needs must be an object")
        missing = {"O", "S", "D"} - set(governance)
        unknown = set(governance) - {"O", "S", "D"}
        if missing or unknown:
            raise ValidationError(
                "governance_needs must contain exactly O, S, and D"
            )
        data["support_needs"] = {
            "criteria_basis_reconstruction": governance["O"],
            "project_state_reconstruction": governance["S"],
            "evidence_action_governance": governance["D"],
        }
    data.setdefault(
        "support_opportunity",
        _opportunity_for_process_state(data.get("process_state")),
    )
    return data


def _risk_view(state: LegacyDecisionState) -> CoreRisk:
    if state.consequence is Risk.HIGH and state.reversibility is Risk.LOW:
        return CoreRisk.HIGH
    if state.consequence is Risk.LOW and state.reversibility is Risk.HIGH:
        return CoreRisk.LOW
    return CoreRisk.MEDIUM


def project_legacy_state(state: LegacyDecisionState) -> SelectorDecisionState:
    if state.support_opportunity is SupportOpportunity.ABSTAIN:
        raise ClarificationRequired("support opportunity abstained upstream")
    return SelectorDecisionState(
        decision_id=state.decision_id,
        process_state=state.process_state,
        support_needs=state.support_needs,
        risk_level=_risk_view(state),
        authorization_required=state.authorization_risk is Risk.HIGH,
        evidence_level=state.evidence_completeness.score,
        confidence=state.state_confidence,
        recent_intervention_count=state.recent_interventions,
        active_verification=state.active_verification,
        evidence_refs=tuple(
            SelectorEvidenceRef(
                evidence_id=item.evidence_id,
                source=item.source.value,
            )
            for item in state.evidence
        ),
    )


def adapt_state(
    raw: SelectorDecisionState | LegacyDecisionState | Mapping[str, Any],
) -> SelectorDecisionState:
    """Return the compact v0.6 state without invoking an LLM."""

    if isinstance(raw, SelectorDecisionState):
        return raw
    if isinstance(raw, LegacyDecisionState):
        return project_legacy_state(raw)
    if not isinstance(raw, Mapping):
        raise ValidationError("state adapter input must be an object")
    if set(raw) == _MINIMAL_FIELDS:
        return SelectorDecisionState.from_dict(raw)
    legacy_raw = _normalize_legacy_mapping(raw)
    return project_legacy_state(LegacyDecisionState.from_dict(legacy_raw))
