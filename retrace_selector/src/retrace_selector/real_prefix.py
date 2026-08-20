from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .config import canonical_json
from .models import ValidationError


PREFIX_SCHEMA_VERSION = "retrace-prefix-manifest-v1"
_QUALIFIED_BOUNDARY = re.compile(r"(?P<context>context_\d+):R(?P<record>\d+)")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _participant_group(participant_id: str) -> str:
    material = f"retrace-prefix-v1:{participant_id}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def _load_transcript(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder(strict=False)
    events: list[dict[str, Any]] = []
    position = 0
    while position < len(content):
        while position < len(content) and content[position].isspace():
            position += 1
        if position >= len(content):
            break
        start = position
        try:
            item, position = decoder.raw_decode(content, position)
        except json.JSONDecodeError as exc:
            line_number = content.count("\n", 0, exc.pos) + 1
            raise ValidationError(
                f"invalid transcript JSON at {path}:{line_number}"
            ) from exc
        if not isinstance(item, dict):
            line_number = content.count("\n", 0, start) + 1
            raise ValidationError(
                f"transcript item must be an object at {path}:{line_number}"
            )
        for field in ("source_context", "record_index", "role"):
            if field not in item:
                line_number = content.count("\n", 0, start) + 1
                raise ValidationError(
                    f"transcript item missing {field} at {path}:{line_number}"
                )
        events.append(item)
    if not events:
        raise ValidationError(f"empty transcript: {path}")
    return events


def _qualified_position(raw: str, events: list[dict[str, Any]]) -> int | None:
    match = _QUALIFIED_BOUNDARY.fullmatch(raw.strip())
    if not match:
        return None
    context = match.group("context")
    record_index = int(match.group("record"))
    hits = [
        index
        for index, item in enumerate(events)
        if item["source_context"] == context and item["record_index"] == record_index
    ]
    return hits[0] if len(hits) == 1 else None


def resolve_onset_position(
    row: Mapping[str, str], events: list[dict[str, Any]]
) -> tuple[int | None, str]:
    """Resolve an onset without guessing across repeated context-local indices."""

    for field in ("proposed_start", "reentry_onset"):
        raw = (row.get(field) or "").strip()
        position = _qualified_position(raw, events)
        if position is not None:
            return position, f"{field}:qualified"

    raw = (row.get("reentry_onset") or "").strip()
    if raw.isdigit():
        record_index = int(raw)
        hits = [
            index
            for index, item in enumerate(events)
            if item["record_index"] == record_index
        ]
        if len(hits) == 1:
            return hits[0], "reentry_onset:unique_record_index"
        if len(hits) > 1:
            return None, "AMBIGUOUS_CONTEXT_LOCAL_RECORD_INDEX"
    return None, "UNRESOLVED_ONSET"


def _event_reference(
    strict_id: str, sequence_index: int, item: Mapping[str, Any], onset_position: int
) -> dict[str, Any]:
    context = str(item["source_context"])
    record_index = int(item["record_index"])
    content = str(item.get("audit_text") or item.get("text") or "")
    return {
        "evidence_id": f"{strict_id}:{context}:R{record_index}",
        "locator": f"{strict_id}/transcript.jsonl#{context}:R{record_index}",
        "sequence_index": sequence_index,
        "source_context": context,
        "record_index": record_index,
        "observed_at": item.get("timestamp"),
        "role": item["role"],
        "content_sha256": _sha256_bytes(content.encode("utf-8")),
        "temporal_role": "TRIGGER" if sequence_index == onset_position else "PREFIX",
        "available_at_decision": True,
    }


def build_prefix_record(row: Mapping[str, str], *, stratum: str) -> dict[str, Any]:
    strict_id = (row.get("strict_id") or "").strip()
    if not strict_id:
        raise ValidationError("strict inventory row requires strict_id")
    transcript_path = Path(row.get("transcript_path") or "")
    base: dict[str, Any] = {
        "schema_version": PREFIX_SCHEMA_VERSION,
        "episode_id": strict_id,
        "stratum": stratum,
        "participant_group": _participant_group(row.get("participant_id") or ""),
        "source_locator": f"{strict_id}/transcript.jsonl",
    }
    if not transcript_path.is_file():
        return {
            **base,
            "status": "REVIEW_REQUIRED",
            "reason": "TRANSCRIPT_NOT_FOUND",
        }
    events = _load_transcript(transcript_path)
    onset_position, resolution = resolve_onset_position(row, events)
    if onset_position is None:
        return {
            **base,
            "status": "REVIEW_REQUIRED",
            "reason": resolution,
            "source_event_count": len(events),
            "transcript_sha256": _sha256_bytes(transcript_path.read_bytes()),
        }
    prefix = events[: onset_position + 1]
    references = [
        _event_reference(strict_id, index, item, onset_position)
        for index, item in enumerate(prefix)
    ]
    if any(item["sequence_index"] > onset_position for item in references):
        raise ValidationError(f"future event leaked into prefix for {strict_id}")
    onset = references[-1]
    prefix_digest_material = [
        {
            "source_context": item["source_context"],
            "record_index": item["record_index"],
            "timestamp": item.get("timestamp"),
            "role": item["role"],
            "text": item.get("audit_text") or item.get("text") or "",
        }
        for item in prefix
    ]
    return {
        **base,
        "status": "READY",
        "reason": None,
        "onset_resolution": resolution,
        "onset": {
            "sequence_index": onset_position,
            "source_context": onset["source_context"],
            "record_index": onset["record_index"],
            "locator": onset["locator"],
        },
        "source_event_count": len(events),
        "prefix_event_count": len(prefix),
        "future_event_count": len(events) - len(prefix),
        "transcript_sha256": _sha256_bytes(transcript_path.read_bytes()),
        "prefix_sha256": _sha256_bytes(
            canonical_json(prefix_digest_material).encode("utf-8")
        ),
        "contains_raw_text": False,
        "leakage_check": "PASS",
        "event_references": references,
    }


def load_inventory(path: str | Path, *, stratum: str) -> list[dict[str, Any]]:
    target = Path(path)
    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        return [build_prefix_record(row, stratum=stratum) for row in csv.DictReader(handle)]


def build_prefix_manifest(
    inventories: Iterable[tuple[str | Path, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path, stratum in inventories:
        records.extend(load_inventory(path, stratum=stratum))
    episode_ids = [item["episode_id"] for item in records]
    if len(episode_ids) != len(set(episode_ids)):
        raise ValidationError("prefix inventories contain duplicate episode IDs")
    records.sort(key=lambda item: item["episode_id"])
    ready = [item for item in records if item["status"] == "READY"]
    reasons: dict[str, int] = {}
    for item in records:
        if item["status"] != "READY":
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    report = {
        "schema_version": "retrace-prefix-build-report-v1",
        "case_count": len(records),
        "ready_count": len(ready),
        "review_required_count": len(records) - len(ready),
        "review_reasons": dict(sorted(reasons.items())),
        "raw_text_exported": False,
        "leakage_failures": sum(
            item.get("leakage_check") != "PASS" for item in ready
        ),
        "strata": {
            stratum: sum(item["stratum"] == stratum for item in records)
            for stratum in sorted({item["stratum"] for item in records})
        },
    }
    return records, report


def write_jsonl(path: str | Path, records: Iterable[Mapping[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(canonical_json(record) + "\n" for record in records),
        encoding="utf-8",
    )
