from __future__ import annotations

import hashlib
import unittest

from retrace_selector.models import ValidationError
from retrace_selector.subagent import run_selector_request

from common import engine, state_dict


class SelectorSubagentTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine()
        self.policy_path = "config/policy.v0.2.json"
        self.templates_path = "config/templates.v0.2.json"

    def request(self, **overrides):
        evidence = {
            "evidence_id": "E1",
            "source": "OBSERVED",
            "locator": "transcript.jsonl#R1",
            "sequence_index": 0,
            "content_sha256": hashlib.sha256(b"E1").hexdigest(),
            "supports_primitives": ["VERIFICATION"],
            "available_at_decision": True,
        }
        state = state_dict(
            schema_version="retrace-state-v2",
            decision_id="subagent-test",
            process_state="REENTRY_OCCASION_OBSERVED",
            governance_needs={"O": 0, "S": 0, "D": 3},
            evidence=[evidence],
            evidence_completeness="partial",
            **overrides,
        )
        return {
            "schema_version": "retrace-selector-request-v1",
            "state": state,
            "policy_ref": self.policy_path,
            "template_ref": self.templates_path,
            "execution_mode": "DRY_RUN",
        }

    def test_subagent_returns_sealed_selection_result(self):
        response = run_selector_request(
            self.request(),
            policy_path=self.policy_path,
            templates_path=self.templates_path,
        )
        self.assertEqual(response["schema_version"], "retrace-selector-response-v1")
        self.assertEqual(response["execution_mode"], "DRY_RUN")
        result = response["result"]
        self.assertEqual(result["outcome"], "NO_INTERVENTION")
        self.assertTrue(result["decision_digest"])

    def test_subagent_rejects_non_dry_run(self):
        request = self.request()
        request["execution_mode"] = "EXECUTE"
        with self.assertRaisesRegex(ValidationError, "DRY_RUN"):
            run_selector_request(
                request,
                policy_path=self.policy_path,
                templates_path=self.templates_path,
            )

    def test_subagent_rejects_trusted_path_mismatch(self):
        request = self.request()
        request["policy_ref"] = "other-policy.json"
        with self.assertRaisesRegex(ValidationError, "policy_ref"):
            run_selector_request(
                request,
                policy_path=self.policy_path,
                templates_path=self.templates_path,
            )


if __name__ == "__main__":
    unittest.main()
