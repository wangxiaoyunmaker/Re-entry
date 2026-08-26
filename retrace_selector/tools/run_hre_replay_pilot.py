"""Run a ten-episode replay pilot against the existing deterministic selector.

This is a compatibility replay: the selected HRE rows use the v0.3 full-name
mapping, then are adapted to the selector's legacy state schema. It validates
Skyline and trace-coverage monitoring without claiming that the v0.3 runtime
adapter is complete.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from retrace_selector.config import load_policy, load_templates
from retrace_selector.selector import SelectionEngine
from retrace_selector.v03 import select_v03


ROOT = Path(__file__).resolve().parents[2]
MAPPING = ROOT / "outputs/reentry_framework_validation_20260822/02_episode_dimension_mapping_provisional.csv"
REVIEWS = ROOT / "reentry_strict_implementation_20260821/manual_evidence"
OUT = ROOT / "outputs/retrace_replay_pilot_20260822"

CASE_IDS = [
    "HRE-0030",  # all three dimensions, adequate
    "HRE-0044",  # state + action, adequate
    "HRE-0073",  # all three, high-risk boundary
    "HRE-0147",  # all three, inadequate edge case
    "HRE-0152",  # action-only, partial edge case
    "HRE-0164",  # criteria + action, adequate
    "HRE-0205",  # state + action, adequate
    "HRE-0221",  # all three, adequate
    "HRE-0275",  # state-only, partial edge case
    "HRE-0280",  # state + action, partial edge case
]


def _first_event_id(case_id: str) -> str:
    review = json.loads(
        (REVIEWS / case_id / "review.json").read_text(encoding="utf-8")
    )
    raw = str(review.get("gate2_event_ids") or review.get("gate1_event_ids") or "")
    first = raw.split(";")[0].strip()
    return first or f"{case_id}:PREFIX"


def _risk(row: dict[str, str]) -> tuple[str, str, str]:
    high_risk = "F6" in (row.get("focused_codes") or "")
    if high_risk:
        return "high", "low", "high"
    return "low", "medium", "low"


def _support(row: dict[str, str]) -> dict[str, bool]:
    return {
        "criteria_basis_reconstruction": row["criteria_basis_provisional"] == "YES",
        "project_state_reconstruction": row["project_state_basis_provisional"] == "YES",
        "evidence_action_governance": row["evidence_action_governance_provisional"] == "YES",
    }


def _state(row: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    coverage = row["trace_coverage_provisional"]
    if coverage == "ADEQUATE":
        normalized_coverage = "ADEQUATE"
        evidence_quality = 0.9
        confidence_label = "HIGH"
    elif coverage == "PARTIAL":
        normalized_coverage = "PARTIAL"
        evidence_quality = 0.6
        confidence_label = "MEDIUM"
    else:
        normalized_coverage = "INADEQUATE"
        evidence_quality = 0.3
        confidence_label = "LOW"

    consequence, reversibility, authorization = _risk(row)
    support = _support(row)
    event_id = _first_event_id(row["episode_id"])
    primitives = []
    if support["criteria_basis_reconstruction"]:
        primitives.append("RULE_ALIGNMENT")
    if support["project_state_reconstruction"]:
        primitives.extend(["PROVENANCE", "CAUSAL_EXPLANATION"])
    if support["evidence_action_governance"]:
        primitives.extend(["VERIFICATION", "DISPOSITION_COORDINATION"])
    support_dimensions = [key for key, value in support.items() if value]
    evidence = {
        "evidence_id": f"{row['episode_id']}:{event_id}",
        "source": "OBSERVED",
        "locator": f"{row['episode_id']}/manual_evidence/{event_id}",
        "sequence_index": 0,
        "content_sha256": "0" * 64,
        "supports_dimensions": support_dimensions,
        "supports_primitives": sorted(set(primitives)),
        "available_at_decision": True,
    }
    profile = {}
    signals = {
        "criteria_basis_reconstruction": "user_defined_criterion",
        "project_state_reconstruction": "user_reconstructed_project_state",
        "evidence_action_governance": "user_defined_evidence_or_action_boundary",
    }
    for dimension, observed in support.items():
        profile[dimension] = {
            "observed_work": "OBSERVED" if observed else "NONE",
            "support_need": "MEDIUM" if observed else "NONE",
            "confidence": confidence_label if observed else "LOW",
            "evidence_ids": [evidence["evidence_id"]] if observed else [],
            "evidence_basis": (
                [
                    {
                        "signal": signals[dimension],
                        "actor": "USER",
                        "temporal_position": "BEFORE_OR_AT_TRIGGER",
                        "uptake_status": "POSSIBLE",
                    }
                ]
                if observed
                else []
            ),
        }
    state = {
        "schema_version": "retrace-state-v3",
        "decision_id": row["episode_id"],
        "process_state": "REENTRY_OCCASION_OBSERVED",
        "support_profile": profile,
        "trace_coverage": normalized_coverage,
        "uncertainties": (
            ["single_episode_trace_is_incomplete"]
            if normalized_coverage != "ADEQUATE"
            else []
        ),
        "evidence": [evidence],
        "consequence": consequence,
        "reversibility": reversibility,
        "authorization_risk": authorization,
        "evidence_quality": evidence_quality,
        "workflow_continuity": 0.8,
        "recent_interventions": 0,
        "active_verification": False,
    }
    meta = {
        "trace_coverage": normalized_coverage,
        "source_trace_coverage": coverage,
        "insufficient_evidence": row["insufficient_evidence_provisional"],
        "support_dimensions": support_dimensions,
    }
    return state, meta


def main() -> None:
    rows = {
        row["episode_id"]: row
        for row in csv.DictReader(MAPPING.open("r", encoding="utf-8"))
    }
    missing = [case_id for case_id in CASE_IDS if case_id not in rows]
    if missing:
        raise SystemExit(f"missing mapping rows: {missing}")

    policy = load_policy(ROOT / "retrace_selector/config/policy.v0.2.json")
    templates = load_templates(ROOT / "retrace_selector/config/templates.v0.2.json")
    engine = SelectionEngine(policy, templates)
    records: list[dict[str, Any]] = []

    for case_id in CASE_IDS:
        state_raw, meta = _state(rows[case_id])
        result_raw = select_v03(state_raw, engine)
        failed_minimum = any(
            not item["allowed"] and item["rule_id"] == "C030_MINIMUM_EVIDENCE"
            for candidate in result_raw["generated_candidates"]
            for item in candidate["constraints"]
        )
        records.append(
            {
                "episode_id": case_id,
                "trace_coverage": result_raw["v03_input"]["trace_coverage"],
                "source_trace_coverage": meta["source_trace_coverage"],
                "insufficient_evidence": meta["insufficient_evidence"],
                "support_dimensions": meta["support_dimensions"],
                "degradation_triggered": meta["trace_coverage"] != "ADEQUATE",
                "minimum_evidence_restriction_observed": failed_minimum,
                "outcome": result_raw["outcome"],
                "selected_ids": result_raw["selected_ids"],
                "feasible_count": len(result_raw["feasible_ids"]),
                "skyline_count": len(result_raw["skyline_ids"]),
                "frontier_ratio": result_raw["frontier_ratio"],
                "warnings": result_raw["warnings"],
                "reason_codes": result_raw["reason_codes"],
            }
        )

    ratios = [item["frontier_ratio"] for item in records if item["frontier_ratio"] is not None]
    feasible_total = sum(item["feasible_count"] for item in records)
    skyline_total = sum(item["skyline_count"] for item in records)
    degraded = [item for item in records if item["degradation_triggered"]]
    restricted = [item for item in records if item["minimum_evidence_restriction_observed"]]
    high_frontier = [item for item in records if (item["frontier_ratio"] or 0) >= 0.9]
    summary = {
        "episode_count": len(records),
        "trace_coverage_counts": {
            key: sum(item["trace_coverage"] == key for item in records)
            for key in ("ADEQUATE", "PARTIAL", "INADEQUATE")
        },
        "trace_coverage_degradation_rate": len(degraded) / len(records),
        "minimum_evidence_restriction_rate": len(restricted) / len(records),
        "mean_frontier_ratio": sum(ratios) / len(ratios) if ratios else None,
        "overall_frontier_ratio": skyline_total / feasible_total if feasible_total else None,
        "high_frontier_ratio_count": len(high_frontier),
        "outcomes": {
            outcome: sum(item["outcome"] == outcome for item in records)
            for outcome in sorted({item["outcome"] for item in records})
        },
        "compatibility_note": (
            "trace_coverage is recorded by the v0.3 replay wrapper; the selector "
            "still consumes the legacy retrace-state-v2 compatibility schema."
        ),
    }
    payload = {
        "schema_version": "retrace-replay-pilot-v0.1",
        "selector_policy": policy.policy_version,
        "selected_cases": CASE_IDS,
        "summary": summary,
        "records": records,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "replay_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# ReTrace v0.3 Replay Pilot（10 个历史 Episode）",
        "",
        "> 本轮是兼容回放：`trace_coverage` 和三类支持维度来自 v0.3 的全名字段，选择器内部仍通过现有 `retrace-state-v2` 兼容接口运行。结果用于检查回放指标和约束行为，不作为 v0.3 运行时效果结论。",
        "",
        "## Summary",
        "",
        f"- Episode 数：{summary['episode_count']}",
        f"- trace_coverage 降级触发率：{summary['trace_coverage_degradation_rate']:.1%}",
        f"- 最低证据约束实际限制率：{summary['minimum_evidence_restriction_rate']:.1%}",
        f"- 平均 FrontierRatio：{summary['mean_frontier_ratio']:.3f}",
        f"- 总体 Skyline 保留率：{summary['overall_frontier_ratio']:.3f}",
        f"- FrontierRatio ≥ 0.9：{summary['high_frontier_ratio_count']}/{summary['episode_count']}",
        "",
        "## Episode results",
        "",
        "| Episode | trace_coverage | Support | Outcome | Feasible | Skyline | FrontierRatio | Evidence restriction |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for item in records:
        support = ", ".join(item["support_dimensions"]) or "NONE"
        lines.append(
            f"| {item['episode_id']} | {item['trace_coverage']} | {support} | "
            f"{item['outcome']} | {item['feasible_count']} | {item['skyline_count']} | "
            f"{item['frontier_ratio'] if item['frontier_ratio'] is not None else 'N/A'} | "
            f"{'YES' if item['minimum_evidence_restriction_observed'] else 'NO'} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `trace_coverage` 降级触发率只表示输入层识别到 PARTIAL/INADEQUATE，不等于选择器已经完整执行了 v0.3 的降级策略。",
            "- FrontierRatio 较高表示当前候选画像存在明显 trade-off，Skyline 剪枝有限；不能为了提高剪枝率而手工调参。",
            "- 下一步应将 `trace_coverage` 作为正式 state 字段接入 selector，并在全名字段接口上重跑同一批 Episode。",
        ]
    )
    (OUT / "replay_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summary, "output_dir": str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
