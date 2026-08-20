from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("state")
    args = parser.parse_args()
    # Allow the script to run from the repository without installation.
    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo / "src"))
    from retrace_selector.models import DecisionState, ValidationError

    try:
        raw = json.loads(Path(args.state).read_text(encoding="utf-8"))
        state = DecisionState.from_dict(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"valid": True, "schema_version": state.schema_version, "decision_id": state.decision_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
