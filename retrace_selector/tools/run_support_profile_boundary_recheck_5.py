"""Evidence-first boundary recheck for five manually adjudicated HRE cases.

This is a small manual audit set. It intentionally includes two cases that
must not be passed to Skyline as a single Re-entry episode: a direct-
delegation negative and a mixed window requiring resegmentation.
"""

from __future__ import annotations

from copy import deepcopy
import csv
import json
from pathlib import Path
from typing import Any

from retrace_selector.config import load_policy, load_templates
from retrace_selector.selector import SelectionEngine
from retrace_selector.support_profile import aggregate_support_profile
from retrace_selector.v03 import select_v03

from run_support_profile_recheck_10 import assessment, build_state, ev
from run_node_decision_audit import _episode_coverage, _episode_risk, _row_map


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/retrace_support_profile_boundary_recheck_20260822"


def _case(
    episode_id: str,
    event_id: str,
    verdict: str,
    reason: str,
    events: list[dict[str, Any]],
    assessments: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    return {
        "episode_id": episode_id,
        "event_id": event_id,
        "manual_verdict": verdict,
        "manual_reason": reason,
        "events": events,
        "assessments": assessments,
    }


def boundary_cases() -> list[dict[str, Any]]:
    return [
        _case(
            "HRE-0068",
            "R17",
            "EXCLUDE_DIRECT_DELEGATION",
            "用户给出详细的视觉实现与回退指令，但没有追查状态关系、请求可验证证据或基于新依据设置治理边界。",
            [
                ev(
                    "R17",
                    "删除 Journey 四阶段新增插图的所有缩放、呼吸、视差和随文字进度变化的动画。只保留静态展示。",
                    acts=["AD-K", "AD-R"],
                    intents=["CODE.REPAIR"],
                    targets=["TO04"],
                    inputs=["IN01", "IN06"],
                ),
                ev(
                    "R22",
                    "Codex 修复指令：Journey 背景图彻底静态化，禁止 transform、animation、transition，并按给出的 CSS 结构修改。",
                    acts=["AD-K", "AD-R"],
                    intents=["CODE.REPAIR"],
                    targets=["TO04"],
                    inputs=["IN01", "IN06"],
                    temporal="AFTER_TRIGGER",
                    behavior_change="CHANGED",
                    behavior_change_basis="用户补充了更细的实现约束，但仍是在直接规定实现方式，不是基于新依据进行治理。",
                ),
                ev(
                    "R27",
                    "撤销这段任务所进行的所有修改，回到无故事插图版本。",
                    acts=["AD-R"],
                    intents=["CODE.REPAIR"],
                    targets=["TO04"],
                    inputs=["IN01", "IN05"],
                    temporal="AFTER_TRIGGER",
                    behavior_change="CHANGED",
                    behavior_change_basis="用户从实现指令转为回退指令，但没有形成新的项目状态依据。",
                ),
            ],
            None,
        ),
        _case(
            "HRE-0204",
            "R313",
            "RESEGMENT_REQUIRED",
            "窗口同时包含专家设计约束、冰箱贴几何、社交卡片、轨迹图清理和缩略图调试等多个治理对象，不能作为一个单一 Episode 聚合。",
            [
                ev(
                    "R313",
                    "两个问题：产品设计风格没有体现专家作用；冰箱贴浮雕模型高度截取不完整，看看什么问题。",
                    acts=["IT-F", "IT-Q"],
                    intents=["STRATEGY.REVIEW", "CODE.REPAIR"],
                    targets=["TO04", "TO06", "TO07"],
                    inputs=["IN01", "IN05", "IN06"],
                ),
                ev(
                    "R378",
                    "我希望把专家输出变成结构化设计约束，让脚本读取，而不是脚本生成完再贴一句专家说明。",
                    acts=["AD-K", "AD-R"],
                    intents=["STRATEGY.ALIGN", "CODE.REPAIR"],
                    targets=["TO06", "TO07"],
                    inputs=["IN05", "IN06"],
                    temporal="AFTER_TRIGGER",
                    behavior_change="CHANGED",
                    behavior_change_basis="用户把问题从效果不明显推进到规定专家输出与脚本之间的结构关系。",
                ),
                ev(
                    "R567",
                    "轨迹图背景很突兀，把背景去掉，轨迹颜色也要和图片相适应。",
                    acts=["IT-F", "AD-K"],
                    intents=["CODE.REPAIR"],
                    targets=["TO04"],
                    inputs=["IN01", "IN06"],
                    temporal="AFTER_TRIGGER",
                    behavior_change="CHANGED",
                    behavior_change_basis="用户转向另一组视觉数据处理问题，改变了治理对象。",
                ),
            ],
            None,
        ),
        _case(
            "HRE-0206",
            "R1394",
            "RETAIN_CORE",
            "用户报告真实 STL 打印失败，提出可验证的制造约束，并在后续要求将同一约束应用到另一份 GPX 产物。",
            [
                ev(
                    "R1394",
                    "打印 STL 时底部边框文字打印不出来、字太小；希望字放大加粗、改为凹进去，轨迹线也要变细。",
                    acts=["IT-F", "IT-Q", "AD-K", "AD-R"],
                    intents=["STRATEGY.ALIGN", "CODE.REPAIR"],
                    targets=["TO04", "TO07"],
                    inputs=["IN01", "IN04", "IN06"],
                ),
                ev(
                    "R1448",
                    "根据这个变动把牧心谷的也做了。",
                    acts=["AD-K", "AD-R"],
                    intents=["CODE.REPAIR"],
                    targets=["TO07"],
                    inputs=["IN05", "IN06"],
                    temporal="AFTER_TRIGGER",
                    behavior_change="CHANGED",
                    behavior_change_basis="用户把当前打印失败中形成的文字和轨迹约束迁移到另一份 GPX 产物。",
                ),
                ev(
                    "R1450",
                    "根据这个变动把牧心谷的冰箱贴模型也做了，GPX 文件在牧心谷文件夹内。",
                    acts=["AD-K", "AD-R"],
                    intents=["CODE.REPAIR"],
                    targets=["TO07"],
                    inputs=["IN05", "IN06"],
                    temporal="AFTER_TRIGGER",
                    behavior_change="REPEATED",
                    behavior_change_basis="继续指定同一迁移任务，未新增判断依据。",
                ),
            ],
            {
                "criteria_basis_reconstruction": assessment(
                    "USED", ["R1394"], ["R1448"], "MEDIUM", ["R1394"], "HIGH",
                    "用户将打印失败转化为文字尺寸、凹刻方式和轨迹粗细等可执行标准，并在后续把这些标准迁移到另一份产物。",
                ),
                "project_state_reconstruction": assessment(
                    "POSSIBLE", [], [], "MEDIUM", ["R1394"], "MEDIUM",
                    "用户观察到 STL 的实际打印状态与目标不一致，但没有确认内部几何原因。",
                ),
                "evidence_action_governance": assessment(
                    "USED", ["R1394"], ["R1448"], "MEDIUM", ["R1394"], "HIGH",
                    "用户先提出制造约束，随后指定将已形成的约束应用到另一份 GPX 文件。",
                ),
            },
        ),
        _case(
            "HRE-0207",
            "R257",
            "RETAIN_CORE",
            "用户指出已构建产品的视觉识别、图片覆盖和故事页面与目标不符，随后基于模型能力与配置状态调整后续指导。",
            [
                ev(
                    "R257",
                    "图片识别只能识别景色，文字和其他元素识别不出来；故事只有一张图片，回顾页面也没有排版。换图片模型可能更合适。",
                    acts=["IT-F", "IT-C", "AD-K", "AD-R"],
                    intents=["STRATEGY.REVIEW", "STRATEGY.ALIGN", "CODE.REPAIR"],
                    targets=["TO04", "TO05", "TO06"],
                    inputs=["IN01", "IN05", "IN06"],
                ),
                ev(
                    "R291",
                    "kimi.k3有没有图片模态，能不能用这个模型？",
                    acts=["IT-Q", "AD-K"],
                    intents=["STRATEGY.REVIEW", "STRATEGY.ALIGN"],
                    targets=["TO06"],
                    inputs=["IN00", "IN06"],
                    temporal="AFTER_TRIGGER",
                    behavior_change="CHANGED",
                    behavior_change_basis="用户从报告识别失败转为比较具体视觉模型的能力与替代可能。",
                ),
                ev(
                    "R304",
                    "我已经配置好了 qwen 的 api，接下来要做什么？",
                    acts=["AD-K", "AD-R"],
                    intents=["STRATEGY.ALIGN", "CODE.REPAIR"],
                    targets=["TO06", "TO07"],
                    inputs=["IN05", "IN06"],
                    temporal="AFTER_TRIGGER",
                    behavior_change="CHANGED",
                    behavior_change_basis="用户基于前面对视觉模型能力的讨论完成配置，并把后续行动交给新的模型路径。",
                ),
            ],
            {
                "criteria_basis_reconstruction": assessment(
                    "USED", ["R257"], ["R291"], "MEDIUM", ["R257"], "HIGH",
                    "用户先指出识别内容和故事页面的验收问题，随后据此比较图片模型能力。",
                ),
                "project_state_reconstruction": assessment(
                    "USED", ["R257"], ["R291", "R304"], "HIGH", ["R257"], "HIGH",
                    "用户形成了当前视觉识别链路能力不足的判断，并据此改变模型与配置路径。",
                ),
                "evidence_action_governance": assessment(
                    "USED", ["R257"], ["R304"], "MEDIUM", ["R257"], "HIGH",
                    "用户提出具体内容边界，随后配置 Qwen API 并请求下一步行动。",
                ),
            },
        ),
        _case(
            "HRE-0208",
            "R848",
            "RETAIN_CORE",
            "用户发现导入数据与已知枚举标准冲突，指定文件与 Sheet，要求重新读取并根据核验结果修正导入。",
            [
                ev(
                    "R848",
                    "这个数据不太对，确认一下你打开的是指定 xls 文件的专注 data sheet 吗？things 标签应该只有 4 个枚举值。",
                    acts=["IT-F", "IT-Q", "AD-K"],
                    intents=["STRATEGY.REVIEW", "CODE.EXPLAIN"],
                    targets=["TO05", "TO06"],
                    inputs=["IN01", "IN05", "IN06"],
                ),
                ev(
                    "R856",
                    "我在 Excel 中确认了最新的表中这个字段只有 4 个值，建议重新读取确认一下指定文件和 Sheet。",
                    acts=["IT-F", "AD-K", "AD-R"],
                    intents=["STRATEGY.REVIEW", "CODE.REPAIR"],
                    targets=["TO05", "TO06", "TO07"],
                    inputs=["IN01", "IN05", "IN06"],
                    temporal="AFTER_TRIGGER",
                    behavior_change="CHANGED",
                    behavior_change_basis="用户从质疑数据不一致转为提供外部核验结果，并明确要求重新读取指定来源。",
                ),
                ev(
                    "R862",
                    "重新读取后确认 things 标签只有 4 个值，按这个结果继续导入 215 条记录。",
                    acts=["IT-F", "AD-K", "AD-R"],
                    intents=["CODE.REPAIR", "STRATEGY.ALIGN"],
                    targets=["TO05", "TO07"],
                    inputs=["IN05", "IN06"],
                    temporal="AFTER_TRIGGER",
                    behavior_change="CHANGED",
                    behavior_change_basis="用户基于重新读取的证据确认标准，并把后续导入约束到 4 个枚举值和指定记录集。",
                ),
            ],
            {
                "criteria_basis_reconstruction": assessment(
                    "USED", ["R848"], ["R856", "R862"], "MEDIUM", ["R848"], "HIGH",
                    "用户提出 4 个枚举值这一标准，随后用外部核验和重读结果约束导入。",
                ),
                "project_state_reconstruction": assessment(
                    "USED", ["R848"], ["R856", "R862"], "HIGH", ["R848"], "HIGH",
                    "用户追查了实际文件、Sheet 和当前导入状态，并根据重新读取结果修正状态判断。",
                ),
                "evidence_action_governance": assessment(
                    "USED", ["R848"], ["R856", "R862"], "MEDIUM", ["R848"], "HIGH",
                    "用户安排外部核验、重新读取和按核验结果继续导入的行动顺序。",
                ),
            },
        ),
    ]


def _selector_state(case: dict[str, Any], extracted: dict[str, Any], row: dict[str, str]) -> dict[str, Any]:
    fake_node = {
        "node_meta": {"node_kind": ["TRIGGER_OR_INSUFFICIENCY", "USER_UPTAKE_OR_RECONSTRUCTION"]},
        "workflow_continuity": 0.8,
    }
    return build_state(case, fake_node, extracted, row)


def main() -> None:
    rows = _row_map()
    policy = load_policy(ROOT / "retrace_selector/config/policy.v0.2.json")
    templates = load_templates(ROOT / "retrace_selector/config/templates.v0.2.json")
    engine = SelectionEngine(policy, templates)
    results: list[dict[str, Any]] = []

    for case in boundary_cases():
        result: dict[str, Any] = {
            "episode_id": case["episode_id"],
            "event_id": case["event_id"],
            "manual_verdict": case["manual_verdict"],
            "manual_reason": case["manual_reason"],
            "selector_status": "SKIPPED",
        }
        if case["assessments"] is None:
            result["audit_note"] = "Boundary case is excluded or requires resegmentation; no single-episode Skyline decision is produced."
            results.append(result)
            continue

        extracted = aggregate_support_profile({
            "behavior_evidence": case["events"],
            "basis_assessment": case["assessments"],
        })
        state = _selector_state(case, extracted, rows[case["episode_id"]])
        current = select_v03(state, engine)
        ablation = deepcopy(case["assessments"])
        for item in ablation.values():
            item["support_need"] = "NONE"
            item["need_evidence_ids"] = []
        counter_packet = aggregate_support_profile({
            "behavior_evidence": case["events"],
            "basis_assessment": ablation,
        })
        counter = select_v03(_selector_state(case, counter_packet, rows[case["episode_id"]]), engine)
        result.update({
            "selector_status": "RUN",
            "evidence_first_packet": extracted,
            "new_skyline": current["skyline_ids"],
            "new_selected": current["selected_ids"],
            "new_outcome": current["outcome"],
            "new_frontier_ratio": current["frontier_ratio"],
            "support_need_ablation_skyline": counter["skyline_ids"],
            "support_need_ablation_selected": counter["selected_ids"],
            "support_need_changes_skyline": current["skyline_ids"] != counter["skyline_ids"],
            "support_need_changes_selected": current["selected_ids"] != counter["selected_ids"],
        })
        results.append(result)

    retained = [r for r in results if r["selector_status"] == "RUN"]
    summary = {
        "case_count": len(results),
        "retained_selector_cases": len(retained),
        "excluded_direct_delegation": sum(r["manual_verdict"] == "EXCLUDE_DIRECT_DELEGATION" for r in results),
        "resegmentation_required": sum(r["manual_verdict"] == "RESEGMENT_REQUIRED" for r in results),
        "selector_cases_support_need_changed_skyline": sum(r["support_need_changes_skyline"] for r in retained),
        "selector_cases_support_need_changed_selected": sum(r["support_need_changes_selected"] for r in retained),
        "interpretation": "This is a manual boundary audit, not a Gold Label set and not a full-data run.",
    }
    payload = {
        "schema_version": "support-profile-boundary-recheck-v0.1",
        "summary": summary,
        "results": results,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "boundary_recheck_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Evidence-first Support Profile：5 个边界案例复核",
        "",
        "> 本轮包含 1 个严格直接委托负例、1 个需要重分段的混合窗口和 3 个保留的 Re-entry 案例。排除/重分段案例不生成单一 Episode 的 Skyline 选择。",
        "",
        f"- 案例数：{summary['case_count']}",
        f"- 进入 Skyline 的保留案例：{summary['retained_selector_cases']}",
        f"- 严格直接委托排除：{summary['excluded_direct_delegation']}",
        f"- 需要重分段：{summary['resegmentation_required']}",
        f"- 保留案例中 support_need 改变 Skyline：{summary['selector_cases_support_need_changed_skyline']}/{summary['retained_selector_cases']}",
        f"- 保留案例中 support_need 改变最终选择：{summary['selector_cases_support_need_changed_selected']}/{summary['retained_selector_cases']}",
        "",
        "## 结果",
        "",
        "| Episode/节点 | 人工边界裁决 | Selector | 新选择 | support_need 消融选择 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['episode_id']} / {r['event_id']} | {r['manual_verdict']} | {r['selector_status']} | "
            f"{', '.join(r.get('new_selected', [])) or '—'} | {', '.join(r.get('support_need_ablation_selected', [])) or '—'} |"
        )
    lines.extend(["", "## 逐案判断", ""])
    for r in results:
        lines.extend([
            f"### {r['episode_id']} / {r['event_id']}",
            "",
            f"- 边界裁决：`{r['manual_verdict']}`。{r['manual_reason']}",
        ])
        if r["selector_status"] == "RUN":
            packet = r["evidence_first_packet"]
            for dim, item in packet["basis_assessment"].items():
                lines.append(
                    f"- `{dim}`：{item['basis_status']}；形成 `{item['formation_evidence_ids'] or '—'}`；"
                    f"使用 `{item['use_evidence_ids'] or '—'}`；支持需求 `{item['need_evidence_ids'] or '—'}`。"
                )
            lines.append(f"- 选择结果：`{', '.join(r['new_selected']) or '—'}`；结果类型 `{r['new_outcome']}`。")
            lines.append(f"- 需求消融：`{', '.join(r['support_need_ablation_selected']) or '—'}`。")
        lines.append("")
    lines.extend([
        "## 初步结论",
        "",
        "1. HRE-0068 被 Evidence-first 流程保留为行为证据，但不应进入 Re-entry Skyline，因为用户始终是在直接规定实现和回退方式。",
        "2. HRE-0204 的问题不是三类依据判断错误，而是当前窗口包含多个治理对象，必须先重分段。",
        "3. HRE-0206、HRE-0207、HRE-0208 均观察到用户依据形成与后续行动变化，未发现明显把 Agent 解释误当作用户 uptake 的问题。",
        "4. 本轮没有明显系统性误判；可以进入下一轮 20–30 个分层节点扩展，但仍应保留 EXCLUDE 和 RESEGMENT 案例作为负向边界层。",
        "",
        "本报告不是 Gold Label，也未运行全量数据。",
    ])
    (OUT / "boundary_recheck_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summary, "output_dir": str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
