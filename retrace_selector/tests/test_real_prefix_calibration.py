from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from retrace_selector.calibration import (
    CALIBRATION_REVIEW_SCHEMA,
    build_calibration_review_templates,
    calibrate_policy,
)
from retrace_selector.models import ValidationError
from retrace_selector.real_prefix import build_prefix_manifest

from common import engine


def transcript_event(context: str, record: int, role: str, text: str):
    return {
        "source_context": context,
        "record_index": record,
        "timestamp": f"2026-08-20T00:00:0{record}Z",
        "role": role,
        "text": text,
        "audit_text": text,
    }


def write_inventory(directory: Path, rows: list[dict]) -> Path:
    path = directory / "inventory.csv"
    fields = [
        "strict_id",
        "participant_id",
        "proposed_start",
        "reentry_onset",
        "transcript_path",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_transcript(path: Path, events: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events),
        encoding="utf-8",
    )


def evidence(evidence_id: str, need: str):
    return {
        "evidence_id": evidence_id,
        "source": "OBSERVED",
        "locator": f"case/transcript.jsonl#{evidence_id}",
        "sequence_index": 0,
        "content_sha256": hashlib.sha256(evidence_id.encode()).hexdigest(),
        "supports_needs": [need],
        "available_at_decision": True,
    }


def reviewed_case(case_id: str, group: str, needs: dict, expected: str, primitives: list[str]):
    item_evidence = []
    if any(needs.values()):
        need = max(needs, key=needs.get)
        item_evidence = [evidence(f"{case_id}:E1", need)]
    return {
        "schema_version": CALIBRATION_REVIEW_SCHEMA,
        "case_id": case_id,
        "participant_group": group,
        "stratum": "core",
        "prefix": {
            "status": "READY",
            "leakage_check": "PASS",
            "available_evidence": [
                {
                    "evidence_id": item["evidence_id"],
                    "locator": item["locator"],
                    "sequence_index": item["sequence_index"],
                    "content_sha256": item["content_sha256"],
                }
                for item in item_evidence
            ],
        },
        "review": {
            "status": "APPROVED",
            "reviewer": "test",
            "state": {
                "schema_version": "retrace-state-v2",
                "decision_id": case_id,
                "process_state": "REENTRY_OCCASION_OBSERVED",
                "governance_needs": needs,
                "evidence": item_evidence,
                "consequence": "medium",
                "reversibility": "medium",
                "authorization_risk": "low",
                "evidence_completeness": "sufficient" if item_evidence else "none",
                "state_confidence": 0.9,
                "recent_interventions": 0,
                "active_verification": False,
            },
        },
        "calibration_target": {
            "selector_visible": False,
            "expected_outcome": expected,
            "acceptable_primitives": primitives,
        },
        "eligibility": {"primary_calibration": True},
    }


class PrefixBuilderTests(unittest.TestCase):
    def test_multiline_message_in_jsonl_is_parsed_without_exporting_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.jsonl"
            transcript.write_text(
                '{"source_context":"context_0001","record_index":1,'
                '"role":"user","text":"first line\nsecond line"}\n',
                encoding="utf-8",
            )
            inventory = write_inventory(
                root,
                [{
                    "strict_id": "SRE-T000",
                    "participant_id": "person-z",
                    "proposed_start": "context_0001:R1",
                    "reentry_onset": "1",
                    "transcript_path": str(transcript),
                }],
            )
            records, _ = build_prefix_manifest([(inventory, "core")])
        self.assertEqual(records[0]["status"], "READY")
        self.assertNotIn("second line", json.dumps(records[0]))

    def test_prefix_stops_at_onset_and_exports_no_raw_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.jsonl"
            write_transcript(
                transcript,
                [
                    transcript_event("context_0001", 1, "user", "secret-before"),
                    transcript_event("context_0001", 2, "user", "secret-trigger"),
                    transcript_event("context_0001", 3, "assistant", "future-secret"),
                ],
            )
            inventory = write_inventory(
                root,
                [
                    {
                        "strict_id": "SRE-T001",
                        "participant_id": "person-a",
                        "proposed_start": "2",
                        "reentry_onset": "2",
                        "transcript_path": str(transcript),
                    }
                ],
            )
            records, report = build_prefix_manifest([(inventory, "core")])
            record = records[0]
            self.assertEqual(record["status"], "READY")
            self.assertEqual(record["prefix_event_count"], 2)
            self.assertEqual(record["future_event_count"], 1)
            self.assertEqual(record["leakage_check"], "PASS")
            serialized = json.dumps(record, ensure_ascii=False)
            self.assertNotIn("secret", serialized)
            self.assertEqual(report["leakage_failures"], 0)

    def test_ambiguous_context_local_onset_requires_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.jsonl"
            write_transcript(
                transcript,
                [
                    transcript_event("context_0001", 1, "user", "a"),
                    transcript_event("context_0002", 1, "user", "b"),
                ],
            )
            inventory = write_inventory(
                root,
                [
                    {
                        "strict_id": "SRE-T002",
                        "participant_id": "person-b",
                        "proposed_start": "1",
                        "reentry_onset": "1",
                        "transcript_path": str(transcript),
                    }
                ],
            )
            records, _ = build_prefix_manifest([(inventory, "core")])
            self.assertEqual(records[0]["status"], "REVIEW_REQUIRED")
            self.assertEqual(
                records[0]["reason"], "AMBIGUOUS_CONTEXT_LOCAL_RECORD_INDEX"
            )


class CalibrationTests(unittest.TestCase):
    def test_review_template_keeps_post_onset_target_separate(self):
        prefix = {
            "episode_id": "SRE-0001",
            "participant_group": "g1",
            "stratum": "core",
            "status": "READY",
            "prefix_sha256": "a" * 64,
            "onset": {"sequence_index": 0},
            "leakage_check": "PASS",
            "event_references": [
                {"evidence_id": "SRE-0001:context_0001:R1"}
            ],
        }
        annotation = {
            "result": {
                "episode": {
                    "episode_id": "SRE-0001",
                    "reentry_decision": "RD01",
                    "recovery_object": ["RO01"],
                    "user_reentry_action": ["RA02"],
                    "evidence_type": ["EV03"],
                    "decision_reclaim": ["DR04"],
                    "source_pointers": [
                        "SRE-0001/context_0001/R1",
                        "SRE-0001/context_0001/R2",
                    ],
                }
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "annotations.jsonl"
            path.write_text(json.dumps(annotation) + "\n", encoding="utf-8")
            templates, report = build_calibration_review_templates([prefix], path)
        target = templates[0]["calibration_target"]
        self.assertFalse(target["selector_visible"])
        self.assertEqual(
            target["post_onset_target_pointers"],
            ["SRE-0001:context_0001:R2"],
        )
        self.assertEqual(report["pending_human_review"], 1)

    def test_calibration_refuses_pending_reviews(self):
        case = reviewed_case(
            "C1", "g1", {"O": 3, "S": 0, "D": 0}, "INTERVENE", ["RULE_ALIGNMENT"]
        )
        case["review"]["status"] = "PENDING"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reviews.jsonl"
            path.write_text(json.dumps(case) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "approved cases"):
                calibrate_policy(path, engine().policy, engine().templates, minimum_cases=1)

    def test_calibration_rejects_evidence_that_does_not_match_prefix(self):
        case = reviewed_case(
            "C1", "g1", {"O": 3, "S": 0, "D": 0}, "INTERVENE", ["RULE_ALIGNMENT"]
        )
        case["review"]["state"]["evidence"][0]["content_sha256"] = "f" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reviews.jsonl"
            path.write_text(json.dumps(case) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "does not match prefix"):
                calibrate_policy(path, engine().policy, engine().templates, minimum_cases=1)

    def test_calibration_runs_grouped_cross_validation_on_approved_prefixes(self):
        cases = [
            reviewed_case(
                "C1", "g1", {"O": 3, "S": 0, "D": 0}, "INTERVENE", ["RULE_ALIGNMENT"]
            ),
            reviewed_case(
                "C2", "g2", {"O": 0, "S": 0, "D": 3}, "INTERVENE", ["VERIFICATION", "DISPOSITION_COORDINATION"]
            ),
            reviewed_case(
                "C3", "g3", {"O": 0, "S": 0, "D": 0}, "NO_INTERVENTION", []
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reviews.jsonl"
            path.write_text(
                "".join(json.dumps(case) + "\n" for case in cases),
                encoding="utf-8",
            )
            result = calibrate_policy(
                path,
                engine().policy,
                engine().templates,
                minimum_cases=3,
                minimum_groups=3,
                weight_step=0.2,
                gain_values=(0.0,),
                near_tie_values=(0.03,),
            )
        self.assertEqual(result["approved_case_count"], 3)
        self.assertEqual(
            result["participant_group_cross_validation"]["fold_count"], 3
        )
        self.assertEqual(result["trial_count_per_search"], 1)


if __name__ == "__main__":
    unittest.main()
