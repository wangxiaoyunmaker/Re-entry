"""Replay the fixed 20-episode smoke set with the epsilon=.005 pilot policy."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_20_episode_replay import (
    REGISTRY_PATH,
    SEQUENCE_INPUT,
    _profiles,
    _run_episode,
    _select_episodes,
)


POLICY_PATH = ROOT / "config/selection_policy.pilot.v2.epsilon005.json"
OUT_JSON = ROOT / "artifacts/real_episode_20_replay_pilot_epsilon005.json"
OUT_MD = ROOT / "artifacts/real_episode_20_replay_pilot_epsilon005.md"


def main() -> None:
    rows = [json.loads(line) for line in SEQUENCE_INPUT.open(encoding="utf-8")]
    selected_rows = _select_episodes(rows, 20)
    profiles = _profiles(selected_rows)
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    base_time = datetime(2026, 8, 26, tzinfo=timezone.utc)
    episodes = [_run_episode(row, profiles, registry, policy, base_time) for row in selected_rows]

    decisions = Counter()
    reasons = Counter()
    gates = Counter()
    choices = 0
    exposures = checkpoints = releases = 0
    for episode in episodes:
        for item in episode["rounds"]:
            selection = item["selection"]
            decision = selection["decision"]
            decisions[decision] += 1
            if decision == "NO_INTERVENTION":
                reasons[selection.get("objective", {}).get("reason", "UNSPECIFIED")] += 1
            mode = selection.get("objective", {}).get("semantic_constraints", {}).get("family_gate_mode")
            if mode:
                gates[mode] += 1
            if decision == "PRESENT_CHOICES":
                choices += 1
            if "exposure" in item:
                exposures += 1
            if "cooldown_selection" in item:
                checkpoints += 1
            if item.get("new_user_event_after_exposure", {}).get("cooldown_released"):
                releases += 1

    payload = {
        "schema_version": "retrace-real-episode-20-replay-pilot-v2",
        "policy": policy,
        "selected_episode_ids": [row["final_episode_id"] for row in selected_rows],
        "posterior_outcome_evidence_used": False,
        "pilot_coding_note": "C/S/A, family hints and evidence refs are transparent text-only pilot coding; family precedence, confirmation/execution-gap handling and intensity cap were updated from the 19-round audit; target is fixed at (2,3,2), not efficacy evaluation.",
        "summary": {
            "episode_count": len(episodes),
            "round_count": sum(len(episode["rounds"]) for episode in episodes),
            "decision_counts": dict(sorted(decisions.items())),
            "no_intervention_reasons": dict(sorted(reasons.items())),
            "family_gate_modes": dict(sorted(gates.items())),
            "choice_count": choices,
            "exposure_count": exposures,
            "cooldown_checkpoint_count": checkpoints,
            "cooldown_release_count": releases,
        },
        "episodes": episodes,
    }
    if payload["summary"]["episode_count"] != 20:
        raise SystemExit("pilot replay did not replay exactly 20 episodes")
    if exposures == 0 or checkpoints != exposures or releases != exposures:
        raise SystemExit("pilot replay did not complete exposure -> cooldown -> user-event release")

    lines = [
        "# 20 个 Episode · epsilon=0.005 pilot replay",
        "",
        "> Pilot 使用 epsilon=0.005 和 semantic_hint_soft_margin=0.12；beta、eta、workflow lambda、tau 和所有硬数据契约保持不变。family precedence、确认/直接执行缺口判定和强度上限依据 19 轮人工审计更新。输入只使用当前冻结用户轮次，不读取 posterior outcome evidence。",
        "",
        f"- Episode：{payload['summary']['episode_count']}；轮次：{payload['summary']['round_count']}",
        f"- 决策分布：{json.dumps(payload['summary']['decision_counts'], ensure_ascii=False)}",
        f"- NO_INTERVENTION 原因：{json.dumps(payload['summary']['no_intervention_reasons'], ensure_ascii=False)}",
        f"- Family gate：{json.dumps(payload['summary']['family_gate_modes'], ensure_ascii=False)}",
        f"- 双选项：{choices}；Exposure / cooldown / 解除：{exposures} / {checkpoints} / {releases}",
        "",
        "| Episode | 轮次序列 |",
        "|---|---|",
    ]
    symbols = {"INTERVENE": "I", "PRESENT_CHOICES": "C", "NO_INTERVENTION": "N"}
    for episode in episodes:
        sequence = "".join(symbols[item["selection"]["decision"]] for item in episode["rounds"])
        lines.append(f"| {episode['episode_id']} | {sequence} |")
    lines.extend([
        "",
        "该组参数是 pilot candidate，不是正式校准结论；下一步仍需把人工逐轮判断作为外部参照。",
    ])
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(OUT_JSON)
    print(OUT_MD)


if __name__ == "__main__":
    main()
