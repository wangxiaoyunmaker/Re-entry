# Support Profile 抽取规范：Evidence-first v0.1

## 目的

`support_profile` 不是对用户心理状态的直接测量，也不能由关键词、Episode 标签或单个 `dialogue_act` 直接推断。它必须经过两层中间结果：

```text
原始 Trace
→ 用户行为证据
→ 判断依据是否形成、是否被使用
→ 支持类型与强度
→ selector-facing support_profile
```

现有 `retrace-state-v3` 仍可作为选择器接口；本规范定义其上游抽取契约。没有通过两阶段证据校验的结果不得作为 Gold Support Profile。

## LLM 在第一层的职责

第一层由 LLM 根据有边界的 Trace 生成事件级行为证据。LLM 负责理解用户话语的语义，
但不负责决定是否发生 Re-entry、支持强度或最终干预。特别是行动相关事件，LLM 还要
输出 `action_focus` 和 `supports_primitives`：

```text
原始用户事件
→ LLM 判断可观察行为与行动方向
→ 输出 VERIFICATION / DISPOSITION / BOTH / NONE / UNCLEAR
→ 绑定固定干预原语
→ 确定性校验器检查事件引用、角色和枚举值
```

当前实现入口为：

`src/retrace_selector/llm_extraction.py`

该模块只负责 Prompt 构造、JSON 解析、事件 ID 越界检查和行为证据 schema 校验；真实
模型调用由独立的 provider adapter 负责。这样替换 API 或模型时，不会改变 Support Profile
和 Skyline 的确定性接口。

第二阶段的实现入口为：

`src/retrace_selector/llm_support_profile.py`

其中 `extract_support_profile(...)` 会串联两个 LLM 调用：先调用第一层生成事件级行为
证据，再调用第二个 Prompt 评估三类判断依据的形成、使用和支持需求，最后调用现有的
确定性校验器与聚合器，返回完整的 `behavior_evidence`、`basis_assessment` 和
`support_profile`。LLM 只提出语义判断；事件引用、时序、用户侧 `USED` 条件、支持证据
以及三类字段的完整性，仍由本地校验逻辑负责把关。

## 运行时状态观察

在生成 Support Profile 之前，系统需要持续观察当前协作是否值得进行更深层分析。这个
观察器不输出一个二元的“是否 Re-entry”标签，而是汇总：

- 用户是否已经出现验证或行动边界行为；
- 是否出现 `basis_relevant_signal`（规则、目标、成功标准、项目状态、历史、不可接受影响或因果判断）；
- 直接委托是否已经失败或重复失败，分别保存在 `delegation_failure_signal` 和 `repeated_unresolved`；
- 当前操作是否有独立的高风险信号；
- 当前 Trace 是否完整、证据质量是否足够；
- 当前工作流是否仍在推进。

实现入口为：

`src/retrace_selector/state_observer.py`

```python
observation = observe_runtime_support_state(
    behavior_evidence=packet["behavior_evidence"],
    direct_delegation_failures=1,
    progress_observed=False,
    trace_coverage="ADEQUATE",
    evidence_quality=0.9,
    workflow_continuity=0.8,
)
```

观察器返回 `observation_state`、`should_generate_support_profile`、独立运行时信号、置信度
和审计理由。`basis_relevant_signal` 只是第二阶段路由信号，不直接指定三类支持维度或
干预原语。只要存在依据相关信号、直接委托失败/重复失败，或独立高风险信号，运行时入口
就可以调用第二个 Prompt；没有这些信号时直接继续委托，不生成 Support Profile。

完整的实时顺序是：

```text
原始 Trace
→ 第一层行为证据
→ 运行时状态观察器
→ 无 basis/failure/risk 信号：继续委托
→ 有信号：第二层 Support Profile
→ v0.3 状态适配器与一次 Skyline
→ `J(c)` 与 `J(B0)` 比较

同一目标的 `target_key`、委托尝试次数、最近确认进展、失败窗口、冷却时间和最近干预
ID 由上游持续状态维护；它们不会由第二阶段 LLM 猜测，也不会由五维分数反推。
```

## 第一层：事件级用户行为证据

第一层只描述可观察行为，不判断用户已经形成了哪类判断依据。每个关键事件沿用现有行为分类规则填写：

```json
{
  "evidence_id": "R1434",
  "actor": "USER",
  "text_span": "请先查一下为什么两个账号会看到同一条数据，再决定怎么改。",
  "dialogue_act": ["IT-Q", "AD-K"],
  "task_intent": ["CODE.EXPLAIN", "CODE.REPAIR"],
  "target_object": ["TO05"],
  "input_type": ["IN00"],
  "validation_strategy": ["VS00"],
  "action_focus": "DISPOSITION",
  "supports_primitives": ["DISPOSITION_COORDINATION"],
  "action_focus_rationale": "用户要求先讨论方案，暂停当前修改。",
  "temporal_position": "BEFORE_OR_AT_TRIGGER",
  "source": "OBSERVED",
  "behavior_change_from_prior": "CHANGED | REPEATED | NOT_APPLICABLE | UNCLEAR",
  "behavior_change_basis": "仅当行为内容相对前一相关用户事件发生可观察变化时填写"
}
```

第一层允许记录：

- 用户是否报告了观察或预期—实际差异；
- 用户是否提问、纠正、提出规则、限定范围、请求证据或安排验证；
- 行为作用于需求、状态、代码、数据、测试或责任边界中的什么对象；
- 用户是否实际运行、查看、比较或验证了结果。

第一层禁止输出：

- `criteria_basis_reconstruction` 等机制类型；
- “用户已经理解项目”；
- “用户需要高强度干预”；
- 仅凭“为什么”“排查”“还是不行”等词语推断依据形成。

## 第二层：判断依据形成与使用

第二层读取第一层行为证据及其时序，分别判断三类判断依据是否真的出现：

```json
{
  "criteria_basis_reconstruction": {
    "basis_status": "FORMED",
    "formation_evidence_ids": ["R1435"],
    "use_evidence_ids": ["R1437"],
    "support_need": "MEDIUM",
    "need_evidence_ids": ["R1434"],
    "confidence": "HIGH",
    "rationale": "用户明确提出不同账号不得互相看到数据，并据此要求先做隔离测试。",
    "need_rationale": "同一事件同时提出了规则，并说明了为什么需要先获得验证支持。"
  },
  "project_state_reconstruction": {
    "basis_status": "POSSIBLE",
    "formation_evidence_ids": [],
    "use_evidence_ids": [],
    "support_need": "HIGH",
    "need_evidence_ids": ["R1434"],
    "confidence": "MEDIUM",
    "rationale": "用户要求追查状态关系，但尚未确认具体原因或历史路径。",
    "need_rationale": "同一事件既暴露了未解决的状态关系，也说明了需要状态重建支持。"
  },
  "evidence_action_governance": {
    "basis_status": "USED",
    "formation_evidence_ids": ["R1435"],
    "use_evidence_ids": ["R1437"],
    "support_need": "LOW",
    "need_evidence_ids": ["R1437"],
    "confidence": "HIGH",
    "rationale": "用户把验证顺序和修改前提明确交给后续行动。",
    "need_rationale": "同一事件同时规定了行动边界，并说明继续推进前需要验证支持。"
  }
}
```

### `basis_status` 的含义

| 值 | 含义 | 必要证据 |
|---|---|---|
| `NOT_OBSERVED` | 当前 Trace 没有足够用户侧证据 | 不得引用形成或使用事件 |
| `POSSIBLE` | 用户行为指向某类依据，但尚未明确形成或使用 | 可有疑问、追问或观察，不能证明 uptake |
| `FORMED` | 用户明确提出、修正或组织了该类依据 | 至少一个用户侧形成证据 |
| `USED` | 用户不仅形成依据，还据此改变后续指导、边界或验证 | 同时需要形成证据和后续用户使用证据；后续事件必须标为 `behavior_change_from_prior=CHANGED` |

### 三类依据的裁决边界

- `criteria_basis_reconstruction`：用户明确提出或修正规则、目标、成功标准、不可接受结果。
- `project_state_reconstruction`：用户追查版本、文件、数据、模块关系、历史变化或因果关系。
- `evidence_action_governance`：用户安排验证、限定修改范围、分配责任、授权、回退或结束条件。

Agent 自己的解释、测试声明或原因分析不能单独构成用户依据形成；功能最终修好也不能反推用户完成依据使用。

重复同一条用户指令、重复同一问题或仅再次报告同一现象，都不能算依据使用。`CHANGED` 必须根据用户行为内容本身判断，不能因为事件 ID 不同、时间更晚或 Agent 在两条事件之间做过处理就自动成立。只有后续用户行为发生了可观察变化，例如新增约束、改变验证方法、限定修改范围、指定回退对象或基于新信息调整行动，才可以标记为 `USED`；该事件还应填写 `behavior_change_basis` 说明具体变化。

`need_evidence_ids` 可以与 `formation_evidence_ids` 或 `use_evidence_ids` 重合。重合不表示校验失败，但 `need_rationale` 必须说明同一事件为什么同时证明“依据已经形成/使用”和“当前仍需要支持”；如果无法说明，应补充另一条独立的支持需求证据或降低判断强度。

例如，`R1153` 可以支持 `project_state_reconstruction=FORMED`，但这里的 FORMED 只表示用户形成了“当前实现存在问题”的状态判断，不表示用户已经确认 Skill 回流的真实根因。若后续只有重复的 `R1217`，则不能填 `use_evidence_ids`，也不能标记为 `USED`。

## 支持类型与强度

`support_need` 是对当前干预支持需求的判断，不是用户的心理能力等级。它必须拥有 `need_evidence_ids`，并与 `basis_status` 分开记录：

- `basis_status` 回答：用户当前是否已经形成并使用判断依据；
- `support_need` 回答：当前干预需要支持哪一类工作、支持强度多大。

因此，`basis_status=USED` 不等于 `support_need=NONE`；用户可能已经形成依据，但仍需要低负担的验证或行动支架。反过来，`basis_status=POSSIBLE` 也不能自动等于 `support_need=HIGH`，仍需依据当前证据和任务后果判断。

## 失败关闭规则

以下情况必须降为 `POSSIBLE`、`NOT_OBSERVED` 或 `UNCLEAR`，不得强行生成高强度支持：

1. 只有 Agent 解释，没有用户回应或使用证据；
2. 用户只说“还是不行”“继续修复”，没有具体依据或行动边界；
3. 只有 Trigger 之后才出现的规则，不能倒推为 Trigger 之前已有标准；
4. 只有普通新功能或视觉偏好，没有与既有状态的关系；
5. Trace 被截断，无法确认用户是否吸收了信息；
6. 用户提出问题，但没有证据说明他据此改变了后续行动。

## 与 selector 的接口

第二层输出经校验后，才聚合为现有 `support_profile`：

```text
basis_status → observed_work
support_need → support_need
formation/use/need evidence → evidence_ids + evidence_basis
confidence → confidence
```

聚合函数位于：

`src/retrace_selector/support_profile.py`

### 行动治理与验证的原语绑定

`evidence_action_governance` 仍然是统一的支持维度，但它不能在所有情况下都只输出一个
无差别的维度标签。事件级证据还应根据用户实际行为绑定到具体干预原语：

| 用户行为 | `supports_primitives` |
|---|---|
| 要求日志、错误码、测试、复现步骤或验收结果 | `VERIFICATION` |
| 要求“先不要改、先讨论方案” | `DISPOSITION_COORDINATION` |
| 规定先做前端、后端之后交接 | `DISPOSITION_COORDINATION` |
| 限定修改范围、要求不影响其他部分 | `DISPOSITION_COORDINATION` |
| 确认责任、顺序、授权、回退或交付边界 | `DISPOSITION_COORDINATION` |
| 同时安排行动并要求验证 | `DISPOSITION_COORDINATION`, `VERIFICATION` |

这一步不是新增用户行为类别，而是把已有行为证据绑定到固定的干预原语。绑定优先级为：

```text
原语绑定 > 支持维度绑定 > 无绑定证据
```

因此，用户说“先不要改，先讨论方案”时，不能只输出
`evidence_action_governance=OBSERVED`；该事件还应将证据绑定到
`DISPOSITION_COORDINATION`。相反，用户要求“请生成错误码并跑一次测试”时，应优先绑定到
`VERIFICATION`。如果无法区分两者，保留维度绑定并允许 Skyline 返回并列选择，不得用
关键词强行决定原语。

它不会从关键词或 Episode fallback 生成机制维度。若 `basis_assessment` 与 `support_profile` 不一致，v0.3 boundary validator 会拒绝该输入。

## 当前审计边界

此前 10 个 Episode、48 个节点的决策日志使用的是临时事件投影，不能回溯性地当作本规范的正式抽取结果。下一步应先对一小批节点独立完成第一层和第二层编码，再重新运行 selector，比较 Skyline 共现和节点内维度区分度是否改善。
