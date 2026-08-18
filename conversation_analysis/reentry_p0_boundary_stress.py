"""Counterfactual boundary stress test for the frozen P0 prompt.

These are researcher-authored diagnostic cases, not empirical trace evidence and
not a replacement for the locked generalization suite.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import reentry_p0_discovery as p0
import reentry_p0_optimization_v4 as opt


OUT_DIR = p0.OUT_DIR / "frozen_dev_v1_boundary_stress"
RUN_DIR = OUT_DIR / "runs"
REPORT_PATH = OUT_DIR / "boundary_stress_report.md"


CASES = [
    ("S01", "Agent: 修复完成，登录测试全部通过。", "登录还是报 500，和修复前一样。", "RETAIN_STRONG", "project_failure"),
    ("S02", "Agent: 已完成账号数据隔离并通过测试。", "测试账号仍能看到另一个用户的数据。", "RETAIN_STRONG", "project_failure"),
    ("S03", "Agent: 我重构了导出模块并完成构建。", "重构后 CSV 导出打不开了，之前是正常的。", "RETAIN_STRONG", "regression"),
    ("S04", "Agent: 图片依据转写文本生成，声音分析只用于描述。", "这条管线不对，图片媒介必须依据声音质感。", "RETAIN_STRONG", "relation_mismatch"),
    ("S05", "Agent: 新功能已经合并，但之前的筛选逻辑被覆盖。", "先查 Git 历史，确认哪个版本覆盖了筛选，再决定怎么恢复。", "RETAIN_STRONG", "history_reconstruction"),
    ("S06", "Agent: 累计爬升 2600 米，最低 40 米、最高 1100 米。", "这个累计爬升是怎么算出来的？数值明显不合理。", "RETAIN_STRONG", "evidence_challenge"),
    ("S07", "Agent: 我再次重构了认证和支付两个模块。", "先别再动支付，只在认证范围内复现并修复这个错误。", "RETAIN_STRONG", "boundary_control"),
    ("S08", "Agent: 当前用户表只有全局 user_id，没有租户关系。", "现在要支持多租户，但现有表结构无法隔离租户，需要先补关系。", "RETAIN_STRONG", "existing_limitation"),
    ("S09", "Agent: 数据迁移和页面更新已完成。", "迁移后仪表盘好像没有继续更新，但我还没拿到日志。", "RETAIN_POSSIBLE", "ambiguous_regression"),
    ("S10", "Agent: 权限模块刚完成重构。", "我怀疑普通成员现在也能看到管理入口，还没有完整复现。", "RETAIN_POSSIBLE", "ambiguous_regression"),
    ("S11", "Agent: 新版本已部署到生产环境。", "现在访问的会不会还是旧环境？页面看起来没有变化。", "RETAIN_POSSIBLE", "ambiguous_state"),
    ("S12", "Agent: 已完成旧数据迁移。", "我不确定迁移后原来的记录 ID 是否还被保留。", "RETAIN_POSSIBLE", "verification_needed"),
    ("N01", "Agent: 请告诉我你想构建什么。", "帮我做一个课程报名网站。", "DO_NOT_RETAIN", "initial_request"),
    ("N02", "Agent: TECH_SPEC.md 已经整理完成。", "接下来按照文档实现搜索模块。", "DO_NOT_RETAIN", "ordinary_next_task"),
    ("N03", "Agent: 已生成蓝色主页。", "我改主意了，换成绿色更好看。", "DO_NOT_RETAIN", "pure_preference"),
    ("N04", "Agent: API safeguards blocked the previous cybersecurity request. Try a new session.", "你好 Claude。", "DO_NOT_RETAIN", "agent_service_failure"),
    ("N05", "Agent: 当前单用户版本运行正常。", "下一版增加消息通知功能。", "DO_NOT_RETAIN", "new_feature"),
    ("N06", "Agent: 请提供飞书表格地址。", "这是表格 URL：https://example.com/table。", "DO_NOT_RETAIN", "information_supply"),
    ("N07", "Agent: 页面已经按要求完成并通过测试。", "好的，谢谢。", "DO_NOT_RETAIN", "acknowledgement"),
    ("N08", "Agent: 可以选择 PostgreSQL、SQLite 或 MySQL。", "这几个后端方案分别是什么意思？", "DO_NOT_RETAIN", "concept_question"),
    ("N09", "Agent: 正在继续生成剩余文件，目前完成一半。", "继续。", "DO_NOT_RETAIN", "ongoing_continuation"),
    ("N10", "Agent: 登录功能已经验收完成。", "很好，接下来做个人资料页。", "DO_NOT_RETAIN", "successful_next_task"),
    ("N11", "Agent: Rate limit exceeded，请稍后重试。", "再试一次。", "DO_NOT_RETAIN", "agent_service_failure"),
    ("N12", "Agent: 我还没有查看或修改你的项目。", "先别看，这个项目还没完善好。", "DO_NOT_RETAIN", "no_prior_instantiation"),
]


def build_chunks(replicate: int) -> list[dict[str, Any]]:
    rows = []
    for case_id, context, target, expected, category in CASES:
        rows.append(
            {
                "case_id": case_id,
                "expected_decision": expected,
                "category": category,
                "unit": {
                    "target_user_event_id": case_id,
                    "source_record_index": None,
                    "source_prompt_event_id": None,
                    "target_user_text": target,
                    "prior_user_context": [],
                    "immediately_preceding_agent_context": [
                        {"event_id": f"E{int(case_id[1:]):06d}", "actor": "AGENT", "text": context}
                    ],
                },
            }
        )
    chunks = []
    for index, start in enumerate(range(0, len(rows), 4), start=1):
        selected = rows[start : start + 4]
        chunks.append(
            {
                "participant_id": "SYNTHETIC_BOUNDARY_STRESS",
                "conversation_id": f"BOUNDARY-STRESS-R{replicate}",
                "source_path": "SYNTHETIC_BOUNDARY_STRESS",
                "chunk_id": f"BOUNDARY-STRESS-R{replicate}-C{index:02d}",
                "target_count": len(selected),
                "scan_units": [row["unit"] for row in selected],
                "references": {row["case_id"]: row for row in selected},
            }
        )
    return chunks


def run(config: str, replicate: int, env_path: Path, provider: str) -> None:
    output = []
    chunks = build_chunks(replicate)
    for index, chunk in enumerate(chunks, start=1):
        try:
            if config == "FROZEN_V1":
                result, meta = p0.call_api(chunk, env_path, provider)
            else:
                result, meta = opt.call_verdict_api(chunk, config, env_path, provider)
            verdicts = {item["target_event_id"]: item for item in result["verdicts"]}
            error = None
        except Exception as exc:
            verdicts = {}
            meta = {}
            error = f"{type(exc).__name__}: {exc}"
        for unit in chunk["scan_units"]:
            case_id = unit["target_user_event_id"]
            reference = chunk["references"][case_id]
            verdict = verdicts.get(case_id)
            output.append(
                {
                    "config": config,
                    "replicate": replicate,
                    "chunk_id": chunk["chunk_id"],
                    "case_id": case_id,
                    "category": reference["category"],
                    "expected_decision": reference["expected_decision"],
                    "expected_positive": reference["expected_decision"] != "DO_NOT_RETAIN",
                    "predicted_decision": verdict and verdict["decision"],
                    "predicted_positive": None if verdict is None else verdict["decision"] != "DO_NOT_RETAIN",
                    "rationale": verdict and verdict["rationale"],
                    "error": error,
                    "chunk_meta": meta if unit is chunk["scan_units"][0] else None,
                }
            )
        p0.write_jsonl(RUN_DIR / f"{config}__r{replicate}.jsonl", output)
        print(f"boundary stress {config} r{replicate} [{index}/{len(chunks)}]")
    report()


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row["predicted_positive"] is not None]
    tp = sum(row["expected_positive"] and row["predicted_positive"] for row in valid)
    fp = sum((not row["expected_positive"]) and row["predicted_positive"] for row in valid)
    fn = sum(row["expected_positive"] and (not row["predicted_positive"]) for row in valid)
    tn = sum((not row["expected_positive"]) and (not row["predicted_positive"]) for row in valid)
    return {
        "valid": len(valid),
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "specificity": tn / (tn + fp) if tn + fp else 0.0,
        "binary_accuracy": (tp + tn) / len(valid) if valid else 0.0,
        "exact_accuracy": sum(row["expected_decision"] == row["predicted_decision"] for row in valid) / len(valid) if valid else 0.0,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


def report() -> None:
    paths = sorted(RUN_DIR.glob("*__r*.jsonl")) if RUN_DIR.exists() else []
    runs = []
    for path in paths:
        config, replicate_text = path.stem.rsplit("__r", 1)
        runs.append((config, int(replicate_text), p0.read_jsonl(path)))
    lines = [
        "# P0 Prompt Boundary Stress Test",
        "",
        "> Researcher-authored diagnostic minimal pairs; not empirical evidence or adjudicated Gold.",
        "",
        "| Config | Rep | Valid | Precision | Recall | Specificity | Binary accuracy | Exact 3-label accuracy | TP | FP | FN | TN |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    scored: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for config, replicate, rows in runs:
        current = metrics(rows)
        scored[config].append(current)
        lines.append(
            f"| {config} | {replicate} | {current['valid']}/24 | {current['precision']:.4f} | {current['recall']:.4f} | "
            f"{current['specificity']:.4f} | {current['binary_accuracy']:.4f} | {current['exact_accuracy']:.4f} | "
            f"{current['tp']} | {current['fp']} | {current['fn']} | {current['tn']} |"
        )
    if scored:
        lines.extend(["", "## Aggregate by config", "", "| Config | Runs | Mean precision | Mean recall | Mean specificity |", "|---|---:|---:|---:|---:|"])
        for config, items in scored.items():
            lines.append(
                f"| {config} | {len(items)} | {mean(item['precision'] for item in items):.4f} | "
                f"{mean(item['recall'] for item in items):.4f} | {mean(item['specificity'] for item in items):.4f} |"
            )
    lines.extend(["", "## Misclassifications", ""])
    for config, replicate, rows in runs:
        lines.extend([f"### {config} — Replicate {replicate}", ""])
        errors = [row for row in rows if row["predicted_decision"] != row["expected_decision"]]
        for row in errors:
            lines.append(
                f"- `{row['case_id']}` `{row['category']}`: expected `{row['expected_decision']}`, got `{row['predicted_decision']}` — {row['rationale']}"
            )
        if not errors:
            lines.append("- 无")
        lines.append("")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument(
        "--config",
        choices=["FROZEN_V1", "GATE_V2_IMPLICIT_C4", "GATE_V2_RECALL_C4", "GATE_V21_C4", "GATE_V22_C4", "GATE_V23_DEV_C1_FLASH", "GATE_V24_DEV_C1_FLASH", "GATE_V25_DEV_C1_FLASH", "GATE_V2_EXPLICIT_C4"],
        default="FROZEN_V1",
    )
    run_parser.add_argument("--replicate", type=int, required=True)
    run_parser.add_argument("--env", type=Path, required=True)
    run_parser.add_argument("--provider", choices=["photomind", "deepseek"], default="photomind")
    sub.add_parser("report")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.command == "run":
        run(args.config, args.replicate, args.env, args.provider)
    else:
        report()
