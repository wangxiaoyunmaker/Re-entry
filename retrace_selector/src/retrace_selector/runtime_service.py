"""Real-time, persistent integration boundary for the v0.6 selector.

The deterministic selector remains side-effect free.  This layer owns runtime
memory, idempotency, configuration pinning, timeout handling, and interaction
acknowledgements from the host UI or agent.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import replace
import hashlib
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from .config import content_hash
from .models import Outcome, ValidationError
from .runtime_models import (
    EXECUTION_MODES,
    RUNTIME_RESPONSE_SCHEMA,
    RuntimeEvent,
    RuntimeSelectionRequest,
)
from .runtime_store import IdempotencyConflict, RuntimeStore
from .selector_v06 import V06SelectionEngine
from .state_adapter import ClarificationRequired, adapt_state
from .strategy_registry import (
    StrategyRegistry,
    load_selection_policy,
    load_strategy_registry,
)
from .v06_models import SelectionPolicy, SelectorDecisionState
from .version import V06_ENGINE_VERSION


def _explicitly_safe(raw_state: Mapping[str, Any]) -> bool:
    """Return True only when the raw state explicitly establishes safe continuation."""

    if "risk_level" in raw_state or "authorization_required" in raw_state:
        risk = str(raw_state.get("risk_level", "")).upper()
        authorization = raw_state.get("authorization_required")
        return risk in {"LOW", "MEDIUM"} and authorization is False
    consequence = str(raw_state.get("consequence", "")).lower()
    reversibility = str(raw_state.get("reversibility", "")).lower()
    authorization = str(raw_state.get("authorization_risk", "")).lower()
    if consequence not in {"low", "medium", "high"}:
        return False
    if reversibility not in {"low", "medium", "high"}:
        return False
    if authorization not in {"low", "medium", "high"}:
        return False
    return authorization != "high" and not (
        consequence == "high" and reversibility == "low"
    )


def _live_registry_ready(registry: StrategyRegistry) -> bool:
    return registry.registry_status == "APPROVED" and not any(
        strategy_id.startswith("TEST_") for strategy_id in registry.catalog
    )


class RuntimeSelectorService:
    """Call the v0.6 selector from an Observer/Skill without intermediate files."""

    def __init__(
        self,
        *,
        store: RuntimeStore,
        registry: StrategyRegistry,
        policy: SelectionPolicy,
        policy_hash: str | None = None,
        execution_mode: str = "SHADOW",
        selection_timeout_seconds: float = 2.0,
    ):
        mode = str(execution_mode).upper()
        if mode not in EXECUTION_MODES:
            raise ValidationError(f"execution_mode must be one of {sorted(EXECUTION_MODES)}")
        if (
            isinstance(selection_timeout_seconds, bool)
            or not isinstance(selection_timeout_seconds, (int, float))
            or selection_timeout_seconds <= 0
        ):
            raise ValidationError("selection_timeout_seconds must be positive")
        self.store = store
        self.execution_mode = mode
        self.selection_timeout_seconds = float(selection_timeout_seconds)
        self._configuration_lock = RLock()
        self._install_configuration(registry, policy, policy_hash=policy_hash)

    @classmethod
    def from_paths(
        cls,
        *,
        database_path: str | Path,
        registry_path: str | Path,
        policy_path: str | Path,
        execution_mode: str = "SHADOW",
        selection_timeout_seconds: float = 2.0,
    ) -> "RuntimeSelectorService":
        policy, policy_hash = load_selection_policy(policy_path)
        registry = load_strategy_registry(registry_path)
        return cls(
            store=RuntimeStore(database_path),
            registry=registry,
            policy=policy,
            policy_hash=policy_hash,
            execution_mode=execution_mode,
            selection_timeout_seconds=selection_timeout_seconds,
        )

    def _install_configuration(
        self,
        registry: StrategyRegistry,
        policy: SelectionPolicy,
        *,
        policy_hash: str | None,
    ) -> None:
        engine = V06SelectionEngine(registry, policy, policy_hash=policy_hash)
        with self._configuration_lock:
            self.registry = registry
            self.policy = policy
            self.engine = engine

    def reload_configuration(
        self,
        *,
        registry_path: str | Path,
        policy_path: str | Path,
    ) -> dict[str, Any]:
        """Atomically switch future requests to a newly validated configuration."""

        policy, policy_hash = load_selection_policy(policy_path)
        registry = load_strategy_registry(registry_path)
        engine = V06SelectionEngine(registry, policy, policy_hash=policy_hash)
        with self._configuration_lock:
            previous = {
                "registry_hash": self.registry.config_hash,
                "policy_hash": self.engine.policy_hash,
            }
            self.registry = registry
            self.policy = policy
            self.engine = engine
        return {
            "previous": previous,
            "current": {
                "registry_hash": registry.config_hash,
                "policy_hash": engine.policy_hash,
                "registry_version": registry.registry_version,
                "registry_status": registry.registry_status,
            },
        }

    def _configuration_snapshot(
        self,
    ) -> tuple[V06SelectionEngine, StrategyRegistry, str]:
        with self._configuration_lock:
            return self.engine, self.registry, self.engine.policy_hash

    @staticmethod
    def _request_hash(request: RuntimeSelectionRequest) -> str:
        try:
            return content_hash(request.to_dict())
        except (TypeError, ValueError) as exc:
            raise ValidationError("runtime request must contain JSON-serializable values") from exc

    def _runtime_metadata(
        self,
        *,
        registry: StrategyRegistry,
        policy_hash: str,
        request_hash: str,
        persisted: bool,
    ) -> dict[str, Any]:
        return {
            "engine_version": V06_ENGINE_VERSION,
            "execution_mode": self.execution_mode,
            "request_hash": request_hash,
            "registry_version": registry.registry_version,
            "registry_status": registry.registry_status,
            "registry_hash": registry.config_hash,
            "policy_hash": policy_hash,
            "persisted": persisted,
        }

    def _fallback_response(
        self,
        request: RuntimeSelectionRequest,
        *,
        registry: StrategyRegistry,
        policy_hash: str,
        request_hash: str,
        reason_code: str,
        message: str,
        outcome: str = "SAFE_HOLD",
        effective_state: SelectorDecisionState | None = None,
        persisted: bool = True,
    ) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_RESPONSE_SCHEMA,
            "request_id": request.request_id,
            "session_id": request.session_id,
            "status": "FALLBACK",
            "idempotent_replay": False,
            "effective_state": effective_state.to_dict() if effective_state else None,
            "result": None,
            "fallback": {
                "outcome": outcome,
                "reason_codes": [reason_code],
                "message": message,
            },
            "interaction_id": None,
            "runtime": self._runtime_metadata(
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                persisted=persisted,
            ),
        }

    def _persist_or_fail_closed(
        self,
        request: RuntimeSelectionRequest,
        *,
        request_hash: str,
        response: dict[str, Any],
        registry: StrategyRegistry,
        policy_hash: str,
        outcome: str,
        interaction_id: str | None,
        selected_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        try:
            saved = self.store.save_request(
                request_id=request.request_id,
                session_id=request.session_id,
                request_hash=request_hash,
                response=response,
                outcome=outcome,
                registry_hash=registry.config_hash,
                policy_hash=policy_hash,
                interaction_id=interaction_id,
                selected_ids=selected_ids,
            )
        except IdempotencyConflict as exc:
            self._record_diagnostic_best_effort(
                request=request,
                code="IDEMPOTENCY_CONFLICT",
                detail=str(exc),
            )
            return self._fallback_response(
                request,
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                reason_code="IDEMPOTENCY_CONFLICT",
                message="The request id was already used for another state; no decision was released.",
                persisted=False,
            )
        except Exception as exc:  # SQLite or filesystem failures must not release an intervention.
            return self._fallback_response(
                request,
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                reason_code="PERSISTENCE_FAILURE",
                message=f"Runtime audit persistence failed ({type(exc).__name__}); no decision was released.",
                persisted=False,
            )
        if not saved:
            try:
                stored = self.store.get_request(request.request_id)
            except Exception as exc:
                return self._fallback_response(
                    request,
                    registry=registry,
                    policy_hash=policy_hash,
                    request_hash=request_hash,
                    reason_code="PERSISTENCE_FAILURE",
                    message=f"Stored idempotent result could not be read ({type(exc).__name__}); no decision was released.",
                    persisted=False,
                )
            if stored is not None and stored.request_hash == request_hash:
                replay = dict(stored.response)
                replay["idempotent_replay"] = True
                return replay
        return response

    def _record_diagnostic_best_effort(
        self,
        *,
        request: RuntimeSelectionRequest,
        code: str,
        detail: str,
    ) -> None:
        try:
            self.store.record_diagnostic(
                session_id=request.session_id,
                request_id=request.request_id,
                code=code,
                detail=detail,
            )
        except Exception:
            # The caller already receives persisted=false; diagnostics cannot
            # be made durable when the persistence layer itself is unavailable.
            return

    def _persist_fallback(
        self,
        request: RuntimeSelectionRequest,
        *,
        registry: StrategyRegistry,
        policy_hash: str,
        request_hash: str,
        reason_code: str,
        message: str,
        outcome: str = "SAFE_HOLD",
        effective_state: SelectorDecisionState | None = None,
    ) -> dict[str, Any]:
        response = self._fallback_response(
            request,
            registry=registry,
            policy_hash=policy_hash,
            request_hash=request_hash,
            reason_code=reason_code,
            message=message,
            outcome=outcome,
            effective_state=effective_state,
        )
        return self._persist_or_fail_closed(
            request,
            request_hash=request_hash,
            response=response,
            registry=registry,
            policy_hash=policy_hash,
            outcome=outcome,
            interaction_id=None,
        )

    def select(
        self,
        raw_request: RuntimeSelectionRequest | Mapping[str, Any],
    ) -> dict[str, Any]:
        request = RuntimeSelectionRequest.from_dict(
            raw_request.to_dict()
            if isinstance(raw_request, RuntimeSelectionRequest)
            else raw_request
        )
        request_hash = self._request_hash(request)
        engine, registry, policy_hash = self._configuration_snapshot()

        try:
            stored = self.store.get_request(request.request_id)
        except Exception as exc:
            return self._fallback_response(
                request,
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                reason_code="PERSISTENCE_FAILURE",
                message=f"Runtime audit persistence is unavailable ({type(exc).__name__}); no decision was released.",
                persisted=False,
            )
        if stored is not None:
            if stored.request_hash == request_hash:
                replay = dict(stored.response)
                replay["idempotent_replay"] = True
                return replay
            detail = f"request_id {request.request_id} was reused with a different payload"
            self._record_diagnostic_best_effort(
                request=request,
                code="IDEMPOTENCY_CONFLICT",
                detail=detail,
            )
            return self._fallback_response(
                request,
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                reason_code="IDEMPOTENCY_CONFLICT",
                message="The request id was already used for another state; no decision was released.",
                persisted=False,
            )

        if self.execution_mode == "LIVE" and not _live_registry_ready(registry):
            return self._persist_fallback(
                request,
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                reason_code="REGISTRY_NOT_APPROVED",
                message="LIVE mode requires an APPROVED strategy registry.",
            )
        if (
            request.expected_registry_hash is not None
            and request.expected_registry_hash != registry.config_hash
        ) or (
            request.expected_policy_hash is not None
            and request.expected_policy_hash != policy_hash
        ):
            return self._persist_fallback(
                request,
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                reason_code="CONFIG_VERSION_MISMATCH",
                message="The pinned policy or registry does not match the active configuration.",
            )

        try:
            snapshot = self.store.session_snapshot(request.session_id)
        except Exception as exc:
            return self._fallback_response(
                request,
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                reason_code="PERSISTENCE_FAILURE",
                message=f"Runtime session memory is unavailable ({type(exc).__name__}); no decision was released.",
                persisted=False,
            )
        try:
            projected = adapt_state(request.state)
            effective_state = replace(
                projected,
                recent_intervention_count=(
                    snapshot.recent_intervention_count
                    if snapshot.intervention_memory_seen
                    else projected.recent_intervention_count
                ),
                active_verification=(
                    snapshot.active_verification
                    if snapshot.verification_memory_seen
                    else projected.active_verification
                ),
            )
        except ClarificationRequired as exc:
            safe = _explicitly_safe(request.state)
            return self._persist_fallback(
                request,
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                reason_code="STATE_ABSTAIN",
                message=str(exc),
                outcome="REQUEST_CLARIFICATION" if safe else "SAFE_HOLD",
            )
        except ValidationError as exc:
            safe = _explicitly_safe(request.state)
            return self._persist_fallback(
                request,
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                reason_code="STATE_INVALID",
                message=str(exc),
                outcome="REQUEST_CLARIFICATION" if safe else "SAFE_HOLD",
            )

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="retrace-v06")
        future = executor.submit(engine.select, effective_state)
        try:
            result = future.result(timeout=self.selection_timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            return self._persist_fallback(
                request,
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                reason_code="SELECTION_TIMEOUT",
                message="Selector deadline exceeded; no intervention was released.",
                effective_state=effective_state,
            )
        except Exception as exc:
            executor.shutdown(wait=True, cancel_futures=True)
            return self._persist_fallback(
                request,
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                reason_code="SELECTION_FAILURE",
                message=f"Selector failed ({type(exc).__name__}); no intervention was released.",
                effective_state=effective_state,
            )
        else:
            executor.shutdown(wait=True)

        result_dict = result.to_dict()
        interaction_id = None
        if self.execution_mode == "LIVE" and result.outcome in {
            Outcome.INTERVENE,
            Outcome.PRESENT_CHOICES,
        }:
            seed = f"{request.request_id}:{result.audit_id}"
            interaction_id = "INT-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
        response = {
            "schema_version": RUNTIME_RESPONSE_SCHEMA,
            "request_id": request.request_id,
            "session_id": request.session_id,
            "status": "COMPLETED",
            "idempotent_replay": False,
            "effective_state": effective_state.to_dict(),
            "result": result_dict,
            "fallback": None,
            "interaction_id": interaction_id,
            "runtime": self._runtime_metadata(
                registry=registry,
                policy_hash=policy_hash,
                request_hash=request_hash,
                persisted=True,
            ),
        }
        return self._persist_or_fail_closed(
            request,
            request_hash=request_hash,
            response=response,
            registry=registry,
            policy_hash=policy_hash,
            outcome=result.outcome.value,
            interaction_id=interaction_id,
            selected_ids=result.selected_ids,
        )

    def record_event(self, raw_event: RuntimeEvent | Mapping[str, Any]) -> dict[str, Any]:
        event = RuntimeEvent.from_dict(
            raw_event.to_dict() if isinstance(raw_event, RuntimeEvent) else raw_event
        )
        try:
            recorded = self.store.record_event(event)
            snapshot = self.store.session_snapshot(event.session_id)
        except ValidationError:
            raise
        except Exception as exc:
            return {
                "schema_version": "retrace-runtime-event-response-v0.6",
                "event_id": event.event_id,
                "recorded": False,
                "idempotent_replay": False,
                "error": {
                    "code": "PERSISTENCE_FAILURE",
                    "message": f"Runtime event persistence failed ({type(exc).__name__}).",
                },
                "session": None,
            }
        return {
            "schema_version": "retrace-runtime-event-response-v0.6",
            "event_id": event.event_id,
            "recorded": recorded,
            "idempotent_replay": not recorded,
            "session": {
                "session_id": snapshot.session_id,
                "recent_intervention_count": snapshot.recent_intervention_count,
                "active_verification": snapshot.active_verification,
                "reset_at": snapshot.reset_at,
            },
        }

    def session_history(self, session_id: str, *, limit: int = 100) -> dict[str, Any]:
        return self.store.session_history(session_id, limit=limit)

    def health(self) -> dict[str, Any]:
        _, registry, policy_hash = self._configuration_snapshot()
        live_ready = _live_registry_ready(registry)
        return {
            "status": "READY" if self.execution_mode == "SHADOW" or live_ready else "NOT_READY",
            "execution_mode": self.execution_mode,
            "live_ready": live_ready,
            "registry_version": registry.registry_version,
            "registry_status": registry.registry_status,
            "registry_hash": registry.config_hash,
            "policy_hash": policy_hash,
            "engine_version": V06_ENGINE_VERSION,
        }
