"""External Strategy Registry and minimal v0.6 policy loaders."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .config import content_hash, load_json
from .models import ValidationError
from .v06_models import SelectionPolicy, StrategyCandidate


def _nonempty(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _exact_keys(data: Any, expected: set[str], context: str) -> None:
    if not isinstance(data, Mapping):
        raise ValidationError(f"{context} must be an object")
    missing = expected - set(data)
    unknown = set(data) - expected
    if missing:
        raise ValidationError(f"{context} missing fields: {sorted(missing)}")
    if unknown:
        raise ValidationError(f"{context} unknown fields: {sorted(unknown)}")


@dataclass(frozen=True)
class StrategyCatalogEntry:
    strategy_id: str
    title: str
    authorization_capable: bool
    verification_support: bool
    deterministic_causal_claim: bool

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "StrategyCatalogEntry":
        expected = {
            "strategy_id",
            "title",
            "authorization_capable",
            "verification_support",
            "deterministic_causal_claim",
        }
        _exact_keys(raw, expected, "Strategy Catalog entry")
        for key in (
            "authorization_capable",
            "verification_support",
            "deterministic_causal_claim",
        ):
            if not isinstance(raw[key], bool):
                raise ValidationError(f"catalog.{key} must be boolean")
        return cls(
            strategy_id=_nonempty(raw["strategy_id"], "catalog.strategy_id"),
            title=_nonempty(raw["title"], "catalog.title"),
            authorization_capable=raw["authorization_capable"],
            verification_support=raw["verification_support"],
            deterministic_causal_claim=raw["deterministic_causal_claim"],
        )


@dataclass(frozen=True)
class StrategyTemplate:
    template_id: str
    title: str
    message: str
    next_step: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "StrategyTemplate":
        _exact_keys(raw, {"template_id", "title", "message", "next_step"}, "strategy template")
        return cls(
            template_id=_nonempty(raw["template_id"], "template_id"),
            title=_nonempty(raw["title"], "template.title"),
            message=_nonempty(raw["message"], "template.message"),
            next_step=_nonempty(raw["next_step"], "template.next_step"),
        )


@dataclass(frozen=True)
class StrategyRegistry:
    registry_version: str
    registry_status: str
    capability_mode: str
    catalog: Mapping[str, StrategyCatalogEntry]
    candidates: tuple[StrategyCandidate, ...]
    templates: Mapping[str, StrategyTemplate]
    config_hash: str

    def catalog_entry(self, candidate: StrategyCandidate) -> StrategyCatalogEntry:
        return self.catalog[candidate.strategy_id]

    def template(self, candidate: StrategyCandidate) -> StrategyTemplate:
        return self.templates[candidate.template_id]


def load_strategy_registry(path: str) -> StrategyRegistry:
    raw = load_json(path)
    if not isinstance(raw, Mapping):
        raise ValidationError("strategy registry must be an object")
    required = {
        "schema_version",
        "registry_version",
        "registry_status",
        "catalog",
        "candidates",
        "templates",
    }
    allowed = required | {"capability_mode"}
    missing = required - set(raw)
    unknown = set(raw) - allowed
    if missing:
        raise ValidationError(f"strategy registry missing fields: {sorted(missing)}")
    if unknown:
        raise ValidationError(f"strategy registry unknown fields: {sorted(unknown)}")
    if raw["schema_version"] != "retrace-strategy-registry-v0.6":
        raise ValidationError("unsupported strategy registry schema")
    registry_status = _nonempty(raw["registry_status"], "registry_status")
    if registry_status not in {"TEST_ONLY", "APPROVED"}:
        raise ValidationError("registry_status must be TEST_ONLY or APPROVED")
    capability_mode = str(raw.get("capability_mode", "INTRINSIC")).upper()
    if capability_mode not in {"INTRINSIC", "STATE_CONDITIONED"}:
        raise ValidationError(
            "capability_mode must be INTRINSIC or STATE_CONDITIONED"
        )

    catalog_raw = raw["catalog"]
    candidates_raw = raw["candidates"]
    templates_raw = raw["templates"]
    if not isinstance(catalog_raw, list):
        raise ValidationError("catalog must be an array")
    if not isinstance(candidates_raw, list):
        raise ValidationError("candidates must be an array")
    if not isinstance(templates_raw, list):
        raise ValidationError("templates must be an array")

    catalog_entries = tuple(StrategyCatalogEntry.from_dict(item) for item in catalog_raw)
    catalog = {item.strategy_id: item for item in catalog_entries}
    if len(catalog) != len(catalog_entries):
        raise ValidationError("catalog strategy_id values must be unique")

    templates_entries = tuple(StrategyTemplate.from_dict(item) for item in templates_raw)
    templates = {item.template_id: item for item in templates_entries}
    if len(templates) != len(templates_entries):
        raise ValidationError("template_id values must be unique")

    candidates = tuple(StrategyCandidate.from_dict(item) for item in candidates_raw)
    candidate_ids = [item.candidate_id for item in candidates]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValidationError("strategy_id and intensity pairs must be unique")
    for candidate in candidates:
        if candidate.strategy_id not in catalog:
            raise ValidationError(
                f"candidate references unknown strategy_id: {candidate.strategy_id}"
            )
        if candidate.template_id not in templates:
            raise ValidationError(
                f"candidate references unknown template_id: {candidate.template_id}"
            )
    if registry_status == "APPROVED" and any(
        strategy_id.startswith("TEST_") for strategy_id in catalog
    ):
        raise ValidationError("APPROVED registries cannot contain TEST_ strategy ids")

    return StrategyRegistry(
        registry_version=_nonempty(raw["registry_version"], "registry_version"),
        registry_status=registry_status,
        capability_mode=capability_mode,
        catalog=MappingProxyType(catalog),
        candidates=tuple(sorted(candidates, key=lambda item: item.candidate_id)),
        templates=MappingProxyType(templates),
        config_hash=content_hash(raw),
    )


def load_selection_policy(path: str) -> tuple[SelectionPolicy, str]:
    raw = load_json(path)
    if not isinstance(raw, Mapping):
        raise ValidationError("v0.6 selection policy must be an object")
    policy = SelectionPolicy.from_dict(raw)
    return policy, content_hash(raw)
