from __future__ import annotations

import json
from pathlib import Path

from .config import canonical_json
from .models import SelectionResult, ValidationError


def write_json(path: str | Path, result: SelectionResult) -> None:
    result.validate_integrity()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def append_jsonl(path: str | Path, result: SelectionResult) -> bool:
    """Append a deterministic audit record; return False when already present."""

    target = Path(path)
    result.validate_integrity()
    new_payload = result.to_dict()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with target.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValidationError(
                        f"invalid existing audit JSONL at line {line_number}"
                    ) from exc
                if not isinstance(item, dict):
                    raise ValidationError(
                        f"invalid existing audit JSONL object at line {line_number}"
                    )
                if item.get("audit_id") == result.audit_id:
                    if canonical_json(item) == canonical_json(new_payload):
                        return False
                    raise ValidationError(
                        f"audit conflict for audit_id {result.audit_id}"
                    )
    with target.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(new_payload) + "\n")
    return True
