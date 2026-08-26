"""Sweep selector parameters on the same 20-episode replay.

The input trace and text-only pilot coding are held fixed.  Only the scalar
selector policy parameters change, so the output is a diagnostic for parameter
geometry rather than a new annotation or efficacy evaluation.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_20_episode_replay import (  # noqa: E402
    POLICY_PATH,
    REGISTRY_PATH,
    SEQUENCE_INPUT,
    _profiles,
    _run_episode,
    _select_episodes,
)


OUTPUT = ROOT / "artifacts/real_episode_20_parameter_sensitivity_v1.json"
OUTPUT_MD = ROOT / "artifacts/real_episode_20_parameter_sensitivity_v1.md"


def _metrics(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = Counter()
    reasons = Counter()
    families = Counter()
    gates = Counter()
    exposure_count = cooldown_count = release_count = choice_count = 0
    adjacent_intervention_pairs = 0
    same_family_adjacent_pairs = 0
    max_intervention_streak = 0
    for episode in episodes:
        previous_decision = None
        previous_family = None
        streak = 0
        for item in episode["rounds"]:
            selection = item["selection"]
            decision = selection["decision"]
            decisions[decision] += 1
            objective = selection.get("objective", {})
            if decision == "NO_INTERVENTION":
                reasons[objective.get("reason", "UNSPECIFIED")] += 1
                streak = 0
            else:
                if previous_decision in {"INTERVENE", "PRESENT_CHOICES"}:
                    adjacent_intervention_pairs += 1
                streak += 1
                max_intervention_streak = max(max_intervention_streak, streak)
            semantic = objective.get("semantic_constraints", {})
            if semantic.get("family_gate_mode"):
                gates[semantic["family_gate_mode"]] += 1
            if decision == "PRESENT_CHOICES":
                choice_count += 1
            options = selection.get("options", [])
            if options:
                families[options[0].get("strategy_family", "UNKNOWN")] += 1
            if previous_family and options and previous_family == options[0].get("strategy_family"):
                same_family_adjacent_pairs += 1
            previous_decision = decision
            previous_family = options[0].get("strategy_family") if options else None
            if "exposure" in item:
                exposure_count += 1
            if "cooldown_selection" in item:
                cooldown_count += 1
            if item.get("new_user_event_after_exposure", {}).get("cooldown_released"):
                release_count += 1
    return {
        "decision_counts": dict(sorted(decisions.items())),
        "no_intervention_reasons": dict(sorted(reasons.items())),
        "family_counts": dict(sorted(families.items())),
        "family_gate_modes": dict(sorted(gates.items())),
        "exposure_count": exposure_count,
        "cooldown_checkpoint_count": cooldown_count,
        "cooldown_release_count": release_count,
        "choice_count": choice_count,
        "adjacent_intervention_pairs": adjacent_intervention_pairs,
        "same_family_adjacent_pairs": same_family_adjacent_pairs,
        "max_intervention_streak": max_intervention_streak,
    }


def run() -> dict[str, Any]:
    rows = [json.loads(line) for line in SEQUENCE_INPUT.open(encoding="utf-8")]
    selected_rows = _select_episodes(rows, 20)
    profiles = _profiles(selected_rows)
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    base_policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    variants: list[dict[str, Any]] = []
    grid = {
        "epsilon": (0.005, 0.01, 0.03),
        "eta": (0.03, 0.05, 0.08),
        "beta": (0.60, 0.75),
        "workflow_exposure_lambda": (0.05, 0.10),
        "workflow_decay_tau_seconds": (300, 600),
        "semantic_hint_soft_margin": (0.00, 0.03, 0.05, 0.08, 0.12),
    }
    fields = tuple(grid)
    for values in itertools.product(*(grid[field] for field in fields)):
        policy = {**base_policy, **dict(zip(fields, values))}
        base_time = datetime(2026, 8, 26, tzinfo=timezone.utc)
        episodes = [
            _run_episode(row, profiles, registry, policy, base_time)
            for row in selected_rows
        ]
        variants.append({"policy": {field: policy[field] for field in fields}, "metrics": _metrics(episodes)})
    return {
        "schema_version": "retrace-real-episode-20-parameter-sensitivity-v1",
        "episode_ids": [row["final_episode_id"] for row in selected_rows],
        "variant_count": len(variants),
        "base_policy": base_policy,
        "grid": grid,
        "interpretation": "same 20-episode trace and text-only pilot coding; parameter sensitivity, not calibration or efficacy",
        "variants": variants,
    }


def _write_markdown(payload: dict[str, Any]) -> None:
    rows = sorted(
        payload["variants"],
        key=lambda item: (
            item["metrics"]["choice_count"],
            item["metrics"]["adjacent_intervention_pairs"],
            item["metrics"]["decision_counts"].get("NO_INTERVENTION", 0),
        ),
    )
    lines = [
        "# 20-episode Selector 参数敏感性",
        "",
        "> 固定同一批 20 个 episode、同一份文本 pilot coding，只改变 epsilon/eta/beta/workflow lambda/tau/semantic hint soft margin。排序仅用于观察候选，不代表正式校准结果。",
        "",
        f"- Variant：{payload['variant_count']}",
        f"- Episode：{', '.join(payload['episode_ids'])}",
        "",
        "| epsilon | eta | beta | lambda | tau | semantic soft margin | I | C | N | 连续 I/C 相邻对 | 同 family 相邻对 | 最大连续段 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in rows[:30]:
        policy = item["policy"]
        metrics = item["metrics"]
        counts = metrics["decision_counts"]
        lines.append(
            f"| {policy['epsilon']} | {policy['eta']} | {policy['beta']} | "
            f"{policy['workflow_exposure_lambda']} | {policy['workflow_decay_tau_seconds']} | {policy['semantic_hint_soft_margin']} | "
            f"{counts.get('INTERVENE', 0)} | {counts.get('PRESENT_CHOICES', 0)} | "
            f"{counts.get('NO_INTERVENTION', 0)} | {metrics['adjacent_intervention_pairs']} | "
            f"{metrics['same_family_adjacent_pairs']} | {metrics['max_intervention_streak']} |"
        )
    lines.extend([
        "",
        "只展示按 C 数量、连续干预相邻对和 N 数量排序后的前 30 个 variant；完整结果见 JSON。",
    ])
    OUTPUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = run()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(payload)
    print(json.dumps({"variant_count": payload["variant_count"], "episode_count": len(payload["episode_ids"])}, ensure_ascii=False, indent=2))
    print(OUTPUT)
    print(OUTPUT_MD)


if __name__ == "__main__":
    main()
