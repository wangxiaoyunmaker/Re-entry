"""Clean configuration-level optimization harness for P0 candidate discovery.

All configurations use the same corrected participant-isolated Coder-1 holdout.
Coder-1 remains a provisional reference, not adjudicated Gold.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from collections import Counter
from functools import lru_cache
from pathlib import Path
from statistics import mean
from typing import Any

import reentry_p0_discovery as p0


ROOT = p0.ROOT
PROMPT_DIR = ROOT / "prompts/reentry_p0/optimization_v4"
SYSTEM_PATH = PROMPT_DIR / "system.txt"
DEVELOPER_PATH = PROMPT_DIR / "developer_verdict.xml"
DEVELOPER_REFINED_PATH = PROMPT_DIR / "developer_verdict_refined.xml"
TRACE6_PATH = PROMPT_DIR / "few_shot_verdict_trace6.xml"
TRACE6_BALANCED_PATH = PROMPT_DIR / "few_shot_verdict_trace6_balanced.xml"
TRACE5_BOUNDARY_PATH = PROMPT_DIR / "few_shot_verdict_trace5_boundary.xml"
NO_FEWSHOT_PATH = PROMPT_DIR / "few_shot_none.xml"
V2_PROMPT_DIR = ROOT / "prompts/reentry_p0/v2_candidate"
V2_SYSTEM_PATH = V2_PROMPT_DIR / "system.txt"
V2_DEVELOPER_IMPLICIT_PATH = V2_PROMPT_DIR / "developer_gate_implicit.xml"
V2_DEVELOPER_RECALL_PATH = V2_PROMPT_DIR / "developer_gate_recall.xml"
V2_DEVELOPER_EXPLICIT_PATH = V2_PROMPT_DIR / "developer_gate_explicit.xml"
V2_FEWSHOT_IMPLICIT_PATH = V2_PROMPT_DIR / "few_shot_gate_implicit.xml"
V2_FEWSHOT_EXPLICIT_PATH = V2_PROMPT_DIR / "few_shot_gate_explicit.xml"
V21_PROMPT_DIR = ROOT / "prompts/reentry_p0/v2_1_candidate"
V21_SYSTEM_PATH = V21_PROMPT_DIR / "system.txt"
V21_DEVELOPER_PATH = V21_PROMPT_DIR / "developer_gate_v21.xml"
V21_FEWSHOT_PATH = V21_PROMPT_DIR / "few_shot_gate_v21.xml"
V22_PROMPT_DIR = ROOT / "prompts/reentry_p0/v2_2_candidate"
V22_DEVELOPER_PATH = V22_PROMPT_DIR / "developer_gate_v22.xml"
V23_PROMPT_DIR = ROOT / "prompts/reentry_p0/v2_3_candidate"
V23_DEVELOPER_PATH = V23_PROMPT_DIR / "developer_gate_v23.xml"
V24_PROMPT_DIR = ROOT / "prompts/reentry_p0/v2_4_candidate"
V24_DEVELOPER_ADDENDUM_PATH = V24_PROMPT_DIR / "developer_gate_v24_addendum.xml"
V25_PROMPT_DIR = ROOT / "prompts/reentry_p0/v2_5_candidate"
V25_DEVELOPER_ADDENDUM_PATH = V25_PROMPT_DIR / "developer_gate_v25_addendum.xml"
V25_FEWSHOT_COMPACT6_PATH = V25_PROMPT_DIR / "few_shot_gate_v25_compact6.xml"
V21_DEV_PATH = p0.OUT_DIR / "p0_v21_development_23.jsonl"
V22_HOLDOUT_PATH = p0.OUT_DIR / "p0_v22_current_theory_holdout/locked_evaluation_36.jsonl"
V23_HOLDOUT_PATH = p0.OUT_DIR / "p0_v23_current_theory_holdout/locked_evaluation_24.jsonl"
V24_HOLDOUT_PATH = p0.OUT_DIR / "p0_v24_current_theory_holdout/locked_evaluation_24.jsonl"
V25_HOLDOUT_PATH = p0.OUT_DIR / "p0_v25_current_theory_holdout/locked_evaluation_20.jsonl"
SAMPLE_PATH = p0.OUT_DIR / "coder1_prompt_evaluation_v3/coder1_holdout_sample.jsonl"
OUT_DIR = p0.OUT_DIR / "prompt_optimization_v4"
RUN_DIR = OUT_DIR / "runs"
REPORT_PATH = OUT_DIR / "comparison_report.md"

DECISIONS = {"RETAIN_STRONG", "RETAIN_POSSIBLE", "DO_NOT_RETAIN"}

CONFIGS: dict[str, dict[str, Any]] = {
    "VERDICT_SEPARATE_C4": {
        "input_style": "separate",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 4,
        "fewshot": TRACE6_PATH,
        "thinking_disabled": False,
    },
    "VERDICT_TIMELINE_COMPACT_C4": {
        "input_style": "timeline",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 4,
        "fewshot": TRACE6_PATH,
        "thinking_disabled": False,
    },
    "VERDICT_TIMELINE_COMPACT_C4_RULES14": {
        "input_style": "timeline",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 4,
        "fewshot": TRACE6_PATH,
        "developer": DEVELOPER_REFINED_PATH,
        "thinking_disabled": False,
    },
    "GATE_V2_IMPLICIT_C4": {
        "input_style": "timeline",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 4,
        "system": V2_SYSTEM_PATH,
        "developer": V2_DEVELOPER_IMPLICIT_PATH,
        "fewshot": V2_FEWSHOT_IMPLICIT_PATH,
        "explicit_gates": False,
        "thinking_disabled": False,
    },
    "GATE_V2_RECALL_C4": {
        "input_style": "timeline",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 4,
        "system": V2_SYSTEM_PATH,
        "developer": V2_DEVELOPER_RECALL_PATH,
        "fewshot": V2_FEWSHOT_IMPLICIT_PATH,
        "explicit_gates": False,
        "thinking_disabled": False,
    },
    "GATE_V21_C4": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 4,
        "system": V21_SYSTEM_PATH,
        "developer": V21_DEVELOPER_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V21_DEV_PATH,
        "explicit_gates": False,
        "thinking_disabled": False,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V21_C1": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 1,
        "system": V21_SYSTEM_PATH,
        "developer": V21_DEVELOPER_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V21_DEV_PATH,
        "explicit_gates": False,
        "thinking_disabled": False,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V22_C4": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 4,
        "system": V21_SYSTEM_PATH,
        "developer": V22_DEVELOPER_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V21_DEV_PATH,
        "explicit_gates": False,
        "thinking_disabled": False,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V22_C1": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 1,
        "system": V21_SYSTEM_PATH,
        "developer": V22_DEVELOPER_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V21_DEV_PATH,
        "explicit_gates": False,
        "thinking_disabled": True,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V22_HOLDOUT_C4": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 4,
        "system": V21_SYSTEM_PATH,
        "developer": V22_DEVELOPER_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V22_HOLDOUT_PATH,
        "explicit_gates": False,
        "thinking_disabled": False,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V22_HOLDOUT_C1": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 1,
        "system": V21_SYSTEM_PATH,
        "developer": V22_DEVELOPER_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V22_HOLDOUT_PATH,
        "explicit_gates": False,
        "thinking_disabled": False,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V22_HOLDOUT_C1_FLASH": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 1,
        "system": V21_SYSTEM_PATH,
        "developer": V22_DEVELOPER_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V22_HOLDOUT_PATH,
        "explicit_gates": False,
        "thinking_disabled": True,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V23_DEV_C1_FLASH": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 1,
        "system": V21_SYSTEM_PATH,
        "developer": V23_DEVELOPER_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V21_DEV_PATH,
        "explicit_gates": False,
        "thinking_disabled": True,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V23_V22HOLDOUT_C1_FLASH": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 1,
        "system": V21_SYSTEM_PATH,
        "developer": V23_DEVELOPER_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V22_HOLDOUT_PATH,
        "explicit_gates": False,
        "thinking_disabled": True,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V23_HOLDOUT_C1_FLASH": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 1,
        "system": V21_SYSTEM_PATH,
        "developer": V23_DEVELOPER_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V23_HOLDOUT_PATH,
        "explicit_gates": False,
        "thinking_disabled": True,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V24_DEV_C1_FLASH": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 1,
        "system": V21_SYSTEM_PATH,
        "developer": V23_DEVELOPER_PATH,
        "developer_append": V24_DEVELOPER_ADDENDUM_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V21_DEV_PATH,
        "explicit_gates": False,
        "thinking_disabled": True,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V24_V23HOLDOUT_C1_FLASH": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 1,
        "system": V21_SYSTEM_PATH,
        "developer": V23_DEVELOPER_PATH,
        "developer_append": V24_DEVELOPER_ADDENDUM_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V23_HOLDOUT_PATH,
        "explicit_gates": False,
        "thinking_disabled": True,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V24_HOLDOUT_C1_FLASH": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1000,
        "agent_clip": 1400,
        "chunk_size": 1,
        "system": V21_SYSTEM_PATH,
        "developer": V23_DEVELOPER_PATH,
        "developer_append": V24_DEVELOPER_ADDENDUM_PATH,
        "fewshot": V21_FEWSHOT_PATH,
        "sample": V24_HOLDOUT_PATH,
        "explicit_gates": False,
        "thinking_disabled": True,
        "full_timeline": True,
        "timeline_depth": 8,
    },
    "GATE_V25_DEV_C1_FLASH": {
        "input_style": "timeline", "user_depth": 2, "agent_depth": 3,
        "user_clip": 1000, "agent_clip": 1400, "chunk_size": 1,
        "system": V21_SYSTEM_PATH, "developer": V23_DEVELOPER_PATH,
        "developer_append": V25_DEVELOPER_ADDENDUM_PATH, "fewshot": V21_FEWSHOT_PATH,
        "sample": V21_DEV_PATH, "explicit_gates": False, "thinking_disabled": True,
        "full_timeline": True, "timeline_depth": 8,
    },
    "GATE_V25_V24HOLDOUT_C1_FLASH": {
        "input_style": "timeline", "user_depth": 2, "agent_depth": 3,
        "user_clip": 1000, "agent_clip": 1400, "chunk_size": 1,
        "system": V21_SYSTEM_PATH, "developer": V23_DEVELOPER_PATH,
        "developer_append": V25_DEVELOPER_ADDENDUM_PATH, "fewshot": V21_FEWSHOT_PATH,
        "sample": V24_HOLDOUT_PATH, "explicit_gates": False, "thinking_disabled": True,
        "full_timeline": True, "timeline_depth": 8,
    },
    "GATE_V25_HOLDOUT_C1_FLASH": {
        "input_style": "timeline", "user_depth": 2, "agent_depth": 3,
        "user_clip": 1000, "agent_clip": 1400, "chunk_size": 1,
        "system": V21_SYSTEM_PATH, "developer": V23_DEVELOPER_PATH,
        "developer_append": V25_DEVELOPER_ADDENDUM_PATH, "fewshot": V21_FEWSHOT_PATH,
        "sample": V25_HOLDOUT_PATH, "explicit_gates": False, "thinking_disabled": True,
        "full_timeline": True, "timeline_depth": 8,
    },
    "GATE_V25_COMPACT6_V24HOLDOUT_C1_FLASH": {
        "input_style": "timeline", "user_depth": 2, "agent_depth": 3,
        "user_clip": 1000, "agent_clip": 1400, "chunk_size": 1,
        "system": V21_SYSTEM_PATH, "developer": V23_DEVELOPER_PATH,
        "developer_append": V25_DEVELOPER_ADDENDUM_PATH, "fewshot": V25_FEWSHOT_COMPACT6_PATH,
        "sample": V24_HOLDOUT_PATH, "explicit_gates": False, "thinking_disabled": True,
        "full_timeline": True, "timeline_depth": 8,
    },
    "GATE_V25_COMPACT6_HOLDOUT_C1_FLASH": {
        "input_style": "timeline", "user_depth": 2, "agent_depth": 3,
        "user_clip": 1000, "agent_clip": 1400, "chunk_size": 1,
        "system": V21_SYSTEM_PATH, "developer": V23_DEVELOPER_PATH,
        "developer_append": V25_DEVELOPER_ADDENDUM_PATH, "fewshot": V25_FEWSHOT_COMPACT6_PATH,
        "sample": V25_HOLDOUT_PATH, "explicit_gates": False, "thinking_disabled": True,
        "full_timeline": True, "timeline_depth": 8,
    },
    "GATE_V2_EXPLICIT_C4": {
        "input_style": "timeline",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 4,
        "system": V2_SYSTEM_PATH,
        "developer": V2_DEVELOPER_EXPLICIT_PATH,
        "fewshot": V2_FEWSHOT_EXPLICIT_PATH,
        "explicit_gates": True,
        "thinking_disabled": False,
    },
    "VERDICT_TIMELINE_COMPACT_C4_FS5BOUNDARY": {
        "input_style": "timeline",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 4,
        "fewshot": TRACE5_BOUNDARY_PATH,
        "thinking_disabled": False,
    },
    "VERDICT_TIMELINE_COMPACT_C4_FS6BALANCED": {
        "input_style": "timeline",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 4,
        "fewshot": TRACE6_BALANCED_PATH,
        "thinking_disabled": False,
    },
    "VERDICT_TIMELINE_COMPACT_C4_NOFS": {
        "input_style": "timeline",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 4,
        "fewshot": NO_FEWSHOT_PATH,
        "thinking_disabled": False,
    },
    "VERDICT_TIMELINE_COMPACT_C2": {
        "input_style": "timeline",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 2,
        "fewshot": TRACE6_PATH,
        "thinking_disabled": False,
    },
    "VERDICT_TIMELINE_COMPACT_C1": {
        "input_style": "timeline",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 1,
        "fewshot": TRACE6_PATH,
        "thinking_disabled": False,
    },
    "VERDICT_TIMELINE_COMPACT_C4_NO_THINKING": {
        "input_style": "timeline",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 4,
        "fewshot": TRACE6_PATH,
        "thinking_disabled": True,
    },
    "VERDICT_TIMELINE_ENRICHED_C4": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1200,
        "agent_clip": 1600,
        "chunk_size": 4,
        "fewshot": TRACE6_PATH,
        "thinking_disabled": False,
    },
    "VERDICT_TIMELINE_ENRICHED_C4_NOFS": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1200,
        "agent_clip": 1600,
        "chunk_size": 4,
        "fewshot": NO_FEWSHOT_PATH,
        "thinking_disabled": False,
    },
    "VERDICT_TIMELINE_ENRICHED_C2": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1200,
        "agent_clip": 1600,
        "chunk_size": 2,
        "fewshot": TRACE6_PATH,
        "thinking_disabled": False,
    },
    "VERDICT_TIMELINE_ENRICHED_C4_NO_THINKING": {
        "input_style": "timeline",
        "user_depth": 2,
        "agent_depth": 3,
        "user_clip": 1200,
        "agent_clip": 1600,
        "chunk_size": 4,
        "fewshot": TRACE6_PATH,
        "thinking_disabled": True,
    },
}

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["conversation_id", "chunk_id", "verdicts"],
    "properties": {
        "conversation_id": {"type": "string"},
        "chunk_id": {"type": "string"},
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target_event_id", "decision", "signal_types", "rationale"],
                "properties": {
                    "target_event_id": {"type": "string"},
                    "decision": {"type": "string", "enum": sorted(DECISIONS)},
                    "signal_types": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(p0.SIGNAL_TYPES)},
                    },
                    "rationale": {"type": "string"},
                },
            },
        },
    },
}

GATE_STATUS = ["CONFIRMED", "NOT_CONFIRMED", "UNCLEAR"]
GATED_VERDICT_SCHEMA: dict[str, Any] = json.loads(json.dumps(VERDICT_SCHEMA))
GATED_VERDICT_SCHEMA["properties"]["verdicts"]["items"]["required"] = [
    "target_event_id",
    "prior_project_state",
    "state_problem",
    "decision",
    "signal_types",
    "rationale",
]
GATED_VERDICT_SCHEMA["properties"]["verdicts"]["items"]["properties"].update(
    {
        "prior_project_state": {"type": "string", "enum": GATE_STATUS},
        "state_problem": {"type": "string", "enum": GATE_STATUS},
    }
)


def event_number(event_id: str) -> int:
    match = re.search(r"(\d+)$", event_id or "")
    return int(match.group(1)) if match else 10**9


@lru_cache(maxsize=1)
def source_event_index() -> dict[tuple[str, str], int]:
    index: dict[tuple[str, str], int] = {}
    for chunk in p0.read_jsonl(p0.OUT_DIR / "p0_scan_chunks.jsonl"):
        for unit in chunk["scan_units"]:
            record_index = unit.get("source_record_index")
            if record_index is not None:
                index[(chunk["source_path"], unit["target_user_event_id"])] = int(record_index)
    return index


@lru_cache(maxsize=256)
def source_records(source_path: str) -> tuple[dict[str, Any], ...]:
    path = Path(source_path)
    if not path.exists():
        return ()
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("role") not in {"user", "assistant"} or record.get("is_control"):
            continue
        records.append(record)
    return tuple(records)


def full_timeline_context(row: dict[str, Any], config: dict[str, Any]) -> list[dict[str, str]]:
    source_path = row.get("source_path")
    source_event_id = row.get("source_target_user_event_id") or row.get("target_user_event_id")
    if not source_path or not source_event_id:
        return []
    target_record_index = source_event_index().get((source_path, source_event_id))
    if target_record_index is None:
        return []
    prior = [
        record
        for record in source_records(source_path)
        if int(record.get("record_index", 10**9)) < target_record_index
    ][-int(config.get("timeline_depth", 8)) :]
    events = []
    for record in prior:
        actor = "USER" if record["role"] == "user" else "AGENT"
        limit = config["user_clip"] if actor == "USER" else config["agent_clip"]
        events.append(
            {
                "event_id": f"R{int(record.get('record_index', 0)):06d}",
                "actor": actor,
                "text": p0.clipped(record.get("text", ""), limit, tail=True),
            }
        )
    return events


def clip_context(items: list[dict[str, Any]], depth: int, limit: int, actor: str) -> list[dict[str, str]]:
    output = []
    for item in items[-depth:]:
        output.append(
            {
                "event_id": item.get("event_id", "UNKNOWN"),
                "actor": item.get("actor", actor),
                "text": p0.clipped(item.get("text", ""), limit, tail=True),
            }
        )
    return output


def build_unit(row: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    users = clip_context(
        row.get("prior_user_context", []), config["user_depth"], config["user_clip"], "USER"
    )
    agents = clip_context(
        row.get("immediately_preceding_agent_context", []),
        config["agent_depth"],
        config["agent_clip"],
        "AGENT",
    )
    target = {
        "event_id": row["audit_id"],
        "source_event_id": row.get("source_target_user_event_id") or row.get("target_user_event_id"),
        "actor": "USER",
        "text": p0.clipped(row["target_user_text"], 3000),
    }
    if config["input_style"] == "timeline":
        trace = full_timeline_context(row, config) if config.get("full_timeline") else []
        if not trace:
            trace = sorted(users + agents, key=lambda item: (event_number(item["event_id"]), item["actor"]))
        return {
            "target_user_event_id": row["audit_id"],
            "trace_events": trace,
            "target_user_event": target,
        }
    return {
        "target_user_event_id": row["audit_id"],
        "prior_user_context": users,
        "immediately_preceding_agent_context": agents,
        "target_user_text": target["text"],
    }


def build_chunks(config_name: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    config = CONFIGS[config_name]
    rows = p0.read_jsonl(Path(config.get("sample", SAMPLE_PATH)))
    lookup = {row["audit_id"]: row for row in rows}
    chunks = []
    size = config["chunk_size"]
    for index, start in enumerate(range(0, len(rows), size), start=1):
        selected = rows[start : start + size]
        chunks.append(
            {
                "participant_id": "PARTICIPANT_ISOLATED_HOLDOUT",
                "conversation_id": f"OPT-V4-{config_name}",
                "source_path": "MULTIPLE_ISOLATED_EVENTS",
                "chunk_id": f"{config_name}-C{index:03d}",
                "scan_units": [build_unit(row, config) for row in selected],
            }
        )
    return chunks, lookup


def render_prompts(config: dict[str, Any]) -> tuple[str, str]:
    system = Path(config.get("system", SYSTEM_PATH)).read_text(encoding="utf-8")
    developer = Path(config.get("developer", DEVELOPER_PATH)).read_text(encoding="utf-8")
    if config.get("developer_append"):
        developer += "\n\n" + Path(config["developer_append"]).read_text(encoding="utf-8")
    fewshot = Path(config["fewshot"]).read_text(encoding="utf-8")
    return system, developer.replace("{{FEW_SHOT_BANK}}", fewshot)


def validate_verdict_output(result: dict[str, Any], chunk: dict[str, Any], config: dict[str, Any]) -> None:
    p0.require_keys(result, {"conversation_id", "chunk_id", "verdicts"}, "root")
    if result["conversation_id"] != chunk["conversation_id"] or result["chunk_id"] != chunk["chunk_id"]:
        raise ValueError("Conversation or chunk identity mismatch")
    expected_ids = {unit["target_user_event_id"] for unit in chunk["scan_units"]}
    actual_ids = set()
    for index, verdict in enumerate(result["verdicts"]):
        expected_keys = {"target_event_id", "decision", "signal_types", "rationale"}
        if config.get("explicit_gates"):
            expected_keys |= {"prior_project_state", "state_problem"}
        p0.require_keys(verdict, expected_keys, f"verdict[{index}]")
        target_id = verdict["target_event_id"]
        if target_id not in expected_ids or target_id in actual_ids:
            raise ValueError(f"Invalid or duplicate target ID: {target_id}")
        actual_ids.add(target_id)
        if verdict["decision"] not in DECISIONS:
            raise ValueError("Invalid decision")
        signals = verdict["signal_types"]
        if not isinstance(signals, list) or not set(signals).issubset(p0.SIGNAL_TYPES):
            raise ValueError("Invalid signal types")
        if verdict["decision"] == "DO_NOT_RETAIN" and signals:
            raise ValueError("Excluded verdict must have empty signal_types")
        if verdict["decision"] != "DO_NOT_RETAIN" and not signals:
            raise ValueError("Retained verdict must have signal_types")
        if config.get("explicit_gates"):
            gate_1 = verdict["prior_project_state"]
            gate_2 = verdict["state_problem"]
            if gate_1 not in GATE_STATUS or gate_2 not in GATE_STATUS:
                raise ValueError("Invalid gate status")
            if "NOT_CONFIRMED" in {gate_1, gate_2} and verdict["decision"] != "DO_NOT_RETAIN":
                raise ValueError("A failed gate must produce DO_NOT_RETAIN")
            if gate_1 == gate_2 == "CONFIRMED" and verdict["decision"] != "RETAIN_STRONG":
                raise ValueError("Two confirmed gates must produce RETAIN_STRONG")
            if "NOT_CONFIRMED" not in {gate_1, gate_2} and "UNCLEAR" in {gate_1, gate_2} and verdict["decision"] != "RETAIN_POSSIBLE":
                raise ValueError("An unclear gate without failure must produce RETAIN_POSSIBLE")
    if actual_ids != expected_ids:
        raise ValueError(f"Missing verdict IDs: {sorted(expected_ids - actual_ids)}")


def normalize_singleton_verdict_output(result: dict[str, Any], chunk: dict[str, Any]) -> dict[str, Any]:
    """Normalize common wrapper/alias drift without relaxing ID validation."""
    expected_ids = {unit["target_user_event_id"] for unit in chunk.get("scan_units") or []}
    if set(result) == {"results"} and isinstance(result["results"], list):
        result = {"verdicts": result["results"]}
    if expected_ids and set(result) == expected_ids and all(isinstance(result[item], dict) for item in expected_ids):
        verdicts = []
        for target_id in sorted(expected_ids, key=event_number):
            candidate = dict(result[target_id])
            candidate["target_event_id"] = target_id
            verdicts.append(candidate)
        result = {"verdicts": verdicts}
    if set(result) == {"verdicts"} and isinstance(result["verdicts"], list):
        result = {
            "conversation_id": chunk["conversation_id"],
            "chunk_id": chunk["chunk_id"],
            "verdicts": result["verdicts"],
        }
    if set(result) == {"conversation_id", "chunk_id", "verdicts"}:
        normalized = dict(result)
        normalized_verdicts = []
        for item in result["verdicts"]:
            if not isinstance(item, dict):
                normalized_verdicts.append(item)
                continue
            current = dict(item)
            alias = current.pop("target_user_event_id", None)
            if alias is not None and alias != current.get("target_event_id", alias):
                return result
            if "target_event_id" not in current and alias is not None:
                current["target_event_id"] = alias
            normalized_verdicts.append(current)
        normalized["verdicts"] = normalized_verdicts
        return normalized
    units = chunk.get("scan_units") or []
    if len(units) != 1:
        return result
    target_id = units[0]["target_user_event_id"]
    if set(result) == {"verdicts"} and isinstance(result["verdicts"], list) and len(result["verdicts"]) == 1:
        candidate = result["verdicts"][0]
        if isinstance(candidate, dict):
            candidate = dict(candidate)
            supplied_id = candidate.get("target_event_id", target_id)
            if supplied_id == target_id:
                candidate["target_event_id"] = target_id
                return {
                    "conversation_id": chunk["conversation_id"],
                    "chunk_id": chunk["chunk_id"],
                    "verdicts": [candidate],
                }
    candidate: Any = result.get(target_id) if set(result) == {target_id} else result
    if not isinstance(candidate, dict):
        return result
    candidate = dict(candidate)
    supplied_id = candidate.pop("target_user_event_id", candidate.get("target_event_id", target_id))
    if supplied_id != target_id:
        return result
    candidate["target_event_id"] = target_id
    if not {"decision", "signal_types", "rationale"}.issubset(candidate):
        return result
    return {
        "conversation_id": chunk["conversation_id"],
        "chunk_id": chunk["chunk_id"],
        "verdicts": [candidate],
    }


def parse_verdict_payload(content: str, chunk: dict[str, Any]) -> dict[str, Any]:
    """Accept a singleton verdict array while retaining strict downstream ID checks."""
    try:
        return p0.parse_json_object(content)
    except ValueError as exc:
        if "root must be an object" not in str(exc) or len(chunk.get("scan_units") or []) != 1:
            raise
        stripped = content.strip()
        if stripped.startswith("```"):
            first_newline = stripped.find("\n")
            if first_newline >= 0:
                stripped = stripped[first_newline + 1 :]
            if stripped.endswith("```"):
                stripped = stripped[:-3]
            stripped = stripped.strip()
        value = json.loads(stripped)
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
            raise
        return value[0]


def call_verdict_api(
    chunk: dict[str, Any], config_name: str, env_path: Path, provider: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = CONFIGS[config_name]
    endpoint, model, api_key = p0.api_config(env_path, provider)
    system, developer = render_prompts(config)
    input_xml = p0.build_input_xml(chunk)
    request_hash = p0.sha256_text(system + developer + input_xml + model + config_name)
    attempts = [
        ("json_schema_developer", True, True),
        ("json_object_developer", True, False),
        ("json_object_system_combined", False, False),
    ]
    started = time.time()
    last_error: Exception | None = None
    for mode, use_developer, use_schema in attempts:
        messages = [{"role": "system", "content": system}]
        if use_developer:
            messages.append({"role": "developer", "content": developer})
        else:
            messages[0]["content"] = system + "\n\n" + developer
        messages.append({"role": "user", "content": input_xml})
        schema = GATED_VERDICT_SCHEMA if config.get("explicit_gates") else VERDICT_SCHEMA
        response_format = (
            {"type": "json_schema", "json_schema": {"name": "p0_verdicts", "strict": True, "schema": schema}}
            if use_schema
            else {"type": "json_object"}
        )
        body: dict[str, Any] = {
            "model": model,
            "temperature": 0,
            "max_tokens": 4000,
            "response_format": response_format,
            "messages": messages,
        }
        if config["thinking_disabled"]:
            body["thinking"] = {"type": "disabled"}
        try:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": p0.API_USER_AGENT,
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=240) as response:
                raw = json.loads(response.read().decode("utf-8"))
            content = p0.parse_message_content(raw["choices"][0]["message"]["content"])
            result = parse_verdict_payload(content, chunk)
            result = normalize_singleton_verdict_output(result, chunk)
            validate_verdict_output(result, chunk, config)
            return result, {
                "config": config_name,
                "model": model,
                "provider": provider,
                "mode": mode,
                "request_hash": request_hash,
                "latency_seconds": round(time.time() - started, 3),
                "usage": raw.get("usage") or {},
                "system_sha256": p0.sha256_text(system),
                "developer_sha256": p0.sha256_text(developer),
                "input_sha256": p0.sha256_text(input_xml),
            }
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def run(config_name: str, replicate: int, env_path: Path, provider: str) -> None:
    chunks, lookup = build_chunks(config_name)
    run_path = RUN_DIR / f"{config_name}__r{replicate}.jsonl"
    rows: list[dict[str, Any]] = []
    for chunk_index, chunk in enumerate(chunks, start=1):
        attempt_errors: list[str] = []
        verdicts: dict[str, Any] = {}
        meta: dict[str, Any] = {}
        error: str | None = None
        for outer_attempt in range(1, 3):
            try:
                result, meta = call_verdict_api(chunk, config_name, env_path, provider)
                verdicts = {item["target_event_id"]: item for item in result["verdicts"]}
                meta["outer_attempt"] = outer_attempt
                meta["recovered_errors"] = attempt_errors
                error = None
                break
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                attempt_errors.append(error)
        if len(verdicts) != len(chunk["scan_units"]) and len(chunk["scan_units"]) > 1:
            split_metas: list[dict[str, Any]] = []
            split_errors: list[str] = []
            for split_index, unit in enumerate(chunk["scan_units"], start=1):
                single = dict(chunk)
                single["chunk_id"] = f"{chunk['chunk_id']}-S{split_index}"
                single["target_count"] = 1
                single["scan_units"] = [unit]
                try:
                    split_result, split_meta = call_verdict_api(single, config_name, env_path, provider)
                    verdicts.update({item["target_event_id"]: item for item in split_result["verdicts"]})
                    split_metas.append(split_meta)
                except Exception as split_exc:
                    split_errors.append(f"{type(split_exc).__name__}: {split_exc}")
            meta = {
                "config": config_name,
                "split_retry": True,
                "original_errors": attempt_errors,
                "split_successes": len(verdicts),
                "split_errors": split_errors,
                "split_metas": split_metas,
                "usage": {
                    "prompt_tokens": sum(item.get("usage", {}).get("prompt_tokens", 0) for item in split_metas),
                    "completion_tokens": sum(item.get("usage", {}).get("completion_tokens", 0) for item in split_metas),
                },
            }
            error = None if len(verdicts) == len(chunk["scan_units"]) else (error or "split retry incomplete")
        for unit in chunk["scan_units"]:
            audit_id = unit["target_user_event_id"]
            reference = lookup[audit_id]
            verdict = verdicts.get(audit_id)
            rows.append(
                {
                    "config": config_name,
                    "replicate": replicate,
                    "chunk_id": chunk["chunk_id"],
                    "audit_id": audit_id,
                    "participant_id": reference["participant_id"],
                    "reference_status": reference["reference_status"],
                    "reference_positive": reference["reference_positive"],
                    "predicted_decision": verdict and verdict["decision"],
                    "predicted_positive": None if verdict is None else verdict["decision"] != "DO_NOT_RETAIN",
                    "predicted_signals": verdict and verdict["signal_types"],
                    "prior_project_state": verdict and verdict.get("prior_project_state"),
                    "state_problem": verdict and verdict.get("state_problem"),
                    "rationale": verdict and verdict["rationale"],
                    "target_user_text": reference["target_user_text"],
                    "error": error,
                    "chunk_meta": meta if unit is chunk["scan_units"][0] else None,
                }
            )
        p0.write_jsonl(run_path, rows)
        print(f"{config_name} r{replicate} [{chunk_index}/{len(chunks)}]")
    write_report()


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row["predicted_positive"] is not None]
    tp = sum(row["reference_positive"] and row["predicted_positive"] for row in valid)
    fp = sum((not row["reference_positive"]) and row["predicted_positive"] for row in valid)
    fn = sum(row["reference_positive"] and (not row["predicted_positive"]) for row in valid)
    tn = sum((not row["reference_positive"]) and (not row["predicted_positive"]) for row in valid)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    strong = [row for row in valid if row["reference_status"] == "RETAIN_STRONG"]
    possible = [row for row in valid if row["reference_status"] == "RETAIN_POSSIBLE"]
    chunks = [row["chunk_meta"] for row in rows if row.get("chunk_meta")]
    usage = [item.get("usage", {}) for item in chunks]
    return {
        "valid": len(valid),
        "total": len(rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "strong_recall": sum(row["predicted_positive"] for row in strong) / len(strong) if strong else 0.0,
        "possible_recall": sum(row["predicted_positive"] for row in possible) / len(possible) if possible else 0.0,
        "prompt_tokens": sum(item.get("prompt_tokens", 0) for item in usage),
        "completion_tokens": sum(item.get("completion_tokens", 0) for item in usage),
        "reasoning_tokens": sum(
            item.get("completion_tokens_details", {}).get("reasoning_tokens", 0) for item in usage
        ),
        "calls": len(chunks),
        "recovered_chunks": sum(
            bool(item.get("recovered_errors")) for item in chunks
        ),
        "errors": sum(row.get("error") is not None for row in rows),
    }


def write_report() -> None:
    run_files = sorted(RUN_DIR.glob("*.jsonl")) if RUN_DIR.exists() else []
    scored = []
    all_rows = {}
    for path in run_files:
        rows = p0.read_jsonl(path)
        if not rows:
            continue
        key = (rows[0]["config"], rows[0]["replicate"])
        all_rows[key] = rows
        scored.append((key, score(rows)))
    lines = [
        "# P0 Prompt Optimization v4",
        "",
        "> 所有配置使用同一份纠正后的参与者隔离 Coder-1 留出集。Coder-1 不是 adjudicated Gold，本报告只用于开发冻结。",
        "",
        "| Config | Rep | Valid | Precision | Recall | Strong R | Possible R | F1 | FP | FN | Calls | Prompt tok | Completion tok | Reasoning tok |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for (config, replicate), current in sorted(scored):
        lines.append(
            f"| {config} | {replicate} | {current['valid']}/{current['total']} | {current['precision']:.4f} | "
            f"{current['recall']:.4f} | {current['strong_recall']:.4f} | {current['possible_recall']:.4f} | "
            f"{current['f1']:.4f} | {current['fp']} | {current['fn']} | {current['calls']} | "
            f"{current['prompt_tokens']} | {current['completion_tokens']} | {current['reasoning_tokens']} |"
        )
    grouped: dict[str, list[tuple[int, list[dict[str, Any]], dict[str, Any]]]] = {}
    for (config, replicate), rows in all_rows.items():
        grouped.setdefault(config, []).append((replicate, rows, score(rows)))
    lines.extend(["", "## Repeated-run aggregate", ""])
    lines.extend([
        "| Config | Runs | Mean P | Mean R | Min R | Mean Strong R | Mean Possible R | Mean F1 | Positive agreement | Exact-label agreement | Mean prompt tok | Mean completion tok |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for config, runs in sorted(grouped.items()):
        if len(runs) < 2:
            continue
        runs.sort(key=lambda item: item[0])
        valid_maps = [
            {row["audit_id"]: row for row in rows if row["predicted_positive"] is not None}
            for _, rows, _ in runs
        ]
        common_ids = set.intersection(*(set(mapping) for mapping in valid_maps))
        positive_agreement = mean(
            len({mapping[audit_id]["predicted_positive"] for mapping in valid_maps}) == 1
            for audit_id in common_ids
        ) if common_ids else 0.0
        exact_agreement = mean(
            len({mapping[audit_id]["predicted_decision"] for mapping in valid_maps}) == 1
            for audit_id in common_ids
        ) if common_ids else 0.0
        metrics = [current for _, _, current in runs]
        lines.append(
            f"| {config} | {len(runs)} | {mean(m['precision'] for m in metrics):.4f} | "
            f"{mean(m['recall'] for m in metrics):.4f} | {min(m['recall'] for m in metrics):.4f} | "
            f"{mean(m['strong_recall'] for m in metrics):.4f} | {mean(m['possible_recall'] for m in metrics):.4f} | "
            f"{mean(m['f1'] for m in metrics):.4f} | {positive_agreement:.4f} | {exact_agreement:.4f} | "
            f"{mean(m['prompt_tokens'] for m in metrics):.0f} | {mean(m['completion_tokens'] for m in metrics):.0f} |"
        )
    lines.extend(["", "## Errors by run", ""])
    for key, rows in sorted(all_rows.items()):
        config, replicate = key
        current = score(rows)
        lines.extend([f"### {config} r{replicate}", ""])
        errors = [
            row
            for row in rows
            if row["predicted_positive"] is not None
            and row["predicted_positive"] != row["reference_positive"]
        ]
        for row in errors:
            kind = "FN" if row["reference_positive"] else "FP"
            lines.append(
                f"- `{kind}` `{row['audit_id']}` `{row['reference_status']}` → `{row['predicted_decision']}`: "
                + row["target_user_text"].replace("\n", " ")[:180]
            )
        if not errors:
            lines.append("- 无")
        if current["errors"]:
            lines.append(f"- API/Schema error rows: {current['errors']}")
        lines.append("")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--config", choices=sorted(CONFIGS), required=True)
    run_parser.add_argument("--replicate", type=int, default=1)
    run_parser.add_argument("--env", type=Path, required=True)
    run_parser.add_argument("--provider", choices=["photomind", "deepseek"], default="photomind")
    sub.add_parser("report")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "run":
        run(args.config, args.replicate, args.env, args.provider)
    else:
        write_report()


if __name__ == "__main__":
    main()
