"""Data-anchored C/S/A coverage handling for the v0.7 Context Bridge.

The runtime selector still consumes ``SupportNeeds``.  This module keeps the
upstream distinction between an observed coverage level and an unknown level,
then derives the remaining support need against a frozen target profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .models import SupportNeeds, ValidationError


DIMENSIONS = ("criteria", "state", "action")
_NEED_COMPATIBILITY = {0: 0.0, 1: 0.35, 2: 0.70, 3: 1.0}


class Assessability(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    LIMITED = "LIMITED"
    ABSTAIN = "ABSTAIN"


class CoverageAbstained(ValidationError):
    """Raised when at least one required dimension cannot be assessed safely."""


@dataclass(frozen=True)
class DimensionCoverage:
    level: int | None
    assessability: Assessability
    evidence_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, dimension: str) -> "DimensionCoverage":
        if not isinstance(raw, Mapping):
            raise ValidationError(f"{dimension} coverage must be an object")
        expected = {"level", "assessability", "evidence_ids"}
        missing = expected - set(raw)
        unknown = set(raw) - expected
        if missing:
            raise ValidationError(f"{dimension} coverage missing fields: {sorted(missing)}")
        if unknown:
            raise ValidationError(f"{dimension} coverage unknown fields: {sorted(unknown)}")

        raw_level = raw["level"]
        if raw_level == "UNKNOWN":
            level = None
        elif isinstance(raw_level, bool) or not isinstance(raw_level, int) or not 0 <= raw_level <= 3:
            raise ValidationError(f"{dimension}.level must be UNKNOWN or an integer within 0..3")
        else:
            level = raw_level

        try:
            assessability = Assessability(str(raw["assessability"]).upper())
        except ValueError as exc:
            raise ValidationError(f"invalid {dimension}.assessability") from exc

        evidence_raw = raw["evidence_ids"]
        if not isinstance(evidence_raw, list) or any(
            not isinstance(item, str) or not item.strip() for item in evidence_raw
        ):
            raise ValidationError(f"{dimension}.evidence_ids must be an array of non-empty strings")
        evidence_ids = tuple(item.strip() for item in evidence_raw)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValidationError(f"{dimension}.evidence_ids must be unique")

        if assessability is Assessability.ABSTAIN:
            raise CoverageAbstained(f"{dimension} coverage abstained")
        if level is None and assessability is not Assessability.LIMITED:
            raise ValidationError(
                f"{dimension}.level UNKNOWN requires assessability=LIMITED"
            )
        if level == 0 and (
            assessability is not Assessability.SUFFICIENT or not evidence_ids
        ):
            raise ValidationError(
                f"{dimension}.level 0 requires SUFFICIENT assessability and direct evidence"
            )
        return cls(
            level=level,
            assessability=assessability,
            evidence_ids=evidence_ids,
        )


@dataclass(frozen=True)
class CoverageDerivation:
    target_profile: Mapping[str, int]
    current_profile: Mapping[str, DimensionCoverage]
    support_needs: SupportNeeds
    unknown_dimensions: tuple[str, ...]
    limited_dimensions: tuple[str, ...]
    confidence_cap: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_profile": dict(self.target_profile),
            "current_profile": {
                dimension: {
                    "level": item.level if item.level is not None else "UNKNOWN",
                    "assessability": item.assessability.value,
                    "evidence_ids": list(item.evidence_ids),
                }
                for dimension, item in self.current_profile.items()
            },
            "support_needs": self.support_needs.to_dict(),
            "unknown_dimensions": list(self.unknown_dimensions),
            "limited_dimensions": list(self.limited_dimensions),
            "confidence_cap": self.confidence_cap,
        }


def _target_profile(raw: Mapping[str, Any]) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        raise ValidationError("target_profile must be an object")
    missing = set(DIMENSIONS) - set(raw)
    unknown = set(raw) - set(DIMENSIONS)
    if missing:
        raise ValidationError(f"target_profile missing fields: {sorted(missing)}")
    if unknown:
        raise ValidationError(f"target_profile unknown fields: {sorted(unknown)}")
    result: dict[str, int] = {}
    for dimension in DIMENSIONS:
        value = raw[dimension]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
            raise ValidationError(f"target_profile.{dimension} must be an integer within 0..3")
        result[dimension] = value
    return result


def derive_support_needs(
    target_profile: Mapping[str, Any],
    current_profile: Mapping[str, Any],
) -> CoverageDerivation:
    """Derive v0.6 support needs without conflating UNKNOWN with observed zero.

    UNKNOWN receives the full unresolved target gap for selection, but is also
    recorded and caps selector confidence at 0.5.  This permits only lightweight
    support in ordinary safe states; high-risk states continue to fail closed via
    the selector's existing confidence/intensity constraints.
    """

    target = _target_profile(target_profile)
    if not isinstance(current_profile, Mapping):
        raise ValidationError("current_profile must be an object")
    missing = set(DIMENSIONS) - set(current_profile)
    unknown = set(current_profile) - set(DIMENSIONS)
    if missing:
        raise ValidationError(f"current_profile missing fields: {sorted(missing)}")
    if unknown:
        raise ValidationError(f"current_profile unknown fields: {sorted(unknown)}")

    current = {
        dimension: DimensionCoverage.from_dict(
            current_profile[dimension], dimension=dimension
        )
        for dimension in DIMENSIONS
    }
    unknown_dimensions = tuple(
        dimension for dimension, item in current.items() if item.level is None
    )
    limited_dimensions = tuple(
        dimension
        for dimension, item in current.items()
        if item.assessability is Assessability.LIMITED
    )
    needs = {
        dimension: max(0, target[dimension] - (current[dimension].level or 0))
        for dimension in DIMENSIONS
    }
    confidence_cap = 0.5 if limited_dimensions else 0.9
    return CoverageDerivation(
        target_profile=target,
        current_profile=current,
        support_needs=SupportNeeds(
            criteria_basis_reconstruction=needs["criteria"],
            project_state_reconstruction=needs["state"],
            evidence_action_governance=needs["action"],
        ),
        unknown_dimensions=unknown_dimensions,
        limited_dimensions=limited_dimensions,
        confidence_cap=confidence_cap,
    )


def condition_candidate_capability(
    capability: tuple[float, float, float],
    support_needs: SupportNeeds,
    *,
    authorization_capable: bool = False,
    authorization_required: bool = False,
) -> tuple[float, float, float]:
    """Apply the original Skyline state-compatibility layer to base capability.

    The returned values are support coverage in the current state, not effect
    probabilities.  A zero-need dimension is kept at zero instead of making an
    otherwise irrelevant strategy Pareto-nondominated.
    """

    needs = (
        support_needs.criteria_basis_reconstruction,
        support_needs.project_state_reconstruction,
        support_needs.evidence_action_governance,
    )
    compatibility = [_NEED_COMPATIBILITY[value] for value in needs]
    if authorization_required and authorization_capable:
        compatibility[2] = 1.0
    return tuple(
        round(base * rho, 12)
        for base, rho in zip(capability, compatibility)
    )
