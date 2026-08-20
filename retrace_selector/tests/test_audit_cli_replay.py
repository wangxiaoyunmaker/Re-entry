from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace

from retrace_selector.audit import append_jsonl
from retrace_selector.config import load_json
from retrace_selector.models import ValidationError
from retrace_selector.replay import replay_scenarios

from common import POLICY_PATH, ROOT, TEMPLATES_PATH, engine, state


class AuditTests(unittest.TestCase):
    def test_jsonl_append_is_idempotent_by_audit_id(self):
        result = engine().select(state())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            self.assertTrue(append_jsonl(path, result))
            self.assertFalse(append_jsonl(path, result))
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["audit_id"], result.audit_id)

    def test_audit_contains_versions_hashes_and_evidence(self):
        data = engine().select(state()).to_dict()
        metadata = data["metadata"]
        self.assertEqual(len(metadata["policy_hash"]), 64)
        self.assertEqual(len(metadata["template_hash"]), 64)
        self.assertEqual(metadata["state"]["evidence"][0]["evidence_id"], "E1")
        self.assertEqual(len(data["decision_digest"]), 64)

    def test_same_audit_id_with_different_decision_is_conflict(self):
        result = engine().select(state())
        conflicting = replace(result, selected_ids=())
        self.assertEqual(result.audit_id, conflicting.audit_id)
        self.assertNotEqual(result.decision_digest, conflicting.decision_digest)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            self.assertTrue(append_jsonl(path, result))
            with self.assertRaisesRegex(ValidationError, "audit conflict"):
                append_jsonl(path, conflicting)

    def test_mutated_nested_result_fails_integrity_check(self):
        result = engine().select(state())
        result.generated[0].utility = 999.0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            with self.assertRaisesRegex(ValidationError, "changed after decision sealing"):
                append_jsonl(path, result)


class ReplayTests(unittest.TestCase):
    def test_canonical_replay_passes(self):
        scenarios = load_json(ROOT / "examples" / "canonical_scenarios.json")
        replay = replay_scenarios(scenarios, engine())
        self.assertEqual(replay["summary"]["failed"], 0)
        self.assertEqual(replay["summary"]["scenario_count"], 13)

    def test_replay_rejects_unknown_oracle_field(self):
        scenario = {
            "scenario_id": "typo",
            "expected_outcomme": "INTERVENE",
            "state": state().to_dict(),
        }
        with self.assertRaisesRegex(ValidationError, "unknown fields"):
            replay_scenarios([scenario], engine())

    def test_replay_requires_oracle_and_unique_ids(self):
        raw_state = state().to_dict()
        without_oracle = {"scenario_id": "no-oracle", "state": raw_state}
        with self.assertRaisesRegex(ValidationError, "at least one oracle"):
            replay_scenarios([without_oracle], engine())
        scenario = {
            "scenario_id": "duplicate",
            "expected_outcome": "INTERVENE",
            "state": raw_state,
        }
        with self.assertRaisesRegex(ValidationError, "duplicate scenario_id"):
            replay_scenarios([scenario, scenario], engine())


class CliTests(unittest.TestCase):
    def _env(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        return env

    def test_select_cli_smoke(self):
        command = [
            sys.executable,
            "-m",
            "retrace_selector.cli",
            "select",
            "--state",
            str(ROOT / "examples" / "state_reentry_verification.json"),
            "--policy",
            str(POLICY_PATH),
            "--templates",
            str(TEMPLATES_PATH),
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=self._env(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(json.loads(completed.stdout)["outcome"], {"INTERVENE", "PRESENT_CHOICES"})

    def test_invalid_cli_input_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text("{}", encoding="utf-8")
            command = [
                sys.executable,
                "-m",
                "retrace_selector.cli",
                "select",
                "--state",
                str(path),
                "--policy",
                str(POLICY_PATH),
                "--templates",
                str(TEMPLATES_PATH),
            ]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=self._env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("missing fields", completed.stderr)


if __name__ == "__main__":
    unittest.main()
