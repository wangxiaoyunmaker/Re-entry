"""Stratified evidence-first recheck of 20 manually selected nodes.

Composition: the original 10-node pilot, five boundary cases, and five new
stratified cases. This is still a manual development audit, not a Gold Label
run or a full historical replay.
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

from run_node_decision_audit import _row_map
from run_support_profile_recheck_10 import assessment, build_state, packet_10, ev
from run_support_profile_boundary_recheck_5 import boundary_cases, _case


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/retrace_support_profile_recheck_20_20260822"


def new_stratified_cases() -> list[dict[str, Any]]:
    return [
        _case(
            "HRE-0161", "R1", "RETAIN_CORE", "复杂交互中的因果关系、保护区和既有行为约束。",
            [
                ev("R1", "目前书签的级联菜单，在上下 hover 的时候有点延迟，不知道是不是之前做三角区导致的，你看看怎么回事，别影响已有的功能", acts=["IT-F", "IT-Q", "AD-K"], intents=["STRATEGY.REVIEW", "CODE.REPAIR"], targets=["TO04", "TO06"], inputs=["IN01", "IN05", "IN06"]),
                ev("R77", "父菜单到子菜单时保护区容易失效，另外已展开的子菜单再次点击不需要重新加载动画。", acts=["IT-F", "AD-K", "AD-R"], intents=["STRATEGY.ALIGN", "CODE.REPAIR"], targets=["TO04", "TO06"], inputs=["IN01", "IN06"], temporal="AFTER_TRIGGER", behavior_change="CHANGED", behavior_change_basis="用户从报告延迟转为明确保护区、重复动画和既有行为的关系约束。"),
                ev("R93", "把调试打开我看看实际的三角区情况。", acts=["AD-K"], intents=["CODE.EXPLAIN"], targets=["TO06"], inputs=["IN05"], temporal="AFTER_TRIGGER", behavior_change="CHANGED", behavior_change_basis="用户从提出问题转为要求查看可验证的三角区状态。"),
                ev("R121", "即使在三角区内，也会被纵向更激进的下一行 hover 打断。", acts=["IT-F", "IT-C", "AD-K"], intents=["STRATEGY.REVIEW", "CODE.REPAIR"], targets=["TO04", "TO06"], inputs=["IN01", "IN05"], temporal="AFTER_TRIGGER", behavior_change="CHANGED", behavior_change_basis="用户基于调试/实测结果进一步指出竞争 hover 路径。"),
            ],
            {
                "criteria_basis_reconstruction": assessment("USED", ["R1"], ["R77"], "MEDIUM", ["R1"], "HIGH", "用户提出不能影响既有功能，并在后续明确保护区和动画复用标准。"),
                "project_state_reconstruction": assessment("USED", ["R1"], ["R121"], "HIGH", ["R1"], "HIGH", "用户把延迟追溯到三角区与纵向 hover 的交互关系，并基于实测补充状态关系。"),
                "evidence_action_governance": assessment("USED", ["R1"], ["R93"], "MEDIUM", ["R93"], "HIGH", "用户要求打开调试查看实际状态，再据此继续指导修改。"),
            },
        ),
        _case(
            "HRE-0166", "R231", "RETAIN_CORE", "截图缓存/恢复问题中，用户形成了可观察的恢复标准并要求后续验证。",
            [
                ev("R231", "为什么离开 Chrome 一段时间之后，重新点开 Chrome，会发现截图消失", acts=["IT-F", "IT-Q"], intents=["STRATEGY.REVIEW", "CODE.REPAIR"], targets=["TO04", "TO06"], inputs=["IN01", "IN05"]),
                ev("R244", "可能只离开几分钟十几分钟，我希望重新打开 Chrome 时第一眼看到封面，有新截图再覆盖上去，做好过渡。", acts=["AD-K", "AD-R"], intents=["STRATEGY.ALIGN", "CODE.REPAIR"], targets=["TO04"], inputs=["IN01", "IN06"], temporal="AFTER_TRIGGER", behavior_change="CHANGED", behavior_change_basis="用户把模糊的消失问题具体化为短时间离开后的恢复与覆盖标准。"),
                ev("R325", "你改完之后，过段时间回 Chrome 后还能正确加载截图吗", acts=["IT-Q", "AD-K"], intents=["CODE.EXPLAIN", "CODE.REPAIR"], targets=["TO04", "TO06"], inputs=["IN05"], temporal="AFTER_TRIGGER", behavior_change="CHANGED", behavior_change_basis="用户从提出修复要求转为安排延迟恢复后的验证。"),
            ],
            {
                "criteria_basis_reconstruction": assessment("USED", ["R231"], ["R244"], "MEDIUM", ["R244"], "HIGH", "用户将截图消失转化为短时间离开后可见封面、新截图覆盖和过渡的行为标准。"),
                "project_state_reconstruction": assessment("POSSIBLE", [], [], "MEDIUM", ["R231"], "MEDIUM", "用户观察到恢复后的截图状态异常，但没有确认缓存或持久化的具体原因。"),
                "evidence_action_governance": assessment("USED", ["R231"], ["R325"], "MEDIUM", ["R325"], "HIGH", "用户在修复后明确安排延迟恢复场景的验证。"),
            },
        ),
        _case(
            "HRE-0267", "R933", "RETAIN_CORE", "用户治理项目分层、通用层与各店独立层之间的状态关系。",
            [
                ev("R933", "以后别的店也用这套，我现在写的那些店里的活儿不就混一块了，到时候怎么分开，总不能再删吧", acts=["IT-Q", "IT-C", "AD-K"], intents=["STRATEGY.REVIEW", "STRATEGY.ALIGN"], targets=["TO06", "TO07"], inputs=["IN01", "IN05", "IN06"]),
                ev("R947", "干活那层分开没问题，但文件夹起名得分清楚，不同行当有不同行当各自的那套", acts=["AD-K", "AD-R"], intents=["STRATEGY.ALIGN"], targets=["TO06", "TO07"], inputs=["IN05", "IN06"], temporal="AFTER_TRIGGER", behavior_change="CHANGED", behavior_change_basis="用户将混在一起的风险具体化为按行当区分文件夹和实现层。"),
                ev("R961", "行当通用的干活层、手机那部分单独，最终各家的活儿从手机部分复制出去，对吧？", acts=["IT-Q", "AD-K"], intents=["STRATEGY.REVIEW", "STRATEGY.ALIGN"], targets=["TO06", "TO07"], inputs=["IN05", "IN06"], temporal="AFTER_TRIGGER", behavior_change="CHANGED", behavior_change_basis="用户基于前述分层关系重新表述复制与继承路径。"),
                ev("R963", "把行当通用的那层列个计划，放到对应文件夹里，得做归档、试、试的归档", acts=["AD-K", "AD-R"], intents=["STRATEGY.ALIGN"], targets=["TO06", "TO07"], inputs=["IN05", "IN06"], temporal="AFTER_TRIGGER", behavior_change="CHANGED", behavior_change_basis="用户把分层理解转化为归档、试运行和文件夹组织计划。"),
            ],
            {
                "criteria_basis_reconstruction": assessment("USED", ["R933"], ["R947", "R961"], "MEDIUM", ["R933"], "HIGH", "用户明确要求不同店铺和通用层不能混在一起，并持续细化命名与分层标准。"),
                "project_state_reconstruction": assessment("USED", ["R933"], ["R961"], "HIGH", ["R933"], "HIGH", "用户重建了通用层、手机层和各店复制层之间的项目关系。"),
                "evidence_action_governance": assessment("USED", ["R933"], ["R963"], "MEDIUM", ["R963"], "HIGH", "用户基于分层关系安排文件归档、试运行和计划落点。"),
            },
        ),
        _case(
            "HRE-0289", "R30", "RETAIN_CORE", "用户从简单排查转向恢复三个外观设置入口之间的同步关系。",
            [
                ev("R30", "没修好", acts=["IT-F"], intents=["CODE.REPAIR"], targets=["TO04"], inputs=["IN01"]),
                ev("R32", "没修好。现在写外观的地方有 newtab、输入框/mode 和 options 三种，我希望在何处更改都可以，更改之后生效，且其他两处的显示也会跟着改变", acts=["IT-F", "IT-C", "AD-K", "AD-R"], intents=["STRATEGY.REVIEW", "CODE.REPAIR"], targets=["TO04", "TO06"], inputs=["IN01", "IN05", "IN06"], temporal="AFTER_TRIGGER", behavior_change="CHANGED", behavior_change_basis="用户从重复报告失败转为明确三个入口之间的同步状态关系和成功标准。"),
                ev("R57", "没影响到 newtab 里面对新标签页单独设置的深浅色模式吧", acts=["IT-Q", "AD-K"], intents=["STRATEGY.REVIEW", "STRATEGY.ALIGN"], targets=["TO04", "TO06"], inputs=["IN05", "IN06"], temporal="AFTER_TRIGGER", behavior_change="CHANGED", behavior_change_basis="用户在同步规则之外补充了对独立 newtab 设置不应被影响的边界。"),
            ],
            {
                "criteria_basis_reconstruction": assessment("USED", ["R30"], ["R32", "R57"], "MEDIUM", ["R32"], "HIGH", "用户先报告未修复，随后明确同步行为和不可影响的独立设置。"),
                "project_state_reconstruction": assessment("USED", ["R30"], ["R32"], "HIGH", ["R32"], "HIGH", "用户把失败从表面现象推进为三个设置入口之间的状态关系问题。"),
                "evidence_action_governance": assessment("USED", ["R30"], ["R57"], "MEDIUM", ["R57"], "HIGH", "用户在后续提出明确的影响边界，用于约束修改和验证。"),
            },
        ),
        _case(
            "HRE-0270", "R3", "RETAIN_EDGE", "用户发现并反复验证并发抢座问题，但后续主要是重复同一测试，不能轻易标记为依据 USED。",
            [
                ev("R3", "挨个过了，重点抢座来了，快速连发10下都订同一个 A1 卡座，结果成了好几笔，之前不是堵住了吗？", acts=["IT-F", "IT-C", "AD-K"], intents=["STRATEGY.REVIEW", "CODE.REPAIR"], targets=["TO05", "TO07"], inputs=["IN01", "IN05", "IN06"], validation=["VS02"]),
                ev("R5", "一个窗口狂点10下，成了4笔，前面几下挡住了，点太快，后面几下漏进去了，蹦出来4笔", acts=["IT-F", "IT-C"], intents=["CODE.REPAIR"], targets=["TO05", "TO07"], inputs=["IN01", "IN05"], validation=["VS02"], temporal="AFTER_TRIGGER", behavior_change="REPEATED", behavior_change_basis="仍是同一快速连点测试和同一并发问题，只是观察结果不同。"),
                ev("R9", "B2 B3 都成1笔，稳了", acts=["IT-F"], intents=["CODE.REPAIR"], targets=["TO05"], inputs=["IN01"], validation=["VS02"], temporal="AFTER_TRIGGER", behavior_change="REPEATED", behavior_change_basis="仍是同一验证方式，不能仅因结果稳定就认定用户行为发生变化。"),
            ],
            {
                "criteria_basis_reconstruction": assessment("FORMED", ["R3"], [], "MEDIUM", ["R3"], "HIGH", "用户明确要求同一座位的快速并发请求不能产生多笔订单。"),
                "project_state_reconstruction": assessment("POSSIBLE", [], [], "HIGH", ["R3"], "MEDIUM", "用户观察到并发状态异常，但没有形成具体内部原因或历史关系解释。"),
                "evidence_action_governance": assessment("FORMED", ["R3"], [], "MEDIUM", ["R3"], "MEDIUM", "用户进行了针对性复现和结果核验，但后续没有出现新的验证方法、范围限制或基于新依据的行动变化。"),
            },
        ),
    ]


def _fake_node() -> dict[str, Any]:
    return {"node_meta": {"node_kind": ["TRIGGER_OR_INSUFFICIENCY", "USER_UPTAKE_OR_RECONSTRUCTION"]}, "workflow_continuity": 0.8}


def main() -> None:
    rows = _row_map()
    all_cases: list[tuple[str, dict[str, Any]]] = []
    for case in packet_10():
        all_cases.append(("pilot_10", case))
    for case in boundary_cases():
        all_cases.append(("boundary_5", case))
    for case in new_stratified_cases():
        all_cases.append(("new_stratified_5", case))
    assert len(all_cases) == 20

    policy = load_policy(ROOT / "retrace_selector/config/policy.v0.2.json")
    templates = load_templates(ROOT / "retrace_selector/config/templates.v0.2.json")
    engine = SelectionEngine(policy, templates)
    results: list[dict[str, Any]] = []

    for stratum, case in all_cases:
        item: dict[str, Any] = {
            "stratum": stratum,
            "episode_id": case["episode_id"],
            "event_id": case["event_id"],
            "manual_verdict": case.get("manual_verdict", "RETAIN_CORE"),
            "manual_reason": case.get("manual_reason", ""),
            "selector_status": "SKIPPED",
        }
        if case.get("assessments") is None:
            item["audit_note"] = "Excluded or resegmentation boundary; no single-episode Skyline decision."
            results.append(item)
            continue

        extracted = aggregate_support_profile({"behavior_evidence": case["events"], "basis_assessment": case["assessments"]})
        state = build_state(case, _fake_node(), extracted, rows[case["episode_id"]])
        current = select_v03(state, engine)
        ablation = deepcopy(case["assessments"])
        for entry in ablation.values():
            entry["support_need"] = "NONE"
            entry["need_evidence_ids"] = []
        counter = aggregate_support_profile({"behavior_evidence": case["events"], "basis_assessment": ablation})
        counter_result = select_v03(build_state(case, _fake_node(), counter, rows[case["episode_id"]]), engine)
        item.update({
            "selector_status": "RUN",
            "evidence_first_packet": extracted,
            "new_selected": current["selected_ids"],
            "new_skyline_count": len(current["skyline_ids"]),
            "new_frontier_ratio": current["frontier_ratio"],
            "new_outcome": current["outcome"],
            "support_need_ablation_selected": counter_result["selected_ids"],
            "support_need_changed_selected": current["selected_ids"] != counter_result["selected_ids"],
            "support_need_changed_skyline": current["skyline_ids"] != counter_result["skyline_ids"],
        })
        results.append(item)

    strata = {}
    for stratum in sorted({r["stratum"] for r in results}):
        subset = [r for r in results if r["stratum"] == stratum]
        ran = [r for r in subset if r["selector_status"] == "RUN"]
        strata[stratum] = {
            "case_count": len(subset),
            "selector_cases": len(ran),
            "skipped_cases": len(subset) - len(ran),
            "support_need_changed_selected": sum(r.get("support_need_changed_selected", False) for r in ran),
            "support_need_changed_skyline": sum(r.get("support_need_changed_skyline", False) for r in ran),
        }
    summary = {
        "case_count": len(results),
        "selector_cases": sum(r["selector_status"] == "RUN" for r in results),
        "skipped_boundary_cases": sum(r["selector_status"] == "SKIPPED" for r in results),
        "strata": strata,
        "support_need_changed_selected": sum(r.get("support_need_changed_selected", False) for r in results),
        "support_need_changed_skyline": sum(r.get("support_need_changed_skyline", False) for r in results),
        "interpretation": "Manual stratified development audit; not Gold Labels and not a full historical run.",
    }
    payload = {"schema_version": "support-profile-recheck-v0.2-20", "summary": summary, "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "recheck_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Evidence-first Support Profile：20 个分层节点复核",
        "",
        "> 组成：原 10 个试点 + 5 个边界案例 + 5 个新增分层案例。所有依据形成、使用和支持需求均来自人工整理的用户事件证据；不是自动 Gold Label。",
        "",
        f"- 节点数：{summary['case_count']}",
        f"- 运行 Skyline：{summary['selector_cases']}",
        f"- 跳过（排除/重分段）：{summary['skipped_boundary_cases']}",
        f"- support_need 改变最终选择：{summary['support_need_changed_selected']}/{summary['selector_cases']}",
        f"- support_need 改变 Skyline：{summary['support_need_changed_skyline']}/{summary['selector_cases']}",
        "",
        "## 分层汇总",
        "",
        "| 分层 | 节点 | 运行 Selector | 跳过 | support_need 改变选择 | support_need 改变 Skyline |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for stratum, v in strata.items():
        lines.append(f"| {stratum} | {v['case_count']} | {v['selector_cases']} | {v['skipped_cases']} | {v['support_need_changed_selected']} | {v['support_need_changed_skyline']} |")
    lines.extend([
        "",
        "## 节点结果",
        "",
        "| 分层 | Episode/节点 | 人工裁决 | Selector | 新选择 | 消融选择 |",
        "|---|---|---|---|---|---|",
    ])
    for r in results:
        lines.append(f"| {r['stratum']} | {r['episode_id']} / {r['event_id']} | {r['manual_verdict']} | {r['selector_status']} | {', '.join(r.get('new_selected', [])) or '—'} | {', '.join(r.get('support_need_ablation_selected', [])) or '—'} |")
    lines.extend([
        "",
        "## 重点检查",
        "",
        "- HRE-0270 的 R5/R9 没有被标记为 CHANGED：结果改善本身不等于用户行为改变，也不自动构成依据 USED。",
        "- HRE-0068 仍作为直接委托负例跳过 Skyline；HRE-0204 仍作为混合窗口跳过 Skyline。",
        "- 其余保留案例均有形成证据；标记 USED 的案例都有后续内容变化，而非仅凭事件 ID 或 Agent 后续动作推断。",
        "",
        "本轮未运行全量数据；详细逐节点证据见 `recheck_results.json`。",
    ])
    (OUT / "recheck_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summary, "output_dir": str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
