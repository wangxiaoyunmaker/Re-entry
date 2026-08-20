from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .config import load_json
from .models import ValidationError
from .subagent import run_selector_request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retrace-selector-subagent")
    parser.add_argument("--request", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--templates", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        response = run_selector_request(
            load_json(args.request),
            policy_path=args.policy,
            templates_path=args.templates,
        )
    except ValidationError as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n")
        return 2
    text = json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
