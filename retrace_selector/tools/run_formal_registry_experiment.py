"""Run a deterministic smoke/grid experiment for the formal ReTrace registry.

This experiment is intentionally synthetic: it tests the selector contract and
parameter geometry, not intervention efficacy. It uses the same v2 runtime
path as the plugin and reports whether the four registered families are
reachable under representative C/S/A target gaps.
"""

from __future__ import annotations

import argparse
from collections import Counter
import itertools
import json
from pathlib import Path
import tempfile
from typing import Any

from retrace_selector.online_v2 import (
    OnlineInferenceService,
    load_registry_v2,
    load_selector_config_v2,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "strategy_registry.formal.v1.json"
POLICY_PATH = ROOT / "config" / "selection_policy.formal.v1.json"
DIMENSIONS = ("criteria", "state", "action")
FAMILIES = {
    "STATE_CONTEXT_RECOVERY",
    "RULE_CLARIFICATION",
    "CLAIM_EVIDENCE_CALIBRATION",
    "GOVERNANCE_ACTION_PLANNING",
}


def _profile(target: tuple[int, int, int]) -> dict[str, Any]:
    return {
        "FD-FORMAL": {
            "profile_id": "FD-FORMAL",
            "decision_object": "formal selector calibration scenario",
            "target_state": {
                "criteria": target[0],
                "state": target[1],
                "action": target[2],
                "rubric_version": "CSA-RUBRIC-V1",
            },
            "allowed_evidence_types": ["RO04"],
        }
    }


def _event(
    event_id: str,
    session_id: str,
    chain_id: str,
    payload: dict[str, Any],
    observed_at: str,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "session_id": session_id,
        "event_type": "USER_PROMPT",
        "actor": "USER",
        "project_id": "P-FORMAL",
        "observed_at": observed_at,
        "source": "CODEX_HOOK",
        "payload": {"chain_id": chain_id, **payload},
    }


def evaluate_case(
    registry: Any,
    config: Any,
    *,
    case_id: str,
    current: tuple[int | None, int | None, int | None],
    target: tuple[int, int, int],
    assessability: str = "SUFFICIENT",
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="retrace-formal-exp-") as directory:
        session_id = f"S-{case_id}"
        chain_id = f"P-FORMAL::O-{case_id}::FD-{case_id}"
        service = OnlineInferenceService(
            database_path=Path(directory) / "online.sqlite3",
            profiles=_profile(target),
            registry=registry,
            config=config,
        )
        occasion = _event(
            f"EVT-{case_id}-OCC",
            session_id,
            chain_id,
            {
                "occasion_signals": {
                    "prior_instantiation": "CONFIRMED",
                    "current_contact": "CONFIRMED",
                    "consequentiality": "CONFIRMED",
                },
                "decision_object_profile_id": "FD-FORMAL",
                "occasion_id": f"O-{case_id}",
                "focal_decision_id": f"FD-{case_id}",
                "claim_ids": [f"CLAIM-{case_id}"],
                "evidence_ids": [f"EVID-{case_id}"],
            },
            "2026-08-25T10:00:00+00:00",
        )
        result = service.ingest_event(occasion)
        if result["occasion"] != "OCCASION_CONFIRMED":
            raise AssertionError(f"occasion did not confirm for {case_id}")
        updates = {
            dimension: {
                "level": level,
                "assessability": assessability,
                "evidence_ids": [f"EVID-{case_id}-{dimension}"],
            }
            for dimension, level in zip(DIMENSIONS, current)
            if level is not None
        }
        service.ingest_event(
            _event(
                f"EVT-{case_id}-STATE",
                session_id,
                chain_id,
                {"csa_updates": updates},
                "2026-08-25T10:00:01+00:00",
            )
        )
        selection = service.select(chain_id)
        return {
            "case_id": case_id,
            "current": dict(zip(DIMENSIONS, current)),
            "target": dict(zip(DIMENSIONS, target)),
            "decision": selection["decision"],
            "selected": selection["selected"],
            "skyline_count": len(selection["skyline_ids"]),
            "objective": selection["objective"],
        }


def run() -> dict[str, Any]:
    registry = load_registry_v2(REGISTRY_PATH)
    config = load_selector_config_v2(POLICY_PATH)
    candidate_ids = {candidate.candidate_id for candidate in registry.candidates}
    family_by_candidate = {
        candidate.candidate_id: candidate.family for candidate in registry.candidates
    }

    canonical = [
        evaluate_case(registry, config, case_id="UNKNOWN", current=(None, None, None), target=(2, 3, 2)),
        evaluate_case(registry, config, case_id="CRITERIA", current=(0, 3, 2), target=(2, 3, 2)),
        evaluate_case(registry, config, case_id="STATE", current=(2, 0, 2), target=(2, 3, 2)),
        evaluate_case(registry, config, case_id="ACTION", current=(2, 3, 0), target=(2, 3, 2)),
        evaluate_case(registry, config, case_id="LIMITED", current=(2, 0, 2), target=(2, 3, 2), assessability="LIMITED"),
    ]

    grids: dict[str, Any] = {}
    for target in ((2, 3, 2), (3, 3, 3)):
        results = [
            evaluate_case(registry, config, case_id=f"GRID-{target}-{index}", current=current, target=target)
            for index, current in enumerate(itertools.product(range(4), repeat=3))
        ]
        decisions = Counter(item["decision"] for item in results)
        families = Counter(
            family_by_candidate[candidate_id]
            for item in results
            for candidate_id in item["selected"]
        )
        intensities = Counter(
            candidate_id
            for item in results
            for candidate_id in item["selected"]
        )
        grids["/".join(map(str, target))] = {
            "case_count": len(results),
            "decision_counts": dict(sorted(decisions.items())),
            "family_counts": dict(sorted(families.items())),
            "selected_candidate_counts": dict(sorted(intensities.items())),
        }

    selected_ids = {
        candidate_id
        for item in canonical
        for candidate_id in item["selected"]
    }
    selected_families = {family_by_candidate[candidate_id] for candidate_id in selected_ids}
    choice_violations = [
        item
        for item in canonical
        if item["decision"] == "PRESENT_CHOICES"
        and (len(item["selected"]) != 2 or len({family_by_candidate[item_id] for item_id in item["selected"]}) != 2)
    ]
    unknown_result = next(item for item in canonical if item["case_id"] == "UNKNOWN")

    checks = {
        "candidate_count_is_12": len(candidate_ids) == 12,
        "all_registered_families_reachable": selected_families == FAMILIES,
        "unknown_state_is_no_intervention": unknown_result["decision"] == "NO_INTERVENTION",
        "selected_ids_are_registered": selected_ids <= candidate_ids,
        "present_choices_have_two_families": not choice_violations,
    }
    return {
        "experiment": "formal_registry_v1_selector_grid",
        "synthetic": True,
        "interpretation": "parameter geometry and selector contract, not intervention efficacy",
        "registry_version": registry.registry_version,
        "registry_status": registry.registry_status,
        "policy": config.to_dict(),
        "candidate_count": len(candidate_ids),
        "families_registered": sorted(FAMILIES),
        "families_reachable_in_canonical_cases": sorted(selected_families),
        "canonical_cases": canonical,
        "grids": grids,
        "checks": {**checks, "choice_violations": choice_violations},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    check_values = [value for key, value in result["checks"].items() if key != "choice_violations"]
    return 0 if all(check_values) else 1


if __name__ == "__main__":
    raise SystemExit(main())
