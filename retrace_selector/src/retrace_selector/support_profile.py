"""Evidence-first Support Profile extraction contract.

This module deliberately separates three operations that must not be collapsed
into keyword matching:

1. event-level user behavior evidence;
2. assessment of whether a basis was formed and used;
3. aggregation into the selector-facing support profile.

The module does not infer a mechanism dimension from a keyword. A dimension
can enter the selector-facing profile only through an explicit, evidence-linked
basis assessment.
"""

from __future__ import annotations

from typing import Any, Mapping

from .models import ValidationError


DIMENSIONS = (
    "criteria_basis_reconstruction",
    "project_state_reconstruction",
    "evidence_action_governance",
)

BASIS_STATUS = {"NOT_OBSERVED", "POSSIBLE", "FORMED", "USED"}
SUPPORT_NEED = {"NONE", "LOW", "MEDIUM", "HIGH"}
CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}
ACTORS = {"USER", "AGENT", "TOOL", "PROJECT_MATERIAL", "UNKNOWN"}
TEMPORAL_POSITION = {"BEFORE_OR_AT_TRIGGER", "AFTER_TRIGGER", "UNKNOWN"}
EVIDENCE_SOURCE = {"OBSERVED", "INFERRED", "DESIGN_ASSUMPTION"}
ACTION_FOCUS = {"NONE", "VERIFICATION", "DISPOSITION", "BOTH", "UNCLEAR"}
ACTION_PRIMITIVES = {"VERIFICATION", "DISPOSITION_COORDINATION"}


def recommend_support_need(
    *,
    observed_work: str,
    requested_need: str,
    basis_status: str | None,
    trace_coverage: str,
    support_opportunity: str,
    consequence: str,
    reversibility: str,
    authorization_risk: str,
    repeated_unresolved: bool = False,
) -> dict[str, Any]:
    """Apply the pilot-derived support-need rubric without rewriting labels.

    This is an audit rule, not a hidden classifier. It reports the level the
    current evidence can justify, so a reviewer can inspect under- or
    over-support decisions before changing policy weights.
    """

    if observed_work == "NONE":
        recommended = "NONE"
    elif support_opportunity == "ABSTAIN" or trace_coverage == "INADEQUATE":
        recommended = "MEDIUM"
    elif (
        authorization_risk == "high"
        or (consequence == "high" and reversibility == "low")
        or (repeated_unresolved and basis_status != "USED")
    ):
        recommended = "HIGH"
    elif basis_status in {"POSSIBLE", "FORMED"} or trace_coverage == "PARTIAL":
        recommended = "MEDIUM"
    else:
        recommended = "LOW"

    rank = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    gate = "ABSTAIN" if support_opportunity == "ABSTAIN" else "SELECT"
    return {
        "requested": requested_need,
        "recommended": recommended,
        "gate": gate,
        "consistent": rank[requested_need] == rank[recommended],
        "under_supported": rank[requested_need] < rank[recommended],
        "over_supported": rank[requested_need] > rank[recommended],
        "rationale": (
            "risk, repeated unresolved behavior, trace coverage, and basis status "
            "were checked in that order; this audit does not alter the requested label"
        ),
    }

REQUIRED_BEHAVIOR_FIELDS = {
    "evidence_id",
    "actor",
    "text_span",
    "dialogue_act",
    "task_intent",
    "target_object",
    "input_type",
    "validation_strategy",
    "temporal_position",
    "source",
}
OPTIONAL_BEHAVIOR_FIELDS = {"behavior_change_from_prior"}
OPTIONAL_BEHAVIOR_FIELDS.add("behavior_change_basis")
OPTIONAL_BEHAVIOR_FIELDS.update(
    {
        "action_focus",
        "supports_primitives",
        "action_focus_rationale",
        "basis_relevant_signal",
    }
)

REQUIRED_ASSESSMENT_FIELDS = {
    "basis_status",
    "formation_evidence_ids",
    "use_evidence_ids",
    "support_need",
    "need_evidence_ids",
    "confidence",
    "rationale",
    "need_rationale",
}


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{name} must be an object")
    return value


def _non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, name: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValidationError(f"{name} must be an array of non-empty strings")
    if not allow_empty and not value:
        raise ValidationError(f"{name} must not be empty")
    return list(dict.fromkeys(item.strip() for item in value))


def _require_exact_keys(data: Mapping[str, Any], required: set[str], name: str) -> None:
    missing = required - set(data)
    unknown = set(data) - required
    if missing or unknown:
        raise ValidationError(
            f"{name} keys invalid; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )


def validate_behavior_evidence(raw: Any) -> dict[str, Any]:
    """Validate one event-level annotation from the frozen behavior codebook."""

    data = _mapping(raw, "behavior_evidence")
    missing = REQUIRED_BEHAVIOR_FIELDS - set(data)
    unknown = set(data) - REQUIRED_BEHAVIOR_FIELDS - OPTIONAL_BEHAVIOR_FIELDS
    if missing or unknown:
        raise ValidationError(
            f"behavior_evidence keys invalid; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    actor = _non_empty_string(data["actor"], "behavior_evidence.actor")
    if actor not in ACTORS:
        raise ValidationError("behavior_evidence.actor is invalid")
    temporal = _non_empty_string(
        data["temporal_position"], "behavior_evidence.temporal_position"
    )
    if temporal not in TEMPORAL_POSITION:
        raise ValidationError("behavior_evidence.temporal_position is invalid")
    source = _non_empty_string(data["source"], "behavior_evidence.source")
    if source not in EVIDENCE_SOURCE:
        raise ValidationError("behavior_evidence.source is invalid")
    behavior_change = data.get("behavior_change_from_prior", "UNCLEAR")
    if behavior_change not in {"CHANGED", "REPEATED", "NOT_APPLICABLE", "UNCLEAR"}:
        raise ValidationError("behavior_evidence.behavior_change_from_prior is invalid")
    change_basis = data.get("behavior_change_basis", "")
    if change_basis and (not isinstance(change_basis, str) or not change_basis.strip()):
        raise ValidationError("behavior_evidence.behavior_change_basis must be non-empty when set")
    if behavior_change == "CHANGED" and not change_basis.strip():
        raise ValidationError(
            "behavior_evidence CHANGED requires behavior_change_basis"
        )
    action_focus = data.get("action_focus", "UNCLEAR")
    if action_focus not in ACTION_FOCUS:
        raise ValidationError("behavior_evidence.action_focus is invalid")
    raw_primitives = data.get("supports_primitives", [])
    primitives = _string_list(
        raw_primitives, "behavior_evidence.supports_primitives"
    )
    if any(item not in ACTION_PRIMITIVES for item in primitives):
        raise ValidationError(
            "behavior_evidence.supports_primitives may only contain "
            "VERIFICATION or DISPOSITION_COORDINATION"
        )
    if action_focus == "VERIFICATION" and set(primitives) != {"VERIFICATION"}:
        raise ValidationError(
            "VERIFICATION action_focus must bind VERIFICATION"
        )
    if action_focus == "DISPOSITION" and set(primitives) != {"DISPOSITION_COORDINATION"}:
        raise ValidationError(
            "DISPOSITION action_focus must bind DISPOSITION_COORDINATION"
        )
    if action_focus == "BOTH" and set(primitives) != ACTION_PRIMITIVES:
        raise ValidationError(
            "BOTH action_focus must bind both action primitives"
        )
    rationale = data.get("action_focus_rationale", "")
    if rationale and (not isinstance(rationale, str) or not rationale.strip()):
        raise ValidationError(
            "behavior_evidence.action_focus_rationale must be non-empty when set"
        )
    basis_relevant_signal = data.get("basis_relevant_signal", False)
    if not isinstance(basis_relevant_signal, bool):
        raise ValidationError(
            "behavior_evidence.basis_relevant_signal must be boolean when set"
        )
    return {
        "evidence_id": _non_empty_string(data["evidence_id"], "behavior_evidence.evidence_id"),
        "actor": actor,
        "text_span": _non_empty_string(data["text_span"], "behavior_evidence.text_span"),
        "dialogue_act": _string_list(data["dialogue_act"], "behavior_evidence.dialogue_act", allow_empty=False),
        "task_intent": _string_list(data["task_intent"], "behavior_evidence.task_intent", allow_empty=False),
        "target_object": _string_list(data["target_object"], "behavior_evidence.target_object", allow_empty=False),
        "input_type": _string_list(data["input_type"], "behavior_evidence.input_type", allow_empty=False),
        "validation_strategy": _string_list(data["validation_strategy"], "behavior_evidence.validation_strategy", allow_empty=False),
        "temporal_position": temporal,
        "source": source,
        "behavior_change_from_prior": behavior_change,
        "behavior_change_basis": change_basis.strip(),
        "action_focus": action_focus,
        "supports_primitives": primitives,
        "action_focus_rationale": rationale.strip(),
        "basis_relevant_signal": basis_relevant_signal,
    }


def validate_behavior_evidence_list(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValidationError("behavior_evidence must be an array")
    normalized = [validate_behavior_evidence(item) for item in raw]
    ids = [item["evidence_id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise ValidationError("behavior_evidence contains duplicate evidence_id values")
    return normalized


def validate_basis_assessment(
    raw: Any,
    *,
    dimension: str,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate the second-stage judgment for one mechanism dimension."""

    if dimension not in DIMENSIONS:
        raise ValidationError(f"unknown basis assessment dimension: {dimension}")
    data = _mapping(raw, f"basis_assessment.{dimension}")
    _require_exact_keys(data, REQUIRED_ASSESSMENT_FIELDS, f"basis_assessment.{dimension}")
    status = _non_empty_string(data["basis_status"], f"basis_assessment.{dimension}.basis_status")
    if status not in BASIS_STATUS:
        raise ValidationError(f"basis_assessment.{dimension}.basis_status is invalid")
    need = _non_empty_string(data["support_need"], f"basis_assessment.{dimension}.support_need")
    if need not in SUPPORT_NEED:
        raise ValidationError(f"basis_assessment.{dimension}.support_need is invalid")
    confidence = _non_empty_string(data["confidence"], f"basis_assessment.{dimension}.confidence")
    if confidence not in CONFIDENCE:
        raise ValidationError(f"basis_assessment.{dimension}.confidence is invalid")
    formation = _string_list(
        data["formation_evidence_ids"],
        f"basis_assessment.{dimension}.formation_evidence_ids",
    )
    use = _string_list(
        data["use_evidence_ids"],
        f"basis_assessment.{dimension}.use_evidence_ids",
    )
    need_ids = _string_list(
        data["need_evidence_ids"],
        f"basis_assessment.{dimension}.need_evidence_ids",
    )
    all_ids = set(formation + use + need_ids)
    missing = sorted(all_ids - set(evidence_by_id))
    if missing:
        raise ValidationError(
            f"basis_assessment.{dimension} references unknown evidence: {missing}"
        )
    if status == "NOT_OBSERVED" and (formation or use):
        raise ValidationError(
            f"basis_assessment.{dimension} cannot cite formation/use evidence when NOT_OBSERVED"
        )
    if status == "FORMED" and not formation:
        raise ValidationError(
            f"basis_assessment.{dimension} FORMED requires formation_evidence_ids"
        )
    if status == "USED" and (not formation or not use):
        raise ValidationError(
            f"basis_assessment.{dimension} USED requires both formation and use evidence"
        )
    if need != "NONE" and not need_ids:
        raise ValidationError(
            f"basis_assessment.{dimension} non-NONE support_need requires need_evidence_ids"
        )
    if use and not any(evidence_by_id[item]["actor"] == "USER" for item in use):
        raise ValidationError(
            f"basis_assessment.{dimension} use evidence must include a USER event"
        )
    if status == "USED":
        unchanged = [
            item for item in use
            if evidence_by_id[item].get("behavior_change_from_prior") != "CHANGED"
        ]
        if unchanged:
            raise ValidationError(
                f"basis_assessment.{dimension} USED requires a changed later USER behavior; "
                f"repeated or unclear evidence: {unchanged}"
            )
        formation_texts = {
            " ".join(evidence_by_id[item]["text_span"].split()).casefold()
            for item in formation
        }
        identical_content = [
            item for item in use
            if " ".join(evidence_by_id[item]["text_span"].split()).casefold() in formation_texts
        ]
        if identical_content:
            raise ValidationError(
                f"basis_assessment.{dimension} USED requires content change; "
                f"identical user evidence: {identical_content}"
            )
    need_overlap = sorted(set(need_ids) & set(formation + use))
    need_rationale = _non_empty_string(
        data["need_rationale"], f"basis_assessment.{dimension}.need_rationale"
    )
    if need_overlap and not need_rationale:
        raise ValidationError(
            f"basis_assessment.{dimension} overlapping need evidence requires need_rationale"
        )
    return {
        "basis_status": status,
        "formation_evidence_ids": formation,
        "use_evidence_ids": use,
        "support_need": need,
        "need_evidence_ids": need_ids,
        "confidence": confidence,
        "rationale": _non_empty_string(data["rationale"], f"basis_assessment.{dimension}.rationale"),
        "need_rationale": need_rationale,
    }


def aggregate_support_profile(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate two-stage extraction and build the selector-facing profile.

    The returned profile is derived from explicit basis assessments, never from
    event keywords or episode-level fallback labels.
    """

    data = _mapping(raw, "support extraction packet")
    behavior = validate_behavior_evidence_list(data.get("behavior_evidence"))
    evidence_by_id = {item["evidence_id"]: item for item in behavior}
    assessments = _mapping(data.get("basis_assessment"), "basis_assessment")
    if set(assessments) != set(DIMENSIONS):
        raise ValidationError("basis_assessment must contain exactly the three dimensions")
    normalized_assessments = {
        dimension: validate_basis_assessment(
            assessments[dimension], dimension=dimension, evidence_by_id=evidence_by_id
        )
        for dimension in DIMENSIONS
    }
    signals = {
        "criteria_basis_reconstruction": "user_defined_criterion",
        "project_state_reconstruction": "user_reconstructed_project_state",
        "evidence_action_governance": "user_defined_evidence_or_action_boundary",
    }
    observed_map = {
        "NOT_OBSERVED": "NONE",
        "POSSIBLE": "POSSIBLE",
        "FORMED": "OBSERVED",
        "USED": "OBSERVED",
    }
    profile: dict[str, Any] = {}
    for dimension, assessment in normalized_assessments.items():
        ids = list(dict.fromkeys(
            assessment["formation_evidence_ids"]
            + assessment["use_evidence_ids"]
            + assessment["need_evidence_ids"]
        ))
        basis = []
        for evidence_id in ids:
            item = evidence_by_id[evidence_id]
            basis.append({
                "signal": signals[dimension],
                "actor": item["actor"],
                "temporal_position": item["temporal_position"],
                "uptake_status": (
                    "OBSERVED" if evidence_id in assessment["use_evidence_ids"]
                    else "POSSIBLE"
                ),
            })
        profile[dimension] = {
            "observed_work": observed_map[assessment["basis_status"]],
            "support_need": assessment["support_need"],
            "confidence": assessment["confidence"],
            "evidence_ids": ids,
            "evidence_basis": basis,
        }
    return {
        "behavior_evidence": behavior,
        "basis_assessment": normalized_assessments,
        "support_profile": profile,
    }
