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


class SupportOpportunity(str, Enum):
    """Runtime classification of whether support is warranted now."""

    NONE = "NONE"
    EARLY_SUPPORT = "EARLY_SUPPORT"
    REENTRY_SUPPORT = "REENTRY_SUPPORT"
    ABSTAIN = "ABSTAIN"


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


SUPPORT_DIMENSIONS = (
    "criteria_basis_reconstruction",
    "project_state_reconstruction",
    "evidence_action_governance",
)
SCORE_DIMENSIONS = (*SUPPORT_DIMENSIONS, "evidence_quality", "workflow_continuity")


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
    observed_at: str | None = None
    sequence_index: int | None = None
    content_sha256: str | None = None
    supports_dimensions: tuple[str, ...] = ()
    supports_primitives: tuple[Primitive, ...] = ()
    available_at_decision: bool | None = None

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any], *, require_binding: bool = False
    ) -> "EvidenceRef":
        data = _require_mapping(raw, "evidence item")
        _check_keys(
            data,
            required={"evidence_id", "source"},
            optional={
                "locator",
                "observed_at",
                "sequence_index",
                "content_sha256",
                "supports_dimensions",
                "supports_primitives",
                "available_at_decision",
            },
            context="evidence item",
        )
        evidence_id = data["evidence_id"]
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValidationError("evidence_id must be a non-empty string")
        locator = data.get("locator")
        if locator is not None and (not isinstance(locator, str) or not locator.strip()):
            raise ValidationError("evidence locator must be a non-empty string when set")
        observed_at = data.get("observed_at")
        if observed_at is not None and (
            not isinstance(observed_at, str) or not observed_at.strip()
        ):
            raise ValidationError("observed_at must be a non-empty string when set")
        sequence_index = data.get("sequence_index")
        if sequence_index is not None:
            sequence_index = _nonnegative_int(sequence_index, "evidence sequence_index")
        content_sha256 = data.get("content_sha256")
        if content_sha256 is not None and (
            not isinstance(content_sha256, str)
            or len(content_sha256) != 64
            or any(character not in "0123456789abcdef" for character in content_sha256)
        ):
            raise ValidationError("content_sha256 must be a lowercase SHA-256 hex digest")
        raw_dimensions = data.get("supports_dimensions", [])
        if not isinstance(raw_dimensions, list) or any(
            item not in SUPPORT_DIMENSIONS for item in raw_dimensions
        ):
            raise ValidationError("supports_dimensions contains an unknown support dimension")
        if len(set(raw_dimensions)) != len(raw_dimensions):
            raise ValidationError("supports_dimensions must not contain duplicates")
        raw_primitives = data.get("supports_primitives", [])
        if not isinstance(raw_primitives, list):
            raise ValidationError("supports_primitives must be an array")
        try:
            supports_primitives = tuple(Primitive(item) for item in raw_primitives)
        except (TypeError, ValueError) as exc:
            raise ValidationError("supports_primitives contains an unknown primitive") from exc
        if len(set(supports_primitives)) != len(supports_primitives):
            raise ValidationError("supports_primitives must not contain duplicates")
        available_at_decision = data.get("available_at_decision")
        if available_at_decision is not None and not isinstance(
            available_at_decision, bool
        ):
            raise ValidationError("available_at_decision must be boolean when set")
        try:
            source = EvidenceSource(data["source"])
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"unknown evidence source: {data['source']}") from exc
        if require_binding:
            if locator is None or sequence_index is None or content_sha256 is None:
                raise ValidationError(
                    "retrace-state-v2 evidence requires locator, sequence_index, and content_sha256"
                )
            if available_at_decision is not True:
                raise ValidationError(
                    "retrace-state-v2 evidence must be available_at_decision=true"
                )
            if not raw_dimensions and not supports_primitives:
                raise ValidationError(
                    "retrace-state-v2 evidence requires a need or primitive binding"
                )
        return cls(
            evidence_id=evidence_id.strip(),
            source=source,
            locator=locator,
            observed_at=observed_at,
            sequence_index=sequence_index,
            content_sha256=content_sha256,
            supports_dimensions=tuple(raw_dimensions),
            supports_primitives=supports_primitives,
            available_at_decision=available_at_decision,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "evidence_id": self.evidence_id,
            "source": self.source.value,
        }
        if self.locator is not None:
            result["locator"] = self.locator
        if self.observed_at is not None:
            result["observed_at"] = self.observed_at
        if self.sequence_index is not None:
            result["sequence_index"] = self.sequence_index
        if self.content_sha256 is not None:
            result["content_sha256"] = self.content_sha256
        if self.supports_dimensions:
            result["supports_dimensions"] = list(self.supports_dimensions)
        if self.supports_primitives:
            result["supports_primitives"] = [
                primitive.value for primitive in self.supports_primitives
            ]
        if self.available_at_decision is not None:
            result["available_at_decision"] = self.available_at_decision
        return result


@dataclass(frozen=True)
class SupportNeeds:
    criteria_basis_reconstruction: int
    project_state_reconstruction: int
    evidence_action_governance: int

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SupportNeeds":
        data = _require_mapping(raw, "support_needs")
        _check_keys(data, required=set(SUPPORT_DIMENSIONS), context="support_needs")
        values: dict[str, int] = {}
        for key in SUPPORT_DIMENSIONS:
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
                raise ValidationError(f"support_needs.{key} must be an integer in [0, 3]")
            values[key] = value
        return cls(**values)

    def normalized(self, key: str) -> float:
        return getattr(self, key) / 3.0

    def to_dict(self) -> dict[str, int]:
        return {key: getattr(self, key) for key in SUPPORT_DIMENSIONS}


@dataclass(frozen=True)
class DecisionState:
    schema_version: str
    decision_id: str
    process_state: ProcessState
    support_opportunity: SupportOpportunity
    support_needs: SupportNeeds
    evidence: tuple[EvidenceRef, ...]
    consequence: Risk
    reversibility: Risk
    authorization_risk: Risk
    evidence_completeness: EvidenceCompleteness
    state_confidence: float
    recent_interventions: int
    active_verification: bool
    support_profile: Mapping[str, Mapping[str, str]] | None = None
    basis_relevant_signal: bool | None = None
    delegation_failure_signal: bool | None = None
    repeated_unresolved: bool | None = None
    target_key: str | None = None
    delegation_attempt_count: int | None = None
    last_confirmed_progress: bool | None = None
    failure_window: int | None = None
    cooldown_until: str | None = None
    recent_intervention_ids: tuple[str, ...] | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DecisionState":
        data = _require_mapping(raw, "state")
        required = {
            "schema_version",
            "decision_id",
            "process_state",
            "support_opportunity",
            "support_needs",
            "evidence",
            "consequence",
            "reversibility",
            "authorization_risk",
            "evidence_completeness",
            "state_confidence",
            "recent_interventions",
            "active_verification",
        }
        _check_keys(
            data,
            required=required,
            optional={
                "support_profile",
                "basis_relevant_signal",
                "delegation_failure_signal",
                "repeated_unresolved",
                "target_key",
                "delegation_attempt_count",
                "last_confirmed_progress",
                "failure_window",
                "cooldown_until",
                "recent_intervention_ids",
            },
            context="state",
        )
        if data["schema_version"] not in {"retrace-state-v1", "retrace-state-v2"}:
            raise ValidationError(f"unsupported state schema: {data['schema_version']}")
        decision_id = data["decision_id"]
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ValidationError("decision_id must be a non-empty string")
        try:
            process_state = ProcessState(data["process_state"])
            support_opportunity = SupportOpportunity(data["support_opportunity"])
            consequence = Risk(data["consequence"])
            reversibility = Risk(data["reversibility"])
            authorization_risk = Risk(data["authorization_risk"])
            evidence_completeness = EvidenceCompleteness(data["evidence_completeness"])
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"invalid state enum: {exc}") from exc
        raw_evidence = data["evidence"]
        if not isinstance(raw_evidence, list):
            raise ValidationError("evidence must be an array")
        require_binding = data["schema_version"] == "retrace-state-v2"
        evidence = tuple(
            EvidenceRef.from_dict(item, require_binding=require_binding)
            for item in raw_evidence
        )
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
        runtime_signals: dict[str, bool | None] = {}
        for field_name in (
            "basis_relevant_signal",
            "delegation_failure_signal",
            "repeated_unresolved",
        ):
            value = data.get(field_name)
            if value is not None and not isinstance(value, bool):
                raise ValidationError(f"{field_name} must be boolean when set")
            runtime_signals[field_name] = value
        target_key = data.get("target_key")
        if target_key is not None and (
            not isinstance(target_key, str) or not target_key.strip()
        ):
            raise ValidationError("target_key must be a non-empty string when set")
        delegation_attempt_count = data.get("delegation_attempt_count")
        if delegation_attempt_count is not None:
            delegation_attempt_count = _nonnegative_int(
                delegation_attempt_count, "delegation_attempt_count"
            )
        last_confirmed_progress = data.get("last_confirmed_progress")
        if last_confirmed_progress is not None and not isinstance(
            last_confirmed_progress, bool
        ):
            raise ValidationError("last_confirmed_progress must be boolean when set")
        failure_window = data.get("failure_window")
        if failure_window is not None:
            failure_window = _nonnegative_int(failure_window, "failure_window")
        cooldown_until = data.get("cooldown_until")
        if cooldown_until is not None and (
            not isinstance(cooldown_until, str) or not cooldown_until.strip()
        ):
            raise ValidationError("cooldown_until must be a non-empty string when set")
        recent_intervention_ids = data.get("recent_intervention_ids")
        if recent_intervention_ids is not None:
            if not isinstance(recent_intervention_ids, list) or any(
                not isinstance(item, str) or not item.strip()
                for item in recent_intervention_ids
            ):
                raise ValidationError(
                    "recent_intervention_ids must be a string array when set"
                )
            if len(set(recent_intervention_ids)) != len(recent_intervention_ids):
                raise ValidationError("recent_intervention_ids must be unique")
        raw_profile = data.get("support_profile")
        support_profile = None
        if raw_profile is not None:
            profile_data = _require_mapping(raw_profile, "support_profile")
            _check_keys(profile_data, required=set(SUPPORT_DIMENSIONS), context="support_profile")
            normalized_profile: dict[str, Mapping[str, str]] = {}
            for dimension in SUPPORT_DIMENSIONS:
                item = _require_mapping(profile_data[dimension], f"support_profile.{dimension}")
                _check_keys(
                    item,
                    required={"observed_work", "support_need", "confidence"},
                    context=f"support_profile.{dimension}",
                )
                observed_work = item["observed_work"]
                support_need = item["support_need"]
                confidence = item["confidence"]
                if observed_work not in {"NONE", "POSSIBLE", "OBSERVED"}:
                    raise ValidationError(
                        f"support_profile.{dimension}.observed_work is invalid"
                    )
                if support_need not in {"NONE", "LOW", "MEDIUM", "HIGH"}:
                    raise ValidationError(
                        f"support_profile.{dimension}.support_need is invalid"
                    )
                if confidence not in {"LOW", "MEDIUM", "HIGH"}:
                    raise ValidationError(
                        f"support_profile.{dimension}.confidence is invalid"
                    )
                normalized_profile[dimension] = {
                    "observed_work": observed_work,
                    "support_need": support_need,
                    "confidence": confidence,
                }
            support_profile = normalized_profile
        return cls(
            schema_version=data["schema_version"],
            decision_id=decision_id.strip(),
            process_state=process_state,
            support_opportunity=support_opportunity,
            support_needs=SupportNeeds.from_dict(data["support_needs"]),
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
            support_profile=support_profile,
            basis_relevant_signal=runtime_signals["basis_relevant_signal"],
            delegation_failure_signal=runtime_signals["delegation_failure_signal"],
            repeated_unresolved=runtime_signals["repeated_unresolved"],
            target_key=target_key.strip() if target_key is not None else None,
            delegation_attempt_count=delegation_attempt_count,
            last_confirmed_progress=last_confirmed_progress,
            failure_window=failure_window,
            cooldown_until=cooldown_until.strip() if cooldown_until is not None else None,
            recent_intervention_ids=(
                tuple(recent_intervention_ids)
                if recent_intervention_ids is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "process_state": self.process_state.value,
            "support_opportunity": self.support_opportunity.value,
            "support_needs": self.support_needs.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "consequence": self.consequence.value,
            "reversibility": self.reversibility.value,
            "authorization_risk": self.authorization_risk.value,
            "evidence_completeness": self.evidence_completeness.value,
            "state_confidence": self.state_confidence,
            "recent_interventions": self.recent_interventions,
            "active_verification": self.active_verification,
        }
        if self.support_profile is not None:
            result["support_profile"] = {
                dimension: dict(values)
                for dimension, values in self.support_profile.items()
            }
        for field_name in (
            "basis_relevant_signal",
            "delegation_failure_signal",
            "repeated_unresolved",
        ):
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = value
        for field_name in (
            "target_key",
            "delegation_attempt_count",
            "last_confirmed_progress",
            "failure_window",
            "cooldown_until",
        ):
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = value
        if self.recent_intervention_ids is not None:
            result["recent_intervention_ids"] = list(self.recent_intervention_ids)
        return result


@dataclass(frozen=True)
class PrimitiveProfile:
    primary_support_dimension: str
    capabilities: Mapping[str, float]
    burden: Mapping[Level, float]
    minimum_evidence: Mapping[Level, EvidenceCompleteness]


@dataclass(frozen=True)
class Thresholds:
    low_confidence: float
    gain: float
    early_support_gain_floor: float
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
    contextual_weight_adjustment: Mapping[str, Any]
    objective: Mapping[str, Any]
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
    criteria_basis_reconstruction: float
    project_state_reconstruction: float
    evidence_action_governance: float
    evidence_quality: float
    workflow_continuity: float

    def __post_init__(self) -> None:
        for key in SCORE_DIMENSIONS:
            value = getattr(self, key)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValidationError(f"score {key} must be finite and within [0, 1]")

    def vector(self) -> tuple[float, ...]:
        return tuple(getattr(self, key) for key in SCORE_DIMENSIONS)

    def to_dict(self) -> dict[str, float]:
        return {key: getattr(self, key) for key in SCORE_DIMENSIONS}


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
    objective_value: float | None = None
    objective_improvement_vs_no_intervention: float | None = None

    # Compatibility aliases for older callers. The selector no longer uses a
    # utility objective; these names now refer to J(c) and its improvement over
    # the no-intervention baseline.
    @property
    def utility(self) -> float | None:
        return self.objective_value

    @utility.setter
    def utility(self, value: float | None) -> None:
        self.objective_value = value

    @property
    def gain_vs_no_intervention(self) -> float | None:
        return self.objective_improvement_vs_no_intervention

    @gain_vs_no_intervention.setter
    def gain_vs_no_intervention(self, value: float | None) -> None:
        self.objective_improvement_vs_no_intervention = value

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
            "objective_value": self.objective_value,
            "objective_improvement_vs_no_intervention": self.objective_improvement_vs_no_intervention,
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
