from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .models import (
    CRITERIA,
    NEEDS,
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
    thresholds = Thresholds(
        low_confidence=_finite_unit_float(
            threshold_raw["low_confidence"], "thresholds.low_confidence"
        ),
        gain=_finite_unit_float(threshold_raw["gain"], "thresholds.gain"),
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
    _check_keys(weight_raw, required=set(CRITERIA), context="weights")
    weights = {
        key: _finite_unit_float(weight_raw[key], f"weights.{key}") for key in CRITERIA
    }
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
        raise ValidationError("policy weights must sum to 1")

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
            required={"primary_need", "capabilities", "burden", "minimum_evidence"},
            context=f"profile.{primitive.value}",
        )
        primary_need = item["primary_need"]
        if primary_need not in NEEDS:
            raise ValidationError(f"profile.{primitive.value}.primary_need is invalid")
        capabilities_raw = _require_mapping(
            item["capabilities"], f"profile.{primitive.value}.capabilities"
        )
        _check_keys(
            capabilities_raw,
            required=set(NEEDS),
            context=f"profile.{primitive.value}.capabilities",
        )
        capabilities = {
            key: _finite_unit_float(
                capabilities_raw[key], f"profile.{primitive.value}.capabilities.{key}"
            )
            for key in NEEDS
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
            primary_need=primary_need,
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
