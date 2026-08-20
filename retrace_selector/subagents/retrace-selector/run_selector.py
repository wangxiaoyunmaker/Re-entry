from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--templates", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "src"))
    from retrace_selector.config import load_json
    from retrace_selector.models import ValidationError
    from retrace_selector.subagent import run_selector_request

    try:
        response = run_selector_request(
            load_json(args.request),
            policy_path=args.policy,
            templates_path=args.templates,
        )
    except ValidationError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    text = json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
