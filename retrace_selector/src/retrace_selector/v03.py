"""v0.3 evidence-conditioned selector boundary.

This boundary validates event-linked evidence, support opportunity, and the
full support-dimension names used by the selector. It is deliberately a
direct adapter: no hidden vocabulary translation is performed here.
"""

from __future__ import annotations

from typing import Any, Mapping

from .models import (
    DecisionState,
    SupportOpportunity,
    SUPPORT_DIMENSIONS,
    ValidationError,
)
from .selector import SelectionEngine
from .support_profile import aggregate_support_profile, recommend_support_need


DIMENSIONS = SUPPORT_DIMENSIONS
SUPPORT_LEVELS = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
OBSERVED_WORK = {"NONE", "POSSIBLE", "OBSERVED"}
CONFIDENCE = {"LOW": 0.5, "MEDIUM": 0.75, "HIGH": 0.9}
TRACE_COVERAGE = {"ADEQUATE", "PARTIAL", "INADEQUATE"}
TEMPORAL_POSITION = {"BEFORE_OR_AT_TRIGGER", "AFTER_TRIGGER", "UNKNOWN"}
UPTAKE_STATUS = {"NOT_OBSERVED", "POSSIBLE", "OBSERVED"}
ACTORS = {"USER", "AGENT", "TOOL", "PROJECT_MATERIAL", "UNKNOWN"}
SOURCES = {"OBSERVED", "INFERRED", "DESIGN_ASSUMPTION"}
VALUE_PROVENANCE = {"DESIGN_PRIOR", "HUMAN_CALIBRATED", "EXPOSURE_CALIBRATED"}
CALIBRATION_STATUS = {"PROVISIONAL", "FROZEN", "VALIDATED"}
PROCESS_STATE_MAP = {
    "DELEGATION_PROGRESSING": "DELEGATION_PROGRESSING",
    "EARLY_SUPPORT": "EARLY_SUPPORT_OPPORTUNITY",
    "EARLY_SUPPORT_OPPORTUNITY": "EARLY_SUPPORT_OPPORTUNITY",
    "REENTRY_SUPPORT": "REENTRY_OCCASION_OBSERVED",
    "REENTRY_OCCASION_OBSERVED": "REENTRY_OCCASION_OBSERVED",
    "GOVERNANCE_RECOVERING": "GOVERNANCE_RECOVERING",
}
SUPPORT_OPPORTUNITY_BY_PROCESS_STATE = {
    "DELEGATION_PROGRESSING": "NONE",
    "EARLY_SUPPORT": "EARLY_SUPPORT",
    "EARLY_SUPPORT_OPPORTUNITY": "EARLY_SUPPORT",
    "REENTRY_SUPPORT": "REENTRY_SUPPORT",
    "REENTRY_OCCASION_OBSERVED": "REENTRY_SUPPORT",
    "GOVERNANCE_RECOVERING": "REENTRY_SUPPORT",
}


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"v0.3 {name} must be an object")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"v0.3 {name} must be a non-empty string")
    return value.strip()


def _unit_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"v0.3 {name} must be a number")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValidationError(f"v0.3 {name} must be within [0, 1]")
    return value


def _risk(value: Any, name: str) -> str:
    value = _string(value, name).lower()
    if value not in {"low", "medium", "high"}:
        raise ValidationError(f"v0.3 {name} must be low, medium, or high")
    return value


def _support_entry(raw: Any, dimension: str) -> dict[str, Any]:
    data = _mapping(raw, f"support_profile.{dimension}")
    required = {"observed_work", "support_need", "confidence", "evidence_ids", "evidence_basis"}
    missing = required - set(data)
    unknown = set(data) - required
    if missing or unknown:
        raise ValidationError(
            f"v0.3 support_profile.{dimension} keys invalid; "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if data["observed_work"] not in OBSERVED_WORK:
        raise ValidationError(f"v0.3 {dimension}.observed_work is invalid")
    if data["support_need"] not in SUPPORT_LEVELS:
        raise ValidationError(f"v0.3 {dimension}.support_need is invalid")
    if data["confidence"] not in CONFIDENCE:
        raise ValidationError(f"v0.3 {dimension}.confidence is invalid")
    ids = data["evidence_ids"]
    if not isinstance(ids, list) or any(not isinstance(item, str) or not item for item in ids):
        raise ValidationError(f"v0.3 {dimension}.evidence_ids must be a string array")
    basis = data["evidence_basis"]
    if not isinstance(basis, list):
        raise ValidationError(f"v0.3 {dimension}.evidence_basis must be an array")
    normalized_basis = []
    for index, item in enumerate(basis):
        entry = _mapping(item, f"{dimension}.evidence_basis[{index}]")
        required_basis = {"signal", "actor", "temporal_position", "uptake_status"}
        if set(entry) != required_basis:
            raise ValidationError(
                f"v0.3 {dimension}.evidence_basis[{index}] keys must be "
                f"{sorted(required_basis)}"
            )
        if entry["actor"] not in ACTORS:
            raise ValidationError(f"v0.3 {dimension}.evidence_basis actor is invalid")
        if entry["temporal_position"] not in TEMPORAL_POSITION:
            raise ValidationError(
                f"v0.3 {dimension}.evidence_basis temporal_position is invalid"
            )
        if entry["uptake_status"] not in UPTAKE_STATUS:
            raise ValidationError(
                f"v0.3 {dimension}.evidence_basis uptake_status is invalid"
            )
        normalized_basis.append(dict(entry))
    return {
        "observed_work": data["observed_work"],
        "support_need": data["support_need"],
        "confidence": data["confidence"],
        "evidence_ids": list(dict.fromkeys(ids)),
        "evidence_basis": normalized_basis,
    }


def _validate_evidence(raw: Any, evidence_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValidationError("v0.3 evidence must be an array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        data = _mapping(item, f"evidence[{index}]")
        evidence_id = _string(data.get("evidence_id"), f"evidence[{index}].evidence_id")
        if evidence_id in seen:
            raise ValidationError(f"duplicate v0.3 evidence_id: {evidence_id}")
        seen.add(evidence_id)
        source = _string(data.get("source"), f"evidence[{index}].source")
        if source not in SOURCES:
            raise ValidationError(f"v0.3 evidence[{index}].source is invalid")
        supports = data.get("supports_dimensions", [])
        if not isinstance(supports, list) or any(item not in DIMENSIONS for item in supports):
            raise ValidationError(
                f"v0.3 evidence[{index}].supports_dimensions is invalid"
            )
        primitives = data.get("supports_primitives", [])
        if not isinstance(primitives, list) or any(not isinstance(item, str) for item in primitives):
            raise ValidationError(
                f"v0.3 evidence[{index}].supports_primitives is invalid"
            )
        available = data.get("available_at_decision", True)
        if not isinstance(available, bool):
            raise ValidationError(
                f"v0.3 evidence[{index}].available_at_decision must be boolean"
            )
        normalized.append(
            {
                "evidence_id": evidence_id,
                "source": source,
                "locator": data.get("locator", f"v0.3/{evidence_id}"),
                "observed_at": data.get("observed_at"),
                "sequence_index": data.get("sequence_index", index),
                "content_sha256": data.get("content_sha256", "0" * 64),
                "supports_dimensions": list(dict.fromkeys(supports)),
                "supports_primitives": list(dict.fromkeys(primitives)),
                "available_at_decision": available,
            }
        )
    evidence_ids.update(seen)
    return normalized


def validate_v03_state(raw: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(_mapping(raw, "state"))
    if "support_opportunity" not in data and isinstance(data.get("process_state"), str):
        data["support_opportunity"] = SUPPORT_OPPORTUNITY_BY_PROCESS_STATE.get(
            data["process_state"], "ABSTAIN"
        )
    required = {
        "schema_version",
        "decision_id",
        "process_state",
        "support_opportunity",
        "support_profile",
        "trace_coverage",
        "uncertainties",
        "consequence",
        "reversibility",
        "authorization_risk",
        "evidence_quality",
        "workflow_continuity",
        "evidence",
        "recent_interventions",
        "active_verification",
    }
    missing = required - set(data)
    if missing:
        raise ValidationError(f"v0.3 state missing fields: {sorted(missing)}")
    if data["schema_version"] != "retrace-state-v3":
        raise ValidationError("v0.3 state schema_version must be retrace-state-v3")
    process_state = _string(data["process_state"], "process_state")
    if process_state not in PROCESS_STATE_MAP:
        raise ValidationError(f"v0.3 process_state is invalid: {process_state}")
    support_opportunity = _string(
        data["support_opportunity"], "support_opportunity"
    )
    if support_opportunity not in {item.value for item in SupportOpportunity}:
        raise ValidationError("v0.3 support_opportunity is invalid")
    expected_opportunity = SUPPORT_OPPORTUNITY_BY_PROCESS_STATE[process_state]
    if support_opportunity != expected_opportunity and support_opportunity != "ABSTAIN":
        raise ValidationError(
            "v0.3 support_opportunity is inconsistent with process_state"
        )
    if data["trace_coverage"] not in TRACE_COVERAGE:
        raise ValidationError("v0.3 trace_coverage is invalid")
    if not isinstance(data["uncertainties"], list) or any(
        not isinstance(item, str) for item in data["uncertainties"]
    ):
        raise ValidationError("v0.3 uncertainties must be a string array")
    if data.get("value_provenance", "DESIGN_PRIOR") not in VALUE_PROVENANCE:
        raise ValidationError("v0.3 value_provenance is invalid")
    if data.get("calibration_status", "PROVISIONAL") not in CALIBRATION_STATUS:
        raise ValidationError("v0.3 calibration_status is invalid")
    if "repeated_unresolved" in data and data["repeated_unresolved"] is not None and not isinstance(data["repeated_unresolved"], bool):
        raise ValidationError("v0.3 repeated_unresolved must be boolean")
    for field_name in ("basis_relevant_signal", "delegation_failure_signal"):
        if field_name in data and data[field_name] is not None and not isinstance(data[field_name], bool):
            raise ValidationError(f"v0.3 {field_name} must be boolean")
    if "target_key" in data and data["target_key"] is not None and (
        not isinstance(data["target_key"], str) or not data["target_key"].strip()
    ):
        raise ValidationError("v0.3 target_key must be a non-empty string")
    if "delegation_attempt_count" in data and data["delegation_attempt_count"] is not None and (
        isinstance(data["delegation_attempt_count"], bool)
        or not isinstance(data["delegation_attempt_count"], int)
        or data["delegation_attempt_count"] < 0
    ):
        raise ValidationError("v0.3 delegation_attempt_count must be a non-negative integer")
    if "last_confirmed_progress" in data and data["last_confirmed_progress"] is not None and not isinstance(
        data["last_confirmed_progress"], bool
    ):
        raise ValidationError("v0.3 last_confirmed_progress must be boolean")
    if "failure_window" in data and data["failure_window"] is not None and (
        isinstance(data["failure_window"], bool)
        or not isinstance(data["failure_window"], int)
        or data["failure_window"] < 0
    ):
        raise ValidationError("v0.3 failure_window must be a non-negative integer")
    if "cooldown_until" in data and data["cooldown_until"] is not None and (
        not isinstance(data["cooldown_until"], str) or not data["cooldown_until"].strip()
    ):
        raise ValidationError("v0.3 cooldown_until must be a non-empty string")
    if "recent_intervention_ids" in data and data["recent_intervention_ids"] is not None:
        recent_ids = data["recent_intervention_ids"]
        if not isinstance(recent_ids, list) or any(
            not isinstance(item, str) or not item.strip() for item in recent_ids
        ):
            raise ValidationError("v0.3 recent_intervention_ids must be a string array")
        if len(set(recent_ids)) != len(recent_ids):
            raise ValidationError("v0.3 recent_intervention_ids must be unique")
    profile = _mapping(data["support_profile"], "support_profile")
    if set(profile) != set(DIMENSIONS):
        raise ValidationError(
            "v0.3 support_profile must contain exactly the three full mechanism names"
        )
    normalized_profile = {
        dimension: _support_entry(profile[dimension], dimension)
        for dimension in DIMENSIONS
    }
    extraction_packet = None
    if "behavior_evidence" in data or "basis_assessment" in data:
        if "behavior_evidence" not in data or "basis_assessment" not in data:
            raise ValidationError(
                "v0.3 behavior_evidence and basis_assessment must be provided together"
            )
        extraction_packet = aggregate_support_profile({
            "behavior_evidence": data["behavior_evidence"],
            "basis_assessment": data["basis_assessment"],
        })
        if extraction_packet["support_profile"] != normalized_profile:
            raise ValidationError(
                "v0.3 support_profile does not match the evidence-first basis assessment"
            )
    support_need_audit = {}
    assessments_for_audit = (
        extraction_packet["basis_assessment"] if extraction_packet is not None else {}
    )
    for dimension, item in normalized_profile.items():
        assessment = assessments_for_audit.get(dimension, {})
        support_need_audit[dimension] = recommend_support_need(
            observed_work=item["observed_work"],
            requested_need=item["support_need"],
            basis_status=assessment.get("basis_status"),
            trace_coverage=data["trace_coverage"],
            support_opportunity=data["support_opportunity"],
            consequence=_risk(data["consequence"], "consequence"),
            reversibility=_risk(data["reversibility"], "reversibility"),
            authorization_risk=_risk(
                data["authorization_risk"], "authorization_risk"
            ),
            repeated_unresolved=bool(data.get("repeated_unresolved", False)),
        )
    referenced = {
        evidence_id
        for item in normalized_profile.values()
        for evidence_id in item["evidence_ids"]
    }
    evidence_ids: set[str] = set()
    normalized_evidence = _validate_evidence(data["evidence"], evidence_ids)
    if not referenced.issubset(evidence_ids):
        missing_ids = sorted(referenced - evidence_ids)
        raise ValidationError(f"v0.3 support_profile references unknown evidence: {missing_ids}")
    for dimension, item in normalized_profile.items():
        if item["observed_work"] == "NONE" and item["support_need"] != "NONE":
            raise ValidationError(
                f"v0.3 {dimension} cannot have support_need without observed_work"
            )
    result = {
        "schema_version": "retrace-state-v3",
        "decision_id": _string(data["decision_id"], "decision_id"),
        "process_state": process_state,
        "support_opportunity": support_opportunity,
        "support_profile": normalized_profile,
        "support_need_audit": support_need_audit,
        "value_provenance": data.get("value_provenance", "DESIGN_PRIOR"),
        "calibration_status": data.get("calibration_status", "PROVISIONAL"),
        "trace_coverage": data["trace_coverage"],
        "uncertainties": list(data["uncertainties"]),
        "consequence": _risk(data["consequence"], "consequence"),
        "reversibility": _risk(data["reversibility"], "reversibility"),
        "authorization_risk": _risk(data["authorization_risk"], "authorization_risk"),
        "evidence_quality": _unit_float(data["evidence_quality"], "evidence_quality"),
        "workflow_continuity": _unit_float(
            data["workflow_continuity"], "workflow_continuity"
        ),
        "evidence": normalized_evidence,
        "recent_interventions": data["recent_interventions"],
        "active_verification": data["active_verification"],
        "basis_relevant_signal": data.get("basis_relevant_signal"),
        "delegation_failure_signal": data.get("delegation_failure_signal"),
        "repeated_unresolved": data.get("repeated_unresolved"),
        "target_key": data.get("target_key"),
        "delegation_attempt_count": data.get("delegation_attempt_count"),
        "last_confirmed_progress": data.get("last_confirmed_progress"),
        "failure_window": data.get("failure_window"),
        "cooldown_until": data.get("cooldown_until"),
        "recent_intervention_ids": data.get("recent_intervention_ids"),
    }
    if extraction_packet is not None:
        result["behavior_evidence"] = extraction_packet["behavior_evidence"]
        result["basis_assessment"] = extraction_packet["basis_assessment"]
    return result


def adapt_v03_state(raw: Mapping[str, Any]) -> tuple[DecisionState, dict[str, Any]]:
    """Validate v0.3 input and create the selector state directly."""

    data = validate_v03_state(raw)
    needs = {
        dimension: SUPPORT_LEVELS[item["support_need"]]
        for dimension, item in data["support_profile"].items()
    }
    dimensions_by_evidence: dict[str, set[str]] = {}
    for dimension, item in data["support_profile"].items():
        for evidence_id in item["evidence_ids"]:
            dimensions_by_evidence.setdefault(evidence_id, set()).add(dimension)
    selector_evidence = []
    for item in data["evidence"]:
        supports_dimensions = sorted(
            set(item["supports_dimensions"])
            or dimensions_by_evidence.get(item["evidence_id"], set())
        )
        selector_evidence.append(
            {
                "evidence_id": item["evidence_id"],
                "source": item["source"],
                "locator": item["locator"],
                "observed_at": item["observed_at"],
                "sequence_index": item["sequence_index"],
                "content_sha256": item["content_sha256"],
                "supports_dimensions": supports_dimensions,
                "supports_primitives": item["supports_primitives"],
                "available_at_decision": item["available_at_decision"],
            }
        )
    if not data["evidence"]:
        # A valid no-support counterfactual may contain no evidence bound to a
        # candidate. Do not force partial/sufficient completeness merely from
        # the episode-level coverage label.
        completeness = "none"
    elif data["trace_coverage"] == "INADEQUATE":
        completeness = "none"
    elif data["trace_coverage"] == "PARTIAL":
        completeness = "partial"
    elif data["evidence_quality"] < 0.5:
        completeness = "partial"
    else:
        completeness = "sufficient"
    confidence_values = [
        CONFIDENCE[item["confidence"]]
        for item in data["support_profile"].values()
        if item["observed_work"] != "NONE"
    ]
    state_confidence = min(confidence_values or [0.5])
    if data["trace_coverage"] == "INADEQUATE":
        state_confidence = min(state_confidence, 0.5)
    selector_state = {
        "schema_version": "retrace-state-v2",
        "decision_id": data["decision_id"],
        "process_state": PROCESS_STATE_MAP[data["process_state"]],
        "support_opportunity": data["support_opportunity"],
        "support_needs": needs,
        "support_profile": {
            dimension: {
                "observed_work": item["observed_work"],
                "support_need": item["support_need"],
                "confidence": item["confidence"],
            }
            for dimension, item in data["support_profile"].items()
        },
        "evidence": selector_evidence,
        "consequence": data["consequence"],
        "reversibility": data["reversibility"],
        "authorization_risk": data["authorization_risk"],
        "evidence_completeness": completeness,
        "state_confidence": state_confidence,
        "recent_interventions": data["recent_interventions"],
        "active_verification": data["active_verification"],
        "basis_relevant_signal": data.get("basis_relevant_signal"),
        "delegation_failure_signal": data.get("delegation_failure_signal"),
        "repeated_unresolved": data.get("repeated_unresolved"),
        "target_key": data.get("target_key"),
        "delegation_attempt_count": data.get("delegation_attempt_count"),
        "last_confirmed_progress": data.get("last_confirmed_progress"),
        "failure_window": data.get("failure_window"),
        "cooldown_until": data.get("cooldown_until"),
        "recent_intervention_ids": data.get("recent_intervention_ids"),
    }
    return DecisionState.from_dict(selector_state), {
        "schema_version": data["schema_version"],
        "trace_coverage": data["trace_coverage"],
        "evidence_quality": data["evidence_quality"],
        "workflow_continuity": data["workflow_continuity"],
        "uncertainties": data["uncertainties"],
        "support_profile": data["support_profile"],
        "support_need_audit": data["support_need_audit"],
        "value_provenance": data["value_provenance"],
        "calibration_status": data["calibration_status"],
        "behavior_evidence": data.get("behavior_evidence"),
        "basis_assessment": data.get("basis_assessment"),
        "support_opportunity": data["support_opportunity"],
        "basis_relevant_signal": data.get("basis_relevant_signal"),
        "delegation_failure_signal": data.get("delegation_failure_signal"),
        "repeated_unresolved": data.get("repeated_unresolved"),
    }


def select_v03(raw: Mapping[str, Any], engine: SelectionEngine) -> dict[str, Any]:
    """Select with the v0.3 contract and return an auditable wrapped result."""

    validated = validate_v03_state(raw)
    state, adapter_meta = adapt_v03_state(validated)
    result = engine.select(state).to_dict()
    result["metadata"]["state"] = validated
    for candidate in result.get("generated_candidates", []):
        score = candidate.get("score")
        if score is not None:
            candidate["score"] = {
                "criteria_basis_reconstruction": score["criteria_basis_reconstruction"],
                "project_state_reconstruction": score["project_state_reconstruction"],
                "evidence_action_governance": score["evidence_action_governance"],
                "evidence_quality": score["evidence_quality"],
                "workflow_continuity": score["workflow_continuity"],
            }
    result["v03_input"] = adapter_meta
    result["v03_input"]["decision_id"] = validated["decision_id"]
    return result
