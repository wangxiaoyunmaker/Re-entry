"""Manual evidence-first recheck for one representative node per pilot episode.

This is a development adjudication artifact, not an automatic annotator. The
ten packets below were written after reading the selected user events and
their local trace context. It compares the old lexical projection with the
two-stage Support Profile and runs a support_need ablation through Skyline.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from retrace_selector.config import load_policy, load_templates
from retrace_selector.selector import SelectionEngine
from retrace_selector.support_profile import aggregate_support_profile
from retrace_selector.v03 import select_v03

from run_node_decision_audit import _episode_coverage, _episode_risk, _row_map


ROOT = Path(__file__).resolve().parents[2]
OLD_LOG = ROOT / "outputs/retrace_node_decision_audit_20260822/node_decision_logs.json"
MAPPING = ROOT / "outputs/reentry_framework_validation_20260822/02_episode_dimension_mapping_provisional.csv"
OUT = ROOT / "outputs/retrace_support_profile_recheck_20260822"


def ev(evidence_id: str, text: str, *, acts: list[str], intents: list[str], targets: list[str], inputs: list[str], validation: list[str] = ["VS00"], temporal: str = "BEFORE_OR_AT_TRIGGER", behavior_change: str = "NOT_APPLICABLE", behavior_change_basis: str = "") -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "actor": "USER",
        "text_span": text,
        "dialogue_act": acts,
        "task_intent": intents,
        "target_object": targets,
        "input_type": inputs,
        "validation_strategy": validation,
        "temporal_position": temporal,
        "source": "OBSERVED",
        "behavior_change_from_prior": behavior_change,
        "behavior_change_basis": behavior_change_basis,
    }


def assessment(status: str, formation: list[str], use: list[str], need: str, need_ids: list[str], confidence: str, rationale: str, need_rationale: str | None = None) -> dict[str, Any]:
    overlap = sorted(set(need_ids) & set(formation + use))
    return {
        "basis_status": status,
        "formation_evidence_ids": formation,
        "use_evidence_ids": use,
        "support_need": need,
        "need_evidence_ids": need_ids,
        "confidence": confidence,
        "rationale": rationale,
        "need_rationale": need_rationale or (
            f"同一事件同时提供了依据形成和支持需求证据：{rationale}"
            if overlap else f"支持需求证据说明：{rationale}"
        ),
    }


def packet_10() -> list[dict[str, Any]]:
    return [
        {
            "episode_id": "HRE-0030", "event_id": "R1461",
            "events": [
                ev("R1461", "发现一个问题，如果起床or睡眠事件的起始时间和结束时间相差超过2个小时，可判断为异常数据；因为我想起来之前起床or睡眠可能只有起始时间or结束时间so去回看数据，发现数据自动把上一thing的结束时间自动存入为空的起始时间or把当前时间自动填入为空的结束时间而导致了异常数据", acts=["IT-F", "AD-K", "AD-R"], intents=["CODE.REPAIR", "STRATEGY.ALIGN"], targets=["TO05", "TO08"], inputs=["IN01", "IN05", "IN06"]),
                ev("R1468", "1.去除编辑时的自动填充默认时间，这个改动是for入睡和起床事件，还是所有事件？2.睡眠异常的检测，不是对睡眠时长检测，而是对入睡or起床这两个事件检测，如果这两个事件的时长超过2h，应判断为异常，并显示异常提醒for提示我去check修正数据", acts=["AD-K", "AD-R"], intents=["STRATEGY.ALIGN", "CODE.REPAIR"], targets=["TO05", "TO08"], inputs=["IN06"], temporal="AFTER_TRIGGER", behavior_change="CHANGED", behavior_change_basis="用户把原先的异常判断进一步限定为入睡/起床事件，并明确自动填充的适用范围。"),
            ],
            "assessments": {
                "criteria_basis_reconstruction": assessment("USED", ["R1461"], ["R1468"], "MEDIUM", ["R1461"], "HIGH", "用户提出两小时异常标准，并在后续明确该标准适用于入睡/起床事件而不是笼统的睡眠时长。"),
                "project_state_reconstruction": assessment("USED", ["R1461"], ["R1468"], "HIGH", ["R1461"], "HIGH", "用户指出默认时间填充改变了历史状态，并据此要求检查数据和调整实现范围。"),
                "evidence_action_governance": assessment("USED", ["R1461"], ["R1468"], "MEDIUM", ["R1461"], "HIGH", "用户指定先排查、再调整，并给出异常检测和提示的行动边界。"),
            },
        },
        {
            "episode_id": "HRE-0044", "event_id": "R36",
            "events": [ev("R36", "你是认真的吗，反重力里面根本没有这个模型吧，为啥还能选择，且连通性正常？", acts=["IT-C", "IT-Q"], intents=["STRATEGY.REVIEW", "CODE.EXPLAIN"], targets=["TO06"], inputs=["IN01", "IN05"])],
            "assessments": {
                "criteria_basis_reconstruction": assessment("POSSIBLE", [], [], "MEDIUM", ["R36"], "MEDIUM", "用户质疑模型选择与连通性结果是否可信，但没有完整陈述验收标准。"),
                "project_state_reconstruction": assessment("FORMED", ["R36"], [], "HIGH", ["R36"], "HIGH", "用户明确指出当前模型列表与实际 Agent 状态不一致，并要求解释这一状态矛盾。"),
                "evidence_action_governance": assessment("POSSIBLE", [], [], "MEDIUM", ["R36"], "MEDIUM", "用户要求核查连通性，但尚未规定验证步骤或结束条件。"),
            },
        },
        {
            "episode_id": "HRE-0073", "event_id": "R122",
            "events": [ev("R122", "为什么在项目详情页中点击确认消耗后毛线球还是会还原大小呢，这个逻辑你麻烦跟我捋清楚在执行可以吗", acts=["IT-Q", "DC-M"], intents=["CODE.EXPLAIN", "CODE.REPAIR"], targets=["TO05", "TO02"], inputs=["IN01"])],
            "assessments": {
                "criteria_basis_reconstruction": assessment("POSSIBLE", [], [], "MEDIUM", ["R122"], "MEDIUM", "用户表达了持续缩小而非还原的预期，但没有把完整验收规则写出。"),
                "project_state_reconstruction": assessment("POSSIBLE", [], [], "HIGH", ["R122"], "MEDIUM", "用户要求先捋清楚当前逻辑，但后续依据只来自 Agent 解释，未观察到用户吸收。"),
                "evidence_action_governance": assessment("FORMED", ["R122"], [], "MEDIUM", ["R122"], "HIGH", "用户明确要求先解释和对齐逻辑，再执行修改。"),
            },
        },
        {
            "episode_id": "HRE-0147", "event_id": "R38",
            "events": [ev("R38", "收藏夹的文件夹上的 icon 和文件夹里面的 url 的 icon 的请求是共用的吧？", acts=["IT-Q"], intents=["CODE.EXPLAIN"], targets=["TO06", "TO04"], inputs=["IN00"])],
            "assessments": {
                "criteria_basis_reconstruction": assessment("NOT_OBSERVED", [], [], "NONE", [], "HIGH", "没有看到用户提出新的可接受标准。"),
                "project_state_reconstruction": assessment("POSSIBLE", [], [], "MEDIUM", ["R38"], "MEDIUM", "用户询问两个图标请求是否共享，说明需要核对关系，但没有观察到状态被重建或使用。"),
                "evidence_action_governance": assessment("NOT_OBSERVED", [], [], "NONE", [], "HIGH", "没有看到用户安排验证、范围或责任。"),
            },
        },
        {
            "episode_id": "HRE-0152", "event_id": "R26",
            "events": [ev("R26", "你看，内容可以穿出到导航栏之上，因此我们做的渐进式渐变遮罩失效了。而下方背景又无法延伸到地址栏UI 背后，显得被裁断了。iOS 有提供文档修复这个问题吗？如有，则适配", acts=["IT-F", "IT-Q", "AD-R"], intents=["STRATEGY.ALIGN", "CODE.REPAIR"], targets=["TO04", "TO06"], inputs=["IN01", "IN04", "IN06"])],
            "assessments": {
                "criteria_basis_reconstruction": assessment("FORMED", ["R26"], [], "MEDIUM", ["R26"], "HIGH", "用户明确提出内容不能穿出、背景不能被裁断，并将其作为适配标准。"),
                "project_state_reconstruction": assessment("POSSIBLE", [], [], "MEDIUM", ["R26"], "MEDIUM", "用户指出当前实现与 iOS 安全区域行为不一致，但没有完成内部机制重建。"),
                "evidence_action_governance": assessment("FORMED", ["R26"], [], "MEDIUM", ["R26"], "HIGH", "用户要求先查官方文档，满足条件后再适配。"),
            },
        },
        {
            "episode_id": "HRE-0164", "event_id": "R513",
            "events": [
                ev("R513", "闪跳和 q 按不动的问题根本没修掉。是在当前会话之前你某一次修改的时候出现的，定位并修复", acts=["IT-F", "IT-C", "AD-R"], intents=["CODE.REPAIR", "STRATEGY.REVIEW"], targets=["TO04", "TO07"], inputs=["IN01", "IN05"]),
                ev("R544", "你之前为了能使得切换器在插件 reload 后即可更新时处理的那部分代码回退下", acts=["AD-R", "AD-K"], intents=["CODE.REPAIR", "STRATEGY.REVIEW"], targets=["TO07", "TO06"], inputs=["IN05", "IN06"], temporal="AFTER_TRIGGER", behavior_change="CHANGED", behavior_change_basis="用户从要求定位并修复，转为指定回退与 reload 更新相关的历史修改范围。"),
            ],
            "assessments": {
                "criteria_basis_reconstruction": assessment("USED", ["R513"], ["R544"], "MEDIUM", ["R513"], "HIGH", "用户明确指出闪跳和按键失效必须真正消失，并在后续据此要求回退相关修改。"),
                "project_state_reconstruction": assessment("USED", ["R513"], ["R544"], "HIGH", ["R513"], "HIGH", "用户把问题追溯到当前会话之前的一次修改，并要求定位历史变更。"),
                "evidence_action_governance": assessment("USED", ["R513"], ["R544"], "MEDIUM", ["R513"], "HIGH", "用户限定先定位根因，随后指定回退哪一类历史修改。"),
            },
        },
        {
            "episode_id": "HRE-0205", "event_id": "R1153",
            "events": [
                ev("R1153", "怎么又成截图了，不是可编辑版本，而且“现场照片和路线数据对的上”这种话怎么又出现了，“此刻在路线上的位置”又出现了。是不是skill改的不彻底，我之前对小红书九宫格的修改好像又被改回来了", acts=["IT-F", "IT-C", "IT-Q"], intents=["STRATEGY.REVIEW", "CODE.REPAIR"], targets=["TO04", "TO06", "TO10"], inputs=["IN01", "IN05", "IN06"]),
                ev("R1217", "怎么又成截图了，不是可编辑版本，而且“现场照片和路线数据对的上”这种话怎么又出现了，“此刻在路线上的位置”又出现了。是不是skill改的不彻底，我之前对小红书九宫格的修改好像又被改回来了", acts=["IT-F", "IT-C", "AD-R"], intents=["CODE.REPAIR", "STRATEGY.REVIEW"], targets=["TO04", "TO06", "TO10"], inputs=["IN01", "IN05", "IN06"], temporal="AFTER_TRIGGER", behavior_change="REPEATED", behavior_change_basis="与 R1153 的问题内容和指导方向相同，仅再次报告未解决。"),
            ],
            "assessments": {
                "criteria_basis_reconstruction": assessment("FORMED", ["R1153"], [], "HIGH", ["R1153"], "HIGH", "用户在 R1153 明确提出可编辑交付和禁用文案边界；R1217 只是重复该要求，没有出现新的用户行为变化。"),
                "project_state_reconstruction": assessment("FORMED", ["R1153"], [], "MEDIUM", ["R1153"], "HIGH", "用户在 R1153 形成了“当前实现存在问题”的状态判断，但尚未确认 Skill 回流的真实根因；后续也没有基于新信息改变行动。"),
                "evidence_action_governance": assessment("POSSIBLE", [], [], "MEDIUM", ["R1153"], "MEDIUM", "用户在 R1153 提到修改范围和回流问题，但没有形成明确的后续验证、授权或回退安排；R1217 仍是重复反馈。"),
            },
        },
        {
            "episode_id": "HRE-0221", "event_id": "R541",
            "events": [
                ev("R541", "你看看应用的具体实现跟你说的是不是一只的", acts=["IT-Q", "AD-R"], intents=["STRATEGY.REVIEW", "CODE.EXPLAIN"], targets=["TO06", "TO07"], inputs=["IN00"]),
                ev("R542", "你看看应用的具体实现跟你说的是不是一致的", acts=["IT-Q", "AD-R"], intents=["STRATEGY.REVIEW", "CODE.EXPLAIN"], targets=["TO06", "TO07"], inputs=["IN00"], temporal="AFTER_TRIGGER"),
            ],
            "assessments": {
                "criteria_basis_reconstruction": assessment("POSSIBLE", [], [], "MEDIUM", ["R541"], "MEDIUM", "用户要求说明与实际实现一致，但没有进一步定义完整验收标准。"),
                "project_state_reconstruction": assessment("FORMED", ["R541"], [], "HIGH", ["R541"], "HIGH", "用户明确要求核对应用的实际实现与 Agent 说明之间的关系。"),
                "evidence_action_governance": assessment("FORMED", ["R541"], [], "MEDIUM", ["R541"], "HIGH", "用户安排了具体的实现核验行动，但没有继续给出授权或回退条件。"),
            },
        },
        {
            "episode_id": "HRE-0275", "event_id": "R38",
            "events": [ev("R38", "收藏夹的文件夹上的 icon 和文件夹里面的 url 的 icon 的请求是共用的吧？", acts=["IT-Q"], intents=["CODE.EXPLAIN"], targets=["TO06", "TO04"], inputs=["IN00"])],
            "assessments": {
                "criteria_basis_reconstruction": assessment("NOT_OBSERVED", [], [], "NONE", [], "HIGH", "没有观察到用户标准或验收规则。"),
                "project_state_reconstruction": assessment("POSSIBLE", [], [], "MEDIUM", ["R38"], "MEDIUM", "用户询问请求和缓存层面的关系，但没有看到用户对答案的吸收或后续使用。"),
                "evidence_action_governance": assessment("NOT_OBSERVED", [], [], "NONE", [], "HIGH", "没有观察到验证安排或修改边界。"),
            },
        },
        {
            "episode_id": "HRE-0280", "event_id": "R13",
            "events": [ev("R13", "现在导入本地壁纸之后，出现导入成功 toast。然后外观页面的壁纸开关必定被关闭。此时如果再点击开关，则会展开 ui，但立马又自动关闭。刷新之后偶尔能解决这个 bug，但不 100%。", acts=["IT-F", "IT-I"], intents=["CODE.REPAIR"], targets=["TO04", "TO05"], inputs=["IN01", "IN03"], validation=["VS02"])],
            "assessments": {
                "criteria_basis_reconstruction": assessment("POSSIBLE", [], [], "MEDIUM", ["R13"], "MEDIUM", "用户报告开关应保持可用这一隐含标准，但没有完整说明目标状态。"),
                "project_state_reconstruction": assessment("POSSIBLE", [], [], "HIGH", ["R13"], "MEDIUM", "用户提供了可复现的状态变化和刷新影响，但没有确认具体内部原因。"),
                "evidence_action_governance": assessment("POSSIBLE", [], [], "LOW", ["R13"], "MEDIUM", "用户提供复现和刷新观察，可支持后续验证，但尚未安排具体责任或回退边界。"),
            },
        },
    ]


def build_state(case: dict[str, Any], old_node: dict[str, Any], profile_packet: dict[str, Any], row: dict[str, str]) -> dict[str, Any]:
    profile = profile_packet["support_profile"]
    evidence = []
    primitive_map = {
        "criteria_basis_reconstruction": ["RULE_ALIGNMENT"],
        "project_state_reconstruction": ["PROVENANCE", "CAUSAL_EXPLANATION"],
        "evidence_action_governance": ["VERIFICATION", "DISPOSITION_COORDINATION"],
    }
    for item in profile_packet["behavior_evidence"]:
        supported = [dim for dim, entry in profile.items() if item["evidence_id"] in entry["evidence_ids"]]
        if not supported:
            continue
        primitives = sorted({p for dim in supported for p in primitive_map[dim]})
        evidence.append({
            "evidence_id": item["evidence_id"],
            "source": "OBSERVED",
            "locator": f"{case['episode_id']}/manual_recheck:{item['evidence_id']}",
            "observed_at": None,
            "sequence_index": int(item["evidence_id"][1:]),
            "content_sha256": "0" * 64,
            "supports_dimensions": supported,
            "supports_primitives": primitives,
            "available_at_decision": True,
        })
    coverage, evidence_quality, _ = _episode_coverage(row)
    consequence, reversibility, authorization = _episode_risk(row)
    node_kinds = set(old_node["node_meta"]["node_kind"])
    state = {
        "schema_version": "retrace-state-v3",
        "decision_id": f"{case['episode_id']}:evidence-first-recheck",
        "process_state": "GOVERNANCE_RECOVERING" if "BOUNDARY_OR_CLOSURE" in node_kinds else "REENTRY_SUPPORT",
        "support_profile": profile,
        "trace_coverage": coverage,
        "uncertainties": ["manual_evidence_first_recheck_development_sample"],
        "consequence": consequence,
        "reversibility": reversibility,
        "authorization_risk": authorization,
        "evidence_quality": evidence_quality,
        "workflow_continuity": old_node["workflow_continuity"],
        "evidence": evidence,
        "behavior_evidence": profile_packet["behavior_evidence"],
        "basis_assessment": profile_packet["basis_assessment"],
        "recent_interventions": 0,
        "active_verification": False,
    }
    return state


def main() -> None:
    old = json.loads(OLD_LOG.read_text(encoding="utf-8"))
    old_by_id = {node["episode_id"] + ":" + node["event_id"]: node for node in old["nodes"]}
    rows = _row_map()
    policy = load_policy(ROOT / "retrace_selector/config/policy.v0.2.json")
    templates = load_templates(ROOT / "retrace_selector/config/templates.v0.2.json")
    engine = SelectionEngine(policy, templates)
    results = []
    for case in packet_10():
        old_node = old_by_id[f"{case['episode_id']}:{case['event_id']}"]
        extracted = aggregate_support_profile({
            "behavior_evidence": case["events"],
            "basis_assessment": case["assessments"],
        })
        state = build_state(case, old_node, extracted, rows[case["episode_id"]])
        current = select_v03(state, engine)
        ablation = deepcopy(case["assessments"])
        for assessment_item in ablation.values():
            assessment_item["support_need"] = "NONE"
            assessment_item["need_evidence_ids"] = []
        counter_packet = aggregate_support_profile({
            "behavior_evidence": case["events"],
            "basis_assessment": ablation,
        })
        counter_state = build_state(case, old_node, counter_packet, rows[case["episode_id"]])
        counter = select_v03(counter_state, engine)
        dimension_ablations = {}
        for dimension in case["assessments"]:
            dimension_packet = deepcopy(case["assessments"])
            dimension_packet[dimension]["support_need"] = "NONE"
            dimension_packet[dimension]["need_evidence_ids"] = []
            dimension_extracted = aggregate_support_profile({
                "behavior_evidence": case["events"],
                "basis_assessment": dimension_packet,
            })
            dimension_state = build_state(case, old_node, dimension_extracted, rows[case["episode_id"]])
            dimension_result = select_v03(dimension_state, engine)
            dimension_ablations[dimension] = {
                "skyline": dimension_result["skyline_ids"],
                "selected": dimension_result["selected_ids"],
                "skyline_changed": dimension_result["skyline_ids"] != current["skyline_ids"],
                "selected_changed": dimension_result["selected_ids"] != current["selected_ids"],
            }
        changed_dimensions = []
        for dimension in extracted["support_profile"]:
            old_entry = old_node["support_profile"][dimension]
            new_entry = extracted["support_profile"][dimension]
            if old_entry != new_entry:
                changed_dimensions.append(dimension)
        results.append({
            "episode_id": case["episode_id"],
            "event_id": case["event_id"],
            "evidence_first_packet": extracted,
            "old_support_profile": old_node["support_profile"],
            "new_support_profile": extracted["support_profile"],
            "old_skyline": old_node["skyline_candidates"],
            "new_skyline": current["skyline_ids"],
            "old_selected": old_node["selected_ids"],
            "new_selected": current["selected_ids"],
            "old_frontier_ratio": old_node["frontier_ratio"],
            "new_frontier_ratio": current["frontier_ratio"],
            "new_outcome": current["outcome"],
            "changed_dimensions": changed_dimensions,
            "support_need_ablation_skyline": counter["skyline_ids"],
            "support_need_ablation_selected": counter["selected_ids"],
            "support_need_changes_skyline": current["skyline_ids"] != counter["skyline_ids"],
            "support_need_changes_selected": current["selected_ids"] != counter["selected_ids"],
            "dimension_ablation": dimension_ablations,
            "audit_note": "Manual evidence-first development recheck; not a Gold Label.",
        })
    summary = {
        "node_count": len(results),
        "skyline_changed_vs_old": sum(r["old_skyline"] != r["new_skyline"] for r in results),
        "selected_changed_vs_old": sum(r["old_selected"] != r["new_selected"] for r in results),
        "skyline_changed_under_support_need_ablation": sum(r["support_need_changes_skyline"] for r in results),
        "selected_changed_under_support_need_ablation": sum(r["support_need_changes_selected"] for r in results),
        "dimension_ablation": {
            dimension: {
                "skyline_changed": sum(item["dimension_ablation"][dimension]["skyline_changed"] for item in results),
                "selected_changed": sum(item["dimension_ablation"][dimension]["selected_changed"] for item in results),
            }
            for dimension in (
                "criteria_basis_reconstruction",
                "project_state_reconstruction",
                "evidence_action_governance",
            )
        },
        "interpretation": "Ablation compares the same evidence packet with all support_need values set to NONE; it tests selector sensitivity, not intervention effectiveness.",
    }
    payload = {"schema_version": "support-profile-recheck-v0.1", "summary": summary, "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "recheck_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Evidence-first Support Profile 10 节点复核",
        "",
        "> 每个试点 Episode 选 1 个代表节点。新 Profile 依据人工复核的行为证据、依据形成证据、依据使用证据和支持需求证据生成；不是自动 Gold Label。",
        "",
        f"- 节点数：{summary['node_count']}",
        f"- 新旧 Skyline 发生变化：{summary['skyline_changed_vs_old']}/{summary['node_count']}",
        f"- 新旧最终选择发生变化：{summary['selected_changed_vs_old']}/{summary['node_count']}",
        f"- 将 support_need 全部置为 NONE 后 Skyline 发生变化：{summary['skyline_changed_under_support_need_ablation']}/{summary['node_count']}",
        f"- 将 support_need 全部置为 NONE 后最终选择发生变化：{summary['selected_changed_under_support_need_ablation']}/{summary['node_count']}",
        "- 单独置空各类 support_need 后的影响：",
        *[
            f"  - `{dimension}`：Skyline {values['skyline_changed']}/{summary['node_count']}，最终选择 {values['selected_changed']}/{summary['node_count']}"
            for dimension, values in summary["dimension_ablation"].items()
        ],
        "",
        "## 节点对照",
        "",
        "| Episode/节点 | 新依据状态 | 新支持需求 | 旧选择 | 新选择 | support_need 置空后的选择 | Skyline 是否受影响 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in results:
        states = ", ".join(f"{k.split('_')[0]}={v['basis_status']}" for k, v in item["evidence_first_packet"]["basis_assessment"].items())
        needs = ", ".join(f"{k.split('_')[0]}={v['support_need']}" for k, v in item["evidence_first_packet"]["basis_assessment"].items())
        lines.append(
            f"| {item['episode_id']} / {item['event_id']} | {states} | {needs} | "
            f"{', '.join(item['old_selected']) or '—'} | {', '.join(item['new_selected']) or '—'} | "
            f"{', '.join(item['support_need_ablation_selected']) or '—'} | "
            f"{'是' if item['support_need_changes_skyline'] else '否'} |"
        )
    lines.extend([
        "",
        "## 三类证据明细",
        "",
    ])
    for item in results:
        lines.extend([
            f"### {item['episode_id']} / {item['event_id']}",
            "",
            f"- Profile 发生变化的维度：{', '.join(item['changed_dimensions']) or '无'}",
        ])
        for dimension, assessment_item in item["evidence_first_packet"]["basis_assessment"].items():
            lines.append(
                f"- `{dimension}`：形成 `{assessment_item['formation_evidence_ids'] or '—'}`；"
                f"使用 `{assessment_item['use_evidence_ids'] or '—'}`；"
                f"支持需求 `{assessment_item['need_evidence_ids'] or '—'}`。"
            )
        lines.append("")
    lines.extend([
        "",
        "## 判断",
        "",
        "1. 新规范把“用户有某种行为”与“用户已经形成并使用判断依据”分开；因此普通追问、重复报错和 Agent 自己的解释不会自动产生 OBSERVED 或 USED。",
        "2. `support_need` 确实能影响 Skyline 候选，但影响来自候选生成和硬约束入口，不应把它解释为已经验证的用户心理强度。",
        "3. 新旧差异需要结合逐案证据查看；本轮 10 个节点只是开发期复核，不能据此直接重跑全部历史节点。",
        "",
        "详细依据形成/使用/需求证据和完整 Profile 对照见 `recheck_results.json`。",
    ])
    (OUT / "recheck_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summary, "output_dir": str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
