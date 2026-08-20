from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from .models import DecisionState, Level, Outcome, Primitive, ValidationError
from .selector import SelectionEngine


SCENARIO_REQUIRED = {"scenario_id", "state"}
SCENARIO_ORACLES = {
    "expected_outcome",
    "expected_selected_ids",
    "required_reason_codes",
    "allowed_levels",
    "allowed_primitive",
    "forbidden_candidates",
}


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValidationError(f"{field_name} must be an array of non-empty strings")
    return value


def _validate_scenario(raw: Mapping[str, Any], index: int) -> None:
    keys = set(raw)
    missing = SCENARIO_REQUIRED - keys
    unknown = keys - SCENARIO_REQUIRED - SCENARIO_ORACLES
    if missing:
        raise ValidationError(f"scenario {index} missing fields: {sorted(missing)}")
    if unknown:
        raise ValidationError(f"scenario {index} unknown fields: {sorted(unknown)}")
    if not (keys & SCENARIO_ORACLES):
        raise ValidationError(f"scenario {index} requires at least one oracle")
    scenario_id = raw["scenario_id"]
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValidationError(f"scenario {index} requires scenario_id")
    if "expected_outcome" in raw:
        try:
            Outcome(raw["expected_outcome"])
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"scenario {scenario_id} has invalid expected_outcome"
            ) from exc
    for field_name in (
        "expected_selected_ids",
        "required_reason_codes",
        "forbidden_candidates",
    ):
        if field_name in raw:
            _string_list(raw[field_name], f"scenario {scenario_id}.{field_name}")
    if "allowed_levels" in raw:
        levels = _string_list(
            raw["allowed_levels"], f"scenario {scenario_id}.allowed_levels"
        )
        for level in levels:
            Level.from_value(level)
    if "allowed_primitive" in raw:
        try:
            Primitive(raw["allowed_primitive"])
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"scenario {scenario_id} has invalid allowed_primitive"
            ) from exc


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
        if not result_dict["selected_ids"]:
            failures.append(
                f"allowed primitive {allowed_primitive} requires a selected candidate"
            )
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
    scenario_ids: set[str] = set()
    decision_ids: set[str] = set()
    for index, raw in enumerate(raw_scenarios):
        if not isinstance(raw, Mapping):
            raise ValidationError(f"scenario {index} must be an object")
        _validate_scenario(raw, index)
        scenario_id = raw.get("scenario_id")
        assert isinstance(scenario_id, str)
        if scenario_id in scenario_ids:
            raise ValidationError(f"duplicate scenario_id: {scenario_id}")
        scenario_ids.add(scenario_id)
        state = DecisionState.from_dict(raw["state"])
        if state.decision_id in decision_ids:
            raise ValidationError(f"duplicate decision_id: {state.decision_id}")
        decision_ids.add(state.decision_id)
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
