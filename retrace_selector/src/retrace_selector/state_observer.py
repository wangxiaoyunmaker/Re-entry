"""Runtime support-state observation for the intervention selector.

This module deliberately does not classify a binary ``Re-entry`` label. It
combines first-stage user behavior evidence with runtime signals such as
unresolved delegation attempts to decide whether deeper support analysis
should run.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .models import ValidationError


_SUPPORT_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
_TRACE_COVERAGE = {"ADEQUATE", "PARTIAL", "INADEQUATE"}
_RISKS = {"low", "medium", "high"}


def _unit_float(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValidationError(f"{name} must be within [0, 1]")
    return value


def observe_runtime_support_state(
    *,
    behavior_evidence: Iterable[Mapping[str, Any]] = (),
    direct_delegation_failures: int = 0,
    progress_observed: bool | None = None,
    trace_coverage: str = "ADEQUATE",
    evidence_quality: float = 1.0,
    workflow_continuity: float = 1.0,
    repeated_unresolved: bool | None = None,
    consequence: str | None = None,
    reversibility: str | None = None,
    authorization_risk: str | None = None,
    target_key: str | None = None,
    delegation_attempt_count: int | None = None,
    last_confirmed_progress: bool | None = None,
    failure_window: int | None = None,
    cooldown_until: str | None = None,
    recent_intervention_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Observe whether deeper support analysis should run.

    This is intentionally upstream of Support Profile generation. It consumes
    only first-stage behavior evidence and runtime signals. It never reads or
    infers a Support Profile and never emits a binary Re-entry label.
    """

    if (
        isinstance(direct_delegation_failures, bool)
        or not isinstance(direct_delegation_failures, int)
        or direct_delegation_failures < 0
    ):
        raise ValidationError("direct_delegation_failures must be a non-negative integer")
    if trace_coverage not in _TRACE_COVERAGE:
        raise ValidationError("trace_coverage is invalid")
    evidence_quality = _unit_float(evidence_quality, "evidence_quality")
    workflow_continuity = _unit_float(workflow_continuity, "workflow_continuity")
    for field_name, value in (
        ("consequence", consequence),
        ("reversibility", reversibility),
        ("authorization_risk", authorization_risk),
    ):
        if value is not None and value not in _RISKS:
            raise ValidationError(f"{field_name} is invalid")
    if target_key is not None and (not isinstance(target_key, str) or not target_key.strip()):
        raise ValidationError("target_key must be a non-empty string when set")
    if delegation_attempt_count is not None and (
        isinstance(delegation_attempt_count, bool)
        or not isinstance(delegation_attempt_count, int)
        or delegation_attempt_count < 0
    ):
        raise ValidationError("delegation_attempt_count must be a non-negative integer")
    if last_confirmed_progress is not None and not isinstance(last_confirmed_progress, bool):
        raise ValidationError("last_confirmed_progress must be boolean when set")
    if failure_window is not None and (
        isinstance(failure_window, bool)
        or not isinstance(failure_window, int)
        or failure_window < 0
    ):
        raise ValidationError("failure_window must be a non-negative integer")
    if cooldown_until is not None and (
        not isinstance(cooldown_until, str) or not cooldown_until.strip()
    ):
        raise ValidationError("cooldown_until must be a non-empty string when set")
    recent_ids = list(recent_intervention_ids)
    if any(not isinstance(item, str) or not item.strip() for item in recent_ids):
        raise ValidationError("recent_intervention_ids must contain non-empty strings")
    if len(set(recent_ids)) != len(recent_ids):
        raise ValidationError("recent_intervention_ids must be unique")
    evidence = list(behavior_evidence)

    if repeated_unresolved is None:
        repeated = (failure_window or 0) >= 2 or direct_delegation_failures >= 2
    else:
        repeated = bool(repeated_unresolved)

    explicit_user_support_action = any(
        item.get("action_focus") in {"VERIFICATION", "DISPOSITION", "BOTH"}
        or bool(item.get("supports_primitives"))
        for item in evidence
        if item.get("actor") == "USER"
    )
    basis_relevant_signal = any(
        item.get("basis_relevant_signal") is True
        for item in evidence
        if item.get("actor") == "USER"
    )
    direct_failure = (
        direct_delegation_failures > 0
        or progress_observed is False
        or last_confirmed_progress is False
    )
    high_risk = authorization_risk == "high" or (
        consequence == "high" and reversibility == "low"
    )

    reasons: list[str] = []
    uncertainties: list[str] = []
    if repeated:
        reasons.append("repeated direct delegation without confirmed progress")
    elif direct_failure:
        reasons.append("direct delegation has not produced confirmed progress")
    if explicit_user_support_action:
        reasons.append("user behavior contains an explicit verification or action-boundary request")
    if basis_relevant_signal:
        reasons.append("user behavior contains a basis-relevant rule, state, impact, or causal signal")
    if high_risk:
        reasons.append("current state contains an independent high-risk signal")
    if trace_coverage == "PARTIAL":
        uncertainties.append("trace is partial")
    elif trace_coverage == "INADEQUATE":
        uncertainties.append("trace is inadequate")
    if evidence_quality < 0.5:
        uncertainties.append("evidence quality is low")
    if workflow_continuity < 0.5:
        uncertainties.append("workflow continuity is low")

    if high_risk or basis_relevant_signal or explicit_user_support_action:
        observation_state = "SUPPORT_PROFILE_CANDIDATE"
        support_opportunity = "ANALYSIS_NEEDED"
        routing_reason = "basis or risk signal requires deeper support analysis before continuing"
    elif direct_failure or repeated:
        observation_state = "EARLY_SUPPORT_OPPORTUNITY"
        support_opportunity = "EARLY_SUPPORT"
        routing_reason = "delegation failure is observable; offer low-burden support before waiting for a behavior shift"
    else:
        observation_state = "DELEGATION_PROGRESSING"
        support_opportunity = "NONE"
        routing_reason = "no support signal or failed delegation is currently observable"

    # Incomplete context does not erase a concrete failure signal, but it
    # prevents confident automatic escalation when there is no such signal.
    if (
        trace_coverage == "INADEQUATE"
        and not direct_failure
        and not basis_relevant_signal
        and not high_risk
    ):
        support_opportunity = "ABSTAIN"
        observation_state = "ABSTAIN"
        routing_reason = "insufficient trace without an independent support or failure signal"
        uncertainties.append("automatic routing abstained because no concrete signal survived the incomplete trace")

    if trace_coverage == "INADEQUATE" or evidence_quality < 0.5:
        confidence = "LOW"
    elif trace_coverage == "PARTIAL":
        confidence = "MEDIUM"
    else:
        confidence = "HIGH"

    return {
        "observation_state": observation_state,
        "support_opportunity": support_opportunity,
        "should_generate_support_profile": support_opportunity == "ANALYSIS_NEEDED"
        or support_opportunity == "EARLY_SUPPORT",
        "repeated_unresolved": repeated,
        "delegation_failure_signal": direct_failure,
        "basis_relevant_signal": basis_relevant_signal,
        "high_risk_signal": high_risk,
        "explicit_user_support_action": explicit_user_support_action,
        "confidence": confidence,
        "reasons": reasons,
        "uncertainties": list(dict.fromkeys(uncertainties)),
        "routing_reason": routing_reason,
        "target_key": target_key.strip() if target_key is not None else None,
        "delegation_attempt_count": delegation_attempt_count,
        "last_confirmed_progress": last_confirmed_progress,
        "failure_window": failure_window,
        "cooldown_until": cooldown_until.strip() if cooldown_until is not None else None,
        "recent_intervention_ids": recent_ids,
    }
