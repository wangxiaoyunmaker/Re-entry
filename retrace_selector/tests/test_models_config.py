from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import unittest

from retrace_selector.config import load_policy, load_templates
from retrace_selector.models import DecisionState, EvidenceCompleteness, Level, Primitive, ValidationError

from common import POLICY_PATH, TEMPLATES_PATH, load_json, state_dict


class DecisionStateTests(unittest.TestCase):
    def test_valid_state_round_trips(self):
        raw = state_dict()
        self.assertEqual(DecisionState.from_dict(raw).to_dict(), raw)

    def test_governance_needs_reject_out_of_range_and_bool(self):
        for value in (-1, 4, 1.5, "2", True):
            raw = state_dict(governance_needs={"O": value, "S": 0, "D": 0})
            with self.subTest(value=value), self.assertRaises(ValidationError):
                DecisionState.from_dict(raw)

    def test_confidence_rejects_nonfinite_and_out_of_range(self):
        for value in (-0.1, 1.1, math.nan, math.inf, True):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                DecisionState.from_dict(state_dict(state_confidence=value))

    def test_unknown_and_missing_fields_fail_closed(self):
        unknown = state_dict(extra="bad")
        with self.assertRaisesRegex(ValidationError, "unknown fields"):
            DecisionState.from_dict(unknown)
        missing = state_dict()
        del missing["process_state"]
        with self.assertRaisesRegex(ValidationError, "missing fields"):
            DecisionState.from_dict(missing)

    def test_duplicate_evidence_ids_rejected(self):
        evidence = [
            {"evidence_id": "E1", "source": "OBSERVED"},
            {"evidence_id": "E1", "source": "INFERRED"},
        ]
        with self.assertRaisesRegex(ValidationError, "unique"):
            DecisionState.from_dict(state_dict(evidence=evidence))

    def test_nonempty_evidence_required_when_completeness_not_none(self):
        with self.assertRaisesRegex(ValidationError, "OBSERVED or INFERRED"):
            DecisionState.from_dict(state_dict(evidence=[]))
        parsed = DecisionState.from_dict(
            state_dict(evidence=[], evidence_completeness="none")
        )
        self.assertEqual(parsed.evidence, ())

    def test_design_assumption_cannot_substantiate_state_completeness(self):
        evidence = [{"evidence_id": "A1", "source": "DESIGN_ASSUMPTION"}]
        with self.assertRaisesRegex(ValidationError, "OBSERVED or INFERRED"):
            DecisionState.from_dict(
                state_dict(evidence=evidence, evidence_completeness="sufficient")
            )


class ConfigTests(unittest.TestCase):
    def test_frozen_policy_and_templates_load(self):
        policy = load_policy(POLICY_PATH)
        templates = load_templates(TEMPLATES_PATH)
        self.assertEqual(policy.policy_version, "skyline-mvp-v0.2")
        self.assertEqual(len(policy.primitive_profiles), 5)
        self.assertEqual(len(policy.config_hash), 64)
        self.assertEqual(len(templates.config_hash), 64)

    def test_config_hashes_are_deterministic(self):
        first_policy = load_policy(POLICY_PATH)
        second_policy = load_policy(POLICY_PATH)
        first_templates = load_templates(TEMPLATES_PATH)
        second_templates = load_templates(TEMPLATES_PATH)
        self.assertEqual(first_policy.config_hash, second_policy.config_hash)
        self.assertEqual(first_templates.config_hash, second_templates.config_hash)

    def test_loaded_configuration_is_deeply_immutable(self):
        policy = load_policy(POLICY_PATH)
        templates = load_templates(TEMPLATES_PATH)
        with self.assertRaises(TypeError):
            policy.weights["O"] = 1.0
        with self.assertRaises(TypeError):
            policy.primitive_profiles[Primitive.CAUSAL_EXPLANATION].minimum_evidence[
                Level.L3
            ] = EvidenceCompleteness.PARTIAL
        with self.assertRaises(TypeError):
            templates.templates[Primitive.VERIFICATION][Level.L1] = None

    def test_invalid_weight_sum_fails_closed(self):
        raw = load_json(POLICY_PATH)
        raw["weights"]["W"] = 0.99
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "sum to 1"):
                load_policy(path)

    def test_unknown_policy_field_fails_closed(self):
        raw = load_json(POLICY_PATH)
        raw["silent_default"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "unknown fields"):
                load_policy(path)

    def test_non_string_version_fails_closed(self):
        raw = load_json(POLICY_PATH)
        raw["policy_version"] = 2
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "non-empty string"):
                load_policy(path)

    def test_incompatible_engine_version_fails_closed(self):
        raw = load_json(POLICY_PATH)
        raw["engine_version"] = "999.0"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "incompatible"):
                load_policy(path)

    def test_non_monotonic_policy_fails_closed(self):
        raw = load_json(POLICY_PATH)
        raw["primitive_profiles"]["VERIFICATION"]["burden"]["L3"] = 0.01
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "non-decreasing"):
                load_policy(path)


if __name__ == "__main__":
    unittest.main()
