"""Run a deterministic 20-episode online-selector smoke replay.

This replay deliberately does not read posterior outcome annotations.  It uses
only the frozen sequence's current user turns, then derives transparent pilot
C/S/A and selector-hint inputs from those turns.  The result tests runtime
contracts and parameter geometry, not intervention efficacy.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

from retrace_selector.online_inference_v2 import OnlineInferenceService


ROOT = Path(__file__).resolve().parents[2]
SEQUENCE_INPUT = Path(
    "/Users/wy/Desktop/HCI-过程性归档-20260825/"
    "outputs-untracked/reentry_full_86_analysis_20260825_v1/00_input/"
    "04_expanded_primary_sequence_2plus.jsonl"
)
OUT_JSON = ROOT / "retrace_selector/artifacts/real_episode_20_replay_v2.json"
OUT_MD = ROOT / "retrace_selector/artifacts/real_episode_20_replay_v2.md"
REGISTRY_PATH = ROOT / "retrace_selector/config/strategy_registry.formal.v1.json"
POLICY_PATH = ROOT / "retrace_selector/config/selection_policy.formal.v1.json"

_DIMENSIONS = ("criteria", "state", "action")
_FAMILIES = (
    "STATE_CONTEXT_RECOVERY",
    "RULE_CLARIFICATION",
    "CLAIM_EVIDENCE_CALIBRATION",
    "GOVERNANCE_ACTION_PLANNING",
)


def _event(
    event_id: str,
    session_id: str,
    event_type: str,
    observed_at: str,
    payload: dict[str, Any],
    *,
    actor: str = "USER",
    turn_id: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "session_id": session_id,
        "event_type": event_type,
        "actor": actor,
        "project_id": "REENTRY-20-EPISODE-SMOKE",
        "observed_at": observed_at,
        "received_at": observed_at,
        "source": "CODEX_HOOK",
        "turn_id": turn_id,
        "payload": payload,
    }


def _semantic_role(text: str) -> str:
    if any(token in text for token in ("为什么", "怎么算", "依据", "差异", "解释", "确定", "核查", "确认", "验证", "测试", "bug", "真的", "加入了吗", "有了吗")):
        return "CLAIM_VERIFICATION"
    if any(token in text for token in ("规则", "条件", "默认", "标准", "边界", "命名", "笼统", "分层", "复制", "字段")):
        return "RULE_STATEMENT"
    if any(token in text for token in ("整理", "计划", "归档", "设计", "方案", "通用", "架构", "下一步", "范围", "回退", "要求", "改", "实现")):
        return "USER_BOUNDARY"
    return "USER_OBSERVATION"


def _family_for(text: str) -> str:
    if any(token in text for token in ("为什么", "怎么算", "依据", "差异", "解释", "核查", "验证", "bug", "真的", "加入了吗", "有了吗", "加了吗")):
        return "CLAIM_EVIDENCE_CALIBRATION"
    detailed_rule_request = (
        len(text) >= 100
        and sum(text.count(token) for token in ("应该", "不应该", "需要", "调整", "点击", "弹出", "补充")) >= 2
    )
    if detailed_rule_request:
        return "RULE_CLARIFICATION"
    if any(token in text for token in ("规则", "条件", "默认", "标准", "边界", "命名", "笼统", "分层", "复制", "字段")):
        return "RULE_CLARIFICATION"
    if any(token in text for token in ("整理", "计划", "归档", "设计", "方案", "通用", "架构", "下一步", "列出来", "列出", "暂停", "回退", "允许", "停止", "范围", "继续")):
        return "GOVERNANCE_ACTION_PLANNING"
    return "STATE_CONTEXT_RECOVERY"


def _coding(text: str, index: int, total: int) -> dict[str, Any]:
    """Transparent text-only pilot coding; no outcome fields are consulted."""
    criteria = min(
        3,
        int(any(token in text for token in ("为什么", "是否", "选择", "方案", "策略", "规则", "条件", "阈值", "标准")))
        + int(len(text) >= 80)
        + int(any(token in text for token in ("比较", "区别", "只能", "不要", "必须", "边界"))),
    )
    state = min(
        3,
        int(any(token in text for token in ("现在", "目前", "实际", "发现", "问题", "异常", "失败", "成功", "历史", "数据", "为什么")))
        + int(any(token in text for token in ("复现", "导致", "因为", "仍然", "还", "已经")))
        + int(any(token in text for token in ("核查", "测试", "验证", "对比"))),
    )
    action = min(
        3,
        int(any(token in text for token in ("请", "帮我", "改", "实现", "添加", "删除", "保留", "恢复", "测试", "验证", "确认", "检查", "要求")))
        + int(any(token in text for token in ("只", "不要", "必须", "确保", "回退", "继续")))
        + int(index == total or any(token in text for token in ("全量", "完成", "直接"))),
    )
    # Force the replay to exercise an unknown/early signal dimension without
    # fabricating a posterior label: short descriptive turns remain low.
    if len(text) < 12:
        criteria = min(criteria, 1)
    return {
        "criteria": criteria,
        "state": state,
        "action": action,
        "basis": "仅依据当前冻结用户轮次文本的透明关键词/长度 pilot coding",
    }


def _hint(text: str, *, evidence_present: bool, coding: dict[str, Any]) -> dict[str, Any]:
    family = _family_for(text)
    execution = any(token in text for token in ("改", "实现", "添加", "删除", "恢复", "继续", "完成"))
    normalized = text.strip().lower()
    normalized_confirmation = normalized.rstrip(" .。!！")
    confirmation_only = (
        len(text.strip()) <= 30
        and (
            normalized_confirmation in {"approve", "可以", "对", "好的", "是的", "行"}
            or normalized_confirmation.endswith("approve")
        )
        and not any(token in text for token in ("为什么", "怎么", "依据", "验证", "确认"))
    )
    trailing_confirmation = (
        normalized.endswith("对吧？")
        or normalized.endswith("对吧?")
        or normalized.endswith("是吧？")
        or normalized.endswith("是吧?")
    ) and not any(token in text for token in ("为什么", "怎么", "依据", "如何"))
    direct_execution_only = (
        any(token in text for token in ("查看", "看下", "拿掉", "删除", "打开"))
        and not any(token in text for token in ("为什么", "依据", "是否", "验证", "确认", "影响"))
    )
    direct_rule_instruction = (
        any(token in text for token in ("别写得", "不要写", "必须", "只保留", "要分开"))
        and not any(token in text for token in ("为什么", "怎么", "依据", "方案", "计划", "是否"))
    )
    gap = not confirmation_only and not trailing_confirmation and not direct_execution_only and not direct_rule_instruction and not any(token in text for token in ("没问题", "不用改", "已经解决", "满意", "可以了", "无需"))
    peak = max(coding[dimension] for dimension in _DIMENSIONS)
    max_intensity = 1
    if (
        peak >= 3 and any(token in text for token in ("比较", "区别", "多个", "计划", "归档", "回退", "验收", "边界"))
    ) or (
        len(text) >= 100
        and sum(text.count(token) for token in ("应该", "不应该", "需要", "调整", "点击", "弹出", "补充")) >= 2
    ):
        max_intensity = 2
    if peak >= 3 and sum(token in text for token in ("计划", "归档", "测试", "回退", "停止")) >= 3:
        max_intensity = 3
    return {
        "support_family": family,
        "allowed_families": [family],
        "confidence": "HIGH" if evidence_present else "MEDIUM",
        "max_intensity": max_intensity,
        "cognitive_gap_detected": gap,
        "execution_request_detected": execution,
        "reason": "仅依据当前冻结用户轮次文本的透明 pilot family coding",
    }


def _evidence_ref(event_id: str, turn_id: str, evidence_id: str, family: str, coding: dict[str, Any], text: str) -> dict[str, Any]:
    semantic_dimensions = {dimension for dimension in _DIMENSIONS if coding[dimension] > 0}
    if not semantic_dimensions:
        semantic_dimensions = {
            "CLAIM_EVIDENCE_CALIBRATION": {"criteria", "state"},
            "RULE_CLARIFICATION": {"criteria"},
            "GOVERNANCE_ACTION_PLANNING": {"action"},
            "STATE_CONTEXT_RECOVERY": {"state"},
        }.get(family, {"state"})
    return {
        "evidence_id": evidence_id,
        "source_event_id": event_id,
        "source": "CURRENT_USER_TURN",
        "semantic_role": _semantic_role(text),
        "supports_families": [family],
        "supports_dimensions": sorted(semantic_dimensions),
        "source_turn_id": turn_id,
    }


def _choose_option(selection: dict[str, Any], text: str) -> tuple[str, str, str]:
    options = list(selection.get("selected", []))
    specs = {item.get("strategy_id"): item for item in selection.get("options", []) if isinstance(item, dict)}
    preferred = _family_for(text)
    chosen = next((item for item in options if specs.get(item, {}).get("strategy_family") == preferred), options[0])
    condition = specs.get(chosen, {}).get("branch_condition_code", "DEFAULT_FIRST_OPTION")
    return chosen, condition, f"text-only pilot branch: preferred_family={preferred}"


def _select_episodes(rows: list[dict[str, Any]], count: int = 20) -> list[dict[str, Any]]:
    eligible = [row for row in rows if len(row.get("user_turns", [])) >= 2]
    if len(eligible) < count:
        raise SystemExit(f"only {len(eligible)} eligible episodes; need {count}")
    indices = [round(index * (len(eligible) - 1) / (count - 1)) for index in range(count)]
    return [eligible[index] for index in indices]


def _profiles(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        f"FD-20-{row['final_episode_id']}": {
            "profile_id": f"FD-20-{row['final_episode_id']}",
            "decision_object": row["focal_target"],
            "target_state": {"criteria": 2, "state": 3, "action": 2, "rubric_version": "CSA-RUBRIC-V1"},
            "allowed_evidence_types": ["EV01", "EV05", "EV06"],
        }
        for row in rows
    }


def _run_episode(row: dict[str, Any], profiles: dict[str, dict[str, Any]], registry: dict[str, Any], policy: dict[str, Any], base_time: datetime) -> dict[str, Any]:
    episode_id = row["final_episode_id"]
    session_id = f"SESSION-20-{episode_id}"
    chain_id = f"CHAIN-20-{episode_id}"
    profile_id = f"FD-20-{episode_id}"
    with tempfile.TemporaryDirectory(prefix=f"retrace-20-{episode_id}-") as directory:
        service = OnlineInferenceService(
            database_path=Path(directory) / "online.sqlite3",
            profiles=profiles,
            registry=registry,
            config=policy,
        )
        occasion_time = base_time.isoformat().replace("+00:00", "Z")
        service.ingest_event(_event(
            f"EVT-{episode_id}-OCC", session_id, "USER_PROMPT", occasion_time, {
                "occasion_signals": {
                    "prior_instantiation": "CONFIRMED",
                    "current_contact": "CONFIRMED",
                    "consequentiality": "CONFIRMED",
                },
                "decision_object_profile_id": profile_id,
                "occasion_id": f"OCC-20-{episode_id}",
                "focal_decision_id": f"FD-20-{episode_id}",
                "chain_id": chain_id,
                "claim_ids": [f"{episode_id}::CLAIM"],
                "evidence_ids": [],
            }, turn_id=f"{episode_id}::OCC"))
        rounds: list[dict[str, Any]] = []
        pending_exposure: dict[str, Any] | None = None
        turns = row["user_turns"]
        for index, turn in enumerate(turns, start=1):
            text = str(turn.get("text", ""))
            turn_id = f"{episode_id}::T{index:03d}"
            event_id = f"EVT-{episode_id}-R{index:03d}"
            observed_at = base_time + timedelta(minutes=index)
            observed = observed_at.isoformat().replace("+00:00", "Z")
            coding = _coding(text, index, len(turns))
            evidence_present = index % 3 != 1
            family = _family_for(text)
            evidence_id = f"{episode_id}::T{index:03d}::USER_TURN"
            evidence_ids = [evidence_id] if evidence_present else []
            hint = _hint(text, evidence_present=evidence_present, coding=coding)
            refs = [_evidence_ref(event_id, turn_id, evidence_id, family, coding, text)] if evidence_present else []
            assessability = "SUFFICIENT" if evidence_present else "LIMITED"
            updates = {
                dimension: {
                    "level": coding[dimension],
                    "assessability": assessability,
                    "evidence_ids": evidence_ids,
                }
                for dimension in _DIMENSIONS
            }
            service.ingest_event(_event(
                event_id,
                session_id,
                "USER_RESPONSE",
                observed,
                {
                    "chain_id": chain_id,
                    "response_kind": "OBSERVER_PROBE",
                    "observer_updates": updates,
                    "selector_hint": {**hint, "evidence_ids": evidence_ids},
                    "evidence_ids": evidence_ids,
                    "evidence_refs": refs,
                    "frozen_user_turn_id": turn.get("source_user_id"),
                },
                turn_id=turn_id,
            ))
            selection = service.select(chain_id, as_of_event_id=event_id, as_of_time=observed_at)
            result: dict[str, Any] = {
                "round": index,
                "user_turn_id": turn.get("source_user_id"),
                "text": text,
                "csa_coding": {dimension: coding[dimension] for dimension in _DIMENSIONS},
                "assessability": assessability,
                "evidence_ids": evidence_ids,
                "selector_hint": hint,
                "selection": {
                    key: selection.get(key)
                    for key in ("decision", "selected", "options", "choice_contract", "objective", "skyline_ids")
                    if key in selection
                },
            }
            if pending_exposure is not None:
                semantic = selection.get("objective", {}).get("semantic_constraints", {})
                result["new_user_event_after_exposure"] = {
                    "exposure_id": pending_exposure["exposure_id"],
                    "cooldown_released": not semantic.get("cooldown_active", False),
                    "cooldown_scope_after_event": semantic.get("cooldown_scope", "NONE"),
                }
                pending_exposure = None
            selected_for_exposure = None
            if selection["decision"] == "PRESENT_CHOICES":
                selected_for_exposure, condition, basis = _choose_option(selection, text)
                choice = service.record_choice(
                    chain_id,
                    selection_decision_id=selection["decision_id"],
                    selected_candidate_id=selected_for_exposure,
                    choice_condition=condition,
                    choice_basis=basis,
                    observed_at=observed_at + timedelta(seconds=2),
                )
                result["choice_branch"] = {
                    "selected_candidate_id": selected_for_exposure,
                    "choice_condition": condition,
                    "choice_basis": basis,
                    "choice_event_id": choice["event_id"],
                }
            if selection["decision"] in {"INTERVENE", "PRESENT_CHOICES"} and index < len(turns):
                exposure_time = observed_at + timedelta(seconds=5)
                exposure = service.expose(
                    chain_id,
                    exposure_id=f"EXP-{episode_id}-R{index:03d}",
                    selection_decision_id=selection["decision_id"],
                    selected_candidate_id=selected_for_exposure,
                    observed_at=exposure_time,
                )
                cooldown = service.select(chain_id, as_of_event_id=exposure["event_id"], as_of_time=exposure_time + timedelta(seconds=1))
                result["exposure"] = exposure
                result["cooldown_selection"] = cooldown
                pending_exposure = {"exposure_id": exposure["exposure_id"]}
            rounds.append(result)
        return {
            "episode_id": episode_id,
            "focal_target": row["focal_target"],
            "turn_count": len(turns),
            "selected_profile_id": profile_id,
            "rounds": rounds,
        }


def _write_report(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    lines = [
        "# 20 个冻结 Episode 的在线 Selector smoke replay",
        "",
        "> 20 个 episode 按 86 个 eligible episode 的序列位置均匀抽样。输入只使用冻结用户轮次；C/S/A、family hint 和 evidence refs 是透明的文本 pilot coding，不读取 posterior outcome annotation，因此本报告用于运行契约/参数几何检查，不用于干预效果结论。",
        "",
        f"- Episode：{summary['episode_count']}；轮次：{summary['round_count']}",
        f"- Episode IDs：{', '.join(payload['selected_episode_ids'])}",
        f"- 决策分布：{json.dumps(summary['decision_counts'], ensure_ascii=False)}",
        f"- family 分布：{json.dumps(summary['family_counts'], ensure_ascii=False)}",
        f"- NO_INTERVENTION 原因：{json.dumps(summary['no_intervention_reasons'], ensure_ascii=False)}",
        f"- Family gate：{json.dumps(summary['family_gate_modes'], ensure_ascii=False)}",
        f"- Exposure / cooldown / 新用户事件解除：{summary['exposure_count']} / {summary['cooldown_checkpoint_count']} / {summary['cooldown_release_count']}",
        f"- 双选项分叉数：{summary['choice_count']}",
        "",
        "## 每个 Episode",
        "",
        "| Episode | 目标 | 轮次 | 决策分布 |",
        "|---|---|---:|---|",
    ]
    for episode in payload["episodes"]:
        counts = Counter(item["selection"]["decision"] for item in episode["rounds"])
        lines.append(f"| {episode['episode_id']} | {episode['focal_target']} | {episode['turn_count']} | {json.dumps(dict(sorted(counts.items())), ensure_ascii=False)} |")
    lines.extend([
        "",
        "## 解释",
        "",
        "- 当前 20 个 episode 使用同一固定 target `(2,3,2)`，便于比较 Selector 的运行分布；这不是对每个真实 decision-object 的正式 target 校准。",
        "- 所有 evidence ID 都形如 `episode::turn::USER_TURN`，并绑定到当前轮次的 `EvidenceRef`；没有从 outcome annotation 复制 evidence。",
        "- exposure 后的下一轮用户事件用于检查 candidate-level cooldown 解除；双选项先记录 `record_choice`，再记录 exposure。",
    ])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    rows = [json.loads(line) for line in SEQUENCE_INPUT.open(encoding="utf-8")]
    selected_rows = _select_episodes(rows, 20)
    profiles = _profiles(selected_rows)
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    base_time = datetime(2026, 8, 26, tzinfo=timezone.utc)
    episodes = [_run_episode(row, profiles, registry, policy, base_time) for row in selected_rows]
    decisions = Counter(item["selection"]["decision"] for episode in episodes for item in episode["rounds"])
    families = Counter()
    reasons = Counter()
    gates = Counter()
    exposure_count = 0
    cooldown_count = 0
    cooldown_release_count = 0
    choice_count = 0
    evidence_turn_count = 0
    for episode in episodes:
        for item in episode["rounds"]:
            if item["evidence_ids"]:
                evidence_turn_count += 1
            selection = item["selection"]
            objective = selection.get("objective", {})
            if selection.get("decision") == "NO_INTERVENTION":
                reasons[objective.get("reason", "UNSPECIFIED")] += 1
            semantic = objective.get("semantic_constraints", {})
            if semantic.get("family_gate_mode"):
                gates[semantic["family_gate_mode"]] += 1
            if selection.get("decision") == "PRESENT_CHOICES":
                choice_count += 1
            for option in selection.get("options", []):
                families[option.get("strategy_family", "UNKNOWN")] += 1
            if "exposure" in item:
                exposure_count += 1
            if "cooldown_selection" in item:
                cooldown_count += 1
            if item.get("new_user_event_after_exposure", {}).get("cooldown_released"):
                cooldown_release_count += 1
    payload = {
        "schema_version": "retrace-real-episode-20-replay-v2",
        "selected_episode_ids": [row["final_episode_id"] for row in selected_rows],
        "selection_method": "20 evenly spaced eligible episodes from the frozen 86-episode sequence",
        "posterior_outcome_evidence_used": False,
        "pilot_coding_note": "C/S/A, family hints and evidence refs are transparent text-only pilot coding; target is fixed at (2,3,2) for comparability, not efficacy evaluation.",
        "summary": {
            "episode_count": len(episodes),
            "round_count": sum(len(episode["rounds"]) for episode in episodes),
            "decision_counts": dict(sorted(decisions.items())),
            "family_counts": dict(sorted(families.items())),
            "no_intervention_reasons": dict(sorted(reasons.items())),
            "family_gate_modes": dict(sorted(gates.items())),
            "exposure_count": exposure_count,
            "cooldown_checkpoint_count": cooldown_count,
            "cooldown_release_count": cooldown_release_count,
            "choice_count": choice_count,
            "evidence_turn_count": evidence_turn_count,
        },
        "episodes": episodes,
    }
    if payload["summary"]["episode_count"] != 20:
        raise SystemExit("did not replay exactly 20 episodes")
    if payload["summary"]["family_gate_modes"].get("HARD", 0) == 0 or payload["summary"]["family_gate_modes"].get("SOFT", 0) == 0:
        raise SystemExit("20-episode replay did not exercise both HARD and SOFT family gates")
    if exposure_count == 0 or cooldown_count != exposure_count or cooldown_release_count != exposure_count:
        raise SystemExit("20-episode replay did not complete exposure -> cooldown -> user-event release")
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_report(payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print("selected_episode_ids=" + ",".join(payload["selected_episode_ids"]))
    print(OUT_JSON)
    print(OUT_MD)


if __name__ == "__main__":
    main()
