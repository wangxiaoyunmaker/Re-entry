from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
import hashlib
import json
import math
from typing import Any, Mapping


class ValidationError(ValueError):
    """Raised when an input or frozen configuration is invalid."""


class ProcessState(str, Enum):
    DELEGATION_PROGRESSING = "DELEGATION_PROGRESSING"
    EARLY_SUPPORT_OPPORTUNITY = "EARLY_SUPPORT_OPPORTUNITY"
    REENTRY_OCCASION_OBSERVED = "REENTRY_OCCASION_OBSERVED"
    GOVERNANCE_RECOVERING = "GOVERNANCE_RECOVERING"


class Primitive(str, Enum):
    RULE_ALIGNMENT = "RULE_ALIGNMENT"
    PROVENANCE = "PROVENANCE"
    CAUSAL_EXPLANATION = "CAUSAL_EXPLANATION"
    VERIFICATION = "VERIFICATION"
    DISPOSITION_COORDINATION = "DISPOSITION_COORDINATION"


class Level(IntEnum):
    L1 = 1
    L2 = 2
    L3 = 3

    @classmethod
    def from_value(cls, value: str | int) -> "Level":
        if isinstance(value, str):
            try:
                return cls[value]
            except KeyError as exc:
                raise ValidationError(f"unknown level: {value}") from exc
        try:
            return cls(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"unknown level: {value}") from exc


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceCompleteness(str, Enum):
    NONE = "none"
    PARTIAL = "partial"
    SUFFICIENT = "sufficient"

    @property
    def rank(self) -> int:
        return {
            EvidenceCompleteness.NONE: 0,
            EvidenceCompleteness.PARTIAL: 1,
            EvidenceCompleteness.SUFFICIENT: 2,
        }[self]

    @property
    def score(self) -> float:
        return {
            EvidenceCompleteness.NONE: 0.0,
            EvidenceCompleteness.PARTIAL: 0.5,
            EvidenceCompleteness.SUFFICIENT: 1.0,
        }[self]


class EvidenceSource(str, Enum):
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    DESIGN_ASSUMPTION = "DESIGN_ASSUMPTION"


class Outcome(str, Enum):
    INTERVENE = "INTERVENE"
    NO_INTERVENTION = "NO_INTERVENTION"
    PRESENT_CHOICES = "PRESENT_CHOICES"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    SAFE_HOLD = "SAFE_HOLD"


CRITERIA = ("O", "S", "D", "E", "W")
NEEDS = ("O", "S", "D")


def _require_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field_name} must be an object")
    return value


def _check_keys(
    data: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    context: str,
) -> None:
    optional = optional or set()
    missing = required - set(data)
    unknown = set(data) - required - optional
    if missing:
        raise ValidationError(f"{context} missing fields: {sorted(missing)}")
    if unknown:
        raise ValidationError(f"{context} unknown fields: {sorted(unknown)}")


def _finite_unit_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValidationError(f"{field_name} must be finite and within [0, 1]")
    return result


def _nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"{field_name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    source: EvidenceSource
    locator: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvidenceRef":
        data = _require_mapping(raw, "evidence item")
        _check_keys(
            data,
            required={"evidence_id", "source"},
            optional={"locator"},
            context="evidence item",
        )
        evidence_id = data["evidence_id"]
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValidationError("evidence_id must be a non-empty string")
        locator = data.get("locator")
        if locator is not None and (not isinstance(locator, str) or not locator.strip()):
            raise ValidationError("evidence locator must be a non-empty string when set")
        try:
            source = EvidenceSource(data["source"])
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"unknown evidence source: {data['source']}") from exc
        return cls(evidence_id=evidence_id.strip(), source=source, locator=locator)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "evidence_id": self.evidence_id,
            "source": self.source.value,
        }
        if self.locator is not None:
            result["locator"] = self.locator
        return result


@dataclass(frozen=True)
class GovernanceNeeds:
    O: int
    S: int
    D: int

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GovernanceNeeds":
        data = _require_mapping(raw, "governance_needs")
        _check_keys(data, required=set(NEEDS), context="governance_needs")
        values: dict[str, int] = {}
        for key in NEEDS:
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
                raise ValidationError(f"governance_needs.{key} must be an integer in [0, 3]")
            values[key] = value
        return cls(**values)

    def normalized(self, key: str) -> float:
        return getattr(self, key) / 3.0

    def to_dict(self) -> dict[str, int]:
        return {key: getattr(self, key) for key in NEEDS}


@dataclass(frozen=True)
class DecisionState:
    schema_version: str
    decision_id: str
    process_state: ProcessState
    governance_needs: GovernanceNeeds
    evidence: tuple[EvidenceRef, ...]
    consequence: Risk
    reversibility: Risk
    authorization_risk: Risk
    evidence_completeness: EvidenceCompleteness
    state_confidence: float
    recent_interventions: int
    active_verification: bool

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DecisionState":
        data = _require_mapping(raw, "state")
        required = {
            "schema_version",
            "decision_id",
            "process_state",
            "governance_needs",
            "evidence",
            "consequence",
            "reversibility",
            "authorization_risk",
            "evidence_completeness",
            "state_confidence",
            "recent_interventions",
            "active_verification",
        }
        _check_keys(data, required=required, context="state")
        if data["schema_version"] != "retrace-state-v1":
            raise ValidationError(f"unsupported state schema: {data['schema_version']}")
        decision_id = data["decision_id"]
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ValidationError("decision_id must be a non-empty string")
        try:
            process_state = ProcessState(data["process_state"])
            consequence = Risk(data["consequence"])
            reversibility = Risk(data["reversibility"])
            authorization_risk = Risk(data["authorization_risk"])
            evidence_completeness = EvidenceCompleteness(data["evidence_completeness"])
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"invalid state enum: {exc}") from exc
        raw_evidence = data["evidence"]
        if not isinstance(raw_evidence, list):
            raise ValidationError("evidence must be an array")
        evidence = tuple(EvidenceRef.from_dict(item) for item in raw_evidence)
        evidence_ids = [item.evidence_id for item in evidence]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValidationError("evidence IDs must be unique")
        empirical_evidence = tuple(
            item
            for item in evidence
            if item.source is not EvidenceSource.DESIGN_ASSUMPTION
        )
        if (
            evidence_completeness is not EvidenceCompleteness.NONE
            and not empirical_evidence
        ):
            raise ValidationError(
                "partial or sufficient completeness requires OBSERVED or INFERRED evidence"
            )
        active_verification = data["active_verification"]
        if not isinstance(active_verification, bool):
            raise ValidationError("active_verification must be boolean")
        return cls(
            schema_version=data["schema_version"],
            decision_id=decision_id.strip(),
            process_state=process_state,
            governance_needs=GovernanceNeeds.from_dict(data["governance_needs"]),
            evidence=evidence,
            consequence=consequence,
            reversibility=reversibility,
            authorization_risk=authorization_risk,
            evidence_completeness=evidence_completeness,
            state_confidence=_finite_unit_float(data["state_confidence"], "state_confidence"),
            recent_interventions=_nonnegative_int(
                data["recent_interventions"], "recent_interventions"
            ),
            active_verification=active_verification,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "process_state": self.process_state.value,
            "governance_needs": self.governance_needs.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "consequence": self.consequence.value,
            "reversibility": self.reversibility.value,
            "authorization_risk": self.authorization_risk.value,
            "evidence_completeness": self.evidence_completeness.value,
            "state_confidence": self.state_confidence,
            "recent_interventions": self.recent_interventions,
            "active_verification": self.active_verification,
        }


@dataclass(frozen=True)
class PrimitiveProfile:
    primary_need: str
    capabilities: Mapping[str, float]
    burden: Mapping[Level, float]
    minimum_evidence: Mapping[Level, EvidenceCompleteness]


@dataclass(frozen=True)
class Thresholds:
    low_confidence: float
    gain: float
    near_tie: float
    dominance_epsilon: float
    max_burden: float
    cooldown_count: int
    cooldown_penalty_per_intervention: float


@dataclass(frozen=True)
class PolicySpec:
    schema_version: str
    policy_version: str
    engine_version: str
    thresholds: Thresholds
    weights: Mapping[str, float]
    allowed_levels: Mapping[ProcessState, tuple[Level, ...]]
    level_multipliers: Mapping[Level, float]
    primitive_profiles: Mapping[Primitive, PrimitiveProfile]
    config_hash: str


@dataclass(frozen=True)
class TemplateEntry:
    title: str
    message: str
    next_step: str


@dataclass(frozen=True)
class TemplateCatalog:
    schema_version: str
    template_version: str
    templates: Mapping[Primitive, Mapping[Level, TemplateEntry]]
    config_hash: str


@dataclass(frozen=True)
class DecisionBrief:
    brief_id: str
    primitive: Primitive | None = None
    level: Level | None = None
    is_no_intervention: bool = False

    @classmethod
    def no_intervention(cls) -> "DecisionBrief":
        return cls(brief_id="NO_INTERVENTION", is_no_intervention=True)

    @classmethod
    def intervention(cls, primitive: Primitive, level: Level) -> "DecisionBrief":
        return cls(
            brief_id=f"{primitive.value}-{level.name}",
            primitive=primitive,
            level=level,
            is_no_intervention=False,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief_id": self.brief_id,
            "primitive": self.primitive.value if self.primitive else None,
            "level": self.level.name if self.level else None,
            "is_no_intervention": self.is_no_intervention,
        }


@dataclass(frozen=True)
class ScoreVector:
    O: float
    S: float
    D: float
    E: float
    W: float

    def __post_init__(self) -> None:
        for key in CRITERIA:
            value = getattr(self, key)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValidationError(f"score {key} must be finite and within [0, 1]")

    def vector(self) -> tuple[float, ...]:
        return tuple(getattr(self, key) for key in CRITERIA)

    def to_dict(self) -> dict[str, float]:
        return {key: getattr(self, key) for key in CRITERIA}


@dataclass(frozen=True)
class ConstraintRecord:
    rule_id: str
    allowed: bool
    reason: str
    priority: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "allowed": self.allowed,
            "reason": self.reason,
            "priority": self.priority,
        }


@dataclass
class CandidateEvaluation:
    brief: DecisionBrief
    constraints: tuple[ConstraintRecord, ...] = ()
    score: ScoreVector | None = None
    utility: float | None = None
    gain_vs_no_intervention: float | None = None

    @property
    def allowed(self) -> bool:
        return all(record.allowed for record in self.constraints)

    @property
    def rejection_reasons(self) -> list[str]:
        return [record.rule_id for record in self.constraints if not record.allowed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief": self.brief.to_dict(),
            "allowed": self.allowed,
            "constraints": [record.to_dict() for record in self.constraints],
            "score": self.score.to_dict() if self.score else None,
            "utility": self.utility,
            "gain_vs_no_intervention": self.gain_vs_no_intervention,
        }


@dataclass(frozen=True)
class RenderedBrief:
    brief_id: str
    title: str
    message: str
    evidence_ids: tuple[str, ...]
    next_step: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief_id": self.brief_id,
            "title": self.title,
            "message": self.message,
            "evidence_ids": list(self.evidence_ids),
            "next_step": self.next_step,
        }


@dataclass(frozen=True)
class SelectionResult:
    audit_id: str
    outcome: Outcome
    reason_codes: tuple[str, ...]
    selected_ids: tuple[str, ...]
    generated: tuple[CandidateEvaluation, ...]
    feasible_ids: tuple[str, ...]
    skyline_ids: tuple[str, ...]
    dominance_witnesses: Mapping[str, tuple[str, ...]]
    frontier_ratio: float | None
    warnings: tuple[str, ...]
    rendered_briefs: tuple[RenderedBrief, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    decision_digest: str = field(init=False)

    def __post_init__(self) -> None:
        payload = self._payload_dict()
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        object.__setattr__(
            self,
            "decision_digest",
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def _payload_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "outcome": self.outcome.value,
            "reason_codes": list(self.reason_codes),
            "selected_ids": list(self.selected_ids),
            "generated_candidates": [item.to_dict() for item in self.generated],
            "feasible_ids": list(self.feasible_ids),
            "skyline_ids": list(self.skyline_ids),
            "dominance_witnesses": {
                key: list(value) for key, value in self.dominance_witnesses.items()
            },
            "frontier_ratio": self.frontier_ratio,
            "warnings": list(self.warnings),
            "rendered_briefs": [item.to_dict() for item in self.rendered_briefs],
            "metadata": dict(self.metadata),
        }

    def to_dict(self) -> dict[str, Any]:
        self.validate_integrity()
        result = self._payload_dict()
        result["decision_digest"] = self.decision_digest
        return result

    def validate_integrity(self) -> None:
        canonical = json.dumps(
            self._payload_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if actual != self.decision_digest:
            raise ValidationError("selection result changed after decision sealing")
