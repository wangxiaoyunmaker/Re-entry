from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from .models import DecisionState, Level, Outcome, ValidationError
from .selector import SelectionEngine


def _scenario_checks(
    scenario: Mapping[str, Any], result_dict: Mapping[str, Any]
) -> list[str]:
    failures: list[str] = []
    expected = scenario.get("expected_outcome")
    if expected and result_dict["outcome"] != expected:
        failures.append(f"expected outcome {expected}, got {result_dict['outcome']}")

    expected_selected = scenario.get("expected_selected_ids")
    if expected_selected is not None and result_dict["selected_ids"] != expected_selected:
        failures.append(
            f"expected selected IDs {expected_selected}, got {result_dict['selected_ids']}"
        )

    required_reasons = set(scenario.get("required_reason_codes", []))
    missing_reasons = sorted(required_reasons - set(result_dict["reason_codes"]))
    if missing_reasons:
        failures.append(f"missing required reason codes: {missing_reasons}")

    allowed_levels = scenario.get("allowed_levels")
    if allowed_levels is not None:
        allowed_set = set(allowed_levels)
        for item in result_dict["generated_candidates"]:
            if item["allowed"] and not item["brief"]["is_no_intervention"]:
                if item["brief"]["level"] not in allowed_set:
                    failures.append(
                        f"feasible candidate {item['brief']['brief_id']} has forbidden level"
                    )

    allowed_primitive = scenario.get("allowed_primitive")
    if allowed_primitive:
        for selected_id in result_dict["selected_ids"]:
            if not selected_id.startswith(f"{allowed_primitive}-"):
                failures.append(f"selected {selected_id} violates allowed primitive")

    forbidden = set(scenario.get("forbidden_candidates", []))
    feasible = set(result_dict["feasible_ids"])
    overlap = sorted(forbidden & feasible)
    if overlap:
        failures.append(f"forbidden candidates remained feasible: {overlap}")
    return failures


def replay_scenarios(
    raw_scenarios: Any, engine: SelectionEngine
) -> dict[str, Any]:
    if not isinstance(raw_scenarios, list):
        raise ValidationError("replay input must be an array of scenarios")
    outcomes: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    ratios: list[float] = []
    total_feasible = 0
    total_dominated = 0
    for index, raw in enumerate(raw_scenarios):
        if not isinstance(raw, Mapping):
            raise ValidationError(f"scenario {index} must be an object")
        scenario_id = raw.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ValidationError(f"scenario {index} requires scenario_id")
        if "state" not in raw:
            raise ValidationError(f"scenario {scenario_id} requires state")
        state = DecisionState.from_dict(raw["state"])
        result = engine.select(state)
        result_dict = result.to_dict()
        failures = _scenario_checks(raw, result_dict)
        outcomes[result.outcome.value] += 1
        if result.frontier_ratio is not None:
            ratios.append(result.frontier_ratio)
        total_feasible += len(result.feasible_ids)
        total_dominated += len(result.dominance_witnesses)
        records.append(
            {
                "scenario_id": scenario_id,
                "passed": not failures,
                "failures": failures,
                "result": result_dict,
            }
        )
    passed = sum(1 for record in records if record["passed"])
    return {
        "summary": {
            "scenario_count": len(records),
            "passed": passed,
            "failed": len(records) - passed,
            "outcomes": dict(sorted(outcomes.items())),
            "mean_frontier_ratio": sum(ratios) / len(ratios) if ratios else None,
            "skyline_deletion_rate": (
                total_dominated / total_feasible if total_feasible else None
            ),
        },
        "scenarios": records,
    }
