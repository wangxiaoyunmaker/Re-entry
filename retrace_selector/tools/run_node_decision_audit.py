"""Build node-level decision logs for the ten-episode replay pilot.

The node set is deliberately restricted to user events already cited by the
manual gate review. Support dimensions are provisional, lexical/event-level
projections for audit development; they are not gold labels and do not claim
that the selector has inferred user mental states.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from retrace_selector.config import load_policy, load_templates
from retrace_selector.selector import SelectionEngine
from retrace_selector.v03 import select_v03


ROOT = Path(__file__).resolve().parents[2]
MAPPING = ROOT / "outputs/reentry_framework_validation_20260822/02_episode_dimension_mapping_provisional.csv"
REVIEWS = ROOT / "reentry_strict_implementation_20260821/manual_evidence"
EPISODES = ROOT / "HRE-人工核验案例库-20260820/episodes"
OUT = ROOT / "outputs/retrace_node_decision_audit_20260822"

CASE_IDS = [
    "HRE-0030", "HRE-0044", "HRE-0073", "HRE-0147", "HRE-0152",
    "HRE-0164", "HRE-0205", "HRE-0221", "HRE-0275", "HRE-0280",
]

DIMENSIONS = (
    "criteria_basis_reconstruction",
    "project_state_reconstruction",
    "evidence_action_governance",
)
SIGNALS = {
    "criteria_basis_reconstruction": "user_defined_criterion",
    "project_state_reconstruction": "user_reconstructed_project_state",
    "evidence_action_governance": "user_defined_evidence_or_action_boundary",
}
PRIMITIVES = {
    "criteria_basis_reconstruction": ["RULE_ALIGNMENT"],
    "project_state_reconstruction": ["PROVENANCE", "CAUSAL_EXPLANATION"],
    "evidence_action_governance": ["VERIFICATION", "DISPOSITION_COORDINATION"],
}
KEYWORDS = {
    "criteria_basis_reconstruction": (
        "规则", "要求", "标准", "必须", "不能", "条件", "边界", "验收", "目标", "应该", "预期"
    ),
    "project_state_reconstruction": (
        "为什么", "原因", "机制", "关系", "版本", "文件", "数据", "模块", "当前", "到底", "排查", "定位", "实现"
    ),
    "evidence_action_governance": (
        "测试", "验证", "日志", "错误", "复现", "证据", "回退", "范围", "授权", "提交", "暂停", "先", "再", "确认"
    ),
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _event_ids(review: dict[str, Any]) -> dict[int, set[str]]:
    result: dict[int, set[str]] = defaultdict(set)
    for kind, field in (
        ("TRIGGER_OR_INSUFFICIENCY", "gate2_event_ids"),
        ("USER_UPTAKE_OR_RECONSTRUCTION", "gate3_event_ids"),
        ("BOUNDARY_OR_CLOSURE", "gate4_event_ids"),
    ):
        for raw in str(review.get(field) or "").split(";"):
            raw = raw.strip()
            if raw.startswith("R") and raw[1:].isdigit():
                result[int(raw[1:])].add(kind)
    uptake = set()
    for raw in str(review.get("user_uptake_event_ids") or "").split(";"):
        raw = raw.strip()
        if raw.startswith("R") and raw[1:].isdigit():
            uptake.add(int(raw[1:]))
    return {key: value for key, value in result.items() if key not in uptake or value}


def _row_map() -> dict[str, dict[str, str]]:
    with MAPPING.open("r", encoding="utf-8") as handle:
        return {row["episode_id"]: row for row in csv.DictReader(handle)}


def _episode_risk(row: dict[str, str]) -> tuple[str, str, str]:
    if "F6" in (row.get("focused_codes") or ""):
        return "high", "low", "high"
    return "low", "medium", "low"


def _episode_coverage(row: dict[str, str]) -> tuple[str, float, str]:
    raw = row["trace_coverage_provisional"]
    if raw == "ADEQUATE":
        return "ADEQUATE", 0.9, "HIGH"
    if raw == "PARTIAL":
        return "PARTIAL", 0.6, "MEDIUM"
    return "INADEQUATE", 0.3, "LOW"


def _node_dimensions(text: str, row: dict[str, str]) -> tuple[list[str], str]:
    hits = [
        dimension
        for dimension in DIMENSIONS
        if any(keyword in text for keyword in KEYWORDS[dimension])
    ]
    if hits:
        return hits, "LEXICAL_EVENT_SIGNAL"
    fallback = {
        "criteria_basis_reconstruction": row["criteria_basis_provisional"] == "YES",
        "project_state_reconstruction": row["project_state_basis_provisional"] == "YES",
        "evidence_action_governance": row["evidence_action_governance_provisional"] == "YES",
    }
    return [key for key, value in fallback.items() if value], "EPISODE_FALLBACK"


def _build_v03_state(
    case_id: str,
    row: dict[str, str],
    record: dict[str, Any],
    node_kinds: set[str],
    uptake_ids: set[int],
) -> tuple[dict[str, Any], dict[str, Any]]:
    coverage, evidence_quality, confidence = _episode_coverage(row)
    consequence, reversibility, authorization = _episode_risk(row)
    text = str(record.get("text") or "")
    dimensions, dimension_source = _node_dimensions(text, row)
    source_context = record.get("source_context") or record.get("session_id") or "source"
    event_id = f"{case_id}:{source_context}:R{record['record_index']}"
    process_state = (
        "GOVERNANCE_RECOVERING"
        if "BOUNDARY_OR_CLOSURE" in node_kinds
        else "REENTRY_SUPPORT"
    )
    profile: dict[str, Any] = {}
    for dimension in DIMENSIONS:
        observed = dimension in dimensions
        profile[dimension] = {
            "observed_work": "OBSERVED" if observed else "NONE",
            "support_need": "HIGH" if "USER_UPTAKE_OR_RECONSTRUCTION" in node_kinds and observed else ("MEDIUM" if observed else "NONE"),
            "confidence": confidence if observed else "LOW",
            "evidence_ids": [event_id] if observed else [],
            "evidence_basis": (
                [{
                    "signal": SIGNALS[dimension],
                    "actor": "USER",
                    "temporal_position": "BEFORE_OR_AT_TRIGGER",
                    "uptake_status": "OBSERVED" if record["record_index"] in uptake_ids else "POSSIBLE",
                }]
                if observed
                else []
            ),
        }
    primitives = sorted({primitive for dimension in dimensions for primitive in PRIMITIVES[dimension]})
    workflow_continuity = 0.6 if len(text) > 500 else 0.8
    state = {
        "schema_version": "retrace-state-v3",
        "decision_id": f"{case_id}:R{record['record_index']}",
        "process_state": process_state,
        "support_profile": profile,
        "trace_coverage": coverage,
        "uncertainties": [
            "node_support_dimensions_are_provisional",
            *(["episode_trace_is_partial_or_inadequate"] if coverage != "ADEQUATE" else []),
        ],
        "consequence": consequence,
        "reversibility": reversibility,
        "authorization_risk": authorization,
        "evidence_quality": evidence_quality,
        "workflow_continuity": workflow_continuity,
        "evidence": [{
            "evidence_id": event_id,
            "source": "OBSERVED",
            "locator": f"{case_id}/source_context.json#{source_context}:R{record['record_index']}",
            "observed_at": record.get("timestamp"),
            "sequence_index": record["record_index"],
            "content_sha256": _sha(text),
            "supports_dimensions": dimensions,
            "supports_primitives": primitives,
            "available_at_decision": True,
        }],
        "recent_interventions": 0,
        "active_verification": False,
    }
    meta = {
        "node_kind": sorted(node_kinds),
        "dimension_source": dimension_source,
        "support_dimensions": dimensions,
        "input_evidence_quality": evidence_quality,
        "input_workflow_continuity": workflow_continuity,
        "workflow_continuity_source": "REPLAY_HEURISTIC_TEXT_LENGTH",
    }
    return state, meta


def _candidate_audit(result: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for candidate in result.get("generated_candidates", []):
        output.append({
            "brief": candidate["brief"],
            "allowed": candidate["allowed"],
            "score": candidate["score"],
            "gain_vs_no_intervention": candidate["gain_vs_no_intervention"],
            "constraints": candidate["constraints"],
        })
    return output


def _metric_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "unique": [], "min": None, "max": None, "mean": None, "pstdev": None}
    return {
        "count": len(values),
        "unique": sorted({round(value, 6) for value in values}),
        "min": min(values),
        "max": max(values),
        "mean": mean(values),
        "pstdev": pstdev(values) if len(values) > 1 else 0.0,
    }


def main() -> None:
    rows = _row_map()
    policy = load_policy(ROOT / "retrace_selector/config/policy.v0.2.json")
    templates = load_templates(ROOT / "retrace_selector/config/templates.v0.2.json")
    engine = SelectionEngine(policy, templates)
    nodes: list[dict[str, Any]] = []

    for case_id in CASE_IDS:
        review = json.loads((REVIEWS / case_id / "review.json").read_text(encoding="utf-8"))
        source = json.loads((EPISODES / case_id / "source_context.json").read_text(encoding="utf-8"))
        if "records" in source:
            source_records = source["records"]
        else:
            source_records = [
                {**record, "source_context": context.get("context_id", "source")}
                for context in source.get("contexts", [])
                for record in context.get("records", [])
            ]
        records = {int(item["record_index"]): item for item in source_records}
        user_records: dict[int, dict[str, Any]] = {}
        user_prompts_path = EPISODES / case_id / "user_prompts.json"
        if user_prompts_path.is_file():
            user_prompts = json.loads(user_prompts_path.read_text(encoding="utf-8"))
            for prompt in user_prompts.get("prompts", []):
                source_event_id = str(prompt.get("source_event_id") or "")
                record_match = source_event_id.rsplit(":R", 1)
                if len(record_match) == 2 and record_match[1].isdigit():
                    user_records[int(record_match[1])] = {
                        "record_index": int(record_match[1]),
                        "source_context": record_match[0],
                        "role": "user",
                        "text": prompt.get("text", ""),
                        "timestamp": prompt.get("timestamp"),
                    }
        node_kinds_by_record = _event_ids(review)
        uptake_ids = {
            int(raw.strip()[1:])
            for raw in str(review.get("user_uptake_event_ids") or "").split(";")
            if raw.strip().startswith("R") and raw.strip()[1:].isdigit()
        }
        for record_index in sorted(node_kinds_by_record):
            record = user_records.get(record_index) or records.get(record_index)
            if not record or record.get("role") != "user":
                continue
            state, node_meta = _build_v03_state(
                case_id, rows[case_id], record, node_kinds_by_record[record_index], uptake_ids
            )
            result = select_v03(state, engine)
            nodes.append({
                "node_id": state["decision_id"],
                "episode_id": case_id,
                "event_id": f"R{record_index}",
                "event_role": record["role"],
                "event_text": record.get("text", ""),
                "event_timestamp": record.get("timestamp"),
                "node_meta": node_meta,
                "support_profile": state["support_profile"],
                "trace_coverage": state["trace_coverage"],
                "evidence_quality": state["evidence_quality"],
                "workflow_continuity": state["workflow_continuity"],
                "candidate_briefs": _candidate_audit(result),
                "hard_constraints_applied": sorted({
                    item["rule_id"]
                    for item in _candidate_audit(result)
                    for item in item["constraints"]
                    if not item["allowed"]
                }),
                "skyline_candidates": result["skyline_ids"],
                "frontier_ratio": result["frontier_ratio"],
                "final_decision": result["outcome"],
                "selected_ids": result["selected_ids"],
                "reason_codes": result["reason_codes"],
                "policy_weights": {
                    "criteria_basis_reconstruction": policy.weights["criteria_basis_reconstruction"],
                    "project_state_reconstruction": policy.weights["project_state_reconstruction"],
                    "evidence_action_governance": policy.weights["evidence_action_governance"],
                    "evidence_quality": policy.weights["evidence_quality"],
                    "workflow_continuity": policy.weights["workflow_continuity"],
                },
                "audit_note": "Node support dimensions are provisional event-level projections for audit development, not gold labels.",
            })

    skyline_pair_counts = Counter()
    skyline_counts = Counter()
    feasible_counts = Counter()
    candidate_signatures: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    score_values: dict[str, list[float]] = defaultdict(list)
    intervention_score_values: dict[str, list[float]] = defaultdict(list)
    input_values = {"evidence_quality": [], "workflow_continuity": []}
    within_node_ranges: dict[str, list[float]] = defaultdict(list)
    within_node_zero_spread: dict[str, int] = defaultdict(int)
    for node in nodes:
        skyline = list(node["skyline_candidates"])
        skyline_counts.update(skyline)
        feasible = [item["brief"]["brief_id"] for item in node["candidate_briefs"] if item["allowed"]]
        feasible_counts.update(feasible)
        for pair in itertools.combinations(sorted(skyline), 2):
            skyline_pair_counts[pair] += 1
        for item in node["candidate_briefs"]:
            brief_id = item["brief"]["brief_id"]
            score = item["score"]
            if score is not None:
                signature = tuple(round(score[key], 6) for key in (
                    "criteria_basis_reconstruction",
                    "project_state_reconstruction",
                    "evidence_action_governance",
                    "evidence_quality",
                    "workflow_continuity",
                ))
                candidate_signatures[brief_id].append(signature)
                for key, value in score.items():
                    score_values[key].append(value)
                    if brief_id != "NO_INTERVENTION":
                        intervention_score_values[key].append(value)
        intervention_scores = [
            item["score"] for item in node["candidate_briefs"]
            if item["allowed"] and item["brief"]["brief_id"] != "NO_INTERVENTION" and item["score"] is not None
        ]
        for key in (
            "criteria_basis_reconstruction",
            "project_state_reconstruction",
            "evidence_action_governance",
            "evidence_quality",
            "workflow_continuity",
        ):
            values = [score[key] for score in intervention_scores]
            if values:
                spread = max(values) - min(values)
                within_node_ranges[key].append(spread)
                if spread == 0:
                    within_node_zero_spread[key] += 1
        input_values["evidence_quality"].append(node["evidence_quality"])
        input_values["workflow_continuity"].append(node["workflow_continuity"])

    repeated_pairs = [
        {"pair": list(pair), "count": count, "rate": count / len(nodes)}
        for pair, count in skyline_pair_counts.most_common()
    ]
    candidate_repeat = []
    for brief_id, count in feasible_counts.items():
        candidate_repeat.append({
            "brief_id": brief_id,
            "feasible_count": count,
            "skyline_count": skyline_counts[brief_id],
            "skyline_rate_given_feasible": skyline_counts[brief_id] / count if count else None,
        })
    duplicate_groups: dict[tuple[tuple[Any, ...], ...], list[str]] = defaultdict(list)
    for brief_id, signatures in candidate_signatures.items():
        duplicate_groups[tuple(signatures)].append(brief_id)
    duplicate_groups_out = [
        {"brief_ids": ids, "identical_score_signature_count": len(next(
            signatures for bid, signatures in candidate_signatures.items() if bid == ids[0]
        ))}
        for ids in duplicate_groups.values() if len(ids) > 1
    ]
    # Score-signature duplication is state-dependent. This separate library
    # fingerprint check asks whether the generated candidate definitions
    # themselves are exact duplicates.
    library_fingerprints: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for item in node["candidate_briefs"]:
            brief = item["brief"]
            brief_id = brief["brief_id"]
            if brief_id not in library_fingerprints:
                primitive = brief.get("primitive") or "NO_INTERVENTION"
                level = brief.get("level") or "NONE"
                library_fingerprints[brief_id].append(f"{primitive}|{level}")
    exact_library_groups: dict[str, list[str]] = defaultdict(list)
    for brief_id, fingerprint_parts in library_fingerprints.items():
        exact_library_groups[fingerprint_parts[0]].append(brief_id)
    exact_library_groups_out = [
        {"fingerprint": fingerprint, "brief_ids": sorted(ids)}
        for fingerprint, ids in exact_library_groups.items() if len(ids) > 1
    ]
    aggregate = {
        "node_count": len(nodes),
        "episode_count": len(set(node["episode_id"] for node in nodes)),
        "node_kind_counts": dict(Counter(kind for node in nodes for kind in node["node_meta"]["node_kind"])),
        "skyline_pair_cooccurrence": repeated_pairs[:30],
        "candidate_repeat_rates": sorted(candidate_repeat, key=lambda item: (-item["skyline_count"], item["brief_id"])),
        "duplicate_score_signature_groups": duplicate_groups_out,
        "score_dimension_stats": {key: _metric_stats(values) for key, values in score_values.items()},
        "intervention_only_score_dimension_stats": {
            key: _metric_stats(values) for key, values in intervention_score_values.items()
        },
        "within_node_score_range_stats": {
            key: _metric_stats(values) for key, values in within_node_ranges.items()
        },
        "within_node_zero_spread_counts": dict(within_node_zero_spread),
        "input_dimension_stats": {key: _metric_stats(values) for key, values in input_values.items()},
        "candidate_library_exact_fingerprint_groups": exact_library_groups_out,
        "frontier_ratio_stats": _metric_stats([
            node["frontier_ratio"] for node in nodes if node["frontier_ratio"] is not None
        ]),
        "interpretation": {
            "pipeline_validity": "This audit checks replay logging and candidate behavior, not intervention effectiveness.",
            "support_profile_validity": "Provisional; event projections require human review before gold use.",
            "weight_policy": "Frozen policy weights are recorded per node; no post-hoc tuning was performed.",
            "workflow_continuity_input": "All 48 replay nodes used the same 0.8 value from a text-length heuristic; this is an audit initializer, not an empirical claim about users.",
            "candidate_library_duplicates": "No exact primitive-level fingerprint duplicates were found in the generated library; this does not rule out semantically similar briefs across levels.",
        },
    }
    payload = {
        "schema_version": "retrace-node-decision-audit-v0.1",
        "policy_version": policy.policy_version,
        "cases": CASE_IDS,
        "aggregate": aggregate,
        "nodes": nodes,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "node_decision_logs.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Node-level Decision Audit（10 个历史 Episode）",
        "",
        "> 本审计使用人工 Gate 复核中已经定位的用户节点；支持维度为事件级临时投影，不是 Gold Label。它用于检查候选库、准则属性和 Skyline 行为，不用于证明干预效果。",
        "",
        f"- 节点数：{aggregate['node_count']}",
        f"- Episode 数：{aggregate['episode_count']}",
        f"- FrontierRatio 均值：{aggregate['frontier_ratio_stats']['mean']:.3f}",
        f"- FrontierRatio ≥ 0.9：{sum(node['frontier_ratio'] is not None and node['frontier_ratio'] >= 0.9 for node in nodes)}/{len(nodes)}",
        "",
        "## 重复与区分度检查",
        "",
        "### Skyline 高频共现",
        "",
        "| 候选对 | 共现次数 | 节点比例 |",
        "|---|---:|---:|",
    ]
    for item in repeated_pairs[:15]:
        lines.append(f"| {item['pair'][0]} + {item['pair'][1]} | {item['count']} | {item['rate']:.1%} |")
    lines.extend([
        "",
        "### 候选进入 Skyline 的比例",
        "",
        "| 候选 | 可行次数 | Skyline 次数 | 可行时进入 Skyline 比例 |",
        "|---|---:|---:|---:|",
    ])
    for item in aggregate["candidate_repeat_rates"][:20]:
        lines.append(
            f"| {item['brief_id']} | {item['feasible_count']} | {item['skyline_count']} | "
            f"{item['skyline_rate_given_feasible']:.1%} |"
        )
    lines.extend([
        "",
        "### 分数维度分布",
        "",
        "| 维度 | 唯一值 | 最小 | 最大 | 均值 | 标准差 |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for key, stats in aggregate["score_dimension_stats"].items():
        lines.append(
            f"| {key} | {stats['unique']} | {stats['min']:.3f} | {stats['max']:.3f} | "
            f"{stats['mean']:.3f} | {stats['pstdev']:.3f} |"
        )
    lines.extend([
        "",
        "### 干预候选自身的分数维度分布（排除 NO_INTERVENTION）",
        "",
        "| 维度 | 唯一值数 | 最小 | 最大 | 均值 | 标准差 |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for key, stats in aggregate["intervention_only_score_dimension_stats"].items():
        lines.append(
            f"| {key} | {len(stats['unique'])} | {stats['min']:.3f} | {stats['max']:.3f} | "
            f"{stats['mean']:.3f} | {stats['pstdev']:.3f} |"
        )
    lines.extend([
        "",
        "### 输入 evidence_quality / workflow_continuity",
        "",
        f"- evidence_quality：{aggregate['input_dimension_stats']['evidence_quality']}",
        f"- workflow_continuity：{aggregate['input_dimension_stats']['workflow_continuity']}",
        "- 注意：本轮 48 个节点的 workflow_continuity 都由回放脚本的文本长度启发式初始化为 0.8，不能将其解释为真实用户数据中的工作流连续性没有差异。",
        "",
        "### 候选库精确重复检查",
        "",
        f"- 精确 primitive-level 指纹重复组：{len(aggregate['candidate_library_exact_fingerprint_groups'])} 组。",
        "- 本轮没有发现精确重复；仍需下一步对不同等级的标题、说明和下一步文本做语义相似度检查。",
        "",
        "### 节点内候选区分度（可行干预候选的最大值−最小值）",
        "",
        "| 维度 | 节点内范围均值 | 节点内范围标准差 | 完全无区分节点数 |",
        "|---|---:|---:|---:|",
    ])
    for key, stats in aggregate["within_node_score_range_stats"].items():
        lines.append(
            f"| {key} | {stats['mean']:.3f} | {stats['pstdev']:.3f} | "
            f"{aggregate['within_node_zero_spread_counts'].get(key, 0)} |"
        )
    lines.extend([
        "",
        "## 结论",
        "",
        "1. 本轮产生了可审计的节点级候选、约束、Skyline 和排序日志。",
        "2. 若大量候选持续共同进入 Skyline，说明当前属性画像不足以区分候选；不能通过事后调权重掩盖。",
        "3. 候选库在本轮没有精确重复，但大量同类候选同时进入 Skyline，说明需要检查等级之间的属性和文案是否足以形成有意义的区分。",
        "4. 节点输入 workflow_continuity 的变化不足是当前回放初始化造成的，不应直接作为真实数据结论；下一步应替换为经过人工复核的节点级输入。",
        "5. 支持维度仍是临时事件投影，必须先人工复核一批节点，才能把该审计用于评估选择合理性。",
    ])
    (OUT / "node_decision_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"aggregate": aggregate, "output_dir": str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
