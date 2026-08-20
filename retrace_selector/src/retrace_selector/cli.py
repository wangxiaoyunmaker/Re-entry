from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .audit import append_jsonl, write_json
from .config import load_json, load_policy, load_templates
from .models import DecisionState, ValidationError
from .replay import replay_scenarios
from .selector import SelectionEngine


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = PROJECT_ROOT / "config" / "policy.v0.2.json"
DEFAULT_TEMPLATES = PROJECT_ROOT / "config" / "templates.v0.2.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retrace-selector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    select = subparsers.add_parser("select", help="select an intervention")
    select.add_argument("--policy", default=str(DEFAULT_POLICY))
    select.add_argument("--templates", default=str(DEFAULT_TEMPLATES))
    select.add_argument("--state", required=True)
    select.add_argument("--output")
    select.add_argument("--audit-jsonl")

    replay = subparsers.add_parser("replay", help="run canonical scenarios")
    replay.add_argument("--policy", default=str(DEFAULT_POLICY))
    replay.add_argument("--templates", default=str(DEFAULT_TEMPLATES))
    replay.add_argument("--states", required=True)
    replay.add_argument("--output")
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
        policy = load_policy(args.policy)
        templates = load_templates(args.templates)
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
