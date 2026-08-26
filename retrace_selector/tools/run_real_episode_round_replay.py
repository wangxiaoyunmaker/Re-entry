"""Replay five frozen re-entry episodes round by round through the formal v2 selector.

This is a deterministic selector replay, not an efficacy evaluation. The frozen
episode text and evidence/proxy fields provide the trace; the C/S/A levels below
are explicit analyst coding for this pilot so that the online selector can be
exercised without pretending that the current 86-episode freeze contains a
gold-standard C/S/A annotation.
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
OUT_JSON = ROOT / "retrace_selector/artifacts/real_episode_round_replay_v2.json"
OUT_MD = ROOT / "retrace_selector/artifacts/real_episode_round_replay_v2.md"
REGISTRY_PATH = ROOT / "retrace_selector/config/strategy_registry.formal.v1.json"
POLICY_PATH = ROOT / "retrace_selector/config/selection_policy.formal.v1.json"
PROFILE_PATH = ROOT / "retrace_selector/config/decision_object_profiles.formal.v1.json"

EPISODE_IDS = ("CRE-0001", "CRE-0002", "CRE-0003", "CRE-0004", "CRE-0008")

PROFILE_BY_EPISODE = {
    "CRE-0001": "FD-REENTRY-CACHE",
    "CRE-0002": "FD-REENTRY-SLEEP-RULE",
    "CRE-0003": "FD-REENTRY-CONNECTIVITY",
    "CRE-0004": "FD-REENTRY-HOVER",
    "CRE-0008": "FD-REENTRY-EXCEL",
}

# Pilot coding rubric: 0 absent, 1 signal/request, 2 explicit dimension,
# 3 explicit dimension plus boundary, comparison, or verification condition.
# The notes are kept in the output so every numeric input is inspectable.
ROUND_CSA: dict[str, list[dict[str, Any]]] = {
    "CRE-0001": [
        {"criteria": 1, "state": 1, "action": 0, "basis": "首轮报告截图消失现象并询问原因"},
        {"criteria": 3, "state": 3, "action": 2, "basis": "用实际时间间隔反驳 TTL，并提出封面优先/新图覆盖"},
        {"criteria": 2, "state": 1, "action": 2, "basis": "提出稳定性标准并要求整理代码"},
        {"criteria": 3, "state": 3, "action": 3, "basis": "观察到整组件闪烁，明确只更新关键内容的边界"},
        {"criteria": 2, "state": 1, "action": 2, "basis": "要求回返验证修改后能否正确加载截图"},
    ],
    "CRE-0002": [
        {"criteria": 1, "state": 1, "action": 1, "basis": "报告历史数据仍未恢复"},
        {"criteria": 3, "state": 3, "action": 2, "basis": "给出 2 小时阈值并重建自动填充导致异常的因果链"},
        {"criteria": 3, "state": 2, "action": 3, "basis": "区分入睡/起床事件与睡眠时长，规定阈值和异常提示"},
        {"criteria": 3, "state": 3, "action": 3, "basis": "给出分支填充规则、提示持久性和等值默认规则"},
        {"criteria": 3, "state": 3, "action": 3, "basis": "新增 30 分钟边界并要求全量检查、对齐和修正"},
    ],
    "CRE-0003": [
        {"criteria": 0, "state": 1, "action": 1, "basis": "仅询问连通性异常"},
        {"criteria": 1, "state": 1, "action": 1, "basis": "质疑连通声明并提出模型选择需求"},
        {"criteria": 1, "state": 1, "action": 2, "basis": "要求核查连通性测试是否有 bug"},
        {"criteria": 2, "state": 3, "action": 1, "basis": "指出不存在模型却可选且显示正常，形成直接反证"},
    ],
    "CRE-0004": [
        {"criteria": 1, "state": 0, "action": 2, "basis": "要求打开调试以观察三角区"},
        {"criteria": 1, "state": 1, "action": 2, "basis": "指定插件打开、浏览器实测的验证动作"},
        {"criteria": 2, "state": 3, "action": 1, "basis": "实测发现三角区被下一行 hover 打断"},
        {"criteria": 3, "state": 3, "action": 3, "basis": "明确关闭调试、移除过渡、保留其他动画"},
    ],
    "CRE-0008": [
        {"criteria": 3, "state": 2, "action": 2, "basis": "指定文件/sheet 和 4 个枚举值，要求重新读取"},
        {"criteria": 1, "state": 1, "action": 2, "basis": "重复提出重新读取"},
        {"criteria": 3, "state": 3, "action": 2, "basis": "在 Excel 中复核四值并推断读取了旧表，要求按路径确认"},
    ],
}


def _hint(
    family: str,
    max_intensity: int,
    *,
    allowed: list[str] | None = None,
    cognitive_gap_detected: bool = True,
    execution_request_detected: bool = False,
) -> dict[str, Any]:
    allowed_families = allowed or [family]
    return {
        "support_family": family,
        "allowed_families": allowed_families,
        "confidence": "HIGH",
        "max_intensity": max_intensity,
        "cognitive_gap_detected": cognitive_gap_detected,
        "execution_request_detected": execution_request_detected,
        "reason": "pilot semantic family and intensity constraint from frozen turn",
    }


ROUND_HINTS: dict[str, list[dict[str, Any]]] = {
    "CRE-0001": [
        _hint("STATE_CONTEXT_RECOVERY", 1),
        _hint("CLAIM_EVIDENCE_CALIBRATION", 2),
        _hint("GOVERNANCE_ACTION_PLANNING", 1),
        _hint("CLAIM_EVIDENCE_CALIBRATION", 2, execution_request_detected=True),
        _hint("CLAIM_EVIDENCE_CALIBRATION", 2),
    ],
    "CRE-0002": [
        _hint("STATE_CONTEXT_RECOVERY", 1),
        _hint("RULE_CLARIFICATION", 2),
        _hint("RULE_CLARIFICATION", 2),
        _hint("RULE_CLARIFICATION", 3, execution_request_detected=True),
        _hint("RULE_CLARIFICATION", 3, execution_request_detected=True),
    ],
    "CRE-0003": [
        _hint("CLAIM_EVIDENCE_CALIBRATION", 1),
        _hint("CLAIM_EVIDENCE_CALIBRATION", 1),
        _hint("CLAIM_EVIDENCE_CALIBRATION", 2),
        _hint("CLAIM_EVIDENCE_CALIBRATION", 2),
    ],
    "CRE-0004": [
        _hint("STATE_CONTEXT_RECOVERY", 1),
        _hint("STATE_CONTEXT_RECOVERY", 1),
        _hint("CLAIM_EVIDENCE_CALIBRATION", 2),
        _hint("GOVERNANCE_ACTION_PLANNING", 3, execution_request_detected=True),
    ],
    "CRE-0008": [
        _hint("CLAIM_EVIDENCE_CALIBRATION", 2, allowed=["CLAIM_EVIDENCE_CALIBRATION", "GOVERNANCE_ACTION_PLANNING"]),
        _hint("CLAIM_EVIDENCE_CALIBRATION", 1),
        _hint("CLAIM_EVIDENCE_CALIBRATION", 2),
    ],
}

# Evidence is bound only to the current frozen user turn. These flags are
# explicit replay inputs, not copied from the posterior governance outcome
# annotation. A false entry intentionally exercises LIMITED/insufficient
# evidence behavior.
ROUND_EVIDENCE_PRESENT: dict[str, tuple[bool, ...]] = {
    "CRE-0001": (False, True, False, True, True),
    "CRE-0002": (False, True, True, True, True),
    "CRE-0003": (False, False, True, True),
    "CRE-0004": (True, False, True, True),
    "CRE-0008": (True, False, True),
}


def _semantic_role(text: str) -> str:
    if any(token in text for token in ("确定", "核查", "确认", "真的", "bug")):
        return "CLAIM_VERIFICATION"
    if any(token in text for token in ("阈值", "超过", "规则", "条件", "默认")):
        return "RULE_STATEMENT"
    if any(token in text for token in ("改", "要求", "保留", "只更新", "整理")):
        return "USER_BOUNDARY"
    return "USER_OBSERVATION"


def _choose_presented_option(selection: dict[str, Any], text: str) -> tuple[str, str, str]:
    """Deterministic pilot branch; production requires an actual user choice event."""
    options = list(selection.get("selected", []))
    if len(options) != 2:
        raise SystemExit("PRESENT_CHOICES must contain exactly two candidate IDs")
    option_specs = {
        item.get("strategy_id"): item
        for item in selection.get("options", [])
        if isinstance(item, dict)
    }
    preferred_family = None
    if any(token in text for token in ("阈值", "规则", "条件", "超过", "默认")):
        preferred_family = "RULE_CLARIFICATION"
    elif any(token in text for token in ("确认", "核查", "确定", "验证", "真的")):
        preferred_family = "CLAIM_EVIDENCE_CALIBRATION"
    elif any(token in text for token in ("状态", "历史", "恢复", "为什么")):
        preferred_family = "STATE_CONTEXT_RECOVERY"
    chosen = next(
        (candidate_id for candidate_id in options if option_specs.get(candidate_id, {}).get("strategy_family") == preferred_family),
        options[0],
    )
    condition = option_specs.get(chosen, {}).get("branch_condition_code", "DEFAULT_FIRST_OPTION")
    return chosen, condition, f"pilot branch rule selected {chosen} from {options}"


def _load_rows() -> dict[str, dict[str, Any]]:
    rows = {}
    with SEQUENCE_INPUT.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["final_episode_id"] in EPISODE_IDS:
                rows[row["final_episode_id"]] = row
    missing = [episode_id for episode_id in EPISODE_IDS if episode_id not in rows]
    if missing:
        raise SystemExit(f"missing frozen sequence rows: {missing}")
    return rows


def _load_profiles() -> dict[str, dict[str, Any]]:
    payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise SystemExit("formal decision-object profile config is empty")
    return profiles


def _load_outcomes() -> dict[str, dict[str, Any]]:
    path = Path(
        "/Users/wy/Desktop/HCI-过程性归档-20260825/reentry_freeze_20260823/"
        "annotations/governance_reentry_outcome_v12/"
        "api_preannotation_full_86_20260825_rederived.jsonl"
    )
    outcomes = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["episode_id"] in EPISODE_IDS:
                annotation = row.get("annotation", {})
                outcomes[row["episode_id"]] = {
                    key: annotation.get(key)
                    for key in (
                        "decision_object", "demonstrated_governance_recovery",
                        "boundary_governed_recovery", "functional_outcome",
                    )
                }
    return outcomes


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
        "project_id": "REENTRY-FORMAL-PILOT",
        "observed_at": observed_at,
        "received_at": observed_at,
        "source": "CODEX_HOOK",
        "turn_id": turn_id,
        "payload": payload,
    }


def _run_episode(row: dict[str, Any], outcome: dict[str, Any], profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    episode_id = row["final_episode_id"]
    session_id = f"SESSION-{episode_id}"
    chain_id = f"CHAIN-{episode_id}"
    profile_id = PROFILE_BY_EPISODE[episode_id]
    base_time = datetime(2026, 8, 25, tzinfo=timezone.utc)
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix=f"retrace-{episode_id}-") as directory:
        service = OnlineInferenceService(
            database_path=Path(directory) / "online.sqlite3",
            profiles=profiles,
            registry=registry,
            config=policy,
        )
        occasion_time = base_time.isoformat().replace("+00:00", "Z")
        occasion = service.ingest_event(
            _event(
                f"EVT-{episode_id}-OCC",
                session_id,
                "USER_PROMPT",
                occasion_time,
                {
                    "occasion_signals": {
                        "prior_instantiation": "CONFIRMED",
                        "current_contact": "CONFIRMED",
                        "consequentiality": "CONFIRMED",
                    },
                    "decision_object_profile_id": profile_id,
                    "occasion_id": f"OCC-{episode_id}",
                    "focal_decision_id": f"FD-{episode_id}",
                    "chain_id": chain_id,
                    "claim_ids": [f"{episode_id}::CLAIM"],
                    "evidence_ids": [],
                },
            )
        )
        if occasion["occasion"] != "OCCASION_CONFIRMED":
            raise SystemExit(f"occasion did not confirm for {episode_id}: {occasion}")

        rounds = []
        csa_rows = ROUND_CSA[episode_id]
        hint_rows = ROUND_HINTS[episode_id]
        evidence_flags = ROUND_EVIDENCE_PRESENT[episode_id]
        user_turns = row["user_turns"]
        if len(csa_rows) != len(user_turns) or len(hint_rows) != len(user_turns) or len(evidence_flags) != len(user_turns):
            raise SystemExit(f"C/S/A coding length mismatch for {episode_id}")
        pending_exposure: dict[str, Any] | None = None
        for index, (user_turn, coded, selector_hint) in enumerate(zip(user_turns, csa_rows, hint_rows), start=1):
            turn_id = f"{episode_id}::T{index:03d}"
            observed_at = base_time + timedelta(minutes=index)
            observed = observed_at.isoformat().replace("+00:00", "Z")
            evidence_id = f"{episode_id}::T{index:03d}::USER_TURN"
            round_evidence_ids = [evidence_id] if evidence_flags[index - 1] else []
            selector_hint = {**selector_hint, "evidence_ids": round_evidence_ids}
            assessability = "SUFFICIENT" if round_evidence_ids else "LIMITED"
            evidence_refs = [
                {
                    "evidence_id": evidence_id,
                    "source_event_id": f"EVT-{episode_id}-R{index:03d}",
                    "source": "CURRENT_USER_TURN",
                    "semantic_role": _semantic_role(user_turn["text"]),
                    "supports_families": selector_hint["allowed_families"],
                    "supports_dimensions": [
                        dimension
                        for dimension in ("criteria", "state", "action")
                        if coded[dimension] > 0
                    ],
                    "source_turn_id": user_turn["source_user_id"],
                }
            ] if round_evidence_ids else []
            updates = {
                dimension: {
                    "level": coded[dimension],
                    "assessability": assessability,
                    "evidence_ids": round_evidence_ids,
                }
                for dimension in ("criteria", "state", "action")
            }
            service.ingest_event(
                _event(
                    f"EVT-{episode_id}-R{index:03d}",
                    session_id,
                    "USER_RESPONSE",
                    observed,
                    {
                        "chain_id": chain_id,
                        "response_kind": "OBSERVER_PROBE",
                        "observer_updates": updates,
                        "selector_hint": selector_hint,
                        "evidence_ids": round_evidence_ids,
                        "evidence_refs": evidence_refs,
                        "frozen_user_turn_id": user_turn["source_user_id"],
                    },
                    turn_id=turn_id,
                )
            )
            selection = service.select(
                chain_id,
                as_of_event_id=f"EVT-{episode_id}-R{index:03d}",
                as_of_time=datetime.fromisoformat(observed.replace("Z", "+00:00")),
            )
            round_result = {
                "round": index,
                "user_turn_id": user_turn["source_user_id"],
                "text": user_turn["text"],
                "csa_coding": {
                    key: coded[key] for key in ("criteria", "state", "action")
                },
                "assessability": assessability,
                "evidence_ids": round_evidence_ids,
                "coding_basis": coded["basis"],
                "selector_hint": selector_hint,
                "selection": {
                    key: selection.get(key)
                    for key in (
                        "decision", "selected", "options", "current_state",
                        "target_state", "objective", "skyline_ids",
                    )
                    if key in selection
                },
            }
            if pending_exposure is not None:
                semantic = selection.get("objective", {}).get("semantic_constraints", {})
                round_result["new_user_event_after_exposure"] = {
                    "event_id": f"EVT-{episode_id}-R{index:03d}",
                    "exposure_id": pending_exposure["exposure_id"],
                    "cooldown_released": not semantic.get("cooldown_active", False),
                    "cooldown_scope_after_event": semantic.get("cooldown_scope", "NONE"),
                }
                pending_exposure = None
            selected_candidate_for_exposure = None
            if selection["decision"] == "PRESENT_CHOICES":
                selected_candidate_for_exposure, choice_condition, choice_basis = _choose_presented_option(selection, user_turn["text"])
                choice = service.record_choice(
                    chain_id,
                    selection_decision_id=selection["decision_id"],
                    selected_candidate_id=selected_candidate_for_exposure,
                    choice_condition=choice_condition,
                    choice_basis=choice_basis,
                    observed_at=observed_at + timedelta(seconds=2),
                )
                round_result["choice_branch"] = {
                    "choice_id": choice["choice_id"],
                    "event_id": choice["event_id"],
                    "selection_decision_id": choice["selection_decision_id"],
                    "selected_candidate_id": choice["selected_candidate_id"],
                    "condition": choice_condition,
                    "basis": choice_basis,
                }
            should_expose = selection["decision"] in {"INTERVENE", "PRESENT_CHOICES"} and index < len(user_turns)
            if should_expose:
                exposure_id = f"EXP-{episode_id}-R{index:03d}"
                exposure_time = observed_at + timedelta(seconds=5)
                exposure = service.expose(
                    chain_id,
                    exposure_id=exposure_id,
                    selection_decision_id=selection["decision_id"],
                    selected_candidate_id=selected_candidate_for_exposure,
                    observed_at=exposure_time,
                )
                cooldown_selection = service.select(
                    chain_id,
                    as_of_event_id=exposure["event_id"],
                    as_of_time=exposure_time + timedelta(seconds=1),
                )
                round_result["exposure"] = {
                    key: exposure.get(key)
                    for key in ("exposure_id", "event_id", "selection_decision_id", "selected_candidate_id", "choice_event_id", "pre_snapshot_id")
                    if key in exposure
                }
                round_result["cooldown_selection"] = {
                    key: cooldown_selection.get(key)
                    for key in ("decision", "selected", "objective", "current_state")
                    if key in cooldown_selection
                }
                pending_exposure = {
                    "exposure_id": exposure_id,
                    "event_id": exposure["event_id"],
                }
            rounds.append(round_result)
        return {
            "episode_id": episode_id,
            "decision_object_profile_id": profile_id,
            "target_state": profiles[profile_id]["target_state"],
            "focal_target": row["focal_target"],
            "activity_types": row["activity_types"],
            "source_layer": row["source_layers"],
            "outcome_linkage": outcome,
            "rounds": rounds,
        }


def _edge_evidence(event_id: str, evidence_id: str, *, family: str = "STATE_CONTEXT_RECOVERY") -> list[dict[str, Any]]:
    return [{
        "evidence_id": evidence_id,
        "source_event_id": event_id,
        "source": "CURRENT_USER_TURN",
        "semantic_role": "USER_OBSERVATION",
        "supports_families": [family],
        "supports_dimensions": ["criteria", "state", "action"],
        "source_turn_id": event_id,
    }]


def _run_edge_cases(profiles: dict[str, dict[str, Any]], registry: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Run explicit negative/timeout cases without using outcome annotations."""
    base = datetime(2026, 8, 26, tzinfo=timezone.utc)
    results: list[dict[str, Any]] = []

    def occasion(service: OnlineInferenceService, case_id: str, session_id: str, chain_id: str, when: datetime) -> None:
        result = service.ingest_event(_event(
            f"EVT-{case_id}-OCC", session_id, "USER_PROMPT", when.isoformat().replace("+00:00", "Z"), {
                "occasion_signals": {
                    "prior_instantiation": "CONFIRMED",
                    "current_contact": "CONFIRMED",
                    "consequentiality": "CONFIRMED",
                },
                "decision_object_profile_id": "FD-REENTRY-CACHE",
                "occasion_id": f"OCC-{case_id}",
                "focal_decision_id": f"FD-{case_id}",
                "chain_id": chain_id,
                "claim_ids": [f"{case_id}::CLAIM"],
                "evidence_ids": [],
            }, turn_id=f"{case_id}::OCC"))
        if result["occasion"] != "OCCASION_CONFIRMED":
            raise SystemExit(f"edge occasion did not confirm for {case_id}")

    def add_state(
        service: OnlineInferenceService,
        case_id: str,
        session_id: str,
        chain_id: str,
        when: datetime,
        *,
        assessability: str,
        cognitive_gap: bool = True,
        evidence: bool = False,
    ) -> None:
        event_id = f"EVT-{case_id}-STATE"
        evidence_id = f"{case_id}::T001::USER_TURN"
        evidence_ids = [evidence_id] if evidence else []
        refs = _edge_evidence(event_id, evidence_id) if evidence else []
        service.ingest_event(_event(
            event_id, session_id, "USER_RESPONSE", when.isoformat().replace("+00:00", "Z"), {
                "chain_id": chain_id,
                "response_kind": "OBSERVER_PROBE",
                "observer_updates": {
                    dimension: {"level": 1, "assessability": assessability, "evidence_ids": evidence_ids}
                    for dimension in ("criteria", "state", "action")
                },
                "selector_hint": {
                    "support_family": "STATE_CONTEXT_RECOVERY",
                    "allowed_families": ["STATE_CONTEXT_RECOVERY"],
                    "confidence": "HIGH" if evidence else "LOW",
                    "max_intensity": 1,
                    "cognitive_gap_detected": cognitive_gap,
                    "execution_request_detected": False,
                    "evidence_ids": evidence_ids,
                },
                "evidence_ids": evidence_ids,
                "evidence_refs": refs,
            }, turn_id=f"{case_id}::T001"))

    with tempfile.TemporaryDirectory(prefix="retrace-edge-cases-") as directory:
        unknown_service = OnlineInferenceService(database_path=Path(directory) / "unknown.sqlite3", profiles=profiles, registry=registry, config=policy)
        unknown_chain = "CHAIN-EDGE-UNKNOWN"
        occasion(unknown_service, "EDGE-UNKNOWN", "SESSION-EDGE-UNKNOWN", unknown_chain, base)
        unknown = unknown_service.select(unknown_chain, as_of_time=base)
        results.append({"scenario": "UNKNOWN", "selection": unknown})

        insufficient_policy = {**policy, "evidence_floor_when_limited": 1.0}
        insufficient_service = OnlineInferenceService(database_path=Path(directory) / "insufficient.sqlite3", profiles=profiles, registry=registry, config=insufficient_policy)
        insufficient_chain = "CHAIN-EDGE-INSUFFICIENT"
        occasion(insufficient_service, "EDGE-INSUFFICIENT", "SESSION-EDGE-INSUFFICIENT", insufficient_chain, base)
        add_state(insufficient_service, "EDGE-INSUFFICIENT", "SESSION-EDGE-INSUFFICIENT", insufficient_chain, base + timedelta(seconds=1), assessability="LIMITED")
        insufficient = insufficient_service.select(insufficient_chain, as_of_time=base + timedelta(seconds=1))
        results.append({"scenario": "INSUFFICIENT_EVIDENCE", "selection": insufficient})

        gap_service = OnlineInferenceService(database_path=Path(directory) / "no-gap.sqlite3", profiles=profiles, registry=registry, config=policy)
        gap_chain = "CHAIN-EDGE-NO-GAP"
        occasion(gap_service, "EDGE-NO-GAP", "SESSION-EDGE-NO-GAP", gap_chain, base)
        add_state(gap_service, "EDGE-NO-GAP", "SESSION-EDGE-NO-GAP", gap_chain, base + timedelta(seconds=1), assessability="SUFFICIENT", cognitive_gap=False, evidence=True)
        no_gap = gap_service.select(gap_chain, as_of_time=base + timedelta(seconds=1))
        results.append({"scenario": "NO_COGNITIVE_GAP", "selection": no_gap})

        timeout_service = OnlineInferenceService(database_path=Path(directory) / "timeout.sqlite3", profiles=profiles, registry=registry, config=policy)
        timeout_chain = "CHAIN-EDGE-TIMEOUT"
        occasion(timeout_service, "EDGE-TIMEOUT", "SESSION-EDGE-TIMEOUT", timeout_chain, base)
        add_state(timeout_service, "EDGE-TIMEOUT", "SESSION-EDGE-TIMEOUT", timeout_chain, base + timedelta(seconds=1), assessability="SUFFICIENT", evidence=True)
        initial = timeout_service.select(timeout_chain, as_of_time=base + timedelta(seconds=1))
        if initial["decision"] == "PRESENT_CHOICES":
            chosen, condition, basis = _choose_presented_option(initial, "请确认这个结果")
            timeout_service.record_choice(timeout_chain, selection_decision_id=initial["decision_id"], selected_candidate_id=chosen, choice_condition=condition, choice_basis=basis, observed_at=base + timedelta(seconds=2))
            selected_for_exposure = chosen
        else:
            selected_for_exposure = None
        if initial["decision"] not in {"INTERVENE", "PRESENT_CHOICES"}:
            raise SystemExit("long-no-response edge case did not produce an intervention to expose")
        exposure_time = base + timedelta(seconds=5)
        timeout_service.expose(timeout_chain, exposure_id="EXP-EDGE-TIMEOUT", selection_decision_id=initial["decision_id"], selected_candidate_id=selected_for_exposure, observed_at=exposure_time)
        timeout_selection = timeout_service.select(timeout_chain, as_of_time=exposure_time + timedelta(seconds=policy["long_no_response_seconds"] + 1))
        results.append({"scenario": "LONG_NO_RESPONSE", "initial_selection": initial, "selection": timeout_selection})
    return results


def _write_report(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    lines = [
        "# Formal Registry + Semantic Gate/Cooldown：5 个真实 Episode 逐轮回放",
        "",
        "> 输入是 86-episode 冻结序列中的用户轮次；Selector 参数来自正式 strategy_registry.formal.v1.json 和 selection_policy.formal.v1.json。C/S/A 数值与 evidence refs 是仅基于当前冻结用户轮次和研究者 rationale 的显式 pilot coding，不读取后验 outcome annotation 的 evidence 字段；因此本报告用于检查运行效果和输出可读性，不用于宣称干预效果。",
        "",
        f"- Episode：{summary['episode_count']}；轮次：{summary['round_count']}",
        f"- 决策分布：{json.dumps(summary['decision_counts'], ensure_ascii=False)}",
        f"- 选中候选族：{json.dumps(summary['family_counts'], ensure_ascii=False)}",
        f"- NO_INTERVENTION 原因：{json.dumps(summary['no_intervention_reasons'], ensure_ascii=False)}",
        f"- Family gate 模式：{json.dumps(summary['family_gate_modes'], ensure_ascii=False)}",
        f"- 模拟 exposure：{summary['exposure_count']}；cooldown checkpoint：{summary['cooldown_checkpoint_count']}；新用户事件解除：{summary['cooldown_release_count']}",
        f"- Cooldown checkpoint 原因：{json.dumps(summary['cooldown_checkpoint_reasons'], ensure_ascii=False)}",
        f"- 边界场景：{json.dumps(summary['edge_case_decisions'], ensure_ascii=False)}",
        "",
        "## Episode 选择",
        "",
        "| Episode | 目标 | 轮次 | 冻结 outcome（当前可链接字段） |",
        "|---|---|---:|---|",
    ]
    for episode in payload["episodes"]:
        outcome = episode["outcome_linkage"]
        lines.append(
            f"| {episode['episode_id']} | {episode['focal_target']} | {len(episode['rounds'])} | "
            f"DGR={outcome.get('demonstrated_governance_recovery')}; "
            f"BGR={outcome.get('boundary_governed_recovery')}; "
            f"functional={outcome.get('functional_outcome')} |"
        )
    lines.extend(["", "## 逐轮结果", ""])
    for episode in payload["episodes"]:
        lines.extend([f"### {episode['episode_id']} · {episode['focal_target']}", ""])
        lines.extend([
            "| 轮次 | 用户轮次 | C/S/A | Selector | 候选 | loss | reason | 编码依据 |",
            "|---:|---|---|---|---|---:|---|---|",
        ])
        for item in episode["rounds"]:
            sel = item["selection"]
            options = sel.get("options", [])
            selected = sel.get("selected", [])
            candidates = ", ".join(
                str(option.get("strategy_id")) for option in options
            ) or ", ".join(selected) or "—"
            loss = sel.get("objective", {}).get("loss", "—")
            reason = sel.get("objective", {}).get("reason", "—")
            csa = item["csa_coding"]
            short_text = item["text"].replace("\n", " ")[:42]
            lines.append(
                f"| {item['round']} | {item['user_turn_id']} {short_text} | "
                f"{csa['criteria']}/{csa['state']}/{csa['action']} | {sel.get('decision')} | "
                f"{candidates} | {loss} | {reason} | {item['coding_basis']} |"
            )
        lines.append("")
    lines.extend([
        "## 解释",
        "",
        "- NO_INTERVENTION 现在通过 reason 区分 TARGET_REACHED、BELOW_ETA、INTERVENTION_NOT_ELIGIBLE、INSUFFICIENT_EVIDENCE、UNKNOWN_STATE、NO_ELIGIBLE_CANDIDATE、COOLDOWN_ACTIVE 和 NO_RESPONSE_TIMEOUT；它不是简单的任务完成标签。",
        "- 本轮会对有后续用户轮次的 INTERVENE/PRESENT_CHOICES 模拟真实 expose，并在同一时序中记录 cooldown checkpoint；下一轮冻结用户事件用于验证候选级 cooldown 是否解除。",
        "- 冻结 outcome 当前只作为治理结果的关联字段，functional_outcome 仍多为 UNASSESSABLE；回放不读取 outcome annotation 的 evidence，也不把 Selector 结果与功能成败混为一谈。",
        "",
    ])
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    rows = _load_rows()
    profiles = _load_profiles()
    outcomes = _load_outcomes()
    registry_payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    policy_payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    episodes = [
        _run_episode(rows[episode_id], outcomes.get(episode_id, {}), profiles)
        for episode_id in EPISODE_IDS
    ]
    edge_cases = _run_edge_cases(profiles, registry_payload, policy_payload)
    decision_counts = Counter(
        round_item["selection"]["decision"]
        for episode in episodes
        for round_item in episode["rounds"]
    )
    family_counts = Counter()
    no_intervention_reasons = Counter()
    family_gate_modes = Counter()
    cooldown_reasons = Counter()
    exposure_count = 0
    cooldown_checkpoint_count = 0
    cooldown_release_count = 0
    for episode in episodes:
        for round_item in episode["rounds"]:
            objective = round_item["selection"].get("objective", {})
            if round_item["selection"].get("decision") == "NO_INTERVENTION":
                no_intervention_reasons[objective.get("reason", "UNSPECIFIED")] += 1
            mode = objective.get("semantic_constraints", {}).get("family_gate_mode")
            if mode:
                family_gate_modes[mode] += 1
            if "exposure" in round_item:
                exposure_count += 1
            if "cooldown_selection" in round_item:
                cooldown_checkpoint_count += 1
                cooldown_objective = round_item["cooldown_selection"].get("objective", {})
                cooldown_reasons[cooldown_objective.get("reason", "UNSPECIFIED")] += 1
            if round_item.get("new_user_event_after_exposure", {}).get("cooldown_released"):
                cooldown_release_count += 1
            for option in round_item["selection"].get("options", []):
                family_counts[option.get("strategy_family", "UNKNOWN")] += 1
    payload = {
        "schema_version": "retrace-real-episode-round-replay-v2",
        "selector_registry": str(REGISTRY_PATH),
        "selector_policy": str(POLICY_PATH),
        "decision_object_profiles": profiles,
        "profile_by_episode": PROFILE_BY_EPISODE,
        "edge_cases": edge_cases,
        "pilot_coding_note": "C/S/A is explicit analyst coding from frozen user-turn text and evidence/proxy fields; not gold-standard annotation.",
        "summary": {
            "episode_count": len(episodes),
            "round_count": sum(len(episode["rounds"]) for episode in episodes),
            "decision_counts": dict(sorted(decision_counts.items())),
            "family_counts": dict(sorted(family_counts.items())),
            "no_intervention_reasons": dict(sorted(no_intervention_reasons.items())),
            "family_gate_modes": dict(sorted(family_gate_modes.items())),
            "exposure_count": exposure_count,
            "cooldown_checkpoint_count": cooldown_checkpoint_count,
            "cooldown_checkpoint_reasons": dict(sorted(cooldown_reasons.items())),
            "cooldown_release_count": cooldown_release_count,
            "edge_case_decisions": {
                item["scenario"]: item.get("selection", {}).get("objective", {}).get("reason")
                for item in edge_cases
            },
        },
        "episodes": episodes,
    }
    summary = payload["summary"]
    if summary["family_gate_modes"].get("HARD", 0) == 0:
        raise SystemExit("replay did not exercise HIGH+valid-evidence HARD family gate")
    if summary["exposure_count"] == 0 or summary["cooldown_checkpoint_count"] != summary["exposure_count"]:
        raise SystemExit("replay did not complete expose -> cooldown checkpoints")
    if summary["cooldown_release_count"] != summary["exposure_count"]:
        raise SystemExit("replay did not observe a new user event after every simulated exposure")
    expected_edge_reasons = {
        "UNKNOWN": "UNKNOWN_STATE",
        "INSUFFICIENT_EVIDENCE": "INSUFFICIENT_EVIDENCE",
        "NO_COGNITIVE_GAP": "INTERVENTION_NOT_ELIGIBLE",
        "LONG_NO_RESPONSE": "NO_RESPONSE_TIMEOUT",
    }
    if summary["edge_case_decisions"] != expected_edge_reasons:
        raise SystemExit(f"edge-case replay reasons mismatch: {summary['edge_case_decisions']}")
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_report(payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(OUT_JSON)
    print(OUT_MD)


if __name__ == "__main__":
    main()
