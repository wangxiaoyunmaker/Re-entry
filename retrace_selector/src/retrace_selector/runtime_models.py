"""Runtime boundary contracts around the deterministic v0.6 selector."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Mapping

from .models import ValidationError


RUNTIME_REQUEST_SCHEMA = "retrace-runtime-request-v0.6"
RUNTIME_EVENT_SCHEMA = "retrace-runtime-event-v0.6"
RUNTIME_RESPONSE_SCHEMA = "retrace-runtime-response-v0.6"

EXECUTION_MODES = {"SHADOW", "LIVE"}
EVENT_TYPES = {
    "INTERVENTION_PRESENTED",
    "USER_ACCEPTED",
    "USER_REJECTED",
    "USER_DISMISSED",
    "USER_SUPPLIED_INFO",
    "VERIFICATION_STARTED",
    "VERIFICATION_COMPLETED",
    "SESSION_RESET",
}
INTERACTION_EVENT_TYPES = {
    "INTERVENTION_PRESENTED",
    "USER_ACCEPTED",
    "USER_REJECTED",
    "USER_DISMISSED",
    "USER_SUPPLIED_INFO",
}


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_hash(value: Any, name: str) -> str | None:
    if value is None:
        return None
    result = _nonempty(value, name).lower()
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValidationError(f"{name} must be a SHA-256 hex digest")
    return result


@dataclass(frozen=True)
class RuntimeSelectionRequest:
    request_id: str
    session_id: str
    state: Mapping[str, Any]
    expected_registry_hash: str | None = None
    expected_policy_hash: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RuntimeSelectionRequest":
        if not isinstance(raw, Mapping):
            raise ValidationError("runtime request must be an object")
        required = {"schema_version", "request_id", "session_id", "state"}
        optional = {"expected_registry_hash", "expected_policy_hash"}
        missing = required - set(raw)
        unknown = set(raw) - required - optional
        if missing:
            raise ValidationError(f"runtime request missing fields: {sorted(missing)}")
        if unknown:
            raise ValidationError(f"runtime request unknown fields: {sorted(unknown)}")
        if raw["schema_version"] != RUNTIME_REQUEST_SCHEMA:
            raise ValidationError("unsupported runtime request schema")
        if not isinstance(raw["state"], Mapping):
            raise ValidationError("runtime request state must be an object")
        try:
            state = json.loads(
                json.dumps(raw["state"], ensure_ascii=False, sort_keys=True)
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "runtime request state must contain JSON-serializable values"
            ) from exc
        return cls(
            request_id=_nonempty(raw["request_id"], "request_id"),
            session_id=_nonempty(raw["session_id"], "session_id"),
            state=state,
            expected_registry_hash=_optional_hash(
                raw.get("expected_registry_hash"), "expected_registry_hash"
            ),
            expected_policy_hash=_optional_hash(
                raw.get("expected_policy_hash"), "expected_policy_hash"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": RUNTIME_REQUEST_SCHEMA,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "state": dict(self.state),
        }
        if self.expected_registry_hash is not None:
            result["expected_registry_hash"] = self.expected_registry_hash
        if self.expected_policy_hash is not None:
            result["expected_policy_hash"] = self.expected_policy_hash
        return result


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    session_id: str
    event_type: str
    interaction_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RuntimeEvent":
        if not isinstance(raw, Mapping):
            raise ValidationError("runtime event must be an object")
        required = {"schema_version", "event_id", "session_id", "event_type"}
        optional = {"interaction_id", "metadata"}
        missing = required - set(raw)
        unknown = set(raw) - required - optional
        if missing:
            raise ValidationError(f"runtime event missing fields: {sorted(missing)}")
        if unknown:
            raise ValidationError(f"runtime event unknown fields: {sorted(unknown)}")
        if raw["schema_version"] != RUNTIME_EVENT_SCHEMA:
            raise ValidationError("unsupported runtime event schema")
        event_type = _nonempty(raw["event_type"], "event_type").upper()
        if event_type not in EVENT_TYPES:
            raise ValidationError(f"unsupported runtime event_type: {event_type}")
        interaction_id = raw.get("interaction_id")
        if event_type in INTERACTION_EVENT_TYPES:
            interaction_id = _nonempty(interaction_id, "interaction_id")
        elif interaction_id is not None:
            interaction_id = _nonempty(interaction_id, "interaction_id")
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValidationError("runtime event metadata must be an object")
        if any(not isinstance(key, str) for key in metadata):
            raise ValidationError("runtime event metadata keys must be strings")
        try:
            metadata_copy = json.loads(
                json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "runtime event metadata must contain JSON-serializable values"
            ) from exc
        return cls(
            event_id=_nonempty(raw["event_id"], "event_id"),
            session_id=_nonempty(raw["session_id"], "session_id"),
            event_type=event_type,
            interaction_id=interaction_id,
            metadata=metadata_copy,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": RUNTIME_EVENT_SCHEMA,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "metadata": dict(self.metadata),
        }
        if self.interaction_id is not None:
            result["interaction_id"] = self.interaction_id
        return result
