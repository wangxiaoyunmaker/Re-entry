from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .audit import append_jsonl, write_json
from .calibration import build_calibration_review_templates, calibrate_policy
from .config import load_json, load_policy, load_templates
from .models import DecisionState, ValidationError
from .real_prefix import build_prefix_manifest, write_jsonl
from .replay import replay_scenarios
from .selector import SelectionEngine


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retrace-selector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    select = subparsers.add_parser("select", help="select an intervention")
    select.add_argument("--policy", required=True)
    select.add_argument("--templates", required=True)
    select.add_argument("--state", required=True)
    select.add_argument("--output")
    select.add_argument("--audit-jsonl")

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


def main(argv: list[str] | None = None) -> int:
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
    except ValidationError as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
