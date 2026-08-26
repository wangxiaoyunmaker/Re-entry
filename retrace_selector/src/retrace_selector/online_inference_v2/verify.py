"""Executable smoke verification for the organized online v2 package."""

from __future__ import annotations

from pathlib import Path
import tempfile

try:  # Works both as ``python -m`` and as a direct script from the README.
    from .core import OnlineInferenceService, SelectorConfigV2
except ImportError:  # pragma: no cover - only used by direct-script execution.
    from retrace_selector.online_inference_v2.core import OnlineInferenceService, SelectorConfigV2


PROFILE = {
    "FD-PROFILE-01": {
        "profile_id": "FD-PROFILE-01",
        "decision_object": "照片删除关系",
        "target_state": {"criteria": 2, "state": 3, "action": 2},
    }
}

REGISTRY = {
    "registry_version": "VERIFY-V2",
    "registry_status": "APPROVED",
    "candidates": [
        {
            "strategy_id": "STATE_TRACE",
            "strategy_family": "STATE_TRACE",
            "intensity": "L2",
            "parameters": {"criteria": 0.1, "state": 0.8, "action": 0.1, "evidence": 0.8, "workflow": 0.8},
            "template_id": "STATE_TRACE_L2",
        },
    ],
    "templates": {"STATE_TRACE_L2": {"title": "trace"}},
}


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        service = OnlineInferenceService(
            database_path=Path(directory) / "online.sqlite3",
            profiles=PROFILE,
            registry=REGISTRY,
            config=SelectorConfigV2(),
        )
        result = service.ingest_event({
            "event_id": "EVT-OCC-VERIFY",
            "session_id": "VERIFY-SESSION",
            "event_type": "USER_PROMPT",
            "actor": "USER",
            "project_id": "VERIFY-PROJECT",
            "observed_at": "2026-08-24T10:00:00+00:00",
            "source": "CODEX_HOOK",
            "payload": {
                "occasion_signals": {"prior_instantiation": "CONFIRMED", "current_contact": "CONFIRMED", "consequentiality": "CONFIRMED"},
                "decision_object_profile_id": "FD-PROFILE-01",
                "occasion_id": "OCC-VERIFY",
                "focal_decision_id": "FD-VERIFY",
                "evidence_ids": ["E-VERIFY"],
            },
        })
        chain_id = result["chain"]["chain_id"]
        service.submit_occasion_baseline(chain_id, evaluation_id="EVAL-BASE-VERIFY", skipped_dimensions=["criteria", "state", "action"])
        service.ingest_event({
            "event_id": "EVT-CSA-VERIFY",
            "session_id": "VERIFY-SESSION",
            "event_type": "USER_PROMPT",
            "actor": "USER",
            "project_id": "VERIFY-PROJECT",
            "observed_at": "2026-08-24T10:01:00+00:00",
            "source": "CODEX_HOOK",
            "payload": {"chain_id": chain_id, "csa_updates": {
                "criteria": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
                "state": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
                "action": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
            }},
        })
        selection = service.select(chain_id)
        service.expose(chain_id, exposure_id="EXP-VERIFY", selection_decision_id=selection["decision_id"])
        service.submit_evaluation(chain_id, evaluation_id="EVAL-POST-VERIFY", skipped_dimensions=["criteria", "state", "action"])
        linkage = service.get_chain_outcome_linkage(chain_id)
        assert linkage["linkage_status"] == "READY_FOR_OFFLINE_LINKAGE"
        assert linkage["csa_measurements"]["pre_snapshot_id"]
        assert linkage["csa_measurements"]["post_snapshot_id"]
        print("ONLINE_V2_VERIFY_OK")


if __name__ == "__main__":
    main()
