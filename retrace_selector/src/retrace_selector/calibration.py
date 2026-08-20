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
from .real_prefix import PREFIX_SCHEMA_VERSION
from .version import ENGINE_VERSION


CALIBRATION_REVIEW_SCHEMA = "retrace-calibration-review-v1"
CALIBRATION_TARGET_SCHEMA = "retrace-calibration-target-v1"
_SOURCE_POINTER = re.compile(r"(?P<episode>SRE-\d+)/(?P<context>context_[^/]+)/R(?P<record>\d+)$")


def _file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    prefix_by_id = {item["episode_id"]: item for item in prefixes}
    rows = [
        json.loads(line)
        for line in Path(annotation_results_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    templates: list[dict[str, Any]] = []
    target_records: list[dict[str, Any]] = []
    counts = {"core": 0, "edge": 0, "excluded": 0, "missing_prefix": 0}
    future_target_pointer_count = 0
    primary_eligible_count = 0
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
        primary_eligible_count += int(primary_eligible)
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
                    "tool_version": None,
                    "state": None,
                    "note": "State and evidence bindings must use prefix.available_evidence only.",
                },
            }
        )
        target_records.append(
            {
                "schema_version": CALIBRATION_TARGET_SCHEMA,
                "case_id": episode_id,
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
            }
        )
    templates.sort(key=lambda item: item["case_id"])
    target_records.sort(key=lambda item: item["case_id"])
    report = {
        "schema_version": "retrace-calibration-template-report-v1",
        "annotation_source_sha256": _file_sha256(annotation_results_path),
        "annotation_case_count": len(rows),
        "template_count": len(templates),
        "strata": counts,
        "pending_human_review": sum(
            item["review"]["status"] == "PENDING" for item in templates
        ),
        "primary_eligible_after_review": primary_eligible_count,
        "post_onset_target_pointer_count": future_target_pointer_count,
        "calibration_ran": False,
        "reason": "No case enters fitting until review.status=APPROVED.",
    }
    return templates, target_records, report


def _load_targets(path: str | Path) -> dict[str, dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"invalid calibration target JSON at line {line_number}"
            ) from exc
        if raw.get("schema_version") != CALIBRATION_TARGET_SCHEMA:
            raise ValidationError(f"invalid calibration target schema at line {line_number}")
        case_id = raw.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValidationError(f"calibration target missing case_id at line {line_number}")
        if case_id in targets:
            raise ValidationError(f"duplicate calibration target case_id: {case_id}")
        targets[case_id] = raw
    return targets


def _load_prefixes(path: str | Path) -> dict[str, dict[str, Any]]:
    prefixes: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"invalid prefix manifest JSON at line {line_number}"
            ) from exc
        if raw.get("schema_version") != PREFIX_SCHEMA_VERSION:
            raise ValidationError(f"invalid prefix manifest schema at line {line_number}")
        case_id = raw.get("episode_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValidationError(f"prefix manifest missing episode_id at line {line_number}")
        if case_id in prefixes:
            raise ValidationError(f"duplicate prefix manifest episode_id: {case_id}")
        if raw.get("status") == "READY":
            onset = raw.get("onset")
            references = raw.get("event_references")
            if not isinstance(onset, dict) or not isinstance(references, list) or not references:
                raise ValidationError(f"READY prefix {case_id} needs onset and evidence")
            onset_sequence = onset.get("sequence_index")
            if isinstance(onset_sequence, bool) or not isinstance(onset_sequence, int) or onset_sequence < 0:
                raise ValidationError(f"READY prefix {case_id} has invalid onset sequence")
            ids = [item.get("evidence_id") for item in references if isinstance(item, dict)]
            locators = [item.get("locator") for item in references if isinstance(item, dict)]
            sequences = [item.get("sequence_index") for item in references if isinstance(item, dict)]
            if len(ids) != len(references) or any(not isinstance(item, str) or not item for item in ids):
                raise ValidationError(f"READY prefix {case_id} has invalid evidence IDs")
            if any(not isinstance(item, str) or not item for item in locators):
                raise ValidationError(f"READY prefix {case_id} has invalid evidence locators")
            if any(
                isinstance(item, bool) or not isinstance(item, int) or item < 0
                for item in sequences
            ):
                raise ValidationError(f"READY prefix {case_id} has invalid evidence sequence")
            if len(set(ids)) != len(ids) or len(set(locators)) != len(locators):
                raise ValidationError(f"READY prefix {case_id} has duplicate evidence references")
            if sequences != list(range(onset_sequence + 1)):
                raise ValidationError(f"READY prefix {case_id} has invalid evidence sequence")
            if any(item.get("available_at_decision") is not True for item in references):
                raise ValidationError(f"READY prefix {case_id} contains unavailable evidence")
            last = references[-1]
            for field in ("locator", "source_context", "record_index"):
                if last.get(field) != onset.get(field):
                    raise ValidationError(f"READY prefix {case_id} onset does not match final event")
            if raw.get("prefix_event_count") != len(references):
                raise ValidationError(f"READY prefix {case_id} prefix count mismatch")
            for hash_field in ("prefix_sha256", "transcript_sha256"):
                digest = raw.get(hash_field)
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    raise ValidationError(f"READY prefix {case_id} has invalid {hash_field}")
            if raw.get("leakage_check") != "PASS":
                raise ValidationError(f"READY prefix {case_id} failed leakage check")
        prefixes[case_id] = raw
    return prefixes


def _load_approved_cases(
    review_path: str | Path,
    target_path: str | Path,
    prefix_manifest_path: str | Path,
) -> list[dict[str, Any]]:
    targets = _load_targets(target_path)
    prefixes = _load_prefixes(prefix_manifest_path)
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        Path(review_path).read_text(encoding="utf-8").splitlines(), start=1
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
        allowed_review_keys = {
            "schema_version",
            "case_id",
            "participant_group",
            "stratum",
            "prefix",
            "review",
        }
        unknown = set(raw) - allowed_review_keys
        if unknown:
            raise ValidationError(
                f"calibration review unknown fields at line {line_number}: {sorted(unknown)}"
            )
        case_id = raw.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValidationError(f"calibration case missing case_id at line {line_number}")
        if case_id in seen:
            raise ValidationError(f"duplicate calibration case_id: {case_id}")
        seen.add(case_id)
        review = raw.get("review")
        if not isinstance(review, dict):
            raise ValidationError(f"calibration case {case_id} missing review object")
        review_status = review.get("status")
        if review_status not in {"PENDING", "APPROVED", "REJECTED"}:
            raise ValidationError(f"calibration case {case_id} has invalid review status")
        if review_status != "APPROVED":
            continue
        for field in ("reviewer", "reviewed_at", "tool_version"):
            value = review.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"approved case {case_id} needs review.{field}")
        prefix = prefixes.get(case_id)
        if prefix is None:
            raise ValidationError(f"approved case {case_id} has no authoritative prefix")
        if prefix.get("stratum") != "core":
            continue
        if prefix.get("status") != "READY" or prefix.get("leakage_check") != "PASS":
            raise ValidationError(f"approved case {case_id} failed prefix leakage check")
        review_prefix = raw.get("prefix") or {}
        for field in ("status", "prefix_sha256", "onset", "leakage_check"):
            if review_prefix.get(field) != prefix.get(field):
                raise ValidationError(
                    f"approved case {case_id} review does not match prefix {field}"
                )
        if review_prefix.get("available_evidence") != prefix.get("event_references"):
            raise ValidationError(
                f"approved case {case_id} review evidence differs from prefix manifest"
            )
        if raw.get("participant_group") != prefix.get("participant_group"):
            raise ValidationError(
                f"approved case {case_id} participant group differs from prefix manifest"
            )
        state_raw = review.get("state")
        state = DecisionState.from_dict(state_raw)
        if state.schema_version != "retrace-state-v2":
            raise ValidationError(f"approved case {case_id} must use retrace-state-v2")
        if state.decision_id != case_id:
            raise ValidationError(
                f"approved case {case_id} state decision_id must match case_id"
            )
        available_items = prefix.get("event_references", [])
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
        target = targets.get(case_id)
        if target is None:
            raise ValidationError(f"approved case {case_id} has no calibration target")
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
        participant_group = prefix.get("participant_group")
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
    settings = {
        "base_policy_config_hash": base.config_hash,
        "calibration_schema": "retrace-calibration-result-v1",
        "weights": dict(weights),
        "gain": gain,
        "near_tie": near_tie,
    }
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
                result.outcome not in {Outcome.INTERVENE, Outcome.PRESENT_CHOICES}
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
    target_path: str | Path,
    prefix_manifest_path: str | Path,
    base_policy: PolicySpec,
    templates: Any,
    *,
    minimum_cases: int = 10,
    minimum_groups: int = 3,
    weight_step: float = 0.1,
    gain_values: Sequence[float] = (0.025, 0.05, 0.075),
    near_tie_values: Sequence[float] = (0.02, 0.03, 0.05),
) -> dict[str, Any]:
    if minimum_groups < 3:
        raise ValidationError("calibration minimum_groups cannot be lower than 3")
    cases = _load_approved_cases(review_path, target_path, prefix_manifest_path)
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
        "engine_version": ENGINE_VERSION,
        "target_mapping_version": CALIBRATION_TARGET_SCHEMA,
        "status": "CALIBRATED_ON_APPROVED_PREFIX_STATES",
        "approved_case_count": len(cases),
        "participant_group_count": len(groups),
        "input_hashes": {
            "reviews_sha256": _file_sha256(review_path),
            "targets_sha256": _file_sha256(target_path),
            "prefix_manifest_sha256": _file_sha256(prefix_manifest_path),
            "base_policy_config_hash": base_policy.config_hash,
        },
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
