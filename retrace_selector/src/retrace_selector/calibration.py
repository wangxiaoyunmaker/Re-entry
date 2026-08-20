from __future__ import annotations

from dataclasses import replace
import hashlib
import itertools
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .config import canonical_json
from .models import DecisionState, Outcome, PolicySpec, Primitive, ValidationError
from .selector import SelectionEngine


CALIBRATION_REVIEW_SCHEMA = "retrace-calibration-review-v1"
_SOURCE_POINTER = re.compile(r"(?P<episode>SRE-\d+)/(?P<context>context_[^/]+)/R(?P<record>\d+)$")


def _target_primitives(episode: Mapping[str, Any]) -> tuple[str, ...]:
    recovery = set(episode.get("recovery_object") or [])
    actions = set(episode.get("user_reentry_action") or [])
    evidence = set(episode.get("evidence_type") or [])
    decisions = set(episode.get("decision_reclaim") or [])
    targets: set[str] = set()
    if recovery & {"RO04"} or decisions & {"DR01", "DR02"} or "RA05" in actions:
        targets.add(Primitive.RULE_ALIGNMENT.value)
    if "RO01" in recovery:
        targets.add(Primitive.PROVENANCE.value)
    if recovery & {"RO02", "RO03"} or "RA04" in actions:
        targets.add(Primitive.CAUSAL_EXPLANATION.value)
    if (
        "RO06" in recovery
        or "RA02" in actions
        or evidence & {"EV03", "EV08"}
    ):
        targets.add(Primitive.VERIFICATION.value)
    if (
        "RO05" in recovery
        or "RA05" in actions
        or decisions & {"DR03", "DR04", "DR05", "DR06"}
    ):
        targets.add(Primitive.DISPOSITION_COORDINATION.value)
    return tuple(sorted(targets))


def _pointer_to_evidence_id(pointer: str) -> str | None:
    match = _SOURCE_POINTER.fullmatch(pointer)
    if not match:
        return None
    return (
        f"{match.group('episode')}:{match.group('context')}:R{int(match.group('record'))}"
    )


def build_calibration_review_templates(
    prefixes: Sequence[Mapping[str, Any]],
    annotation_results_path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prefix_by_id = {item["episode_id"]: item for item in prefixes}
    rows = [
        json.loads(line)
        for line in Path(annotation_results_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    templates: list[dict[str, Any]] = []
    counts = {"core": 0, "edge": 0, "excluded": 0, "missing_prefix": 0}
    future_target_pointer_count = 0
    for raw in rows:
        result = raw.get("result") or {}
        episode = result.get("episode") or {}
        episode_id = episode.get("episode_id")
        if not isinstance(episode_id, str):
            raise ValidationError("annotation result missing episode.episode_id")
        prefix = prefix_by_id.get(episode_id)
        if prefix is None:
            counts["missing_prefix"] += 1
            continue
        stratum = prefix["stratum"]
        counts[stratum] = counts.get(stratum, 0) + 1
        available_ids = {
            item["evidence_id"] for item in prefix.get("event_references", [])
        }
        target_pointers = tuple(
            evidence_id
            for pointer in episode.get("source_pointers") or []
            if (evidence_id := _pointer_to_evidence_id(pointer)) is not None
        )
        future_pointers = tuple(
            item for item in target_pointers if item not in available_ids
        )
        future_target_pointer_count += len(future_pointers)
        decision = episode.get("reentry_decision")
        if decision == "RD01":
            expected_outcome = Outcome.INTERVENE.value
        elif decision == "RD02":
            expected_outcome = Outcome.NO_INTERVENTION.value
        else:
            expected_outcome = None
        targets = _target_primitives(episode) if expected_outcome == "INTERVENE" else ()
        primary_eligible = (
            prefix.get("status") == "READY"
            and stratum == "core"
            and expected_outcome is not None
            and (expected_outcome == "NO_INTERVENTION" or bool(targets))
        )
        templates.append(
            {
                "schema_version": CALIBRATION_REVIEW_SCHEMA,
                "case_id": episode_id,
                "participant_group": prefix["participant_group"],
                "stratum": stratum,
                "prefix": {
                    "status": prefix["status"],
                    "prefix_sha256": prefix.get("prefix_sha256"),
                    "onset": prefix.get("onset"),
                    "leakage_check": prefix.get("leakage_check"),
                    "available_evidence": prefix.get("event_references", []),
                },
                "review": {
                    "status": "PENDING",
                    "reviewer": None,
                    "reviewed_at": None,
                    "state": None,
                    "note": "State and evidence bindings must use prefix.available_evidence only.",
                },
                "calibration_target": {
                    "selector_visible": False,
                    "expected_outcome": expected_outcome,
                    "acceptable_primitives": list(targets),
                    "annotation_version": episode.get("annotation_version")
                    or result.get("annotation_version"),
                    "target_source_pointers": list(target_pointers),
                    "post_onset_target_pointers": list(future_pointers),
                    "codes": {
                        "reentry_decision": decision,
                        "recovery_object": episode.get("recovery_object") or [],
                        "user_reentry_action": episode.get("user_reentry_action") or [],
                        "evidence_type": episode.get("evidence_type") or [],
                        "decision_reclaim": episode.get("decision_reclaim") or [],
                    },
                },
                "eligibility": {
                    "primary_calibration": primary_eligible,
                    "edge_sensitivity_only": stratum == "edge",
                    "reason": (
                        "ELIGIBLE_AFTER_HUMAN_PREFIX_REVIEW"
                        if primary_eligible
                        else "NOT_PRIMARY_ELIGIBLE"
                    ),
                },
            }
        )
    templates.sort(key=lambda item: item["case_id"])
    report = {
        "schema_version": "retrace-calibration-template-report-v1",
        "annotation_case_count": len(rows),
        "template_count": len(templates),
        "strata": counts,
        "pending_human_review": sum(
            item["review"]["status"] == "PENDING" for item in templates
        ),
        "primary_eligible_after_review": sum(
            item["eligibility"]["primary_calibration"] for item in templates
        ),
        "post_onset_target_pointer_count": future_target_pointer_count,
        "calibration_ran": False,
        "reason": "No case enters fitting until review.status=APPROVED.",
    }
    return templates, report


def _load_approved_cases(path: str | Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"invalid calibration review JSON at line {line_number}"
            ) from exc
        if raw.get("schema_version") != CALIBRATION_REVIEW_SCHEMA:
            raise ValidationError(f"invalid calibration review schema at line {line_number}")
        case_id = raw.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValidationError(f"calibration case missing case_id at line {line_number}")
        if case_id in seen:
            raise ValidationError(f"duplicate calibration case_id: {case_id}")
        seen.add(case_id)
        if raw.get("review", {}).get("status") != "APPROVED":
            continue
        if not raw.get("eligibility", {}).get("primary_calibration"):
            continue
        if raw.get("prefix", {}).get("leakage_check") != "PASS":
            raise ValidationError(f"approved case {case_id} failed prefix leakage check")
        state_raw = raw.get("review", {}).get("state")
        state = DecisionState.from_dict(state_raw)
        if state.schema_version != "retrace-state-v2":
            raise ValidationError(f"approved case {case_id} must use retrace-state-v2")
        available_items = raw.get("prefix", {}).get("available_evidence", [])
        available = {
            item.get("evidence_id"): item
            for item in available_items
            if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
        }
        for item in state.evidence:
            prefix_item = available.get(item.evidence_id)
            if prefix_item is None:
                raise ValidationError(
                    f"approved case {case_id} uses post-prefix evidence"
                )
            for field, value in (
                ("locator", item.locator),
                ("sequence_index", item.sequence_index),
                ("content_sha256", item.content_sha256),
            ):
                if prefix_item.get(field) != value:
                    raise ValidationError(
                        f"approved case {case_id} evidence {item.evidence_id} "
                        f"does not match prefix {field}"
                    )
        target = raw.get("calibration_target") or {}
        if target.get("selector_visible") is not False:
            raise ValidationError(f"case {case_id} target must be selector_visible=false")
        expected = target.get("expected_outcome")
        if expected not in {Outcome.INTERVENE.value, Outcome.NO_INTERVENTION.value}:
            raise ValidationError(f"case {case_id} has invalid expected outcome")
        acceptable = target.get("acceptable_primitives") or []
        for primitive in acceptable:
            Primitive(primitive)
        if expected == Outcome.INTERVENE.value and not acceptable:
            raise ValidationError(f"case {case_id} needs acceptable primitives")
        participant_group = raw.get("participant_group")
        if not isinstance(participant_group, str) or not participant_group:
            raise ValidationError(f"approved case {case_id} needs participant_group")
        cases.append(
            {
                "case_id": case_id,
                "participant_group": participant_group,
                "state": state,
                "expected_outcome": expected,
                "acceptable_primitives": tuple(acceptable),
            }
        )
    return cases


def _weight_grid(step: float) -> Iterable[dict[str, float]]:
    units = round(1.0 / step)
    if units * step != 1.0 or units < 5:
        raise ValidationError("weight step must divide 1 and permit five positive weights")
    for cuts in itertools.combinations(range(1, units), 4):
        positions = (0, *cuts, units)
        values = [positions[index + 1] - positions[index] for index in range(5)]
        yield {
            key: round(value * step, 10)
            for key, value in zip(("O", "S", "D", "E", "W"), values)
        }


def _trial_policy(
    base: PolicySpec,
    weights: Mapping[str, float],
    gain: float,
    near_tie: float,
) -> PolicySpec:
    settings = {"weights": dict(weights), "gain": gain, "near_tie": near_tie}
    digest = hashlib.sha256(canonical_json(settings).encode("utf-8")).hexdigest()
    return replace(
        base,
        weights=MappingProxyType(dict(weights)),
        thresholds=replace(base.thresholds, gain=gain, near_tie=near_tie),
        config_hash=f"calibration-{digest}",
    )


def _evaluate_cases(
    cases: Sequence[Mapping[str, Any]],
    policy: PolicySpec,
    templates: Any,
) -> dict[str, Any]:
    engine = SelectionEngine(policy, templates)
    correct = 0
    expected_intervention = 0
    target_hits = 0
    over_interventions = 0
    under_interventions = 0
    for case in cases:
        result = engine.select(case["state"])
        selected_primitives = {
            selected.rsplit("-", 1)[0] for selected in result.selected_ids
        }
        if case["expected_outcome"] == Outcome.NO_INTERVENTION.value:
            case_correct = result.outcome is Outcome.NO_INTERVENTION
            over_interventions += int(not case_correct)
        else:
            expected_intervention += 1
            target_hit = bool(
                selected_primitives & set(case["acceptable_primitives"])
            )
            target_hits += int(target_hit)
            case_correct = result.outcome in {
                Outcome.INTERVENE,
                Outcome.PRESENT_CHOICES,
            } and target_hit
            under_interventions += int(
                result.outcome is Outcome.NO_INTERVENTION
            )
        correct += int(case_correct)
    count = len(cases)
    return {
        "case_count": count,
        "correct_count": correct,
        "expected_intervention_count": expected_intervention,
        "target_hit_count": target_hits,
        "over_intervention_count": over_interventions,
        "under_intervention_count": under_interventions,
        "accuracy": correct / count if count else 0.0,
        "target_primitive_recall": (
            target_hits / expected_intervention if expected_intervention else 1.0
        ),
        "over_intervention_rate": over_interventions / count if count else 0.0,
        "under_intervention_rate": under_interventions / count if count else 0.0,
    }


def calibrate_policy(
    review_path: str | Path,
    base_policy: PolicySpec,
    templates: Any,
    *,
    minimum_cases: int = 10,
    minimum_groups: int = 3,
    weight_step: float = 0.1,
    gain_values: Sequence[float] = (0.025, 0.05, 0.075),
    near_tie_values: Sequence[float] = (0.02, 0.03, 0.05),
) -> dict[str, Any]:
    cases = _load_approved_cases(review_path)
    groups = {case["participant_group"] for case in cases}
    if len(cases) < minimum_cases:
        raise ValidationError(
            f"calibration requires at least {minimum_cases} approved cases; got {len(cases)}"
        )
    if len(groups) < minimum_groups:
        raise ValidationError(
            f"calibration requires at least {minimum_groups} participant groups; got {len(groups)}"
        )
    settings_grid = [
        {"weights": weights, "gain": gain, "near_tie": near_tie}
        for weights in _weight_grid(weight_step)
        for gain in gain_values
        for near_tie in near_tie_values
    ]

    def search(search_cases: Sequence[Mapping[str, Any]]):
        best: tuple[tuple[float, ...], dict[str, Any], dict[str, Any]] | None = None
        for settings in settings_grid:
            policy = _trial_policy(
                base_policy,
                settings["weights"],
                settings["gain"],
                settings["near_tie"],
            )
            metrics = _evaluate_cases(search_cases, policy, templates)
            objective = (
                metrics["accuracy"],
                metrics["target_primitive_recall"],
                -metrics["over_intervention_rate"],
                -metrics["under_intervention_rate"],
                -sum(
                    abs(settings["weights"][key] - base_policy.weights[key])
                    for key in settings["weights"]
                ),
                -settings["gain"],
                -settings["near_tie"],
            )
            if best is None or objective > best[0]:
                best = (objective, settings, metrics)
        assert best is not None
        return best

    best = search(cases)
    ordered_groups = sorted(groups)
    fold_count = min(5, len(ordered_groups))
    group_fold = {
        group: index % fold_count for index, group in enumerate(ordered_groups)
    }
    fold_records: list[dict[str, Any]] = []
    for fold in range(fold_count):
        train = [
            case for case in cases if group_fold[case["participant_group"]] != fold
        ]
        test = [
            case for case in cases if group_fold[case["participant_group"]] == fold
        ]
        fold_best = search(train)
        fold_policy = _trial_policy(
            base_policy,
            fold_best[1]["weights"],
            fold_best[1]["gain"],
            fold_best[1]["near_tie"],
        )
        heldout_metrics = _evaluate_cases(test, fold_policy, templates)
        fold_records.append(
            {
                "fold": fold + 1,
                "train_case_count": len(train),
                "heldout_case_count": len(test),
                "heldout_metrics": heldout_metrics,
                "selected_settings": fold_best[1],
            }
        )
    total_heldout = sum(item["heldout_case_count"] for item in fold_records)
    total_expected_intervention = sum(
        item["heldout_metrics"]["expected_intervention_count"]
        for item in fold_records
    )
    aggregate_cv = {
        "accuracy": sum(
            item["heldout_metrics"]["correct_count"] for item in fold_records
        )
        / total_heldout,
        "target_primitive_recall": sum(
            item["heldout_metrics"]["target_hit_count"] for item in fold_records
        )
        / total_expected_intervention
        if total_expected_intervention
        else 1.0,
        "over_intervention_rate": sum(
            item["heldout_metrics"]["over_intervention_count"]
            for item in fold_records
        )
        / total_heldout,
        "under_intervention_rate": sum(
            item["heldout_metrics"]["under_intervention_count"]
            for item in fold_records
        )
        / total_heldout,
    }
    return {
        "schema_version": "retrace-calibration-result-v1",
        "status": "CALIBRATED_ON_APPROVED_PREFIX_STATES",
        "approved_case_count": len(cases),
        "participant_group_count": len(groups),
        "trial_count_per_search": len(settings_grid),
        "recommended": best[1],
        "training_metrics": best[2],
        "participant_group_cross_validation": {
            "fold_count": fold_count,
            "aggregate_metrics": aggregate_cv,
            "folds": fold_records,
        },
        "limitations": [
            "Parameters remain observational and policy-relative, not causal effect estimates.",
            "Grouped cross-validation estimates selection fit, not intervention effects.",
        ],
    }
