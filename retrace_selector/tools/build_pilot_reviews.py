from __future__ import annotations

import json
from pathlib import Path


PILOT_CASES = {
    "SRE-0012": {
        "needs": {"criteria_basis_reconstruction": 1, "project_state_reconstruction": 2, "evidence_action_governance": 3},
        "consequence": "medium",
        "reversibility": "medium",
        "authorization_risk": "low",
        "evidence_completeness": "partial",
        "state_confidence": 0.86,
        "recent_interventions": 3,
        "active_verification": False,
        "supports_primitives": ["VERIFICATION", "DISPOSITION_COORDINATION"],
        "note": "Prefix shows a user request for real-page validation after prior repair attempts; it does not establish that the repair is actually verified.",
    },
    "SRE-0017": {
        "needs": {"criteria_basis_reconstruction": 3, "project_state_reconstruction": 2, "evidence_action_governance": 1},
        "consequence": "low",
        "reversibility": "high",
        "authorization_risk": "low",
        "evidence_completeness": "partial",
        "state_confidence": 0.91,
        "recent_interventions": 0,
        "active_verification": False,
        "supports_primitives": ["RULE_ALIGNMENT", "PROVENANCE"],
        "note": "Prefix contains a direct user correction of scope/content alignment; the state records the rule being reasserted, not later repair behavior.",
    },
    "SRE-0061": {
        "needs": {"criteria_basis_reconstruction": 2, "project_state_reconstruction": 3, "evidence_action_governance": 3},
        "consequence": "medium",
        "reversibility": "medium",
        "authorization_risk": "low",
        "evidence_completeness": "partial",
        "state_confidence": 0.84,
        "recent_interventions": 0,
        "active_verification": False,
        "supports_primitives": ["RULE_ALIGNMENT", "DISPOSITION_COORDINATION"],
        "note": "Prefix contains the user's domain-level connection requirements between SPS work data and gamified rewards; implementation adequacy is not inferred from later turns.",
    },
    "SRE-0112": {
        "needs": {"criteria_basis_reconstruction": 2, "project_state_reconstruction": 3, "evidence_action_governance": 2},
        "consequence": "medium",
        "reversibility": "medium",
        "authorization_risk": "low",
        "evidence_completeness": "partial",
        "state_confidence": 0.88,
        "recent_interventions": 0,
        "active_verification": False,
        "supports_primitives": ["CAUSAL_EXPLANATION", "PROVENANCE"],
        "note": "Prefix contains a user challenge to the account-binding explanation; later account inspection is not included in the state.",
    },
}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    artifact_dir = root / "artifacts" / "pilot_annotation_20260820"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        json.loads(line)["episode_id"]: json.loads(line)
        for line in (root / "artifacts/real_prefix_20260820/prefix_manifest.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    }
    templates = {
        json.loads(line)["case_id"]: json.loads(line)
        for line in (root / "artifacts/real_prefix_20260820/calibration_review_template.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    }
    reviews = []
    notes = []
    for case_id, spec in PILOT_CASES.items():
        prefix = manifest[case_id]
        template = templates[case_id]
        trigger = prefix["event_references"][prefix["onset"]["sequence_index"]]
        evidence = {
            "evidence_id": trigger["evidence_id"],
            "source": "OBSERVED",
            "locator": trigger["locator"],
            "observed_at": trigger["observed_at"],
            "sequence_index": trigger["sequence_index"],
            "content_sha256": trigger["content_sha256"],
            "supports_primitives": spec["supports_primitives"],
            "available_at_decision": True,
        }
        state = {
            "schema_version": "retrace-state-v2",
            "decision_id": case_id,
            "process_state": "REENTRY_OCCASION_OBSERVED",
            "support_opportunity": "REENTRY_SUPPORT",
            "support_needs": spec["needs"],
            "evidence": [evidence],
            "consequence": spec["consequence"],
            "reversibility": spec["reversibility"],
            "authorization_risk": spec["authorization_risk"],
            "evidence_completeness": spec["evidence_completeness"],
            "state_confidence": spec["state_confidence"],
            "recent_interventions": spec["recent_interventions"],
            "active_verification": spec["active_verification"],
        }
        (artifact_dir / f"{case_id}.state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        template["review"] = {
            "status": "APPROVED",
            "reviewer": "codex-pilot",
            "reviewed_at": "2026-08-20T00:00:00Z",
            "tool_version": "manual-prefix-pilot-v1",
            "state": state,
            "note": spec["note"],
        }
        reviews.append(template)
        notes.append({"case_id": case_id, "annotation_note": spec["note"]})
    reviews.sort(key=lambda item: item["case_id"])
    (artifact_dir / "pilot_reviews.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in reviews),
        encoding="utf-8",
    )
    (artifact_dir / "pilot_annotation_notes.json").write_text(
        json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"case_count": len(reviews), "output_dir": str(artifact_dir)}))


if __name__ == "__main__":
    main()
