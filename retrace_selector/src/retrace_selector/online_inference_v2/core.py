"""ReTrace online-inference v2 implementation.

The package is intentionally self-contained: ``OnlineEvent`` and the domain
contracts live at the top, ``OnlineStore`` owns durable records, and the
service at the bottom wires M1–M5 together.  The old ``v0.6`` selector remains
available for historical replay and never shares its state or objective.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from threading import RLock
from contextlib import contextmanager
from typing import Any, Iterable, Mapping

from ..config import canonical_json, content_hash
from ..models import ValidationError
from .adaptive import AdaptiveController, UserAssessedNeed, UserPolicyPreference, UserProfile, _score


class OnlineEventType(str, Enum):
    SESSION_START = "SESSION_START"
    USER_PROMPT = "USER_PROMPT"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    AGENT_FINAL = "AGENT_FINAL"
    COMPACTION_START = "COMPACTION_START"
    COMPACTION_END = "COMPACTION_END"
    SESSION_END = "SESSION_END"
    USER_RESPONSE = "USER_RESPONSE"
    INTERVENTION_EXPOSURE = "INTERVENTION_EXPOSURE"
    INTERVENTION_ACTION = "INTERVENTION_ACTION"
    LATE_EVENT = "LATE_EVENT"
    SNAPSHOT_PRE = "SNAPSHOT_PRE"
    SNAPSHOT_POST = "SNAPSHOT_POST"
    SNAPSHOT_CLOSE = "SNAPSHOT_CLOSE"
    BASELINE_MISSED = "BASELINE_MISSED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    POLICY_PREFERENCE_UPDATED = "POLICY_PREFERENCE_UPDATED"
    ADAPTATION_UPDATE = "ADAPTATION_UPDATE"


class EventSource(str, Enum):
    CODEX_HOOK = "CODEX_HOOK"
    MCP_UI = "MCP_UI"
    COLLECTOR = "COLLECTOR"


_DIMENSIONS = ("criteria", "state", "action")
_ASSESSABILITY = {"SUFFICIENT", "LIMITED", "UNKNOWN"}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
_EVIDENCE_SOURCES = {"CURRENT_USER_TURN", "SAME_CHAIN_EVENT", "SYSTEM_EVENT"}
_OCCASION_VALUES = {"CONFIRMED", "NOT_CONFIRMED", "UNCLEAR"}
_MEASUREMENT_POINTS = {"PRE", "POST", "CLOSE"}
_DEFAULT_BRANCH_CONDITIONS = {
    "STATE_CONTEXT_RECOVERY": {
        "code": "STATE_FIRST",
        "description": "优先恢复当前状态、最近动作和影响范围",
    },
    "RULE_CLARIFICATION": {
        "code": "RULE_FIRST",
        "description": "优先把模糊要求转成规则、保护项和验收条件",
    },
    "CLAIM_EVIDENCE_CALIBRATION": {
        "code": "EVIDENCE_FIRST",
        "description": "优先拆分 Agent 声明、已有证据和待验证项",
    },
    "GOVERNANCE_ACTION_PLANNING": {
        "code": "GOVERNANCE_FIRST",
        "description": "优先决定下一步、允许范围、回退和停止条件",
    },
}


# ---------------------------------------------------------------------------
# M1: normalized events and frozen decision-chain contracts
# ---------------------------------------------------------------------------

def _str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _str(value, name)


def _unit(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValidationError(f"{name} must be finite and within [0, 1]")
    return result


def _level(value: Any, name: str, *, allow_unknown: bool = True) -> int | None:
    if value is None:
        if allow_unknown:
            return None
        raise ValidationError(f"{name} must be an integer in [0, 3]")
    if allow_unknown and str(value).upper() == "UNKNOWN":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
        raise ValidationError(f"{name} must be an integer in [0, 3] or UNKNOWN")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _iso_key(value: str) -> str:
    # ISO-8601 timestamps emitted by the collector are canonical enough for a
    # deterministic lexical comparison; invalid values are rejected first.
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"invalid ISO-8601 timestamp: {value}") from exc
    return value


@dataclass(frozen=True)
class OnlineEvent:
    event_id: str
    session_id: str
    event_type: OnlineEventType
    actor: str
    project_id: str
    observed_at: str
    source: EventSource
    content_ref: str | None = None
    turn_id: str | None = None
    received_at: str | None = None
    causal_parent_ids: tuple[str, ...] = ()
    tool_use_id: str | None = None
    collector_seq: int | None = None
    is_late: bool = False
    late_for_snapshot_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    user_id: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "OnlineEvent":
        if not isinstance(raw, Mapping):
            raise ValidationError("online event must be an object")
        required = {
            "event_id", "session_id", "event_type", "actor", "project_id",
            "observed_at", "source",
        }
        optional = {
            "content_ref", "turn_id", "received_at", "causal_parent_ids",
            "tool_use_id", "collector_seq", "is_late", "late_for_snapshot_id",
            "payload", "metadata", "user_id",
        }
        missing = required - set(raw)
        unknown = set(raw) - required - optional
        if missing:
            raise ValidationError(f"online event missing fields: {sorted(missing)}")
        if unknown:
            raise ValidationError(f"online event unknown fields: {sorted(unknown)}")
        try:
            event_type = OnlineEventType(str(raw["event_type"]).upper())
            source = EventSource(str(raw["source"]).upper())
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"invalid online event enum: {exc}") from exc
        parents = raw.get("causal_parent_ids", [])
        if not isinstance(parents, list) or any(not isinstance(item, str) for item in parents):
            raise ValidationError("causal_parent_ids must be an array of strings")
        seq = raw.get("collector_seq")
        if seq is not None and (isinstance(seq, bool) or not isinstance(seq, int) or seq < 1):
            raise ValidationError("collector_seq must be a positive integer when set")
        payload = raw.get("payload", raw.get("metadata", {}))
        if not isinstance(payload, Mapping):
            raise ValidationError("payload must be an object")
        try:
            payload_copy = json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        except (TypeError, ValueError) as exc:
            raise ValidationError("payload must be JSON serializable") from exc
        for ref_container_name, ref_container in (
            ("payload.evidence_refs", payload_copy.get("evidence_refs", [])),
            ("payload.selector_hint.evidence_refs", payload_copy.get("selector_hint", {}).get("evidence_refs", []) if isinstance(payload_copy.get("selector_hint"), Mapping) else []),
        ):
            if ref_container is None:
                continue
            if not isinstance(ref_container, list):
                raise ValidationError(f"{ref_container_name} must be an array")
            if any(
                isinstance(ref, Mapping)
                and str(ref.get("source", "")).upper() == "OUTCOME_ANNOTATION"
                for ref in ref_container
            ):
                raise ValidationError(
                    "posterior outcome annotation evidence cannot enter online inference"
                )
        observed = _iso_key(_str(raw["observed_at"], "observed_at"))
        received = raw.get("received_at")
        if received is not None:
            received = _iso_key(_str(received, "received_at"))
        return cls(
            event_id=_str(raw["event_id"], "event_id"),
            session_id=_str(raw["session_id"], "session_id"),
            event_type=event_type,
            actor=_str(raw["actor"], "actor").upper(),
            project_id=_str(raw["project_id"], "project_id"),
            observed_at=observed,
            source=source,
            content_ref=_optional_str(raw.get("content_ref"), "content_ref"),
            turn_id=_optional_str(raw.get("turn_id"), "turn_id"),
            received_at=received,
            causal_parent_ids=tuple(parents),
            tool_use_id=_optional_str(raw.get("tool_use_id"), "tool_use_id"),
            collector_seq=seq,
            is_late=bool(raw.get("is_late", False)),
            late_for_snapshot_id=_optional_str(raw.get("late_for_snapshot_id"), "late_for_snapshot_id"),
            payload=payload_copy,
            user_id=_optional_str(raw.get("user_id"), "user_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "event_type": self.event_type.value,
            "actor": self.actor,
            "project_id": self.project_id,
            "observed_at": self.observed_at,
            "source": self.source.value,
            "content_ref": self.content_ref,
            "turn_id": self.turn_id,
            "received_at": self.received_at,
            "causal_parent_ids": list(self.causal_parent_ids),
            "tool_use_id": self.tool_use_id,
            "collector_seq": self.collector_seq,
            "is_late": self.is_late,
            "late_for_snapshot_id": self.late_for_snapshot_id,
            "payload": dict(self.payload),
            "user_id": self.user_id,
        }


@dataclass(frozen=True)
class OccasionSignals:
    prior_instantiation: str
    current_contact: str
    consequentiality: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "OccasionSignals":
        if not isinstance(raw, Mapping):
            raise ValidationError("occasion_signals must be an object")
        values = {}
        for key in ("prior_instantiation", "current_contact", "consequentiality"):
            value = str(raw.get(key, "UNCLEAR")).upper()
            if value not in _OCCASION_VALUES:
                raise ValidationError(f"{key} must be CONFIRMED, NOT_CONFIRMED, or UNCLEAR")
            values[key] = value
        return cls(**values)

    @property
    def status(self) -> str:
        values = (self.prior_instantiation, self.current_contact, self.consequentiality)
        if all(value == "CONFIRMED" for value in values):
            return "OCCASION_CONFIRMED"
        if any(value == "NOT_CONFIRMED" for value in values):
            return "NOT_OCCASION"
        if self.prior_instantiation == "CONFIRMED" and self.current_contact == "CONFIRMED":
            return "OCCASION_CANDIDATE"
        return "UNKNOWN"


@dataclass(frozen=True)
class TargetState:
    criteria: int
    state: int
    action: int
    rubric_version: str = "CSA-RUBRIC-V1"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TargetState":
        if not isinstance(raw, Mapping):
            raise ValidationError("target_state must be an object")
        return cls(
            criteria=_level(raw.get("criteria"), "target_state.criteria", allow_unknown=False),
            state=_level(raw.get("state"), "target_state.state", allow_unknown=False),
            action=_level(raw.get("action"), "target_state.action", allow_unknown=False),
            rubric_version=_str(raw.get("rubric_version", "CSA-RUBRIC-V1"), "rubric_version"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"criteria": self.criteria, "state": self.state, "action": self.action,
                "rubric_version": self.rubric_version}


@dataclass(frozen=True)
class DecisionObjectProfile:
    profile_id: str
    decision_object: str
    target_state: TargetState
    allowed_evidence_types: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DecisionObjectProfile":
        return cls(
            profile_id=_str(raw.get("profile_id"), "profile_id"),
            decision_object=_str(raw.get("decision_object"), "decision_object"),
            target_state=TargetState.from_dict(raw.get("target_state", {})),
            allowed_evidence_types=tuple(str(item) for item in raw.get("allowed_evidence_types", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"profile_id": self.profile_id, "decision_object": self.decision_object,
                "target_state": self.target_state.to_dict(),
                "allowed_evidence_types": list(self.allowed_evidence_types)}


@dataclass(frozen=True)
class EvidenceRef:
    """A same-chain evidence packet with explicit semantic support claims."""

    evidence_id: str
    source_event_id: str
    source: str
    semantic_role: str
    supports_families: tuple[str, ...] = ()
    supports_dimensions: tuple[str, ...] = ()
    source_turn_id: str | None = None

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
        *,
        default_source_event_id: str,
        default_source_turn_id: str | None,
        default_source: str,
    ) -> "EvidenceRef":
        if not isinstance(raw, Mapping):
            raise ValidationError("evidence_ref must be an object")
        evidence_id = _str(raw.get("evidence_id"), "evidence_ref.evidence_id")
        source_event_id = _str(raw.get("source_event_id", default_source_event_id), "evidence_ref.source_event_id")
        source = _str(raw.get("source", default_source), "evidence_ref.source").upper()
        if source not in _EVIDENCE_SOURCES:
            raise ValidationError("evidence_ref.source must be a current or same-chain source")
        semantic_role = _str(raw.get("semantic_role"), "evidence_ref.semantic_role").upper()
        raw_families = raw.get("supports_families", [])
        raw_dimensions = raw.get("supports_dimensions", [])
        if not isinstance(raw_families, list) or any(not isinstance(item, str) or not item.strip() for item in raw_families):
            raise ValidationError("evidence_ref.supports_families must be an array of strings")
        if not isinstance(raw_dimensions, list) or any(item not in _DIMENSIONS for item in raw_dimensions):
            raise ValidationError("evidence_ref.supports_dimensions must contain valid dimensions")
        source_turn_id = raw.get("source_turn_id", default_source_turn_id)
        if source_turn_id is not None:
            source_turn_id = _str(source_turn_id, "evidence_ref.source_turn_id")
        return cls(
            evidence_id=evidence_id,
            source_event_id=source_event_id,
            source=source,
            semantic_role=semantic_role,
            supports_families=tuple(dict.fromkeys(item.strip().upper() for item in raw_families)),
            supports_dimensions=tuple(dict.fromkeys(raw_dimensions)),
            source_turn_id=source_turn_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_event_id": self.source_event_id,
            "source": self.source,
            "semantic_role": self.semantic_role,
            "supports_families": list(self.supports_families),
            "supports_dimensions": list(self.supports_dimensions),
            "source_turn_id": self.source_turn_id,
        }


@dataclass(frozen=True)
class DecisionChain:
    chain_id: str
    session_id: str
    project_id: str
    occasion_id: str
    focal_decision_id: str
    decision_object_profile_id: str
    claim_ids: tuple[str, ...]
    decision_object: str
    anchor_event_id: str
    evidence_ids: tuple[str, ...]
    target_state: TargetState
    status: str = "BASELINE_EVALUATION_PENDING"
    baseline_evaluation_id: str | None = None
    exposure_id: str | None = None
    evaluation_id: str | None = None
    latest_selection_id: str | None = None
    baseline_missed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id, "session_id": self.session_id,
            "project_id": self.project_id, "occasion_id": self.occasion_id,
            "focal_decision_id": self.focal_decision_id,
            "decision_object_profile_id": self.decision_object_profile_id,
            "claim_ids": list(self.claim_ids), "decision_object": self.decision_object,
            "anchor_event_id": self.anchor_event_id, "evidence_ids": list(self.evidence_ids),
            "target_state": self.target_state.to_dict(), "status": self.status,
            "baseline_evaluation_id": self.baseline_evaluation_id,
            "exposure_id": self.exposure_id, "evaluation_id": self.evaluation_id,
            "latest_selection_id": self.latest_selection_id,
            "baseline_missed": self.baseline_missed,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DecisionChain":
        return cls(
            chain_id=_str(raw["chain_id"], "chain_id"), session_id=_str(raw["session_id"], "session_id"),
            project_id=_str(raw["project_id"], "project_id"), occasion_id=_str(raw["occasion_id"], "occasion_id"),
            focal_decision_id=_str(raw["focal_decision_id"], "focal_decision_id"),
            decision_object_profile_id=_str(raw["decision_object_profile_id"], "decision_object_profile_id"),
            claim_ids=tuple(str(x) for x in raw.get("claim_ids", [])),
            decision_object=_str(raw["decision_object"], "decision_object"),
            anchor_event_id=_str(raw["anchor_event_id"], "anchor_event_id"),
            evidence_ids=tuple(str(x) for x in raw.get("evidence_ids", [])),
            target_state=TargetState.from_dict(raw["target_state"]), status=_str(raw.get("status", "OBSERVING"), "status"),
            baseline_evaluation_id=raw.get("baseline_evaluation_id"), exposure_id=raw.get("exposure_id"),
            evaluation_id=raw.get("evaluation_id"), latest_selection_id=raw.get("latest_selection_id"),
            baseline_missed=bool(raw.get("baseline_missed", False)),
        )


@dataclass(frozen=True)
class DimensionState:
    level: int | None
    assessability: str
    evidence_ids: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if self.level is not None and not 0 <= self.level <= 3:
            raise ValidationError("dimension level must be in [0, 3]")
        if self.assessability not in _ASSESSABILITY:
            raise ValidationError("invalid dimension assessability")

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level if self.level is not None else "UNKNOWN",
                "assessability": self.assessability, "evidence_ids": list(self.evidence_ids),
                "evidence_refs": [ref.to_dict() for ref in self.evidence_refs]}


@dataclass(frozen=True)
class ObserverState:
    chain_id: str
    criteria: DimensionState
    state: DimensionState
    action: DimensionState
    recent_exposure_count: int = 0
    recent_exposure_burden: float = 0.0
    active_verification: bool = False
    support_family: str | None = None
    allowed_families: tuple[str, ...] = ()
    support_confidence: str = "UNKNOWN"
    max_intensity: int = 3
    cognitive_gap_detected: bool = True
    execution_request_detected: bool = False
    support_reason: str = ""
    hint_evidence_ids: tuple[str, ...] = ()
    cooldown_active: bool = False
    cooldown_candidate_ids: tuple[str, ...] = ()
    cooldown_strategy_families: tuple[str, ...] = ()
    cooldown_scope: str = "NONE"
    last_exposure_at: str | None = None
    no_response_timeout_active: bool = False
    last_user_event_at: str | None = None
    user_preference_version: str = "PREF-DEFAULT"
    user_preference_mode: str = "AUTO"
    frequency_preference: float | None = None
    intensity_preference: float | None = None

    @property
    def intervention_eligible(self) -> bool:
        """Backward-compatible view; cognitive gap is the canonical field."""
        return self.cognitive_gap_detected

    def dimensions(self) -> dict[str, DimensionState]:
        return {"criteria": self.criteria, "state": self.state, "action": self.action}

    def to_dict(self) -> dict[str, Any]:
        return {"chain_id": self.chain_id, "current_state": {k: v.to_dict() for k, v in self.dimensions().items()},
                "recent_exposure_count": self.recent_exposure_count,
                "recent_exposure_burden": self.recent_exposure_burden,
                "active_verification": self.active_verification,
                "selector_hint": {
                    "support_family": self.support_family,
                    "allowed_families": list(self.allowed_families),
                    "confidence": self.support_confidence,
                    "max_intensity": self.max_intensity,
                    "cognitive_gap_detected": self.cognitive_gap_detected,
                    "execution_request_detected": self.execution_request_detected,
                    # Deprecated compatibility field. New producers must emit
                    # the two fields above instead of collapsing them.
                    "intervention_eligible": self.intervention_eligible,
                    "reason": self.support_reason,
                    "evidence_ids": list(self.hint_evidence_ids),
                },
                "cooldown_active": self.cooldown_active,
                "cooldown_candidate_ids": list(self.cooldown_candidate_ids),
                "cooldown_strategy_families": list(self.cooldown_strategy_families),
                "cooldown_scope": self.cooldown_scope,
                "last_exposure_at": self.last_exposure_at,
                "no_response_timeout_active": self.no_response_timeout_active,
                "last_user_event_at": self.last_user_event_at,
                "user_preference": {
                    "version": self.user_preference_version,
                    "mode": self.user_preference_mode,
                    "frequency_preference": self.frequency_preference,
                    "intensity_preference": self.intensity_preference,
                }}


@dataclass(frozen=True)
class StrategyV2:
    strategy_id: str
    family: str
    intensity: int
    criteria: float
    state: float
    action: float
    evidence: float
    workflow: float
    template_id: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "StrategyV2":
        params = raw.get("parameters", raw)
        if not isinstance(params, Mapping):
            raise ValidationError("strategy parameters must be an object")
        strategy_id = _str(raw.get("strategy_id", raw.get("strategy")), "strategy_id")
        family = _str(raw.get("strategy_family", raw.get("family", strategy_id)), "strategy_family")
        intensity = raw.get("intensity")
        if isinstance(intensity, str) and intensity.upper().startswith("L"):
            intensity = int(intensity[1:])
        if isinstance(intensity, bool) or not isinstance(intensity, int) or not 1 <= intensity <= 3:
            raise ValidationError("strategy intensity must be 1, 2, or 3")
        return cls(strategy_id, family, intensity, _unit(params.get("criteria"), "criteria"),
                   _unit(params.get("state"), "state"), _unit(params.get("action"), "action"),
                   _unit(params.get("evidence"), "evidence"), _unit(params.get("workflow"), "workflow"),
                   _str(raw.get("template_id", strategy_id), "template_id"))

    @property
    def candidate_id(self) -> str:
        return f"{self.strategy_id}_L{self.intensity}"

    def vector(self, workflow: float | None = None) -> tuple[float, float, float, float, float]:
        return (self.criteria, self.state, self.action, self.evidence,
                self.workflow if workflow is None else workflow)

    def to_dict(self) -> dict[str, Any]:
        return {"strategy_id": self.strategy_id, "strategy_family": self.family,
                "intensity": f"L{self.intensity}", "parameters": {
                    "criteria": self.criteria, "state": self.state, "action": self.action,
                    "evidence": self.evidence, "workflow": self.workflow},
                "template_id": self.template_id}


@dataclass(frozen=True)
class RegistryV2:
    registry_version: str
    registry_status: str
    candidates: tuple[StrategyV2, ...]
    templates: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    config_hash: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RegistryV2":
        if not isinstance(raw, Mapping):
            raise ValidationError("v2 registry must be an object")
        candidates = tuple(StrategyV2.from_dict(item) for item in raw.get("candidates", []))
        ids = [item.candidate_id for item in candidates]
        if not candidates or len(ids) != len(set(ids)):
            raise ValidationError("v2 registry candidates must be non-empty and unique")
        status = _str(raw.get("registry_status", "TEST_ONLY"), "registry_status")
        if status not in {"TEST_ONLY", "APPROVED"}:
            raise ValidationError("registry_status must be TEST_ONLY or APPROVED")
        templates = raw.get("templates", {})
        if not isinstance(templates, Mapping):
            raise ValidationError("registry templates must be an object")
        return cls(_str(raw.get("registry_version", "STRATEGY-REGISTRY-V2"), "registry_version"),
                   status, tuple(sorted(candidates, key=lambda c: c.candidate_id)), dict(templates), content_hash(raw))


def load_registry_v2(path: str | Path) -> RegistryV2:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load v2 registry {path}: {exc}") from exc
    return RegistryV2.from_dict(raw)


@dataclass(frozen=True)
class SelectorConfigV2:
    # Keep the runtime fallback aligned with the frozen formal policy. The
    # service still accepts an explicit JSON config and records its hash.
    beta: float = 0.75
    eta: float = 0.05
    epsilon: float = 0.03
    evidence_floor_when_limited: float = 0.60
    workflow_decay_tau_seconds: float = 300.0
    workflow_exposure_lambda: float = 0.05
    # Soft family preference slack, separate from epsilon (choice tie margin).
    # It only changes ordering among candidates; it never removes a family.
    semantic_hint_soft_margin: float = 0.0
    same_chain_cooldown_seconds: float = 300.0
    long_no_response_seconds: float = 900.0
    selection_rule_version: str = "SELECTOR-V2-FORMAL-3-SEMANTIC-SOFT-GATE"
    semantic_hint_min_confidence: str = "MEDIUM"
    enforce_family_gate: bool = True
    enforce_intensity_cap: bool = True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SelectorConfigV2":
        expected = {"beta", "eta", "epsilon", "evidence_floor_when_limited",
                    "workflow_decay_tau_seconds", "workflow_exposure_lambda", "semantic_hint_soft_margin", "same_chain_cooldown_seconds", "long_no_response_seconds", "selection_rule_version",
                    "semantic_hint_min_confidence", "enforce_family_gate", "enforce_intensity_cap"}
        unknown = set(raw) - expected
        if unknown:
            raise ValidationError(f"v2 selector config unknown fields: {sorted(unknown)}")
        values = {key: raw.get(key, getattr(cls, key)) for key in expected}
        for key in ("beta", "eta", "epsilon", "evidence_floor_when_limited", "workflow_exposure_lambda", "semantic_hint_soft_margin"):
            values[key] = _unit(values[key], key)
        if isinstance(values["workflow_decay_tau_seconds"], bool) or not isinstance(values["workflow_decay_tau_seconds"], (int, float)) or values["workflow_decay_tau_seconds"] <= 0:
            raise ValidationError("workflow_decay_tau_seconds must be positive")
        if isinstance(values["same_chain_cooldown_seconds"], bool) or not isinstance(values["same_chain_cooldown_seconds"], (int, float)) or values["same_chain_cooldown_seconds"] <= 0:
            raise ValidationError("same_chain_cooldown_seconds must be positive")
        if isinstance(values["long_no_response_seconds"], bool) or not isinstance(values["long_no_response_seconds"], (int, float)) or values["long_no_response_seconds"] <= 0:
            raise ValidationError("long_no_response_seconds must be positive")
        values["selection_rule_version"] = _str(values["selection_rule_version"], "selection_rule_version")
        values["semantic_hint_min_confidence"] = _str(values["semantic_hint_min_confidence"], "semantic_hint_min_confidence").upper()
        if values["semantic_hint_min_confidence"] not in {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}:
            raise ValidationError("semantic_hint_min_confidence must be HIGH, MEDIUM, LOW, or UNKNOWN")
        for key in ("enforce_family_gate", "enforce_intensity_cap"):
            if not isinstance(values[key], bool):
                raise ValidationError(f"{key} must be boolean")
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {"beta": self.beta, "eta": self.eta, "epsilon": self.epsilon,
                "evidence_floor_when_limited": self.evidence_floor_when_limited,
                "workflow_decay_tau_seconds": self.workflow_decay_tau_seconds,
                "workflow_exposure_lambda": self.workflow_exposure_lambda,
                "semantic_hint_soft_margin": self.semantic_hint_soft_margin,
                "same_chain_cooldown_seconds": self.same_chain_cooldown_seconds,
                "long_no_response_seconds": self.long_no_response_seconds,
                "selection_rule_version": self.selection_rule_version,
                "semantic_hint_min_confidence": self.semantic_hint_min_confidence,
                "enforce_family_gate": self.enforce_family_gate,
                "enforce_intensity_cap": self.enforce_intensity_cap}


def load_selector_config_v2(path: str | Path) -> SelectorConfigV2:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load v2 selector config {path}: {exc}") from exc
    return SelectorConfigV2.from_dict(raw)


@dataclass(frozen=True)
class V2Selection:
    decision_id: str
    chain_id: str
    decision: str
    selected: tuple[str, ...]
    current_state: Mapping[str, Any]
    target_state: Mapping[str, Any]
    objective: Mapping[str, Any]
    evidence_ids: tuple[str, ...]
    skyline_ids: tuple[str, ...]
    registry_version: str
    selection_rule_version: str
    as_of_event_id: str | None = None
    snapshot_kind: str = "LIVE"
    selected_specs: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = {"decision_id": self.decision_id, "chain_id": self.chain_id,
                "decision": self.decision, "selected": list(self.selected),
                "current_state": dict(self.current_state), "target_state": dict(self.target_state),
                "objective": dict(self.objective), "evidence_ids": list(self.evidence_ids),
                "skyline_ids": list(self.skyline_ids), "registry_version": self.registry_version,
                "selection_rule_version": self.selection_rule_version,
                "as_of_event_id": self.as_of_event_id, "snapshot_kind": self.snapshot_kind}
        if self.selected_specs:
            result["options"] = [dict(spec) for spec in self.selected_specs]
            if len(self.selected_specs) == 1:
                result.update(dict(self.selected_specs[0]))
            elif self.decision == "PRESENT_CHOICES":
                result["choice_contract"] = {
                    "type": "EXPLICIT_USER_BRANCH",
                    "required_fields": [
                        "selected_candidate_id",
                        "choice_condition",
                        "choice_basis",
                    ],
                    "condition_source": "FROZEN_OPTION_BRANCH_CONDITION",
                }
        return result


# ---------------------------------------------------------------------------
# M2: durable collector ordering and immutable measurement records
# ---------------------------------------------------------------------------

def _dominates(left: tuple[float, ...], right: tuple[float, ...], epsilon: float = 1e-12) -> bool:
    return all(a >= b - epsilon for a, b in zip(left, right)) and any(a > b + epsilon for a, b in zip(left, right))


class OnlineStore:
    """SQLite persistence for v2 records; it coexists with the v0.6 tables."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS online_events (
                event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, collector_seq INTEGER NOT NULL,
                event_time TEXT NOT NULL, received_at TEXT NOT NULL, is_late INTEGER NOT NULL,
                late_for_snapshot_id TEXT, event_json TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_online_event_session_seq ON online_events(session_id, collector_seq);
            CREATE TABLE IF NOT EXISTS online_chains (chain_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, chain_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS online_snapshots (
                snapshot_id TEXT PRIMARY KEY, chain_id TEXT NOT NULL, measurement_point TEXT NOT NULL,
                as_of_event_id TEXT, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            DROP INDEX IF EXISTS idx_online_snapshot_point;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_online_snapshot_point ON online_snapshots(chain_id, measurement_point) WHERE measurement_point IN ('PRE', 'POST', 'CLOSE');
            CREATE TABLE IF NOT EXISTS online_evaluations (evaluation_id TEXT PRIMARY KEY, chain_id TEXT NOT NULL, evaluation_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS online_selections (decision_id TEXT PRIMARY KEY, chain_id TEXT NOT NULL, selection_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS online_user_preferences (user_id TEXT PRIMARY KEY, preference_json TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS online_user_profiles (user_id TEXT PRIMARY KEY, profile_json TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS online_adaptation_updates (update_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, chain_id TEXT NOT NULL, update_json TEXT NOT NULL);
            """)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def ingest(self, event: OnlineEvent) -> tuple[OnlineEvent, bool]:
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT event_json FROM online_events WHERE event_id=?", (event.event_id,)).fetchone()
            if old is not None:
                old_event = OnlineEvent.from_dict(json.loads(old["event_json"]))
                old_payload = dict(old_event.to_dict())
                new_payload = dict(event.to_dict())
                for payload in (old_payload, new_payload):
                    payload.pop("collector_seq", None)
                    payload.pop("received_at", None)
                    payload.pop("is_late", None)
                    payload.pop("late_for_snapshot_id", None)
                if content_hash(old_payload) != content_hash(new_payload):
                    conn.rollback()
                    raise ValidationError(f"event_id {event.event_id} was reused with a different payload")
                conn.rollback()
                return old_event, False
            seq = conn.execute("SELECT COALESCE(MAX(collector_seq), 0)+1 FROM online_events WHERE session_id=?", (event.session_id,)).fetchone()[0]
            received = event.received_at or _now()
            chain_id = event.payload.get("chain_id") if isinstance(event.payload, Mapping) else None
            late_for = None
            if chain_id:
                rows = conn.execute("SELECT snapshot_id, created_at, snapshot_json FROM online_snapshots WHERE chain_id=?", (chain_id,)).fetchall()
                for row in rows:
                    event_time = datetime.fromisoformat(event.observed_at.replace("Z", "+00:00"))
                    snapshot_created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                    if event_time < snapshot_created:
                        late_for = row["snapshot_id"]
                        break
            late = late_for is not None
            stored = replace(event, collector_seq=seq, received_at=received, is_late=late, late_for_snapshot_id=late_for)
            conn.execute("INSERT INTO online_events VALUES (?,?,?,?,?,?,?,?)", (stored.event_id, stored.session_id, seq, stored.observed_at, received, int(late), late_for, canonical_json(stored.to_dict())))
            conn.commit()
            return stored, True

    def events(self, session_id: str, *, chain_id: str | None = None) -> list[OnlineEvent]:
        with self._connection() as conn:
            rows = conn.execute("SELECT event_json FROM online_events WHERE session_id=? ORDER BY collector_seq", (session_id,)).fetchall()
        events = [OnlineEvent.from_dict(json.loads(row["event_json"])) for row in rows]
        if chain_id is not None:
            events = [event for event in events if event.payload.get("chain_id") == chain_id]
        return events

    def save_chain(self, chain: DecisionChain) -> None:
        with self._connection() as conn:
            conn.execute("INSERT INTO online_chains VALUES (?,?,?) ON CONFLICT(chain_id) DO UPDATE SET chain_json=excluded.chain_json", (chain.chain_id, chain.session_id, canonical_json(chain.to_dict())))

    def get_chain(self, chain_id: str) -> DecisionChain | None:
        with self._connection() as conn:
            row = conn.execute("SELECT chain_json FROM online_chains WHERE chain_id=?", (chain_id,)).fetchone()
        return DecisionChain.from_dict(json.loads(row["chain_json"])) if row else None

    def chains(self, session_id: str) -> list[DecisionChain]:
        with self._connection() as conn:
            rows = conn.execute("SELECT chain_json FROM online_chains WHERE session_id=? ORDER BY rowid", (session_id,)).fetchall()
        return [DecisionChain.from_dict(json.loads(row["chain_json"])) for row in rows]

    def save_snapshot(self, snapshot_id: str, chain_id: str, point: str, as_of_event_id: str | None, payload: Mapping[str, Any], *, created_at: str | None = None) -> bool:
        with self._connection() as conn:
            try:
                conn.execute("INSERT INTO online_snapshots VALUES (?,?,?,?,?,?)", (snapshot_id, chain_id, point, as_of_event_id, canonical_json(payload), created_at or _now()))
            except sqlite3.IntegrityError:
                return False
        return True

    def snapshots(self, chain_id: str) -> dict[str, dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("SELECT snapshot_id, measurement_point, as_of_event_id, snapshot_json, created_at FROM online_snapshots WHERE chain_id=?", (chain_id,)).fetchall()
        return {row["measurement_point"].lower(): {"snapshot_id": row["snapshot_id"], "measurement_point": row["measurement_point"], "as_of_event_id": row["as_of_event_id"], "created_at": row["created_at"], **json.loads(row["snapshot_json"])} for row in rows}

    def save_evaluation(self, evaluation_id: str, chain_id: str, payload: Mapping[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute("INSERT INTO online_evaluations VALUES (?,?,?) ON CONFLICT(evaluation_id) DO NOTHING", (evaluation_id, chain_id, canonical_json(payload)))

    def get_evaluation(self, evaluation_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT evaluation_json FROM online_evaluations WHERE evaluation_id=?", (evaluation_id,)).fetchone()
        return json.loads(row["evaluation_json"]) if row else None

    def count_user_post_evaluations(self, user_id: str) -> int:
        with self._connection() as conn:
            rows = conn.execute("SELECT evaluation_json FROM online_evaluations").fetchall()
        return sum(
            1
            for row in rows
            if (
                (payload := json.loads(row["evaluation_json"])).get("user_id") == user_id
                and payload.get("evaluation_kind") == "POST_EVALUATION"
                and set(payload.get("responses", {})) == set(_DIMENSIONS)
                and not payload.get("skipped")
            )
        )

    def count_user_complete_chains(self, user_id: str) -> int:
        """Count distinct closed chains with complete baseline and post scores.

        A POST row by itself is not evidence that a chain was completed: replay
        fixtures and retries can contain post-only rows. Adaptation history is
        therefore defined by the intersection of complete BASELINE/POST pairs
        and chains explicitly closed by the service.
        """
        with self._connection() as conn:
            chain_rows = conn.execute("SELECT chain_id, chain_json FROM online_chains").fetchall()
            evaluation_rows = conn.execute("SELECT chain_id, evaluation_json FROM online_evaluations").fetchall()
        closed_chain_ids = {
            row["chain_id"]
            for row in chain_rows
            if json.loads(row["chain_json"]).get("status") == "CLOSED"
        }
        complete_by_kind: dict[str, set[str]] = {
            "OCCASION_BASELINE": set(),
            "POST_EVALUATION": set(),
        }
        for row in evaluation_rows:
            payload = json.loads(row["evaluation_json"])
            if payload.get("user_id") != user_id:
                continue
            kind = payload.get("evaluation_kind")
            if kind not in complete_by_kind:
                continue
            responses = payload.get("responses", {})
            skipped = payload.get("skipped", payload.get("skipped_dimensions", []))
            if set(responses) == set(_DIMENSIONS) and not skipped:
                complete_by_kind[kind].add(row["chain_id"])
        return len(
            closed_chain_ids
            & complete_by_kind["OCCASION_BASELINE"]
            & complete_by_kind["POST_EVALUATION"]
        )

    def save_user_preference(self, preference: UserPolicyPreference, *, updated_at: str | None = None) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO online_user_preferences VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET preference_json=excluded.preference_json, updated_at=excluded.updated_at",
                (preference.user_id, canonical_json(preference.to_dict()), updated_at or _now()),
            )

    def get_user_preference(self, user_id: str) -> UserPolicyPreference | None:
        with self._connection() as conn:
            row = conn.execute("SELECT preference_json FROM online_user_preferences WHERE user_id=?", (user_id,)).fetchone()
        return UserPolicyPreference.from_dict(json.loads(row["preference_json"])) if row else None

    def save_user_profile(self, profile: UserProfile, *, updated_at: str | None = None) -> None:
        """Persist the three-layer profile and refresh the legacy preference index."""
        timestamp = updated_at or _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO online_user_profiles VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET profile_json=excluded.profile_json, updated_at=excluded.updated_at",
                    (profile.user_id, canonical_json(profile.to_dict()), timestamp),
                )
                conn.execute(
                    "INSERT INTO online_user_preferences VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET preference_json=excluded.preference_json, updated_at=excluded.updated_at",
                    (profile.user_id, canonical_json(profile.effective_policy.to_dict()), timestamp),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def get_user_profile(self, user_id: str) -> UserProfile | None:
        with self._connection() as conn:
            row = conn.execute("SELECT profile_json FROM online_user_profiles WHERE user_id=?", (user_id,)).fetchone()
            legacy_row = None if row else conn.execute("SELECT preference_json FROM online_user_preferences WHERE user_id=?", (user_id,)).fetchone()
        if row:
            return UserProfile.from_dict(json.loads(row["profile_json"]))
        if legacy_row:
            # Lazy compatibility migration for databases written before the
            # three-layer profile table existed.
            effective = UserPolicyPreference.from_dict(json.loads(legacy_row["preference_json"]))
            subjective = effective if effective.source == "USER_UI" else UserPolicyPreference(user_id=user_id)
            return UserProfile(
                user_id,
                subjective,
                UserAssessedNeed(user_id=user_id),
                effective,
                version="PROFILE-MIGRATED",
            )
        return None

    def save_adaptation_update(self, update_id: str, user_id: str, chain_id: str, payload: Mapping[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute("INSERT INTO online_adaptation_updates VALUES (?,?,?,?) ON CONFLICT(update_id) DO NOTHING", (update_id, user_id, chain_id, canonical_json(payload)))

    def latest_adaptation_update(self, chain_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT update_id, user_id, update_json FROM online_adaptation_updates WHERE chain_id=? ORDER BY rowid DESC LIMIT 1",
                (chain_id,),
            ).fetchone()
        if row is None:
            return None
        return {"update_id": row["update_id"], "user_id": row["user_id"], **json.loads(row["update_json"])}

    def save_selection(self, selection: V2Selection) -> None:
        with self._connection() as conn:
            conn.execute("INSERT INTO online_selections VALUES (?,?,?) ON CONFLICT(decision_id) DO NOTHING", (selection.decision_id, selection.chain_id, canonical_json(selection.to_dict())))

    def latest_selection(self, chain_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT selection_json FROM online_selections WHERE chain_id=? ORDER BY rowid DESC LIMIT 1", (chain_id,)).fetchone()
        return json.loads(row["selection_json"]) if row else None

    def get_selection(self, decision_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT selection_json FROM online_selections WHERE decision_id=?", (decision_id,)).fetchone()
        return json.loads(row["selection_json"]) if row else None


class OccasionDetector:
    def detect(self, event: OnlineEvent) -> tuple[str, OccasionSignals | None]:
        raw = event.payload.get("occasion_signals")
        if raw is None:
            return "UNKNOWN", None
        signals = OccasionSignals.from_dict(raw)
        return signals.status, signals


class TargetBuilder:
    def __init__(self, profiles: Mapping[str, DecisionObjectProfile]):
        self.profiles = dict(profiles)

    def build(self, event: OnlineEvent) -> DecisionObjectProfile:
        profile_id = event.payload.get("decision_object_profile_id")
        if not isinstance(profile_id, str) or profile_id not in self.profiles:
            raise ValidationError("CHAIN_UNASSESSABLE: no unique decision_object_profile match")
        return self.profiles[profile_id]


# ---------------------------------------------------------------------------
# M3: behavior observer, Registry and D_obj + beta * D_user selector
# ---------------------------------------------------------------------------

class StateObserverV2:
    def observe(self, chain: DecisionChain, events: Iterable[OnlineEvent], config: SelectorConfigV2, *, now: datetime | None = None) -> ObserverState:
        dimensions = {name: DimensionState(None, "UNKNOWN", ()) for name in _DIMENSIONS}
        active = False
        exposures: list[str] = []
        exposure_times: list[datetime] = []
        support_family: str | None = None
        allowed_families: tuple[str, ...] = ()
        support_confidence = "UNKNOWN"
        max_intensity = 3
        cognitive_gap_detected = True
        execution_request_detected = False
        support_reason = ""
        hint_evidence_ids: tuple[str, ...] = ()
        ordered = list(events)
        exposure_datetimes: list[datetime] = []
        user_progress_times: list[datetime] = []
        user_event_times: list[datetime] = []
        exposure_scopes: list[tuple[datetime, tuple[str, ...], tuple[str, ...], str]] = []
        for event in ordered:
            raw_hint = event.payload.get("selector_hint") if isinstance(event.payload, Mapping) else None
            if raw_hint is not None:
                if not isinstance(raw_hint, Mapping):
                    raise ValidationError("selector_hint must be an object")
                raw_family = raw_hint.get("support_family")
                if raw_family is not None and (not isinstance(raw_family, str) or not raw_family.strip()):
                    raise ValidationError("selector_hint.support_family must be a non-empty string or null")
                support_family = raw_family.strip().upper() if isinstance(raw_family, str) else None
                raw_allowed = raw_hint.get("allowed_families", [])
                if not isinstance(raw_allowed, list) or any(not isinstance(item, str) or not item.strip() for item in raw_allowed):
                    raise ValidationError("selector_hint.allowed_families must be an array of strings")
                normalized_allowed = [item.strip().upper() for item in raw_allowed]
                if support_family and support_family not in normalized_allowed:
                    normalized_allowed.insert(0, support_family)
                allowed_families = tuple(dict.fromkeys(normalized_allowed))
                support_confidence = str(raw_hint.get("confidence", "UNKNOWN")).upper()
                if support_confidence not in _CONFIDENCE:
                    raise ValidationError("selector_hint.confidence must be HIGH, MEDIUM, LOW, or UNKNOWN")
                raw_max_intensity = raw_hint.get("max_intensity", 3)
                if isinstance(raw_max_intensity, bool) or not isinstance(raw_max_intensity, int) or not 1 <= raw_max_intensity <= 3:
                    raise ValidationError("selector_hint.max_intensity must be 1, 2, or 3")
                max_intensity = raw_max_intensity
                if "cognitive_gap_detected" in raw_hint:
                    raw_gap = raw_hint["cognitive_gap_detected"]
                    if not isinstance(raw_gap, bool):
                        raise ValidationError("selector_hint.cognitive_gap_detected must be boolean")
                    cognitive_gap_detected = raw_gap
                elif "intervention_eligible" in raw_hint:
                    # Accept v1 traces, but normalize them to the split v2
                    # representation. This compatibility path is intentionally
                    # not used by the new prompt contract.
                    raw_eligible = raw_hint["intervention_eligible"]
                    if not isinstance(raw_eligible, bool):
                        raise ValidationError("selector_hint.intervention_eligible must be boolean")
                    cognitive_gap_detected = raw_eligible
                raw_execution = raw_hint.get("execution_request_detected", False)
                if not isinstance(raw_execution, bool):
                    raise ValidationError("selector_hint.execution_request_detected must be boolean")
                execution_request_detected = raw_execution
                support_reason = str(raw_hint.get("reason", ""))
                raw_hint_evidence = raw_hint.get("evidence_ids", [])
                if not isinstance(raw_hint_evidence, list) or any(not isinstance(item, str) for item in raw_hint_evidence):
                    raise ValidationError("selector_hint.evidence_ids must be an array of strings")
                hint_evidence_ids = tuple(dict.fromkeys(raw_hint_evidence))
            raw_evidence_refs: list[Any] = []
            if isinstance(event.payload, Mapping):
                payload_refs = event.payload.get("evidence_refs", [])
                if payload_refs is not None:
                    if not isinstance(payload_refs, list):
                        raise ValidationError("event evidence_refs must be an array")
                    raw_evidence_refs.extend(payload_refs)
            if isinstance(raw_hint, Mapping) and "evidence_refs" in raw_hint:
                hint_refs = raw_hint.get("evidence_refs")
                if not isinstance(hint_refs, list):
                    raise ValidationError("selector_hint.evidence_refs must be an array")
                raw_evidence_refs.extend(hint_refs)
            default_source = "CURRENT_USER_TURN" if event.actor == "USER" else "SAME_CHAIN_EVENT"
            evidence_refs_by_id: dict[str, EvidenceRef] = {}
            for raw_ref in raw_evidence_refs:
                ref = EvidenceRef.from_dict(
                    raw_ref,
                    default_source_event_id=event.event_id,
                    default_source_turn_id=event.turn_id,
                    default_source=default_source,
                )
                evidence_refs_by_id[ref.evidence_id] = ref
            if event.event_type is OnlineEventType.INTERVENTION_EXPOSURE:
                exposures.append(event.event_id)
                exposure_time = datetime.fromisoformat(event.observed_at.replace("Z", "+00:00"))
                exposure_times.append(exposure_time)
                exposure_datetimes.append(exposure_time)
                raw_candidate_ids = event.payload.get("cooldown_candidate_ids", event.payload.get("candidate_ids", []))
                if not isinstance(raw_candidate_ids, list) or any(not isinstance(item, str) for item in raw_candidate_ids):
                    raise ValidationError("exposure cooldown_candidate_ids must be an array of strings")
                raw_families = event.payload.get("cooldown_strategy_families", event.payload.get("strategy_families", []))
                if not isinstance(raw_families, list) or any(not isinstance(item, str) for item in raw_families):
                    raise ValidationError("exposure cooldown_strategy_families must be an array of strings")
                scope = str(event.payload.get("cooldown_scope", "CHAIN" if not raw_candidate_ids else "CANDIDATE")).upper()
                if scope not in {"CANDIDATE", "FAMILY", "CHAIN"}:
                    raise ValidationError("exposure cooldown_scope must be CANDIDATE, FAMILY, or CHAIN")
                exposure_scopes.append((
                    exposure_time,
                    tuple(dict.fromkeys(raw_candidate_ids)),
                    tuple(dict.fromkeys(raw_families)),
                    scope,
                ))
            elif event.actor == "USER" and event.event_type in {
                OnlineEventType.USER_RESPONSE,
                OnlineEventType.INTERVENTION_ACTION,
            }:
                user_event_time = datetime.fromisoformat(event.observed_at.replace("Z", "+00:00"))
                user_progress_times.append(user_event_time)
                user_event_times.append(user_event_time)
            elif event.actor == "USER" and event.event_type is OnlineEventType.USER_PROMPT:
                user_event_times.append(datetime.fromisoformat(event.observed_at.replace("Z", "+00:00")))
            if event.event_type is OnlineEventType.VERIFICATION_STARTED:
                active = True
            elif event.event_type is OnlineEventType.VERIFICATION_COMPLETED:
                active = False
            if event.event_type is OnlineEventType.USER_RESPONSE:
                if event.payload.get("response_kind") != "OBSERVER_PROBE":
                    continue
            elif event.actor != "USER":
                continue
            updates = event.payload.get("observer_updates", {}) if event.event_type is OnlineEventType.USER_RESPONSE else event.payload.get("csa_updates", {})
            if not isinstance(updates, Mapping):
                continue
            for dimension in _DIMENSIONS:
                raw = updates.get(dimension)
                if not isinstance(raw, Mapping):
                    continue
                level = _level(raw.get("level"), f"csa_updates.{dimension}.level")
                raw_evidence = raw.get("evidence_ids", event.payload.get("evidence_ids", []))
                if not isinstance(raw_evidence, list) or any(not isinstance(item, str) for item in raw_evidence):
                    raise ValidationError("csa_updates evidence_ids must be an array of strings")
                evidence = tuple(dict.fromkeys(str(item) for item in raw_evidence))
                dimension_refs = tuple(
                    ref for ref in evidence_refs_by_id.values()
                    if ref.evidence_id in evidence
                    or dimension in ref.supports_dimensions
                )
                if not evidence and dimension_refs:
                    evidence = tuple(ref.evidence_id for ref in dimension_refs)
                assessability = str(raw.get("assessability", "SUFFICIENT" if level is not None else "UNKNOWN")).upper()
                if assessability not in _ASSESSABILITY:
                    raise ValidationError("invalid csa update assessability")
                dimensions[dimension] = DimensionState(level, assessability, evidence, dimension_refs)
        now = now or datetime.now(timezone.utc)
        burden = sum(max(0.0, math.exp(-(now - when).total_seconds() / config.workflow_decay_tau_seconds)) for when in exposure_times)
        last_exposure = max(exposure_datetimes) if exposure_datetimes else None
        cooldown_candidate_ids: set[str] = set()
        cooldown_strategy_families: set[str] = set()
        legacy_chain_cooldown = False
        for exposure_time, candidate_ids, families, scope in exposure_scopes:
            has_progress_after_exposure = any(event_time > exposure_time for event_time in user_progress_times)
            active = (
                not has_progress_after_exposure
                and max(0.0, (now - exposure_time).total_seconds()) < config.same_chain_cooldown_seconds
            )
            if not active:
                continue
            if scope == "CHAIN":
                legacy_chain_cooldown = True
            elif scope == "FAMILY":
                cooldown_strategy_families.update(families)
            else:
                cooldown_candidate_ids.update(candidate_ids)
        cooldown_active = bool(legacy_chain_cooldown or cooldown_candidate_ids or cooldown_strategy_families)
        if legacy_chain_cooldown:
            cooldown_scope = "CHAIN"
        elif cooldown_candidate_ids and cooldown_strategy_families:
            cooldown_scope = "CANDIDATE_AND_FAMILY"
        elif cooldown_candidate_ids:
            cooldown_scope = "CANDIDATE"
        elif cooldown_strategy_families:
            cooldown_scope = "FAMILY"
        else:
            cooldown_scope = "NONE"
        last_user_event = max(user_event_times) if user_event_times else None
        latest_exposure_has_no_response = bool(
            last_exposure is not None
            and not any(event_time > last_exposure for event_time in user_progress_times)
        )
        no_response_timeout_active = bool(
            latest_exposure_has_no_response
            and max(0.0, (now - last_exposure).total_seconds()) >= config.long_no_response_seconds
        )
        return ObserverState(
            chain_id=chain.chain_id,
            criteria=dimensions["criteria"],
            state=dimensions["state"],
            action=dimensions["action"],
            recent_exposure_count=len(exposures),
            recent_exposure_burden=burden,
            active_verification=active,
            support_family=support_family,
            allowed_families=allowed_families,
            support_confidence=support_confidence,
            max_intensity=max_intensity,
            cognitive_gap_detected=cognitive_gap_detected,
            execution_request_detected=execution_request_detected,
            support_reason=support_reason,
            hint_evidence_ids=hint_evidence_ids,
            cooldown_active=cooldown_active,
            cooldown_candidate_ids=tuple(sorted(cooldown_candidate_ids)),
            cooldown_strategy_families=tuple(sorted(cooldown_strategy_families)),
            cooldown_scope=cooldown_scope,
            last_exposure_at=last_exposure.isoformat().replace("+00:00", "Z") if last_exposure else None,
            no_response_timeout_active=no_response_timeout_active,
            last_user_event_at=last_user_event.isoformat().replace("+00:00", "Z") if last_user_event else None,
        )


class SkylineSelectorV2:
    def __init__(self, registry: RegistryV2, config: SelectorConfigV2 | None = None):
        self.registry = registry
        self.config = config or SelectorConfigV2()

    def _candidate_vector(self, candidate: StrategyV2, state: ObserverState) -> tuple[float, ...]:
        workflow = max(0.0, candidate.workflow - self.config.workflow_exposure_lambda * state.recent_exposure_burden)
        return candidate.vector(workflow)

    def _semantic_constraints(self, state: ObserverState, *, hint_evidence_valid: bool) -> dict[str, Any]:
        ranks = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        family_hint_active = (
            bool(state.allowed_families)
            and self.config.enforce_family_gate
            and ranks[state.support_confidence] >= ranks[self.config.semantic_hint_min_confidence]
        )
        hard_family_gate = family_hint_active and state.support_confidence == "HIGH" and hint_evidence_valid
        family_gate_mode = (
            "HARD" if hard_family_gate else "SOFT" if family_hint_active else "OFF"
        )
        cap_applied = (
            self.config.enforce_intensity_cap
            and state.max_intensity < 3
            and (
                state.support_confidence != "UNKNOWN"
                or state.intensity_preference is not None
            )
        )
        return {
            "hint_enforced": hard_family_gate,
            "family_gate_mode": family_gate_mode,
            "family_hint_active": family_hint_active,
            "hint_evidence_valid": hint_evidence_valid,
            "semantic_support_valid": hint_evidence_valid,
            "soft_preference_applied": False,
            "allowed_families": list(state.allowed_families),
            "support_family": state.support_family,
            "support_confidence": state.support_confidence,
            "max_intensity": state.max_intensity if cap_applied else 3,
            "intensity_cap_applied": cap_applied,
            "semantic_hint_soft_margin": self.config.semantic_hint_soft_margin,
            "cognitive_gap_detected": state.cognitive_gap_detected,
            "execution_request_detected": state.execution_request_detected,
            "intervention_eligible": state.intervention_eligible,
            "cooldown_active": state.cooldown_active,
            "cooldown_candidate_ids": list(state.cooldown_candidate_ids),
            "cooldown_strategy_families": list(state.cooldown_strategy_families),
            "cooldown_scope": state.cooldown_scope,
            "no_response_timeout_active": state.no_response_timeout_active,
            "last_user_event_at": state.last_user_event_at,
            "user_preference_version": state.user_preference_version,
            "user_preference_mode": state.user_preference_mode,
            "frequency_preference": state.frequency_preference,
            "intensity_preference": state.intensity_preference,
            "effective_eta": self.config.eta,
            "effective_same_chain_cooldown_seconds": self.config.same_chain_cooldown_seconds,
        }

    def _candidate_pool(self, state: ObserverState, *, hint_evidence_valid: bool) -> tuple[tuple[StrategyV2, ...], dict[str, Any]]:
        constraints = self._semantic_constraints(state, hint_evidence_valid=hint_evidence_valid)
        candidates = self.registry.candidates
        if constraints["family_gate_mode"] == "HARD":
            allowed = set(constraints["allowed_families"])
            candidates = tuple(item for item in candidates if item.family in allowed)
        if constraints["intensity_cap_applied"]:
            candidates = tuple(item for item in candidates if item.intensity <= constraints["max_intensity"])
        return candidates, constraints

    def _candidate_spec(self, candidate: StrategyV2) -> dict[str, Any]:
        template = self.registry.templates.get(candidate.template_id, {})
        actions = template.get("allowed_action_codes", []) if isinstance(template, Mapping) else []
        default_condition = _DEFAULT_BRANCH_CONDITIONS.get(
            candidate.family,
            {"code": f"{candidate.family}_FIRST", "description": f"优先处理 {candidate.family}"},
        )
        branch_condition_code = (
            template.get("choice_condition_code", default_condition["code"])
            if isinstance(template, Mapping)
            else default_condition["code"]
        )
        branch_condition = (
            template.get("choice_condition", default_condition["description"])
            if isinstance(template, Mapping)
            else default_condition["description"]
        )
        branch_condition_code = _str(branch_condition_code, "choice_condition_code")
        branch_condition = _str(branch_condition, "choice_condition")
        if not isinstance(actions, list):
            actions = []
        return {
            "strategy_id": candidate.candidate_id,
            "strategy_family": candidate.family,
            "template_id": candidate.template_id,
            "allowed_action_codes": [str(action) for action in actions],
            "branch_condition_code": branch_condition_code,
            "branch_condition": branch_condition,
        }

    def select(self, chain: DecisionChain, state: ObserverState, *, as_of_event_id: str | None = None) -> V2Selection:
        current = state.dimensions()
        target = {"criteria": chain.target_state.criteria, "state": chain.target_state.state, "action": chain.target_state.action}
        observed_evidence = set(chain.evidence_ids)
        for dimension in current.values():
            observed_evidence.update(dimension.evidence_ids)
        hint_families = set(state.allowed_families)
        if state.support_family:
            hint_families.add(state.support_family)
        known_dimensions = {
            dimension for dimension in _DIMENSIONS if current[dimension].level is not None
        }
        semantic_refs = {
            ref.evidence_id: ref
            for dimension in current.values()
            for ref in dimension.evidence_refs
        }
        # Raw chain.evidence_ids remain linkage metadata only. They are not
        # selector evidence until a current/same-chain/system EvidenceRef
        # semantically binds the ID to a family or C/S/A dimension.
        semantic_evidence_ids = tuple(sorted(semantic_refs))
        hint_evidence_valid = any(
            ref.evidence_id in state.hint_evidence_ids
            and ref.evidence_id in observed_evidence
            and bool(set(ref.supports_families) & hint_families)
            and bool(set(ref.supports_dimensions) & known_dimensions)
            and ref.source in _EVIDENCE_SOURCES
            for ref in semantic_refs.values()
        )
        candidates, semantic_constraints = self._candidate_pool(state, hint_evidence_valid=hint_evidence_valid)
        if state.user_preference_mode == "PAUSED":
            paused_gaps = [
                max(0.0, (target[dimension] - current[dimension].level) / 3.0)
                for dimension in known_dimensions
            ]
            paused_obj = math.sqrt(sum(value * value for value in paused_gaps) / len(paused_gaps)) if paused_gaps else 0.0
            decision_id = "SEL-" + content_hash({"chain": chain.chain_id, "state": state.to_dict(), "reason": "USER_PAUSED", "as_of": as_of_event_id})[:20]
            objective = {"reason": "USER_PAUSED", "d_obj": paused_obj, "d_user": 0.0, "beta": self.config.beta, "loss": paused_obj, "semantic_constraints": semantic_constraints}
            return V2Selection(decision_id, chain.chain_id, "NO_INTERVENTION", (), {k: v.to_dict() for k, v in current.items()}, target, objective, semantic_evidence_ids, ("NO_INTERVENTION",), self.registry.registry_version, self.config.selection_rule_version, as_of_event_id)
        known = [dimension for dimension in _DIMENSIONS if current[dimension].level is not None]
        if not known:
            decision_id = "SEL-" + content_hash({"chain": chain.chain_id, "as_of": as_of_event_id, "reason": "UNKNOWN"})[:20]
            return V2Selection(decision_id, chain.chain_id, "NO_INTERVENTION", (), {k: v.to_dict() for k, v in current.items()}, target, {"reason": "UNKNOWN_STATE", "semantic_constraints": semantic_constraints}, semantic_evidence_ids, ("NO_INTERVENTION",), self.registry.registry_version, self.config.selection_rule_version, as_of_event_id)
        gaps = {dimension: max(0.0, (target[dimension] - current[dimension].level) / 3.0) for dimension in known}
        baseline_obj = math.sqrt(sum(value * value for value in gaps.values()) / len(known))
        baseline = (0.0, 0.0, 0.0, 0.0, 1.0)
        all_dimensions_known = len(known) == len(_DIMENSIONS)
        evidence_sufficient = all(
            current[dimension].assessability == "SUFFICIENT"
            and bool(current[dimension].evidence_ids)
            and any(
                ref.evidence_id in current[dimension].evidence_ids
                and dimension in ref.supports_dimensions
                and ref.source in _EVIDENCE_SOURCES
                for ref in current[dimension].evidence_refs
            )
            for dimension in _DIMENSIONS
        )
        if baseline_obj == 0.0 and all_dimensions_known and evidence_sufficient:
            decision_id = "SEL-" + content_hash({"chain": chain.chain_id, "state": state.to_dict(), "reason": "TARGET_REACHED", "as_of": as_of_event_id})[:20]
            objective = {"reason": "TARGET_REACHED", "d_obj": baseline_obj, "d_user": 0.0, "beta": self.config.beta, "loss": baseline_obj, "semantic_constraints": semantic_constraints}
            return V2Selection(decision_id, chain.chain_id, "NO_INTERVENTION", (), {k: v.to_dict() for k, v in current.items()}, target, objective, semantic_evidence_ids, ("NO_INTERVENTION",), self.registry.registry_version, self.config.selection_rule_version, as_of_event_id)
        if not state.cognitive_gap_detected:
            decision_id = "SEL-" + content_hash({"chain": chain.chain_id, "state": state.to_dict(), "reason": "INELIGIBLE", "as_of": as_of_event_id})[:20]
            objective = {"reason": "INTERVENTION_NOT_ELIGIBLE", "d_obj": baseline_obj, "d_user": 0.0, "beta": self.config.beta, "loss": baseline_obj, "semantic_constraints": semantic_constraints}
            return V2Selection(decision_id, chain.chain_id, "NO_INTERVENTION", (), {k: v.to_dict() for k, v in current.items()}, target, objective, semantic_evidence_ids, ("NO_INTERVENTION",), self.registry.registry_version, self.config.selection_rule_version, as_of_event_id)
        if state.no_response_timeout_active:
            decision_id = "SEL-" + content_hash({"chain": chain.chain_id, "state": state.to_dict(), "reason": "NO_RESPONSE_TIMEOUT", "as_of": as_of_event_id})[:20]
            objective = {"reason": "NO_RESPONSE_TIMEOUT", "d_obj": baseline_obj, "d_user": 0.0, "beta": self.config.beta, "loss": baseline_obj, "semantic_constraints": semantic_constraints}
            return V2Selection(decision_id, chain.chain_id, "NO_INTERVENTION", (), {k: v.to_dict() for k, v in current.items()}, target, objective, semantic_evidence_ids, ("NO_INTERVENTION",), self.registry.registry_version, self.config.selection_rule_version, as_of_event_id)
        evaluations: list[tuple[StrategyV2, tuple[float, ...], float, float]] = []
        limited = any(current[dimension].assessability == "LIMITED" for dimension in known)
        cooldown_skipped: set[str] = set()
        for candidate in candidates:
            if state.cooldown_scope == "CHAIN" or candidate.candidate_id in state.cooldown_candidate_ids or candidate.family in state.cooldown_strategy_families:
                cooldown_skipped.add(candidate.candidate_id)
                continue
            vector = self._candidate_vector(candidate, state)
            if limited and candidate.evidence < self.config.evidence_floor_when_limited:
                continue
            residual = {dimension: max(0.0, gaps[dimension] - vector[index]) for index, dimension in enumerate(_DIMENSIONS) if dimension in gaps}
            d_obj = math.sqrt(sum(value * value for value in residual.values()) / len(residual))
            d_user = 1.0 - vector[4]
            evaluations.append((candidate, vector, d_obj, d_obj + self.config.beta * d_user))
        semantic_constraints["cooldown_skipped_candidate_ids"] = sorted(cooldown_skipped)
        vectors = {"NO_INTERVENTION": baseline, **{item[0].candidate_id: item[1] for item in evaluations}}
        skyline_ids = tuple(sorted(candidate_id for candidate_id, vector in vectors.items() if not any(other_id != candidate_id and _dominates(other, vector) for other_id, other in vectors.items())))
        frontier = [item for item in evaluations if item[0].candidate_id in skyline_ids]
        frontier.sort(key=lambda item: (item[3], -item[1][4], -item[1][3], item[0].intensity, item[0].candidate_id))
        decision = "NO_INTERVENTION"
        selected: tuple[str, ...] = ()
        no_intervention_reason = "BELOW_ETA"
        if not candidates:
            no_intervention_reason = "NO_ELIGIBLE_CANDIDATE"
        elif cooldown_skipped and len(cooldown_skipped) == len(candidates):
            no_intervention_reason = "COOLDOWN_ACTIVE"
        elif not evaluations:
            no_intervention_reason = "INSUFFICIENT_EVIDENCE"
        if frontier:
            first = frontier[0]
            if semantic_constraints["family_gate_mode"] == "SOFT" and semantic_constraints["allowed_families"]:
                matching = [item for item in frontier if item[0].family in semantic_constraints["allowed_families"]]
                if matching:
                    matching.sort(key=lambda item: (item[3], -item[1][4], -item[1][3], item[0].intensity, item[0].candidate_id))
                    if matching[0][3] <= first[3] + self.config.semantic_hint_soft_margin:
                        first = matching[0]
                        frontier = [first] + [item for item in frontier if item is not first]
                        semantic_constraints["soft_preference_applied"] = True
                        # A soft semantic preference resolves the ordering; it
                        # must not manufacture a new user branch from a pair
                        # that was only close after reordering. PRESENT_CHOICES
                        # remains anchored to the raw epsilon tie geometry.
                        semantic_constraints["choice_suppressed_by_soft_preference"] = True
            if first[3] < baseline_obj - self.config.eta:
                decision = "INTERVENE"
                selected = (first[0].candidate_id,)
                objective: dict[str, Any] = {"d_obj": first[2], "d_user": 1.0 - first[1][4], "beta": self.config.beta, "loss": first[3], "semantic_constraints": semantic_constraints}
                if len(frontier) > 1 and not semantic_constraints.get("soft_preference_applied", False):
                    second = next((item for item in frontier[1:] if item[0].family != first[0].family and item[3] < baseline_obj - self.config.eta), None)
                    if second is not None and second[3] <= first[3] + self.config.epsilon:
                        decision, selected = "PRESENT_CHOICES", (first[0].candidate_id, second[0].candidate_id)
            else:
                no_intervention_reason = "BELOW_ETA"
        if decision == "NO_INTERVENTION":
            objective = {"reason": no_intervention_reason, "d_obj": baseline_obj, "d_user": 0.0, "beta": self.config.beta, "loss": baseline_obj, "semantic_constraints": semantic_constraints}
        selected_specs = tuple(
            self._candidate_spec(candidate)
            for candidate in self.registry.candidates
            if candidate.candidate_id in selected
        )
        selection = V2Selection("SEL-" + content_hash({"chain": chain.chain_id, "state": state.to_dict(), "selected": selected, "as_of": as_of_event_id})[:20], chain.chain_id, decision, selected, {k: v.to_dict() for k, v in current.items()}, target, objective, semantic_evidence_ids, skyline_ids, self.registry.registry_version, self.config.selection_rule_version, as_of_event_id, selected_specs=selected_specs)
        return selection


# ---------------------------------------------------------------------------
# M4/M5: online orchestration, measurements, exposure and UI polling state
# ---------------------------------------------------------------------------

class OnlineInferenceService:
    """M1–M5 service boundary used by a plugin or a local MCP adapter."""

    def __init__(self, *, database_path: str | Path, profiles: Mapping[str, Mapping[str, Any]] | Mapping[str, DecisionObjectProfile], registry: RegistryV2 | Mapping[str, Any], config: SelectorConfigV2 | Mapping[str, Any] | None = None):
        self.store = OnlineStore(database_path)
        self.collector = self.store
        self.detector = OccasionDetector()
        self.profiles = {key: value if isinstance(value, DecisionObjectProfile) else DecisionObjectProfile.from_dict(value) for key, value in profiles.items()}
        self.target_builder = TargetBuilder(self.profiles)
        self.registry = registry if isinstance(registry, RegistryV2) else RegistryV2.from_dict(registry)
        self.config = config if isinstance(config, SelectorConfigV2) else SelectorConfigV2.from_dict(config or {})
        self.observer = StateObserverV2()
        self.selector = SkylineSelectorV2(self.registry, self.config)
        self.adaptive_controller = AdaptiveController()

    def _user_id_for_chain(self, chain_id: str) -> str:
        chain = self.get_chain(chain_id)
        for event in self.store.events(chain.session_id):
            payload_chain_id = event.payload.get("chain_id") if isinstance(event.payload, Mapping) else None
            if payload_chain_id != chain_id and event.event_id != chain.anchor_event_id:
                continue
            user_id = event.user_id
            if user_id is None and isinstance(event.payload, Mapping):
                user_id = event.payload.get("user_id")
            if isinstance(user_id, str) and user_id.strip():
                return user_id.strip()
        return "ANONYMOUS"

    @staticmethod
    def _profile_version(profile: UserProfile) -> str:
        return "PROFILE-" + content_hash(profile.to_dict())[:20]

    def _make_profile(
        self,
        user_id: str,
        *,
        subjective_preference: UserPolicyPreference,
        assessed_need: UserAssessedNeed,
        effective_policy: UserPolicyPreference,
    ) -> UserProfile:
        draft = UserProfile(user_id, subjective_preference, assessed_need, effective_policy, version="PROFILE-PENDING")
        return replace(draft, version=self._profile_version(draft))

    def _profile_for_user(self, user_id: str) -> UserProfile:
        return self.store.get_user_profile(user_id) or UserProfile.default(user_id)

    def get_user_profile(self, user_id: str) -> dict[str, Any]:
        user_id = _str(user_id, "user_id")
        return self._profile_for_user(user_id).to_dict()

    def get_user_preferences(self, user_id: str) -> dict[str, Any]:
        user_id = _str(user_id, "user_id")
        return self._profile_for_user(user_id).effective_policy.to_dict()

    def set_user_preferences(
        self,
        user_id: str,
        *,
        frequency_preference: float,
        intensity_preference: float,
        mode: str = "AUTO",
        manual_lock: bool = False,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        user_id = _str(user_id, "user_id")
        raw = {
            "user_id": user_id,
            "frequency_preference": frequency_preference,
            "intensity_preference": intensity_preference,
            "mode": mode,
            "source": "USER_UI",
            "explicit": True,
            "manual_lock": manual_lock,
        }
        version = "PREF-" + content_hash(raw)[:20]
        preference = UserPolicyPreference(
            user_id=user_id,
            frequency_preference=frequency_preference,
            intensity_preference=intensity_preference,
            mode=str(mode).upper(),
            version=version,
            source="USER_UI",
            explicit=True,
            manual_lock=manual_lock,
        )
        previous_profile = self._profile_for_user(user_id)
        event = OnlineEvent(
            "EVT-PREFERENCE-" + version,
            session_id or "USER-" + user_id,
            OnlineEventType.POLICY_PREFERENCE_UPDATED,
            "USER",
            "RETRACE",
            _now(),
            EventSource.MCP_UI,
            payload={"user_id": user_id, "preference": preference.to_dict()},
            user_id=user_id,
        )
        stored, accepted = self.store.ingest(event)
        if accepted:
            profile = self._make_profile(
                user_id,
                subjective_preference=preference,
                assessed_need=previous_profile.assessed_need,
                effective_policy=preference,
            )
            self.store.save_user_profile(profile)
        return {"event_id": stored.event_id, "accepted": accepted, "preference": preference.to_dict()}

    def _preference_for_chain(self, chain_id: str, user_id: str | None = None) -> UserPolicyPreference:
        resolved_user_id = _str(user_id, "user_id") if user_id is not None else self._user_id_for_chain(chain_id)
        return self._profile_for_user(resolved_user_id).effective_policy

    def _effective_config(self, preference: UserPolicyPreference) -> SelectorConfigV2:
        if not preference.explicit:
            return self.config
        # The slider changes tolerance and cooldown within bounded ranges. It
        # never changes epsilon, evidence requirements, or the Registry.
        eta = max(0.0, min(1.0, self.config.eta + (0.5 - preference.frequency_preference) * 0.02))
        cooldown = max(30.0, self.config.same_chain_cooldown_seconds * (1.4 - 0.8 * preference.frequency_preference))
        return replace(self.config, eta=eta, same_chain_cooldown_seconds=cooldown)

    def _state_with_preference(self, state: ObserverState, preference: UserPolicyPreference) -> ObserverState:
        if not preference.explicit:
            return state
        preferred_cap = 1 if preference.intensity_preference < 1 / 3 else 2 if preference.intensity_preference < 2 / 3 else 3
        return replace(
            state,
            max_intensity=min(state.max_intensity, preferred_cap),
            user_preference_version=preference.version,
            user_preference_mode=preference.mode,
            frequency_preference=preference.frequency_preference,
            intensity_preference=preference.intensity_preference,
        )

    def _runtime_context(self, chain_id: str, *, user_id: str | None = None, as_of_time: datetime | None = None) -> tuple[UserPolicyPreference, SelectorConfigV2, ObserverState]:
        chain = self.get_chain(chain_id)
        preference = self._preference_for_chain(chain_id, user_id)
        config = self._effective_config(preference)
        state = self.observer.observe(chain, self.store.events(chain.session_id, chain_id=chain_id), config, now=as_of_time)
        return preference, config, self._state_with_preference(state, preference)

    def ingest_event(self, raw_event: Mapping[str, Any] | OnlineEvent) -> dict[str, Any]:
        event = raw_event if isinstance(raw_event, OnlineEvent) else OnlineEvent.from_dict(raw_event)
        preference_update = None
        if event.event_type == OnlineEventType.POLICY_PREFERENCE_UPDATED:
            if event.source != EventSource.MCP_UI:
                raise ValidationError("POLICY_PREFERENCE_UPDATED must come from MCP_UI")
        elif event.event_type == OnlineEventType.ADAPTATION_UPDATE:
            if event.source != EventSource.COLLECTOR:
                raise ValidationError("ADAPTATION_UPDATE must come from COLLECTOR")
        if event.event_type in {OnlineEventType.POLICY_PREFERENCE_UPDATED, OnlineEventType.ADAPTATION_UPDATE}:
            raw_preference = event.payload.get("preference")
            if not isinstance(raw_preference, Mapping):
                raise ValidationError(f"{event.event_type.value} requires a preference object")
            preference_update = UserPolicyPreference.from_dict(raw_preference)
        stored, accepted = self.store.ingest(event)
        if accepted and preference_update is not None:
            profile = self._profile_for_user(preference_update.user_id)
            if event.event_type == OnlineEventType.POLICY_PREFERENCE_UPDATED:
                profile = self._make_profile(
                    preference_update.user_id,
                    subjective_preference=preference_update,
                    assessed_need=profile.assessed_need,
                    effective_policy=preference_update,
                )
            else:
                raw_assessed_need = event.payload.get("assessed_need")
                assessed_need = (
                    UserAssessedNeed.from_dict(raw_assessed_need)
                    if isinstance(raw_assessed_need, Mapping)
                    else profile.assessed_need
                )
                profile = self._make_profile(
                    preference_update.user_id,
                    subjective_preference=profile.subjective_preference,
                    assessed_need=assessed_need,
                    effective_policy=preference_update,
                )
            self.store.save_user_profile(profile)
        late_audit_id = None
        if accepted and stored.is_late:
            audit = OnlineEvent(
                "LATE-AUDIT-" + stored.event_id,
                stored.session_id,
                OnlineEventType.LATE_EVENT,
                "SYSTEM",
                stored.project_id,
                _now(),
                EventSource.COLLECTOR,
                payload={"late_event_id": stored.event_id, "late_for_snapshot_id": stored.late_for_snapshot_id,
                         "chain_id": stored.payload.get("chain_id")},
            )
            self.store.ingest(audit)
            late_audit_id = audit.event_id
        chain_result = None
        status, _ = self.detector.detect(stored)
        if status == "OCCASION_CONFIRMED":
            chain_result = self._freeze_chain(stored)
        return {"event_id": stored.event_id, "collector_seq": stored.collector_seq, "accepted": accepted, "duplicate": not accepted, "is_late": stored.is_late, "late_event_id": stored.event_id if stored.is_late else None, "late_audit_event_id": late_audit_id, "watermark": stored.observed_at, "occasion": status, "chain": chain_result.to_dict() if chain_result else None}

    def _freeze_chain(self, event: OnlineEvent) -> DecisionChain:
        profile = self.target_builder.build(event)
        payload = event.payload
        occasion_id = _str(payload.get("occasion_id", "OCC-" + event.event_id), "occasion_id")
        focal_id = _str(payload.get("focal_decision_id", "FD-" + event.event_id), "focal_decision_id")
        chain_id = _str(payload.get("chain_id", f"{event.project_id}::{occasion_id}::{focal_id}"), "chain_id")
        existing = self.store.get_chain(chain_id)
        if existing:
            return existing
        chain = DecisionChain(chain_id, event.session_id, event.project_id, occasion_id, focal_id, profile.profile_id,
                              tuple(str(x) for x in payload.get("claim_ids", [])), profile.decision_object, event.event_id,
                              tuple(str(x) for x in payload.get("evidence_ids", [])), profile.target_state)
        self.store.save_chain(chain)
        return chain

    def get_chain(self, chain_id: str) -> DecisionChain:
        chain = self.store.get_chain(chain_id)
        if chain is None:
            raise ValidationError(f"unknown chain_id: {chain_id}")
        return chain

    def observe(self, chain_id: str, *, as_of_time: datetime | None = None, user_id: str | None = None) -> ObserverState:
        return self._runtime_context(chain_id, user_id=user_id, as_of_time=as_of_time)[2]

    def select(self, chain_id: str, *, as_of_event_id: str | None = None, as_of_time: datetime | None = None, user_id: str | None = None) -> dict[str, Any]:
        chain = self.get_chain(chain_id)
        preference, config, state = self._runtime_context(chain_id, user_id=user_id, as_of_time=as_of_time)
        selection = SkylineSelectorV2(self.registry, config).select(chain, state, as_of_event_id=as_of_event_id or self._latest_event_id(chain.session_id))
        self.store.save_selection(selection)
        self.store.save_chain(replace(chain, latest_selection_id=selection.decision_id))
        return selection.to_dict()

    def replay_trace(self, raw_events: Iterable[Mapping[str, Any] | OnlineEvent]) -> dict[str, Any]:
        """Replay a normalized trace with a deterministic event-time cutoff.

        This is intentionally separate from live ``select``: the replay clock
        is the trace's latest observed event time, so workflow burden and audit
        hashes do not depend on wall-clock time.
        """
        events = [item if isinstance(item, OnlineEvent) else OnlineEvent.from_dict(item) for item in raw_events]
        events = [item if item.received_at is not None else replace(item, received_at=item.observed_at) for item in events]
        ingested = [self.ingest_event(item) for item in events]
        chains = []
        selections = []
        for session_id in sorted({item.session_id for item in events}):
            session_events = self.store.events(session_id)
            observed_times = [datetime.fromisoformat(item.observed_at.replace("Z", "+00:00")) for item in session_events]
            cutoff = max(observed_times) if observed_times else datetime.now(timezone.utc)
            for chain in self.store.chains(session_id):
                chains.append(chain.to_dict())
                selections.append(self.select(chain.chain_id, as_of_event_id=session_events[-1].event_id if session_events else None, as_of_time=cutoff))
        replay = {"schema_version": "retrace-online-replay-v2", "events": ingested, "chains": chains, "selections": selections}
        replay["replay_hash"] = content_hash(replay)
        return replay

    def _latest_event_id(self, session_id: str) -> str | None:
        events = self.store.events(session_id)
        return events[-1].event_id if events else None

    def submit_occasion_baseline(self, chain_id: str, *, evaluation_id: str, responses: Mapping[str, Any] | None = None, skipped_dimensions: Iterable[str] = (), question_set_version: str = "CSA-LIKERT-V1", interaction_id: str | None = None, as_of_event_id: str | None = None, timeout: bool = False) -> dict[str, Any]:
        return self._submit_evaluation(chain_id, evaluation_id=evaluation_id, evaluation_kind="OCCASION_BASELINE", measurement_point="BASELINE", responses=responses or {}, skipped_dimensions=skipped_dimensions, question_set_version=question_set_version, interaction_id=interaction_id, as_of_event_id=as_of_event_id, timeout=timeout)

    def submit_observer_probe(self, chain_id: str, *, evaluation_id: str, responses: Mapping[str, Any] | None = None, skipped_dimensions: Iterable[str] = (), evidence_updates: Mapping[str, Any] | None = None, interaction_id: str | None = None, as_of_event_id: str | None = None) -> dict[str, Any]:
        return self._submit_evaluation(chain_id, evaluation_id=evaluation_id, evaluation_kind="OBSERVER_PROBE", measurement_point="PROBE", responses=responses or {}, skipped_dimensions=skipped_dimensions, question_set_version="CSA-PROBE-V1", interaction_id=interaction_id, as_of_event_id=as_of_event_id, timeout=False, observer_updates=evidence_updates or {})

    def _submit_evaluation(self, chain_id: str, *, evaluation_id: str, evaluation_kind: str, measurement_point: str, responses: Mapping[str, Any], skipped_dimensions: Iterable[str], question_set_version: str, interaction_id: str | None, as_of_event_id: str | None, timeout: bool = False, observer_updates: Mapping[str, Any] | None = None) -> dict[str, Any]:
        chain = self.get_chain(chain_id)
        skipped = sorted(set(str(item) for item in skipped_dimensions))
        if any(item not in _DIMENSIONS for item in skipped) or any(key not in _DIMENSIONS for key in responses):
            raise ValidationError("evaluation dimensions must be criteria, state, and action")
        if set(skipped) & set(responses):
            raise ValidationError("a dimension cannot be both answered and skipped")
        scale = str(question_set_version).upper()
        for key, value in responses.items():
            if evaluation_kind in {"OCCASION_BASELINE", "POST_EVALUATION"}:
                _score(value, f"responses.{key}", scale=scale)
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValidationError("evaluation responses must be finite numeric values or missing")
        payload = {"evaluation_id": evaluation_id, "evaluation_kind": evaluation_kind, "measurement_point": measurement_point, "chain_id": chain_id, "user_id": self._user_id_for_chain(chain_id), "responses": dict(responses), "skipped": skipped, "question_set_version": scale, "as_of_event_id": as_of_event_id, "interaction_id": interaction_id, "timeout": bool(timeout)}
        if observer_updates:
            payload["observer_updates"] = dict(observer_updates)
        self.store.save_evaluation(evaluation_id, chain_id, payload)
        event = OnlineEvent("EVT-RESP-" + evaluation_id, chain.session_id, OnlineEventType.USER_RESPONSE, "USER", chain.project_id, _now(), EventSource.MCP_UI, payload={**payload, "chain_id": chain_id, "response_kind": evaluation_kind})
        self.store.ingest(event)
        if evaluation_kind == "OCCASION_BASELINE":
            chain = replace(chain, status="OBSERVING", baseline_evaluation_id=evaluation_id)
            self.store.save_chain(chain)
        return {"event_ids": [event.event_id], "evaluation_id": evaluation_id, "measurement_point": evaluation_kind, "as_of_event_id": as_of_event_id, "accepted": True}

    def capture_measurement_snapshot(self, chain_id: str, measurement_point: str, *, as_of_event_id: str | None = None, trigger_event_id: str | None = None, reason: str = "", created_at: datetime | str | None = None) -> dict[str, Any]:
        point = _str(measurement_point, "measurement_point").upper()
        if point not in _MEASUREMENT_POINTS:
            raise ValidationError("measurement_point must be PRE, POST, or CLOSE")
        chain = self.get_chain(chain_id)
        state = self.observe(chain_id)
        snapshot_id = "SNAP-" + content_hash({"chain": chain_id, "point": point})[:20]
        payload = {"chain_id": chain_id, "measurement_point": point, "as_of_event_id": as_of_event_id or self._latest_event_id(chain.session_id), "trigger_event_id": trigger_event_id, "reason": reason, "current_state": state.to_dict(), "target_state": chain.target_state.to_dict(), "rubric_version": chain.target_state.rubric_version, "immutable": True}
        if isinstance(created_at, datetime):
            snapshot_created_at = created_at.isoformat().replace("+00:00", "Z")
        elif created_at is None:
            snapshot_created_at = None
        else:
            snapshot_created_at = _iso_key(created_at)
        self.store.save_snapshot(snapshot_id, chain_id, point, payload["as_of_event_id"], payload, created_at=snapshot_created_at)
        return {"snapshot_id": snapshot_id, **payload}

    def record_choice(self, chain_id: str, *, selection_decision_id: str, selected_candidate_id: str, choice_condition: str, choice_basis: str, observed_at: datetime | None = None) -> dict[str, Any]:
        chain = self.get_chain(chain_id)
        selection = self.store.get_selection(selection_decision_id)
        if not isinstance(selection, Mapping) or selection.get("chain_id") != chain_id:
            raise ValidationError("choice selection_decision_id does not identify this chain")
        if selection.get("decision") != "PRESENT_CHOICES":
            raise ValidationError("choice selection requires a PRESENT_CHOICES decision")
        if selected_candidate_id not in selection.get("selected", []):
            raise ValidationError("selected_candidate_id must be one of the presented options")
        choice_condition = _str(choice_condition, "choice_condition")
        choice_basis = _str(choice_basis, "choice_basis")
        selected_option = next(
            (
                option for option in selection.get("options", [])
                if isinstance(option, Mapping) and option.get("strategy_id") == selected_candidate_id
            ),
            None,
        )
        expected_condition = selected_option.get("branch_condition_code") if selected_option else None
        if expected_condition and choice_condition != expected_condition:
            raise ValidationError(
                "choice_condition must equal the frozen branch_condition_code for the selected option"
            )
        choice_id = "CHOICE-" + content_hash({"chain": chain_id, "selection": selection_decision_id, "candidate": selected_candidate_id})[:20]
        choice_time = (observed_at or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
        event = OnlineEvent(
            "EVT-CHOICE-" + choice_id,
            chain.session_id,
            OnlineEventType.USER_RESPONSE,
            "USER",
            chain.project_id,
            choice_time,
            EventSource.MCP_UI,
            payload={
                "chain_id": chain_id,
                "response_kind": "CHOICE_SELECTION",
                "choice_id": choice_id,
                "selection_decision_id": selection_decision_id,
                "selected_candidate_id": selected_candidate_id,
                "choice_condition": choice_condition,
                "choice_condition_code": choice_condition,
                "choice_condition_description": selected_option.get("branch_condition") if selected_option else None,
                "choice_basis": choice_basis,
            },
        )
        stored, accepted = self.store.ingest(event)
        return {
            "choice_id": choice_id,
            "event_id": stored.event_id,
            "selection_decision_id": selection_decision_id,
            "selected_candidate_id": selected_candidate_id,
            "choice_condition": choice_condition,
            "choice_condition_description": selected_option.get("branch_condition") if selected_option else None,
            "choice_basis": choice_basis,
            "accepted": accepted,
        }

    def expose(self, chain_id: str, *, exposure_id: str, interaction_id: str | None = None, selection_decision_id: str | None = None, selected_candidate_id: str | None = None, observed_at: datetime | None = None) -> dict[str, Any]:
        chain = self.get_chain(chain_id)
        if chain.baseline_evaluation_id is None:
            chain = replace(chain, baseline_missed=True)
        exposure_time = observed_at or datetime.now(timezone.utc)
        exposure_timestamp = exposure_time.isoformat().replace("+00:00", "Z")
        selection = self.store.get_selection(selection_decision_id) if selection_decision_id else None
        choice_event = None
        selected_ids = list(selection.get("selected", [])) if isinstance(selection, Mapping) else []
        if isinstance(selection, Mapping) and selection.get("decision") == "PRESENT_CHOICES":
            choice_events = [
                item for item in self.store.events(chain.session_id, chain_id=chain_id)
                if item.event_type is OnlineEventType.USER_RESPONSE
                and item.payload.get("response_kind") == "CHOICE_SELECTION"
                and item.payload.get("selection_decision_id") == selection_decision_id
            ]
            if not choice_events:
                raise ValidationError("PRESENT_CHOICES exposure requires an explicit CHOICE_SELECTION event")
            choice_event = choice_events[-1]
            chosen = choice_event.payload.get("selected_candidate_id")
            if selected_candidate_id is not None and selected_candidate_id != chosen:
                raise ValidationError("selected_candidate_id does not match the recorded CHOICE_SELECTION")
            selected_candidate_id = _str(chosen, "selected_candidate_id")
            selected_ids = [selected_candidate_id]
        elif selected_candidate_id is not None:
            if not isinstance(selection, Mapping) or selected_candidate_id not in selection.get("selected", []):
                raise ValidationError("selected_candidate_id must be the selected candidate of this decision")
            selected_ids = [selected_candidate_id]
        selected_specs = selection.get("options", []) if isinstance(selection, Mapping) else []
        if selected_candidate_id is not None:
            selected_specs = [
                item for item in selected_specs
                if isinstance(item, Mapping) and item.get("strategy_id") == selected_candidate_id
            ]
        selected_families = sorted({
            str(item.get("strategy_family"))
            for item in selected_specs
            if isinstance(item, Mapping) and item.get("strategy_family")
        })
        if not selected_families and isinstance(selection, Mapping) and selection.get("strategy_family"):
            selected_families = [str(selection["strategy_family"])]
        # Validate the complete branch contract before writing any PRE or
        # exposure record. A failed choice must not leave a partial snapshot.
        pre = self.capture_measurement_snapshot(
            chain_id,
            "PRE",
            reason="before_intervention_exposure",
            created_at=exposure_time,
        )
        payload = {"chain_id": chain_id, "exposure_id": exposure_id, "interaction_id": interaction_id, "selection_decision_id": selection_decision_id, "selected_candidate_id": selected_candidate_id, "choice_event_id": choice_event.event_id if choice_event else None, "response_kind": "INTERVENTION_EXPOSURE", "baseline_missed": chain.baseline_missed, "decision_object_profile_id": chain.decision_object_profile_id, "cooldown_scope": "CANDIDATE" if selected_ids else "CHAIN", "cooldown_candidate_ids": selected_ids, "cooldown_strategy_families": selected_families}
        missed_event_id = None
        if chain.baseline_missed:
            missed = OnlineEvent("EVT-MISSED-" + exposure_id, chain.session_id, OnlineEventType.BASELINE_MISSED, "SYSTEM", chain.project_id, exposure_timestamp, EventSource.COLLECTOR, payload={"chain_id": chain_id, "exposure_id": exposure_id, "reason": "exposure_before_baseline"})
            missed, _ = self.store.ingest(missed)
            missed_event_id = missed.event_id
        event = OnlineEvent("EVT-EXP-" + exposure_id, chain.session_id, OnlineEventType.INTERVENTION_EXPOSURE, "SYSTEM", chain.project_id, exposure_timestamp, EventSource.MCP_UI, payload=payload)
        stored, _ = self.store.ingest(event)
        chain = replace(chain, status="OBSERVING", exposure_id=exposure_id)
        self.store.save_chain(chain)
        return {"exposure_id": exposure_id, "event_id": stored.event_id, "selection_decision_id": selection_decision_id, "selected_candidate_id": selected_candidate_id, "choice_event_id": choice_event.event_id if choice_event else None, "baseline_missed": chain.baseline_missed, "baseline_missed_event_id": missed_event_id, "pre_snapshot_id": pre["snapshot_id"]}

    def record_action(self, chain_id: str, *, action: str, interaction_id: str | None = None, action_id: str | None = None, observed_at: datetime | None = None) -> dict[str, Any]:
        chain = self.get_chain(chain_id)
        action_id = action_id or ("ACT-" + content_hash({"chain": chain_id, "action": action, "time": _now()})[:20])
        action_time = (observed_at or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
        event = OnlineEvent("EVT-ACT-" + action_id, chain.session_id, OnlineEventType.INTERVENTION_ACTION, "USER", chain.project_id, action_time, EventSource.MCP_UI, payload={"chain_id": chain_id, "action_id": action_id, "action": _str(action, "action"), "interaction_id": interaction_id})
        stored, accepted = self.store.ingest(event)
        return {"event_id": stored.event_id, "action_id": action_id, "accepted": accepted}

    def complete_interaction(self, chain_id: str, *, evaluation_id: str | None = None) -> dict[str, Any]:
        chain = self.get_chain(chain_id)
        chain = replace(chain, status="EVALUATION_PENDING", evaluation_id=evaluation_id)
        self.store.save_chain(chain)
        return {"chain_id": chain_id, "status": chain.status, "evaluation_id": evaluation_id}

    def _adapt_after_post(self, chain_id: str, *, baseline_evaluation_id: str | None, post_evaluation_id: str, feedback: str, burden: float) -> dict[str, Any]:
        user_id = self._user_id_for_chain(chain_id)
        profile = self._profile_for_user(user_id)
        preference = profile.effective_policy
        baseline = self.store.get_evaluation(baseline_evaluation_id) if baseline_evaluation_id else None
        post = self.store.get_evaluation(post_evaluation_id)
        baseline_scores = baseline.get("responses", {}) if baseline else {}
        post_scores = post.get("responses", {}) if post else {}
        chain = self.get_chain(chain_id)
        completed_count = self.store.count_user_complete_chains(user_id)
        update = self.adaptive_controller.update(
            preference,
            pre_scores=baseline_scores,
            post_scores=post_scores,
            target_scores=chain.target_state.to_dict(),
            completed_chain_count=completed_count,
            feedback=feedback,
            burden=burden,
            pre_score_scale=str(baseline.get("question_set_version", "AUTO")) if baseline else "AUTO",
            post_score_scale=str(post.get("question_set_version", "AUTO")) if post else "AUTO",
        )
        new_preference = update.preference
        if update.changed:
            version = "PREF-" + content_hash({"user_id": user_id, **new_preference.to_dict(), "chain_id": chain_id})[:20]
            new_preference = replace(new_preference, version=version, source="ADAPTIVE", explicit=True)
        update_payload = update.to_dict()
        update_payload["preference"] = new_preference.to_dict()
        update_payload["baseline_evaluation_id"] = baseline_evaluation_id
        update_payload["post_evaluation_id"] = post_evaluation_id
        update_id = "ADAPT-" + content_hash({"chain_id": chain_id, "evaluation_id": post_evaluation_id, "preference": new_preference.to_dict(), "metrics": update.metrics})[:20]
        metrics = update.metrics
        assessed_need = UserAssessedNeed(
            user_id=user_id,
            frequency_need=metrics.get("frequency_need"),
            intensity_need=metrics.get("intensity_need"),
            csa_gap_after=metrics.get("gap_after"),
            csa_progress=metrics.get("progress"),
            completed_chain_count=int(metrics.get("completed_chain_count", 0)),
            last_reason=update.reason,
            last_update_id=update_id,
            last_feedback=metrics.get("feedback"),
            last_burden=metrics.get("burden"),
            version="NEED-" + content_hash({"user_id": user_id, "update_id": update_id, "metrics": dict(metrics)})[:20],
            source="ADAPTIVE",
        )
        update_payload["assessed_need"] = assessed_need.to_dict()
        event = OnlineEvent(
            "EVT-ADAPT-" + update_id,
            chain.session_id,
            OnlineEventType.ADAPTATION_UPDATE,
            "SYSTEM",
            chain.project_id,
            _now(),
            EventSource.COLLECTOR,
            payload={"update_id": update_id, "user_id": user_id, "chain_id": chain_id, "evaluation_id": post_evaluation_id, **update_payload},
        )
        stored, accepted = self.store.ingest(event)
        # Persist the derived profile only after its authoritative event has
        # been accepted or idempotently replayed. This keeps the durable
        # profile replayable from the event log instead of half-written.
        updated_profile = self._make_profile(
            user_id,
            subjective_preference=profile.subjective_preference,
            assessed_need=assessed_need,
            effective_policy=new_preference,
        )
        self.store.save_user_profile(updated_profile)
        self.store.save_adaptation_update(update_id, user_id, chain_id, update_payload)
        return {"update_id": update_id, "event_id": stored.event_id, "accepted": accepted, **update_payload}

    def submit_evaluation(
        self,
        chain_id: str,
        *,
        evaluation_id: str,
        responses: Mapping[str, Any] | None = None,
        skipped_dimensions: Iterable[str] = (),
        intervention_feedback: str = "UNSPECIFIED",
        burden_score: float = 0.0,
    ) -> dict[str, Any]:
        # Validate adaptation inputs before persisting the POST evaluation so
        # malformed UI payloads cannot leave a half-written result behind.
        intervention_feedback = self.adaptive_controller.validate_feedback(intervention_feedback)
        burden_score = self.adaptive_controller.validate_burden(burden_score)
        chain_before = self.get_chain(chain_id)
        result = self._submit_evaluation(chain_id, evaluation_id=evaluation_id, evaluation_kind="POST_EVALUATION", measurement_point="POST", responses=responses or {}, skipped_dimensions=skipped_dimensions, question_set_version="CSA-LIKERT-V1", interaction_id=None, as_of_event_id=self._latest_event_id(self.get_chain(chain_id).session_id))
        post = self.capture_measurement_snapshot(chain_id, "POST", as_of_event_id=result["as_of_event_id"], reason="post_evaluation")
        chain = self.get_chain(chain_id)
        self.store.save_chain(replace(chain, status="CLOSED", evaluation_id=evaluation_id))
        adaptation = self._adapt_after_post(
            chain_id,
            baseline_evaluation_id=chain_before.baseline_evaluation_id,
            post_evaluation_id=evaluation_id,
            feedback=intervention_feedback,
            burden=burden_score,
        )
        return {**result, "post_snapshot_id": post["snapshot_id"], "adaptation": adaptation}

    def get_retrace_state(self, chain_id: str, *, version: str = "v2") -> dict[str, Any]:
        if version != "v2":
            raise ValidationError("unsupported retrace state version")
        chain = self.get_chain(chain_id)
        user_id = self._user_id_for_chain(chain_id)
        return {"schema_version": "retrace-state-v2-online", "version": version, "chain": chain.to_dict(), "user_profile": self.get_user_profile(user_id), "current_state": self.observe(chain_id).to_dict(), "latest_selection": self.store.latest_selection(chain_id), "snapshots": self.store.snapshots(chain_id)}

    def get_chain_outcome_linkage(self, chain_id: str, *, as_of_event_id: str | None = None) -> dict[str, Any]:
        chain = self.get_chain(chain_id)
        snapshots = self.store.snapshots(chain_id)
        selection = self.store.latest_selection(chain_id) or {}
        options = selection.get("options", [])
        first = options[0] if len(options) == 1 else {}
        semantic_constraints = selection.get("objective", {}).get("semantic_constraints", {})
        selection_preference = {
            "version": semantic_constraints.get("user_preference_version", "PREF-DEFAULT"),
            "mode": semantic_constraints.get("user_preference_mode", "AUTO"),
            "frequency_preference": semantic_constraints.get("frequency_preference"),
            "intensity_preference": semantic_constraints.get("intensity_preference"),
        }
        adaptation = self.store.latest_adaptation_update(chain_id)
        profile = self._profile_for_user(self._user_id_for_chain(chain_id))
        return {"chain_id": chain.chain_id, "occasion_id": chain.occasion_id, "focal_decision_id": chain.focal_decision_id, "decision_object_profile_id": chain.decision_object_profile_id, "claim_ids": list(chain.claim_ids), "csa_measurements": {"occasion_baseline_evaluation_id": chain.baseline_evaluation_id, "pre_snapshot_id": snapshots.get("pre", {}).get("snapshot_id"), "post_snapshot_id": snapshots.get("post", {}).get("snapshot_id"), "close_snapshot_id": snapshots.get("close", {}).get("snapshot_id")}, "exposure": {"selection_decision_id": chain.latest_selection_id, "exposure_id": chain.exposure_id}, "strategy_id": first.get("strategy_id"), "strategy_family": first.get("strategy_family"), "template_id": first.get("template_id"), "action_codes": first.get("allowed_action_codes", []), "options": options, "policy_preference": selection_preference, "preference_used_for_selection": selection_preference, "current_policy_preference": profile.effective_policy.to_dict(), "adaptation_preference": adaptation.get("preference") if adaptation else None, "user_profile_version": profile.version, "subjective_preference": profile.subjective_preference.to_dict(), "assessed_need": profile.assessed_need.to_dict(), "effective_policy": profile.effective_policy.to_dict(), "adaptation_update_id": adaptation.get("update_id") if adaptation else None, "governance_outcome_ref": None, "functional_outcome_ref": None, "as_of_event_id": as_of_event_id or self._latest_event_id(chain.session_id), "linkage_status": "READY_FOR_OFFLINE_LINKAGE"}

    def apply_late_event(self, late_event_id: str) -> dict[str, Any]:
        event = next((item for item in self.store.events(self._session_for_event(late_event_id)) if item.event_id == late_event_id), None)
        if event is None or not event.is_late:
            raise ValidationError("late_event_id does not identify a late event")
        chain_id = event.payload.get("chain_id")
        if not isinstance(chain_id, str):
            return {"affected_chain_ids": [], "new_snapshot_ids": [], "preserved_snapshot_ids": [], "recompute_status": "NO_CHAIN"}
        chain = self.get_chain(chain_id)
        state = self.observe(chain_id)
        snapshot_id = "SNAP-REV-" + content_hash({"late_event": late_event_id, "chain": chain_id})[:20]
        payload = {"chain_id": chain_id, "measurement_point": "LATE_RECOMPUTE", "as_of_event_id": late_event_id, "current_state": state.to_dict(), "target_state": chain.target_state.to_dict(), "rubric_version": chain.target_state.rubric_version, "immutable": True}
        self.store.save_snapshot(snapshot_id, chain_id, "LATE_RECOMPUTE", late_event_id, payload)
        return {"affected_chain_ids": [chain_id], "new_snapshot_ids": [snapshot_id], "preserved_snapshot_ids": list(self.store.snapshots(chain_id)), "recompute_status": "COMPLETED"}

    def _session_for_event(self, event_id: str) -> str:
        with self.store._connection() as conn:
            row = conn.execute("SELECT session_id FROM online_events WHERE event_id=?", (event_id,)).fetchone()
        if not row:
            raise ValidationError(f"unknown event_id: {event_id}")
        return row["session_id"]
