"""Minimal runtime contracts for the v0.6 selector.

The legacy ``models.DecisionState`` remains the evidence-rich upstream record.
This module contains only the three selector-facing objects and deterministic
result types required by the v0.6 runtime path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping

from .models import (
    ConstraintRecord,
    EvidenceCompleteness,
    Level,
    Outcome,
    ProcessState,
    ScoreVector,
    SupportNeeds,
    ValidationError,
)


class CoreRisk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


def _unit_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field_name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValidationError(f"{field_name} must be finite and within [0, 1]")
    return result


def _nonempty_string(value: Any, field_name: str) -> str:
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
class SelectorEvidenceRef:
    evidence_id: str
    source: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SelectorEvidenceRef":
        _exact_keys(raw, {"evidence_id", "source"}, "evidence ref")
        evidence_id = _nonempty_string(raw["evidence_id"], "evidence_id")
        source = _nonempty_string(raw["source"], "evidence source").upper()
        if source not in {"OBSERVED", "INFERRED", "DESIGN_ASSUMPTION"}:
            raise ValidationError(f"unknown evidence source: {source}")
        return cls(evidence_id=evidence_id, source=source)

    def to_dict(self) -> dict[str, str]:
        return {"evidence_id": self.evidence_id, "source": self.source}


@dataclass(frozen=True)
class SelectorDecisionState:
    """The compact v0.6 DecisionState view consumed by the selector."""

    decision_id: str
    process_state: ProcessState
    support_needs: SupportNeeds
    risk_level: CoreRisk
    authorization_required: bool
    evidence_level: float
    confidence: float
    recent_intervention_count: int
    active_verification: bool
    evidence_refs: tuple[SelectorEvidenceRef, ...]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SelectorDecisionState":
        expected = {
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
        _exact_keys(raw, expected, "v0.6 DecisionState")
        try:
            process_state = ProcessState(raw["process_state"])
            risk_level = CoreRisk(str(raw["risk_level"]).upper())
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"invalid v0.6 state enum: {exc}") from exc
        if not isinstance(raw["authorization_required"], bool):
            raise ValidationError("authorization_required must be boolean")
        if not isinstance(raw["active_verification"], bool):
            raise ValidationError("active_verification must be boolean")
        recent_count = raw["recent_intervention_count"]
        if isinstance(recent_count, bool) or not isinstance(recent_count, int) or recent_count < 0:
            raise ValidationError("recent_intervention_count must be a non-negative integer")
        evidence_raw = raw["evidence_refs"]
        if not isinstance(evidence_raw, list):
            raise ValidationError("evidence_refs must be an array")
        evidence_refs = tuple(SelectorEvidenceRef.from_dict(item) for item in evidence_raw)
        evidence_ids = [item.evidence_id for item in evidence_refs]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValidationError("evidence_refs must contain unique evidence_id values")
        return cls(
            decision_id=_nonempty_string(raw["decision_id"], "decision_id"),
            process_state=process_state,
            support_needs=SupportNeeds.from_dict(raw["support_needs"]),
            risk_level=risk_level,
            authorization_required=raw["authorization_required"],
            evidence_level=_unit_float(raw["evidence_level"], "evidence_level"),
            confidence=_unit_float(raw["confidence"], "confidence"),
            recent_intervention_count=recent_count,
            active_verification=raw["active_verification"],
            evidence_refs=evidence_refs,
        )

    @property
    def support_signal(self) -> bool:
        return any(
            getattr(self.support_needs, dimension) > 0
            for dimension in (
                "criteria_basis_reconstruction",
                "project_state_reconstruction",
                "evidence_action_governance",
            )
        )

    @property
    def support_opportunity(self) -> str:
        return "SUPPORT" if self.support_signal else "NONE"

    @property
    def safe_to_continue(self) -> bool:
        return self.risk_level is not CoreRisk.HIGH and not self.authorization_required

    @property
    def has_observed_evidence(self) -> bool:
        return any(item.source == "OBSERVED" for item in self.evidence_refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "process_state": self.process_state.value,
            "support_needs": self.support_needs.to_dict(),
            "risk_level": self.risk_level.value,
            "authorization_required": self.authorization_required,
            "evidence_level": self.evidence_level,
            "confidence": self.confidence,
            "recent_intervention_count": self.recent_intervention_count,
            "active_verification": self.active_verification,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
        }


# The v0.6 contract calls this object DecisionState.  The explicit selector
# name prevents accidental use of the evidence-rich legacy class internally.
DecisionState = SelectorDecisionState


@dataclass(frozen=True)
class StrategyCandidate:
    strategy_id: str
    capability: tuple[float, float, float]
    evidence_quality: float
    workflow_cost: float
    intensity: Level
    minimum_evidence: EvidenceCompleteness
    template_id: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "StrategyCandidate":
        expected = {
            "strategy_id",
            "capability",
            "evidence_quality",
            "workflow_cost",
            "intensity",
            "minimum_evidence",
            "template_id",
        }
        _exact_keys(raw, expected, "StrategyCandidate")
        capability_raw = raw["capability"]
        if not isinstance(capability_raw, Mapping):
            raise ValidationError("capability must be an object")
        _exact_keys(capability_raw, {"criteria", "state", "action"}, "capability")
        try:
            intensity = Level.from_value(raw["intensity"])
            minimum_evidence = EvidenceCompleteness(str(raw["minimum_evidence"]).lower())
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"invalid StrategyCandidate enum: {exc}") from exc
        return cls(
            strategy_id=_nonempty_string(raw["strategy_id"], "strategy_id"),
            capability=(
                _unit_float(capability_raw["criteria"], "capability.criteria"),
                _unit_float(capability_raw["state"], "capability.state"),
                _unit_float(capability_raw["action"], "capability.action"),
            ),
            evidence_quality=_unit_float(raw["evidence_quality"], "evidence_quality"),
            workflow_cost=_unit_float(raw["workflow_cost"], "workflow_cost"),
            intensity=intensity,
            minimum_evidence=minimum_evidence,
            template_id=_nonempty_string(raw["template_id"], "template_id"),
        )

    @property
    def candidate_id(self) -> str:
        return f"{self.strategy_id}:{self.intensity.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "capability": {
                "criteria": self.capability[0],
                "state": self.capability[1],
                "action": self.capability[2],
            },
            "evidence_quality": self.evidence_quality,
            "workflow_cost": self.workflow_cost,
            "intensity": self.intensity.name,
            "minimum_evidence": self.minimum_evidence.name,
            "template_id": self.template_id,
        }


@dataclass(frozen=True)
class SelectionPolicy:
    weights: tuple[float, float, float, float, float]
    lambda_value: float
    tau: float
    epsilon_tie: float

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SelectionPolicy":
        _exact_keys(raw, {"weights", "lambda", "tau", "epsilon_tie"}, "SelectionPolicy")
        weights_raw = raw["weights"]
        if not isinstance(weights_raw, list) or len(weights_raw) != 5:
            raise ValidationError("weights must contain exactly five values")
        weights = tuple(_unit_float(value, f"weights[{index}]") for index, value in enumerate(weights_raw))
        if not math.isclose(sum(weights), 1.0, abs_tol=1e-9):
            raise ValidationError("weights must sum to 1")
        return cls(
            weights=weights,  # type: ignore[arg-type]
            lambda_value=_unit_float(raw["lambda"], "lambda"),
            tau=_unit_float(raw["tau"], "tau"),
            epsilon_tie=_unit_float(raw["epsilon_tie"], "epsilon_tie"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": list(self.weights),
            "lambda": self.lambda_value,
            "tau": self.tau,
            "epsilon_tie": self.epsilon_tie,
        }


@dataclass(frozen=True)
class V06CandidateEvaluation:
    candidate: StrategyCandidate | None
    constraints: tuple[ConstraintRecord, ...]
    score: ScoreVector | None
    effective_workflow_cost: float
    objective_value: float | None = None
    gain: float | None = None

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id if self.candidate is not None else "NO_INTERVENTION"

    @property
    def allowed(self) -> bool:
        return all(item.allowed for item in self.constraints)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "strategy_candidate": self.candidate.to_dict() if self.candidate else None,
            "allowed": self.allowed,
            "constraints": [item.to_dict() for item in self.constraints],
            "score": self.score.to_dict() if self.score else None,
            "effective_workflow_cost": self.effective_workflow_cost,
            "objective_value": self.objective_value,
            "gain": self.gain,
        }


@dataclass(frozen=True)
class V06RenderedBrief:
    candidate_id: str
    strategy_id: str
    intensity: str
    title: str
    message: str
    evidence_ids: tuple[str, ...]
    next_step: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "strategy_id": self.strategy_id,
            "intensity": self.intensity,
            "title": self.title,
            "message": self.message,
            "evidence_ids": list(self.evidence_ids),
            "next_step": self.next_step,
        }


@dataclass(frozen=True)
class V06SelectionResult:
    audit_id: str
    outcome: Outcome
    reason_codes: tuple[str, ...]
    selected_ids: tuple[str, ...]
    generated: tuple[V06CandidateEvaluation, ...]
    feasible_ids: tuple[str, ...]
    skyline_ids: tuple[str, ...]
    dominance_witnesses: Mapping[str, tuple[str, ...]]
    frontier_ratio: float | None
    rendered_briefs: tuple[V06RenderedBrief, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    decision_digest: str = field(init=False)

    def __post_init__(self) -> None:
        canonical = json.dumps(
            self._payload_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        object.__setattr__(
            self,
            "decision_digest",
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def _payload_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "contract_version": "retrace-selector-v0.6",
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
            "rendered_briefs": [item.to_dict() for item in self.rendered_briefs],
            "metadata": dict(self.metadata),
        }

    def validate_integrity(self) -> None:
        canonical = json.dumps(
            self._payload_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if actual != self.decision_digest:
            raise ValidationError("v0.6 selection result changed after decision sealing")

    def to_dict(self) -> dict[str, Any]:
        self.validate_integrity()
        result = self._payload_dict()
        result["decision_digest"] = self.decision_digest
        return result
