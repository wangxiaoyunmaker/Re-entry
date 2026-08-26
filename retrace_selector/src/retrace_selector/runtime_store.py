"""Durable SQLite state for the v0.6 runtime integration layer."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from .config import canonical_json, content_hash
from .models import ValidationError
from .runtime_models import RuntimeEvent


SCHEMA_VERSION = 1
REACTION_EVENTS = {
    "USER_ACCEPTED",
    "USER_REJECTED",
    "USER_DISMISSED",
    "USER_SUPPLIED_INFO",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    recent_intervention_count: int
    active_verification: bool
    reset_at: str
    intervention_memory_seen: bool
    verification_memory_seen: bool


@dataclass(frozen=True)
class StoredRequest:
    request_id: str
    session_id: str
    request_hash: str
    response: dict[str, Any]
    interaction_id: str | None
    registry_hash: str
    policy_hash: str


class IdempotencyConflict(ValidationError):
    """The same idempotency key was reused with a different payload."""


class RuntimeStore:
    def __init__(self, path: str | Path, *, busy_timeout_seconds: float = 5.0):
        if (
            isinstance(busy_timeout_seconds, bool)
            or not isinstance(busy_timeout_seconds, (int, float))
            or busy_timeout_seconds <= 0
        ):
            raise ValidationError("busy_timeout_seconds must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_seconds = float(busy_timeout_seconds)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            f"PRAGMA busy_timeout = {int(self.busy_timeout_seconds * 1000)}"
        )
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            current_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if current_version > SCHEMA_VERSION:
                raise ValidationError(
                    f"runtime database schema {current_version} is newer than supported {SCHEMA_VERSION}"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_sessions (
                    session_id TEXT PRIMARY KEY,
                    active_verification INTEGER NOT NULL DEFAULT 0 CHECK (active_verification IN (0, 1)),
                    reset_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_requests (
                    request_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id),
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    interaction_id TEXT,
                    registry_hash TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_runtime_requests_session
                    ON runtime_requests(session_id, completed_at);

                CREATE TABLE IF NOT EXISTS runtime_interactions (
                    interaction_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE REFERENCES runtime_requests(request_id),
                    session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id),
                    selected_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('SELECTED', 'PRESENTED')),
                    selected_at TEXT NOT NULL,
                    presented_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_runtime_interactions_recent
                    ON runtime_interactions(session_id, presented_at);

                CREATE TABLE IF NOT EXISTS runtime_events (
                    event_id TEXT PRIMARY KEY,
                    event_hash TEXT NOT NULL,
                    session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id),
                    interaction_id TEXT REFERENCES runtime_interactions(interaction_id),
                    event_type TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_runtime_events_session
                    ON runtime_events(session_id, recorded_at);

                CREATE TABLE IF NOT EXISTS runtime_diagnostics (
                    diagnostic_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    request_id TEXT,
                    code TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                """
            )
            if current_version < SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        try:
            self.path.chmod(0o600)
        except OSError as exc:
            raise ValidationError(
                "runtime database permissions could not be restricted to the current user"
            ) from exc

    @staticmethod
    def _ensure_session(
        connection: sqlite3.Connection,
        session_id: str,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO runtime_sessions(
                session_id, active_verification, reset_at, created_at, updated_at
            ) VALUES (?, 0, ?, ?, ?)
            ON CONFLICT(session_id) DO NOTHING
            """,
            (session_id, now, now, now),
        )

    def session_snapshot(self, session_id: str) -> SessionSnapshot:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_session(connection, session_id, now)
            row = connection.execute(
                """
                SELECT session_id, active_verification, reset_at
                FROM runtime_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            count = connection.execute(
                """
                SELECT COUNT(*)
                FROM runtime_interactions
                WHERE session_id = ?
                  AND presented_at IS NOT NULL
                  AND presented_at >= ?
                """,
                (session_id, row["reset_at"]),
            ).fetchone()[0]
            memory = connection.execute(
                """
                SELECT
                    MAX(CASE WHEN event_type IN (
                        'INTERVENTION_PRESENTED', 'SESSION_RESET'
                    ) THEN 1 ELSE 0 END) AS intervention_memory_seen,
                    MAX(CASE WHEN event_type IN (
                        'VERIFICATION_STARTED', 'VERIFICATION_COMPLETED', 'SESSION_RESET'
                    ) THEN 1 ELSE 0 END) AS verification_memory_seen
                FROM runtime_events
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            connection.commit()
        return SessionSnapshot(
            session_id=session_id,
            recent_intervention_count=int(count),
            active_verification=bool(row["active_verification"]),
            reset_at=row["reset_at"],
            intervention_memory_seen=bool(memory["intervention_memory_seen"]),
            verification_memory_seen=bool(memory["verification_memory_seen"]),
        )

    def get_request(self, request_id: str) -> StoredRequest | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT request_id, session_id, request_hash, response_json,
                       interaction_id, registry_hash, policy_hash
                FROM runtime_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            response = json.loads(row["response_json"])
        except json.JSONDecodeError as exc:
            raise ValidationError("stored runtime response is not valid JSON") from exc
        if not isinstance(response, dict):
            raise ValidationError("stored runtime response must be an object")
        return StoredRequest(
            request_id=row["request_id"],
            session_id=row["session_id"],
            request_hash=row["request_hash"],
            response=response,
            interaction_id=row["interaction_id"],
            registry_hash=row["registry_hash"],
            policy_hash=row["policy_hash"],
        )

    def save_request(
        self,
        *,
        request_id: str,
        session_id: str,
        request_hash: str,
        response: dict[str, Any],
        outcome: str,
        registry_hash: str,
        policy_hash: str,
        interaction_id: str | None,
        selected_ids: tuple[str, ...] = (),
    ) -> bool:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_session(connection, session_id, now)
            existing = connection.execute(
                "SELECT request_hash FROM runtime_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                if existing["request_hash"] == request_hash:
                    return False
                raise IdempotencyConflict(
                    f"request_id {request_id} was reused with a different payload"
                )
            connection.execute(
                """
                INSERT INTO runtime_requests(
                    request_id, session_id, request_hash, response_json, outcome,
                    interaction_id, registry_hash, policy_hash, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    session_id,
                    request_hash,
                    canonical_json(response),
                    outcome,
                    interaction_id,
                    registry_hash,
                    policy_hash,
                    now,
                    now,
                ),
            )
            if interaction_id is not None:
                connection.execute(
                    """
                    INSERT INTO runtime_interactions(
                        interaction_id, request_id, session_id, selected_ids_json,
                        status, selected_at, presented_at
                    ) VALUES (?, ?, ?, ?, 'SELECTED', ?, NULL)
                    """,
                    (
                        interaction_id,
                        request_id,
                        session_id,
                        canonical_json(list(selected_ids)),
                        now,
                    ),
                )
            connection.commit()
        return True

    def record_event(self, event: RuntimeEvent) -> bool:
        now = _utc_now()
        event_hash = content_hash(event.to_dict())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_session(connection, event.session_id, now)
            existing = connection.execute(
                "SELECT event_hash FROM runtime_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                if existing["event_hash"] == event_hash:
                    return False
                raise IdempotencyConflict(
                    f"event_id {event.event_id} was reused with a different payload"
                )

            interaction = None
            if event.interaction_id is not None:
                interaction = connection.execute(
                    """
                    SELECT interaction_id, session_id, status
                    FROM runtime_interactions
                    WHERE interaction_id = ?
                    """,
                    (event.interaction_id,),
                ).fetchone()
                if interaction is None:
                    connection.rollback()
                    raise ValidationError("runtime event references an unknown interaction_id")
                if interaction["session_id"] != event.session_id:
                    connection.rollback()
                    raise ValidationError("runtime event interaction belongs to another session")
                if event.event_type in REACTION_EVENTS and interaction["status"] != "PRESENTED":
                    connection.rollback()
                    raise ValidationError("user reaction requires a presented interaction")

            if event.event_type == "INTERVENTION_PRESENTED":
                connection.execute(
                    """
                    UPDATE runtime_interactions
                    SET status = 'PRESENTED', presented_at = COALESCE(presented_at, ?)
                    WHERE interaction_id = ?
                    """,
                    (now, event.interaction_id),
                )
            elif event.event_type == "VERIFICATION_STARTED":
                connection.execute(
                    """
                    UPDATE runtime_sessions
                    SET active_verification = 1, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (now, event.session_id),
                )
            elif event.event_type == "VERIFICATION_COMPLETED":
                connection.execute(
                    """
                    UPDATE runtime_sessions
                    SET active_verification = 0, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (now, event.session_id),
                )
            elif event.event_type == "SESSION_RESET":
                connection.execute(
                    """
                    UPDATE runtime_sessions
                    SET active_verification = 0, reset_at = ?, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (now, now, event.session_id),
                )

            connection.execute(
                """
                INSERT INTO runtime_events(
                    event_id, event_hash, session_id, interaction_id,
                    event_type, metadata_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event_hash,
                    event.session_id,
                    event.interaction_id,
                    event.event_type,
                    canonical_json(dict(event.metadata)),
                    now,
                ),
            )
            connection.commit()
        return True

    def record_diagnostic(
        self,
        *,
        session_id: str,
        request_id: str | None,
        code: str,
        detail: str,
    ) -> None:
        payload = {
            "session_id": session_id,
            "request_id": request_id,
            "code": code,
            "detail": detail,
        }
        diagnostic_id = content_hash(payload)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO runtime_diagnostics(
                    diagnostic_id, session_id, request_id, code, detail, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(diagnostic_id) DO NOTHING
                """,
                (diagnostic_id, session_id, request_id, code, detail, _utc_now()),
            )

    def session_history(self, session_id: str, *, limit: int = 100) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValidationError("history limit must be an integer in [1, 1000]")
        snapshot = self.session_snapshot(session_id)
        with self._connection() as connection:
            request_rows = connection.execute(
                """
                SELECT request_id, response_json, outcome, interaction_id,
                       registry_hash, policy_hash, completed_at
                FROM runtime_requests
                WHERE session_id = ?
                ORDER BY completed_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
            event_rows = connection.execute(
                """
                SELECT event_id, interaction_id, event_type, metadata_json, recorded_at
                FROM runtime_events
                WHERE session_id = ?
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
            diagnostic_rows = connection.execute(
                """
                SELECT diagnostic_id, request_id, code, detail, recorded_at
                FROM runtime_diagnostics
                WHERE session_id = ?
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return {
            "session": {
                "session_id": snapshot.session_id,
                "recent_intervention_count": snapshot.recent_intervention_count,
                "active_verification": snapshot.active_verification,
                "reset_at": snapshot.reset_at,
            },
            "requests": [
                {
                    "request_id": row["request_id"],
                    "outcome": row["outcome"],
                    "interaction_id": row["interaction_id"],
                    "registry_hash": row["registry_hash"],
                    "policy_hash": row["policy_hash"],
                    "completed_at": row["completed_at"],
                    "response": json.loads(row["response_json"]),
                }
                for row in request_rows
            ],
            "events": [
                {
                    "event_id": row["event_id"],
                    "interaction_id": row["interaction_id"],
                    "event_type": row["event_type"],
                    "metadata": json.loads(row["metadata_json"]),
                    "recorded_at": row["recorded_at"],
                }
                for row in event_rows
            ],
            "diagnostics": [dict(row) for row in diagnostic_rows],
        }
