from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .audit import append_jsonl, write_json
from .calibration import build_calibration_review_templates, calibrate_policy
from .config import load_json, load_policy, load_templates
from .models import DecisionState, ValidationError
from .real_prefix import build_prefix_manifest, write_jsonl
from .replay import replay_scenarios
from .runtime_models import RuntimeEvent
from .runtime_service import RuntimeSelectorService
from .runtime_store import RuntimeStore
from .selector import SelectionEngine
from .selector_v06 import V06SelectionEngine
from .state_adapter import ClarificationRequired, adapt_state
from .strategy_registry import load_selection_policy, load_strategy_registry
from .subagent_cli import main as subagent_main
from .v03 import select_v03
from .online_v2 import OnlineInferenceService, load_registry_v2, load_selector_config_v2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retrace-selector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    select = subparsers.add_parser("select", help="select an intervention")
    select.add_argument("--policy", required=True)
    select.add_argument("--templates", required=True)
    select.add_argument("--state", required=True)
    select.add_argument("--output")
    select.add_argument("--audit-jsonl")

    select_v06_parser = subparsers.add_parser(
        "select-v06", help="select from an external v0.6 strategy registry"
    )
    select_v06_parser.add_argument("--policy", required=True)
    select_v06_parser.add_argument("--registry", required=True)
    select_v06_parser.add_argument("--state", required=True)
    select_v06_parser.add_argument("--output")
    select_v06_parser.add_argument("--audit-jsonl")

    runtime_select = subparsers.add_parser(
        "runtime-select",
        help="select from an in-memory/stdin Observer state with durable runtime memory",
    )
    runtime_select.add_argument("--database", required=True)
    runtime_select.add_argument("--policy", required=True)
    runtime_select.add_argument("--registry", required=True)
    runtime_select.add_argument(
        "--request", required=True, help="runtime request JSON path, or - for stdin"
    )
    runtime_select.add_argument("--mode", choices=("SHADOW", "LIVE"), default="SHADOW")
    runtime_select.add_argument("--timeout", type=float, default=2.0)
    runtime_select.add_argument("--output")

    runtime_event = subparsers.add_parser(
        "runtime-event", help="record presentation, reaction, verification, or reset events"
    )
    runtime_event.add_argument("--database", required=True)
    runtime_event.add_argument(
        "--event", required=True, help="runtime event JSON path, or - for stdin"
    )
    runtime_event.add_argument("--output")

    runtime_history = subparsers.add_parser(
        "runtime-history", help="read the durable history for one runtime session"
    )
    runtime_history.add_argument("--database", required=True)
    runtime_history.add_argument("--session-id", required=True)
    runtime_history.add_argument("--limit", type=int, default=100)
    runtime_history.add_argument("--output")

    runtime_health = subparsers.add_parser(
        "runtime-health", help="check active v0.6 configuration and LIVE readiness"
    )
    runtime_health.add_argument("--database", required=True)
    runtime_health.add_argument("--policy", required=True)
    runtime_health.add_argument("--registry", required=True)
    runtime_health.add_argument("--mode", choices=("SHADOW", "LIVE"), default="SHADOW")
    runtime_health.add_argument("--output")

    online_commands = {
        "online-ingest": "ingest a normalized v2 event",
        "online-select": "run the v2 C/S/A selector",
        "online-state": "poll the versioned v2 ReTrace state",
        "online-expose": "record a real intervention exposure",
        "online-choice": "record an explicit branch choice for PRESENT_CHOICES",
        "online-action": "record a user intervention action",
        "online-baseline": "submit an Occasion baseline evaluation",
        "online-evaluate": "submit a POST evaluation and close the chain",
        "online-linkage": "export the offline outcome linkage envelope",
        "online-replay": "replay a fixed normalized v2 event trace",
        "online-preferences": "get or set per-user intervention preferences",
        "online-profile": "get the persisted three-layer user intervention profile",
    }
    for command, help_text in online_commands.items():
        online = subparsers.add_parser(command, help=help_text)
        online.add_argument("--database", required=True)
        online.add_argument("--profiles", required=True, help="decision-object profile JSON")
        online.add_argument("--registry", required=True)
        online.add_argument("--config", required=True)
        online.add_argument("--chain-id")
        online.add_argument("--event")
        online.add_argument("--trace")
        online.add_argument("--evaluation")
        online.add_argument("--user-id")
        online.add_argument("--preference")
        online.add_argument("--exposure-id")
        online.add_argument("--selection-id")
        online.add_argument("--candidate-id")
        online.add_argument("--choice-condition")
        online.add_argument("--choice-basis")
        online.add_argument("--interaction-id")
        online.add_argument("--action")
        online.add_argument("--output")

    select_v03_parser = subparsers.add_parser(
        "select-v03", help="select an intervention from the v0.3 evidence-conditioned contract"
    )
    select_v03_parser.add_argument("--policy", required=True)
    select_v03_parser.add_argument("--templates", required=True)
    select_v03_parser.add_argument("--state", required=True)
    select_v03_parser.add_argument("--output")

    replay = subparsers.add_parser("replay", help="run canonical scenarios")
    replay.add_argument("--policy", required=True)
    replay.add_argument("--templates", required=True)
    replay.add_argument("--states", required=True)
    replay.add_argument("--output")

    prefixes = subparsers.add_parser(
        "build-prefixes", help="build leakage-guarded real episode prefix manifests"
    )
    prefixes.add_argument("--core-inventory", required=True)
    prefixes.add_argument("--edge-inventory")
    prefixes.add_argument("--excluded-inventory")
    prefixes.add_argument("--annotations")
    prefixes.add_argument("--output-dir", required=True)

    calibrate = subparsers.add_parser(
        "calibrate", help="fit policy parameters on approved prefix reviews"
    )
    calibrate.add_argument("--policy", required=True)
    calibrate.add_argument("--templates", required=True)
    calibrate.add_argument("--reviews", required=True)
    calibrate.add_argument("--targets", required=True)
    calibrate.add_argument("--prefix-manifest", required=True)
    calibrate.add_argument("--output", required=True)
    calibrate.add_argument("--minimum-cases", type=int, default=10)
    calibrate.add_argument("--minimum-groups", type=int, default=3)
    return parser


def _emit(data: dict, output: str | None) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _load_json_source(source: str) -> dict:
    if source == "-":
        try:
            raw = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"cannot load JSON from stdin: {exc}") from exc
    else:
        raw = load_json(source)
    if not isinstance(raw, dict):
        raise ValidationError("runtime input must be a JSON object")
    return raw


def _load_json_any_source(source: str):
    if source == "-":
        try:
            return json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"cannot load JSON from stdin: {exc}") from exc
    return load_json(source)


def _online_service(args) -> OnlineInferenceService:
    profiles = load_json(args.profiles)
    if not isinstance(profiles, dict):
        raise ValidationError("online profiles must be an object keyed by profile_id")
    return OnlineInferenceService(
        database_path=args.database,
        profiles=profiles,
        registry=load_registry_v2(args.registry),
        config=load_selector_config_v2(args.config),
    )


def main(argv: list[str] | None = None) -> int:
    effective_argv = sys.argv[1:] if argv is None else argv
    if effective_argv and effective_argv[0] == "subagent":
        return subagent_main(effective_argv[1:])
    args = _parser().parse_args(argv)
    try:
        if args.command == "build-prefixes":
            inventories = [(args.core_inventory, "core")]
            if args.edge_inventory:
                inventories.append((args.edge_inventory, "edge"))
            if args.excluded_inventory:
                inventories.append((args.excluded_inventory, "excluded"))
            records, report = build_prefix_manifest(inventories)
            output_dir = Path(args.output_dir)
            write_jsonl(output_dir / "prefix_manifest.jsonl", records)
            _emit(report, str(output_dir / "prefix_build_report.json"))
            if args.annotations:
                reviews, targets, review_report = build_calibration_review_templates(
                    records, args.annotations
                )
                write_jsonl(output_dir / "calibration_review_template.jsonl", reviews)
                write_jsonl(output_dir / "calibration_targets.jsonl", targets)
                _emit(
                    review_report,
                    str(output_dir / "calibration_template_report.json"),
                )
            _emit(report, None)
            return 0 if report["leakage_failures"] == 0 else 2

        if args.command == "runtime-select":
            service = RuntimeSelectorService.from_paths(
                database_path=args.database,
                registry_path=args.registry,
                policy_path=args.policy,
                execution_mode=args.mode,
                selection_timeout_seconds=args.timeout,
            )
            response = service.select(_load_json_source(args.request))
            _emit(response, args.output)
            return 0

        if args.command == "runtime-event":
            store = RuntimeStore(args.database)
            event = RuntimeEvent.from_dict(_load_json_source(args.event))
            recorded = store.record_event(event)
            snapshot = store.session_snapshot(event.session_id)
            _emit(
                {
                    "schema_version": "retrace-runtime-event-response-v0.6",
                    "event_id": event.event_id,
                    "recorded": recorded,
                    "idempotent_replay": not recorded,
                    "session": {
                        "session_id": snapshot.session_id,
                        "recent_intervention_count": snapshot.recent_intervention_count,
                        "active_verification": snapshot.active_verification,
                        "reset_at": snapshot.reset_at,
                    },
                },
                args.output,
            )
            return 0

        if args.command == "runtime-history":
            history = RuntimeStore(args.database).session_history(
                args.session_id, limit=args.limit
            )
            _emit(history, args.output)
            return 0

        if args.command == "runtime-health":
            service = RuntimeSelectorService.from_paths(
                database_path=args.database,
                registry_path=args.registry,
                policy_path=args.policy,
                execution_mode=args.mode,
            )
            _emit(service.health(), args.output)
            return 0

        if args.command.startswith("online-"):
            service = _online_service(args)
            if args.command == "online-ingest":
                if not args.event:
                    raise ValidationError("--event is required for online-ingest")
                result = service.ingest_event(_load_json_source(args.event))
            elif args.command == "online-replay":
                if not args.trace:
                    raise ValidationError("--trace is required for online-replay")
                trace = _load_json_any_source(args.trace)
                if not isinstance(trace, list):
                    raise ValidationError("online replay trace must be an array")
                result = service.replay_trace(trace)
            elif args.command == "online-select":
                if not args.chain_id:
                    raise ValidationError("--chain-id is required for online-select")
                result = service.select(args.chain_id)
            elif args.command == "online-state":
                if not args.chain_id:
                    raise ValidationError("--chain-id is required for online-state")
                result = service.get_retrace_state(args.chain_id)
            elif args.command == "online-expose":
                if not args.chain_id or not args.exposure_id:
                    raise ValidationError("--chain-id and --exposure-id are required for online-expose")
                result = service.expose(args.chain_id, exposure_id=args.exposure_id, interaction_id=args.interaction_id, selection_decision_id=args.selection_id, selected_candidate_id=args.candidate_id)
            elif args.command == "online-choice":
                if not args.chain_id or not args.selection_id or not args.candidate_id or not args.choice_condition or not args.choice_basis:
                    raise ValidationError("--chain-id, --selection-id, --candidate-id, --choice-condition, and --choice-basis are required for online-choice")
                result = service.record_choice(args.chain_id, selection_decision_id=args.selection_id, selected_candidate_id=args.candidate_id, choice_condition=args.choice_condition, choice_basis=args.choice_basis)
            elif args.command == "online-action":
                if not args.chain_id or not args.action:
                    raise ValidationError("--chain-id and --action are required for online-action")
                result = service.record_action(args.chain_id, action=args.action, interaction_id=args.interaction_id)
            elif args.command in {"online-baseline", "online-evaluate"}:
                if not args.chain_id or not args.evaluation:
                    raise ValidationError("--chain-id and --evaluation are required for online evaluation commands")
                evaluation = _load_json_source(args.evaluation)
                evaluation_id = evaluation.get("evaluation_id")
                if not isinstance(evaluation_id, str) or not evaluation_id.strip():
                    raise ValidationError("evaluation_id is required")
                responses = evaluation.get("responses", {})
                skipped = evaluation.get("skipped_dimensions", evaluation.get("skipped", []))
                if args.command == "online-baseline":
                    result = service.submit_occasion_baseline(args.chain_id, evaluation_id=evaluation_id, responses=responses, skipped_dimensions=skipped, question_set_version=evaluation.get("question_set_version", "CSA-LIKERT-V1"), interaction_id=args.interaction_id, as_of_event_id=evaluation.get("as_of_event_id"), timeout=bool(evaluation.get("timeout", False)))
                else:
                    result = service.submit_evaluation(args.chain_id, evaluation_id=evaluation_id, responses=responses, skipped_dimensions=skipped)
            elif args.command == "online-linkage":
                if not args.chain_id:
                    raise ValidationError("--chain-id is required for online-linkage")
                result = service.get_chain_outcome_linkage(args.chain_id)
            elif args.command == "online-profile":
                if not args.user_id:
                    raise ValidationError("--user-id is required for online-profile")
                result = service.get_user_profile(args.user_id)
            elif args.command == "online-preferences":
                if not args.user_id:
                    raise ValidationError("--user-id is required for online-preferences")
                if not args.preference:
                    result = service.get_user_preferences(args.user_id)
                else:
                    preference = _load_json_source(args.preference)
                    if not isinstance(preference, dict):
                        raise ValidationError("online preference must be an object")
                    required = {"frequency_preference", "intensity_preference"}
                    if not required.issubset(preference):
                        raise ValidationError("preference requires frequency_preference and intensity_preference")
                    manual_lock = preference.get("manual_lock", False)
                    if not isinstance(manual_lock, bool):
                        raise ValidationError("manual_lock must be boolean")
                    result = service.set_user_preferences(
                        args.user_id,
                        frequency_preference=preference["frequency_preference"],
                        intensity_preference=preference["intensity_preference"],
                        mode=preference.get("mode", "AUTO"),
                        manual_lock=manual_lock,
                        session_id=preference.get("session_id"),
                    )
            _emit(result, args.output)
            return 0

        if args.command == "select-v06":
            policy_v06, policy_hash = load_selection_policy(args.policy)
            registry = load_strategy_registry(args.registry)
            try:
                state_v06 = adapt_state(load_json(args.state))
            except ClarificationRequired as exc:
                _emit(
                    {
                        "contract_version": "retrace-selector-v0.6",
                        "outcome": "REQUEST_CLARIFICATION",
                        "reason_codes": ["STATE_ABSTAIN"],
                        "message": str(exc),
                    },
                    args.output,
                )
                return 0
            result_v06 = V06SelectionEngine(
                registry,
                policy_v06,
                policy_hash=policy_hash,
            ).select(state_v06)
            if args.output:
                write_json(args.output, result_v06)
            else:
                _emit(result_v06.to_dict(), None)
            if args.audit_jsonl:
                append_jsonl(args.audit_jsonl, result_v06)
            return 0

        policy = load_policy(args.policy)
        templates = load_templates(args.templates)
        if args.command == "calibrate":
            calibration = calibrate_policy(
                args.reviews,
                args.targets,
                args.prefix_manifest,
                policy,
                templates,
                minimum_cases=args.minimum_cases,
                minimum_groups=args.minimum_groups,
            )
            _emit(calibration, args.output)
            return 0

        engine = SelectionEngine(policy, templates)
        if args.command == "select-v03":
            result = select_v03(load_json(args.state), engine)
            _emit(result, args.output)
            return 0
        if args.command == "select":
            state = DecisionState.from_dict(load_json(args.state))
            result = engine.select(state)
            if args.output:
                write_json(args.output, result)
            else:
                _emit(result.to_dict(), None)
            if args.audit_jsonl:
                append_jsonl(args.audit_jsonl, result)
            return 0
        replay = replay_scenarios(load_json(args.states), engine)
        _emit(replay, args.output)
        return 0 if replay["summary"]["failed"] == 0 else 2
    except (ValidationError, sqlite3.Error) as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
