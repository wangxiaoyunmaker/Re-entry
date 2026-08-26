from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .models import (
    SCORE_DIMENSIONS,
    SUPPORT_DIMENSIONS,
    EvidenceCompleteness,
    Level,
    PolicySpec,
    Primitive,
    PrimitiveProfile,
    ProcessState,
    TemplateCatalog,
    TemplateEntry,
    Thresholds,
    ValidationError,
    _check_keys,
    _finite_unit_float,
    _nonnegative_int,
    _require_mapping,
)
from .version import ENGINE_VERSION


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def _nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _frozen_mapping(data: Mapping[Any, Any]) -> Mapping[Any, Any]:
    return MappingProxyType(dict(data))


def _finite_signed_unit_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field_name} must be numeric")
    value = float(value)
    if not math.isfinite(value) or not -1.0 <= value <= 1.0:
        raise ValidationError(f"{field_name} must be finite and within [-1, 1]")
    return value


def load_json(path: str | Path) -> Any:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load JSON {path}: {exc}") from exc


def _level_map(raw: Mapping[str, Any], field_name: str, converter) -> dict[Level, Any]:
    data = _require_mapping(raw, field_name)
    expected = {level.name for level in Level}
    _check_keys(data, required=expected, context=field_name)
    return {Level[name]: converter(data[name], f"{field_name}.{name}") for name in expected}


def _evidence_value(value: Any, field_name: str) -> EvidenceCompleteness:
    try:
        return EvidenceCompleteness(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} has unknown evidence level: {value}") from exc


def load_policy(path: str | Path) -> PolicySpec:
    raw = load_json(path)
    data = _require_mapping(raw, "policy")
    required = {
        "schema_version",
        "policy_version",
        "engine_version",
        "thresholds",
        "weights",
        "contextual_weight_adjustment",
        "objective",
        "allowed_levels",
        "level_multipliers",
        "primitive_profiles",
    }
    _check_keys(data, required=required, context="policy")
    if data["schema_version"] != "retrace-policy-v1":
        raise ValidationError(f"unsupported policy schema: {data['schema_version']}")
    declared_engine_version = _nonempty_string(
        data["engine_version"], "engine_version"
    )
    if declared_engine_version != ENGINE_VERSION:
        raise ValidationError(
            f"policy engine_version {declared_engine_version} is incompatible with "
            f"engine {ENGINE_VERSION}"
        )

    threshold_raw = _require_mapping(data["thresholds"], "thresholds")
    threshold_keys = {
        "low_confidence",
        "gain",
        "early_support_gain_floor",
        "near_tie",
        "dominance_epsilon",
        "max_burden",
        "cooldown_count",
        "cooldown_penalty_per_intervention",
    }
    _check_keys(threshold_raw, required=threshold_keys, context="thresholds")
    epsilon = threshold_raw["dominance_epsilon"]
    if isinstance(epsilon, bool) or not isinstance(epsilon, (int, float)):
        raise ValidationError("dominance_epsilon must be numeric")
    epsilon = float(epsilon)
    if not math.isfinite(epsilon) or not 0 < epsilon <= 1:
        raise ValidationError("dominance_epsilon must be finite and within (0, 1]")
    early_support_gain_floor = threshold_raw["early_support_gain_floor"]
    if isinstance(early_support_gain_floor, bool) or not isinstance(
        early_support_gain_floor, (int, float)
    ):
        raise ValidationError("thresholds.early_support_gain_floor must be numeric")
    early_support_gain_floor = float(early_support_gain_floor)
    if not math.isfinite(early_support_gain_floor) or not -1.0 <= early_support_gain_floor <= 1.0:
        raise ValidationError(
            "thresholds.early_support_gain_floor must be finite and within [-1, 1]"
        )
    thresholds = Thresholds(
        low_confidence=_finite_unit_float(
            threshold_raw["low_confidence"], "thresholds.low_confidence"
        ),
        gain=_finite_unit_float(threshold_raw["gain"], "thresholds.gain"),
        early_support_gain_floor=early_support_gain_floor,
        near_tie=_finite_unit_float(
            threshold_raw["near_tie"], "thresholds.near_tie"
        ),
        dominance_epsilon=epsilon,
        max_burden=_finite_unit_float(
            threshold_raw["max_burden"], "thresholds.max_burden"
        ),
        cooldown_count=_nonnegative_int(
            threshold_raw["cooldown_count"], "thresholds.cooldown_count"
        ),
        cooldown_penalty_per_intervention=_finite_unit_float(
            threshold_raw["cooldown_penalty_per_intervention"],
            "thresholds.cooldown_penalty_per_intervention",
        ),
    )

    weight_raw = _require_mapping(data["weights"], "weights")
    _check_keys(weight_raw, required=set(SCORE_DIMENSIONS), context="weights")
    weights = {
        key: _finite_unit_float(weight_raw[key], f"weights.{key}")
        for key in SCORE_DIMENSIONS
    }
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
        raise ValidationError("policy weights must sum to 1")

    objective_raw = _require_mapping(data["objective"], "objective")
    _check_keys(
        objective_raw,
        required={"max_gap_penalty", "workflow_target", "evidence_target_by_risk"},
        context="objective",
    )
    max_gap_penalty = _finite_unit_float(
        objective_raw["max_gap_penalty"], "objective.max_gap_penalty"
    )
    workflow_target = _finite_unit_float(
        objective_raw["workflow_target"], "objective.workflow_target"
    )
    evidence_targets_raw = _require_mapping(
        objective_raw["evidence_target_by_risk"],
        "objective.evidence_target_by_risk",
    )
    _check_keys(
        evidence_targets_raw,
        required={"low", "medium", "high"},
        context="objective.evidence_target_by_risk",
    )
    evidence_targets = {
        risk: _finite_unit_float(
            evidence_targets_raw[risk],
            f"objective.evidence_target_by_risk.{risk}",
        )
        for risk in ("low", "medium", "high")
    }

    contextual_raw = _require_mapping(
        data["contextual_weight_adjustment"], "contextual_weight_adjustment"
    )
    _check_keys(
        contextual_raw,
        required={"max_abs_delta", "rules"},
        context="contextual_weight_adjustment",
    )
    max_abs_delta = _finite_unit_float(
        contextual_raw["max_abs_delta"],
        "contextual_weight_adjustment.max_abs_delta",
    )
    rules_raw = _require_mapping(
        contextual_raw["rules"], "contextual_weight_adjustment.rules"
    )
    contextual_rules: dict[str, Mapping[str, float]] = {}
    for rule_id, delta_raw_value in rules_raw.items():
        rule_name = _nonempty_string(rule_id, "contextual_weight_adjustment rule id")
        delta_raw = _require_mapping(
            delta_raw_value, f"contextual_weight_adjustment.rules.{rule_name}"
        )
        _check_keys(
            delta_raw,
            required=set(SCORE_DIMENSIONS),
            context=f"contextual_weight_adjustment.rules.{rule_name}",
        )
        delta = {
            key: _finite_signed_unit_float(
                delta_raw[key],
                f"contextual_weight_adjustment.rules.{rule_name}.{key}",
            )
            for key in SCORE_DIMENSIONS
        }
        if any(abs(value) > max_abs_delta for value in delta.values()):
            raise ValidationError(
                f"contextual adjustment {rule_name} exceeds max_abs_delta"
            )
        if not math.isclose(sum(delta.values()), 0.0, abs_tol=1e-9):
            raise ValidationError(
                f"contextual adjustment {rule_name} must sum to zero"
            )
        contextual_rules[rule_name] = _frozen_mapping(delta)

    allowed_raw = _require_mapping(data["allowed_levels"], "allowed_levels")
    _check_keys(
        allowed_raw,
        required={state.value for state in ProcessState},
        context="allowed_levels",
    )
    allowed_levels: dict[ProcessState, tuple[Level, ...]] = {}
    for state in ProcessState:
        raw_levels = allowed_raw[state.value]
        if not isinstance(raw_levels, list):
            raise ValidationError(f"allowed_levels.{state.value} must be an array")
        levels = tuple(Level.from_value(item) for item in raw_levels)
        if len(set(levels)) != len(levels):
            raise ValidationError(f"allowed_levels.{state.value} contains duplicates")
        allowed_levels[state] = tuple(sorted(levels))

    level_multipliers = _level_map(
        data["level_multipliers"], "level_multipliers", _finite_unit_float
    )
    if not all(
        level_multipliers[left] <= level_multipliers[right]
        for left, right in ((Level.L1, Level.L2), (Level.L2, Level.L3))
    ):
        raise ValidationError("level_multipliers must be non-decreasing from L1 to L3")
    profile_raw = _require_mapping(data["primitive_profiles"], "primitive_profiles")
    _check_keys(
        profile_raw,
        required={primitive.value for primitive in Primitive},
        context="primitive_profiles",
    )
    primitive_profiles: dict[Primitive, PrimitiveProfile] = {}
    for primitive in Primitive:
        item = _require_mapping(profile_raw[primitive.value], f"profile.{primitive.value}")
        _check_keys(
            item,
            required={
                "primary_support_dimension",
                "capabilities",
                "burden",
                "minimum_evidence",
            },
            context=f"profile.{primitive.value}",
        )
        primary_dimension = item["primary_support_dimension"]
        if primary_dimension not in SUPPORT_DIMENSIONS:
            raise ValidationError(
                f"profile.{primitive.value}.primary_support_dimension is invalid"
            )
        capabilities_raw = _require_mapping(
            item["capabilities"], f"profile.{primitive.value}.capabilities"
        )
        _check_keys(
            capabilities_raw,
            required=set(SUPPORT_DIMENSIONS),
            context=f"profile.{primitive.value}.capabilities",
        )
        capabilities = {
            key: _finite_unit_float(
                capabilities_raw[key], f"profile.{primitive.value}.capabilities.{key}"
            )
            for key in SUPPORT_DIMENSIONS
        }
        burden = _level_map(
            item["burden"], f"profile.{primitive.value}.burden", _finite_unit_float
        )
        minimum_evidence = _level_map(
            item["minimum_evidence"],
            f"profile.{primitive.value}.minimum_evidence",
            _evidence_value,
        )
        if not all(
            burden[left] <= burden[right]
            for left, right in ((Level.L1, Level.L2), (Level.L2, Level.L3))
        ):
            raise ValidationError(
                f"profile.{primitive.value}.burden must be non-decreasing"
            )
        if not all(
            minimum_evidence[left].rank <= minimum_evidence[right].rank
            for left, right in ((Level.L1, Level.L2), (Level.L2, Level.L3))
        ):
            raise ValidationError(
                f"profile.{primitive.value}.minimum_evidence must be non-decreasing"
            )
        primitive_profiles[primitive] = PrimitiveProfile(
            primary_support_dimension=primary_dimension,
            capabilities=_frozen_mapping(capabilities),
            burden=_frozen_mapping(burden),
            minimum_evidence=_frozen_mapping(minimum_evidence),
        )

    return PolicySpec(
        schema_version=data["schema_version"],
        policy_version=_nonempty_string(data["policy_version"], "policy_version"),
        engine_version=ENGINE_VERSION,
        thresholds=thresholds,
        weights=_frozen_mapping(weights),
        contextual_weight_adjustment=_frozen_mapping({
            "max_abs_delta": max_abs_delta,
            "rules": _frozen_mapping(contextual_rules),
        }),
        objective=_frozen_mapping({
            "max_gap_penalty": max_gap_penalty,
            "workflow_target": workflow_target,
            "evidence_target_by_risk": _frozen_mapping(evidence_targets),
        }),
        allowed_levels=_frozen_mapping(allowed_levels),
        level_multipliers=_frozen_mapping(level_multipliers),
        primitive_profiles=_frozen_mapping(primitive_profiles),
        config_hash=content_hash(raw),
    )


def load_templates(path: str | Path) -> TemplateCatalog:
    raw = load_json(path)
    data = _require_mapping(raw, "templates catalog")
    _check_keys(
        data,
        required={"schema_version", "template_version", "templates"},
        context="templates catalog",
    )
    if data["schema_version"] != "retrace-templates-v1":
        raise ValidationError(f"unsupported template schema: {data['schema_version']}")
    raw_templates = _require_mapping(data["templates"], "templates")
    _check_keys(
        raw_templates,
        required={primitive.value for primitive in Primitive},
        context="templates",
    )
    templates: dict[Primitive, dict[Level, TemplateEntry]] = {}
    for primitive in Primitive:
        levels_raw = _require_mapping(raw_templates[primitive.value], primitive.value)
        _check_keys(
            levels_raw,
            required={level.name for level in Level},
            context=f"templates.{primitive.value}",
        )
        entries: dict[Level, TemplateEntry] = {}
        for level in Level:
            entry_raw = _require_mapping(
                levels_raw[level.name], f"templates.{primitive.value}.{level.name}"
            )
            _check_keys(
                entry_raw,
                required={"title", "message", "next_step"},
                context=f"templates.{primitive.value}.{level.name}",
            )
            values = [entry_raw[key] for key in ("title", "message", "next_step")]
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValidationError("template values must be non-empty strings")
            entries[level] = TemplateEntry(
                title=entry_raw["title"],
                message=entry_raw["message"],
                next_step=entry_raw["next_step"],
            )
        templates[primitive] = _frozen_mapping(entries)
    return TemplateCatalog(
        schema_version=data["schema_version"],
        template_version=_nonempty_string(
            data["template_version"], "template_version"
        ),
        templates=_frozen_mapping(templates),
        config_hash=content_hash(raw),
    )
