from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import MappingProxyType
import unittest

from retrace_selector.config import content_hash, load_json
from retrace_selector.models import ValidationError
from retrace_selector.runtime_service import RuntimeSelectorService
from retrace_selector.runtime_store import RuntimeStore
from retrace_selector.strategy_registry import (
    load_selection_policy,
    load_strategy_registry,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "selection_policy.v0.6.json"
REGISTRY_PATH = ROOT / "config" / "strategy_registry.v0.6.json"
STATE_PATH = ROOT / "artifacts" / "pilot_annotation_20260820" / "SRE-0017.state.json"


def request(request_id: str, *, session_id: str = "SESSION-1", state=None, **extra):
    raw = {
        "schema_version": "retrace-runtime-request-v0.6",
        "request_id": request_id,
        "session_id": session_id,
        "state": load_json(STATE_PATH) if state is None else state,
    }
    raw.update(extra)
    return raw


def event(event_id: str, event_type: str, *, session_id="SESSION-1", **extra):
    raw = {
        "schema_version": "retrace-runtime-event-v0.6",
        "event_id": event_id,
        "session_id": session_id,
        "event_type": event_type,
    }
    raw.update(extra)
    return raw


class RuntimeV06Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "runtime.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def shadow_service(self, **overrides):
        values = {
            "database_path": self.database,
            "registry_path": REGISTRY_PATH,
            "policy_path": POLICY_PATH,
            "execution_mode": "SHADOW",
        }
        values.update(overrides)
        return RuntimeSelectorService.from_paths(**values)

    def live_service(self):
        policy, policy_hash = load_selection_policy(POLICY_PATH)
        registry = load_strategy_registry(REGISTRY_PATH)
        id_map = {
            strategy_id: strategy_id.replace("TEST_", "APPROVED_FIXTURE_", 1)
            for strategy_id in registry.catalog
        }
        catalog = MappingProxyType(
            {
                id_map[strategy_id]: replace(entry, strategy_id=id_map[strategy_id])
                for strategy_id, entry in registry.catalog.items()
            }
        )
        candidates = tuple(
            replace(candidate, strategy_id=id_map[candidate.strategy_id])
            for candidate in registry.candidates
        )
        registry = replace(
            registry,
            registry_status="APPROVED",
            catalog=catalog,
            candidates=candidates,
            config_hash=content_hash(
                {
                    "fixture_base": registry.config_hash,
                    "registry_status": "APPROVED",
                    "strategy_ids": sorted(catalog),
                }
            ),
        )
        return RuntimeSelectorService(
            store=RuntimeStore(self.database),
            registry=registry,
            policy=policy,
            policy_hash=policy_hash,
            execution_mode="LIVE",
        )

    def test_shadow_request_is_persisted_without_creating_interaction(self):
        service = self.shadow_service()
        result = service.select(request("REQ-1"))
        self.assertEqual(result["status"], "COMPLETED")
        self.assertIn(result["result"]["outcome"], {"INTERVENE", "PRESENT_CHOICES"})
        self.assertIsNone(result["interaction_id"])
        self.assertTrue(result["runtime"]["persisted"])
        history = service.session_history("SESSION-1")
        self.assertEqual(len(history["requests"]), 1)
        self.assertEqual(history["session"]["recent_intervention_count"], 0)

    def test_state_skill_v2_output_enters_runtime_without_file_adapter(self):
        service = self.shadow_service()
        skill_state = load_json(ROOT / "examples" / "subagent_request_pilot.json")["state"]
        result = service.select(request("REQ-SKILL", state=skill_state))
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["effective_state"]["decision_id"], "SRE-0017")
        self.assertEqual(
            result["effective_state"]["evidence_refs"][0]["source"], "OBSERVED"
        )

    def test_same_request_is_idempotent_and_does_not_duplicate_history(self):
        service = self.shadow_service()
        first = service.select(request("REQ-1"))
        second = service.select(request("REQ-1"))
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(first["result"]["decision_digest"], second["result"]["decision_digest"])
        self.assertEqual(len(service.session_history("SESSION-1")["requests"]), 1)

    def test_reused_request_id_with_different_state_fails_closed(self):
        service = self.shadow_service()
        service.select(request("REQ-1"))
        changed = load_json(STATE_PATH)
        changed["decision_id"] = "changed"
        conflict = service.select(request("REQ-1", state=changed))
        self.assertEqual(conflict["fallback"]["outcome"], "SAFE_HOLD")
        self.assertEqual(conflict["fallback"]["reason_codes"], ["IDEMPOTENCY_CONFLICT"])
        history = service.session_history("SESSION-1")
        self.assertEqual(len(history["requests"]), 1)
        self.assertEqual(history["diagnostics"][0]["code"], "IDEMPOTENCY_CONFLICT")

    def test_test_only_registry_is_blocked_in_live_mode(self):
        service = self.shadow_service(execution_mode="LIVE")
        result = service.select(request("REQ-LIVE-BLOCK"))
        self.assertEqual(result["status"], "FALLBACK")
        self.assertEqual(result["fallback"]["reason_codes"], ["REGISTRY_NOT_APPROVED"])
        self.assertEqual(service.health()["status"], "NOT_READY")

    def test_presented_intervention_and_verification_feed_next_state(self):
        service = self.live_service()
        first = service.select(request("REQ-1"))
        interaction_id = first["interaction_id"]
        self.assertIsNotNone(interaction_id)

        presented = service.record_event(
            event(
                "EV-1",
                "INTERVENTION_PRESENTED",
                interaction_id=interaction_id,
            )
        )
        self.assertEqual(presented["session"]["recent_intervention_count"], 1)
        accepted = service.record_event(
            event("EV-2", "USER_ACCEPTED", interaction_id=interaction_id)
        )
        self.assertTrue(accepted["recorded"])
        verification = service.record_event(event("EV-3", "VERIFICATION_STARTED"))
        self.assertTrue(verification["session"]["active_verification"])

        next_state = load_json(STATE_PATH)
        next_state["decision_id"] = "SRE-0017-next"
        second = service.select(request("REQ-2", state=next_state))
        self.assertEqual(second["effective_state"]["recent_intervention_count"], 1)
        self.assertTrue(second["effective_state"]["active_verification"])
        history = service.session_history("SESSION-1")
        self.assertEqual([item["event_type"] for item in history["events"][:3]], [
            "VERIFICATION_STARTED",
            "USER_ACCEPTED",
            "INTERVENTION_PRESENTED",
        ])

    def test_reaction_before_presentation_is_rejected(self):
        service = self.live_service()
        selected = service.select(request("REQ-1"))
        with self.assertRaisesRegex(ValidationError, "requires a presented interaction"):
            service.record_event(
                event(
                    "EV-1",
                    "USER_REJECTED",
                    interaction_id=selected["interaction_id"],
                )
            )

    def test_event_idempotency_and_session_reset(self):
        service = self.live_service()
        selected = service.select(request("REQ-1"))
        interaction_id = selected["interaction_id"]
        raw_presented = event(
            "EV-1", "INTERVENTION_PRESENTED", interaction_id=interaction_id
        )
        first = service.record_event(raw_presented)
        replay = service.record_event(raw_presented)
        self.assertTrue(first["recorded"])
        self.assertTrue(replay["idempotent_replay"])
        service.record_event(event("EV-2", "VERIFICATION_STARTED"))
        reset = service.record_event(event("EV-3", "SESSION_RESET"))
        self.assertEqual(reset["session"]["recent_intervention_count"], 0)
        self.assertFalse(reset["session"]["active_verification"])
        stale_state = load_json(STATE_PATH)
        stale_state["decision_id"] = "stale-runtime-memory"
        stale_state["recent_interventions"] = 3
        stale_state["active_verification"] = True
        after_reset = service.select(request("REQ-2", state=stale_state))
        self.assertEqual(after_reset["effective_state"]["recent_intervention_count"], 0)
        self.assertFalse(after_reset["effective_state"]["active_verification"])

    def test_invalid_safe_state_requests_clarification_and_unknown_state_holds(self):
        service = self.shadow_service()
        safe_but_invalid = load_json(STATE_PATH)
        safe_but_invalid.pop("evidence")
        clarification = service.select(request("REQ-SAFE-BAD", state=safe_but_invalid))
        self.assertEqual(clarification["fallback"]["outcome"], "REQUEST_CLARIFICATION")
        unknown = service.select(request("REQ-UNKNOWN", state={}))
        self.assertEqual(unknown["fallback"]["outcome"], "SAFE_HOLD")

    def test_abstain_requests_clarification_only_when_risk_is_explicitly_safe(self):
        service = self.shadow_service()
        safe_abstain = load_json(STATE_PATH)
        safe_abstain["support_opportunity"] = "ABSTAIN"
        safe = service.select(request("REQ-ABSTAIN-SAFE", state=safe_abstain))
        self.assertEqual(safe["fallback"]["outcome"], "REQUEST_CLARIFICATION")
        unsafe_abstain = dict(safe_abstain)
        unsafe_abstain["consequence"] = "high"
        unsafe_abstain["reversibility"] = "low"
        unsafe = service.select(request("REQ-ABSTAIN-UNSAFE", state=unsafe_abstain))
        self.assertEqual(unsafe["fallback"]["outcome"], "SAFE_HOLD")

    def test_config_hash_mismatch_is_persisted_as_safe_hold(self):
        service = self.shadow_service()
        result = service.select(
            request("REQ-MISMATCH", expected_policy_hash="0" * 64)
        )
        self.assertEqual(result["fallback"]["reason_codes"], ["CONFIG_VERSION_MISMATCH"])
        self.assertTrue(result["runtime"]["persisted"])
        self.assertEqual(len(service.session_history("SESSION-1")["requests"]), 1)

    def test_timeout_fails_closed_without_releasing_selection(self):
        service = self.shadow_service(selection_timeout_seconds=0.001)
        real_engine = service.engine

        class SlowEngine:
            policy_hash = real_engine.policy_hash

            @staticmethod
            def select(state):
                time.sleep(0.02)
                return real_engine.select(state)

        service.engine = SlowEngine()
        result = service.select(request("REQ-SLOW"))
        self.assertIsNone(result["result"])
        self.assertEqual(result["fallback"]["reason_codes"], ["SELECTION_TIMEOUT"])
        self.assertEqual(result["fallback"]["outcome"], "SAFE_HOLD")

    def test_persistence_failure_never_releases_intervention(self):
        service = self.shadow_service()

        def fail_save(**kwargs):
            raise sqlite3.OperationalError("simulated unavailable database")

        service.store.save_request = fail_save
        result = service.select(request("REQ-NODB"))
        self.assertIsNone(result["result"])
        self.assertEqual(result["fallback"]["reason_codes"], ["PERSISTENCE_FAILURE"])
        self.assertFalse(result["runtime"]["persisted"])

    def test_unavailable_idempotency_store_fails_before_selection(self):
        service = self.shadow_service()

        def fail_read(request_id):
            raise sqlite3.OperationalError("simulated unavailable database")

        service.store.get_request = fail_read
        result = service.select(request("REQ-NOREAD"))
        self.assertIsNone(result["result"])
        self.assertEqual(result["fallback"]["reason_codes"], ["PERSISTENCE_FAILURE"])
        self.assertFalse(result["runtime"]["persisted"])

    def test_event_persistence_failure_is_reported_without_state_change(self):
        service = self.live_service()

        def fail_event(raw_event):
            raise sqlite3.OperationalError("simulated unavailable database")

        service.store.record_event = fail_event
        result = service.record_event(event("EV-NODB", "VERIFICATION_STARTED"))
        self.assertFalse(result["recorded"])
        self.assertEqual(result["error"]["code"], "PERSISTENCE_FAILURE")
        self.assertIsNone(result["session"])

    def test_configuration_reload_is_atomic_and_old_request_is_replayable(self):
        service = self.shadow_service()
        first = service.select(request("REQ-OLD"))
        old_policy_hash = first["runtime"]["policy_hash"]
        changed_policy = load_json(POLICY_PATH)
        changed_policy["tau"] = 0.06
        changed_path = Path(self.temporary.name) / "changed-policy.json"
        changed_path.write_text(json.dumps(changed_policy), encoding="utf-8")
        switch = service.reload_configuration(
            registry_path=REGISTRY_PATH,
            policy_path=changed_path,
        )
        self.assertEqual(switch["previous"]["policy_hash"], old_policy_hash)
        self.assertNotEqual(switch["current"]["policy_hash"], old_policy_hash)
        replay = service.select(request("REQ-OLD"))
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["runtime"]["policy_hash"], old_policy_hash)


class RuntimeCliTests(unittest.TestCase):
    def test_runtime_select_accepts_request_on_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            command = [
                sys.executable,
                "-m",
                "retrace_selector.cli",
                "runtime-select",
                "--database",
                str(database),
                "--policy",
                str(POLICY_PATH),
                "--registry",
                str(REGISTRY_PATH),
                "--request",
                "-",
                "--mode",
                "SHADOW",
            ]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                input=json.dumps(request("REQ-STDIN")),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            response = json.loads(completed.stdout)
            self.assertEqual(response["schema_version"], "retrace-runtime-response-v0.6")
            self.assertTrue(response["runtime"]["persisted"])


if __name__ == "__main__":
    unittest.main()
