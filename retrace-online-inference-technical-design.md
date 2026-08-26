# ReTrace 在线干预推理技术方案（收敛版）

**用途**：定义 ReTrace 如何在 Codex 协作过程中识别 Re-entry Occasion、持续估计用户当前的 Criteria/State/Action 状态，并从交互设计团队提供的候选策略中动态选择是否干预及如何干预。

**系统形态**：ReTrace Codex 插件中的在线推理服务；不修改 Codex 主 Agent 的生成逻辑，也不自动修改项目。
**配套文档**：[Codex 插件技术方案](./retrace-codex-plugin-technical-design-v0.1.md)负责事件接入和 UI；[离线分析技术方案](./retrace-offline-analysis-technical-design-v0.1.md)负责完整 chain 的结果分析。

---

## 1. 要解决的问题

当一个已经进入项目的局部决定重新影响当前处置时，用户可能需要恢复以下三类内容：

- **Criteria（C）**：当前目标、规则、边界和验收条件；
- **State（S）**：Agent 已经做了什么，以及当前状态、历史来源和关键关系；
- **Action（A）**：下一步如何验证、修正、授权、限制、回退或停止。

ReTrace 不把某个行为固定映射成某个策略，而是持续回答三个问题：

1. 当前是否出现了一个值得开始考虑支持的 Occasion？
2. 对当前局部 decision chain，用户当前 C/S/A 状态与目标状态之间还有多大差距？
3. 哪个候选策略能以较小打断更好地缩小该差距？

系统输出只有三种：

```text
NO_INTERVENTION
INTERVENE（一个候选）
PRESENT_CHOICES（最多两个不同策略路径）
```

---

## 2. 核心流程

```text
Codex 实时事件与项目证据
        ↓
1. Occasion Detector
   识别支持时机并冻结 focal decision chain
        ↓
2. Target Builder
   为当前 chain 生成目标 C/S/A
        ↓
3. Occasion Baseline Evaluation
   在首次 exposure 前完成三维场景化基线评测
        ↓
4. State Observer
   根据后续行为序列持续更新当前 C/S/A
        ↓
5. Strategy Registry
   读取每个“策略 × 强度”的五维参数
        ↓
6. Skyline Selector
   动态五维向量 → Skyline → D_obj + βD_user
        ↓
7. Intervention & Evaluation
   展示干预 → 检测互动结束 → 三维场景化结果评测
```

Occasion 只表示系统开始考虑支持。识别出 Occasion 并冻结目标后，系统先发起一次三维场景化基线评测，再继续观察用户行为；是否干预以及选择哪种策略由之后不断更新的状态决定。基线评测不得晚于第一次真实 exposure。

---

## 3. 在线输入

### 3.1 Codex 事件

插件通过生命周期 Hooks 提供以下结构化事件：

```text
SESSION_START
USER_PROMPT
TOOL_CALL
TOOL_RESULT
AGENT_FINAL
COMPACTION_START / COMPACTION_END
SESSION_END
```

每个事件至少带有：

```json
{
  "event_id": "EVT-0031",
  "session_id": "SESSION-01",
  "user_id": "PARTICIPANT-0031",
  "turn_id": "TURN-04",
  "event_type": "USER_PROMPT",
  "actor": "USER",
  "content_ref": "CONTENT-0031",
  "project_id": "PROJECT-01",
  "observed_at": "2026-08-24T10:32:11+08:00",
  "source": "CODEX_HOOK",
  "collector_seq": 31,
  "received_at": "2026-08-24T10:32:11.120+08:00",
  "is_late": false,
  "late_for_snapshot_id": null
}
```

在线推理还接收以下由插件交互层或 Collector 产生的事件。它们不是 Codex Hook，必须通过 `source=MCP_UI` 或 `source=COLLECTOR` 标记：

```text
USER_RESPONSE              Observer probe、Occasion baseline 或 POST evaluation 的用户回答
INTERVENTION_EXPOSURE      UI 已成功呈现干预，定义真实 exposure
INTERVENTION_ACTION        用户选择、关闭、跳过或提交
POLICY_PREFERENCE_UPDATED  用户保存频率、力度或零干预模式
ADAPTATION_UPDATE          三维 baseline/post 结果触发的版本化偏好更新
LATE_EVENT                 已有快照之后到达的事件审计记录
SNAPSHOT_PRE / SNAPSHOT_POST
                            对应测量点的不可变快照记录
```

Hook 采集和 UI 交互都必须经过同一个 Collector。在线推理不直接读取 iframe 状态，也不把 Selector recommendation 当作 exposure。

事件采集、乱序处理和持久化由插件技术方案定义。在线推理只消费已经规范化的事件，不读取 Codex DOM，也不依赖 transcript 文件格式。

### 3.2 行为与证据特征

已有 1A/1B 序列分析作为 Observer 的基础数据：

```json
{
  "unit_id": "U0031::M02",
  "event_id": "EVT-0031",
  "UA": ["UA05"],
  "target_objects": ["RO04"],
  "evidence_types": ["EV06"],
  "context_relations": ["DR02"],
  "evidence_ids": ["EVID-0142"],
  "coding_status": "OBSERVED"
}
```

这些字段表示可观察行为和证据。Observer 可以根据连续行为序列更新 C/S/A，但不得由某个 UA、EV 或 DR 标签直接推出“用户已经理解”或固定干预策略。

### 3.3 Collector 顺序与 late-event

异步 Hook 可能乱序完成；并行工具之间也可能只有偏序而没有可恢复的总顺序。因此 Collector 为每个 session 分配单调 `collector_seq`，同时保存 `event_time`、`received_at`、`causal_parent_ids`、`turn_id` 和 `tool_use_id`。`collector_seq` 只用于可复现处理，不被解释为用户行为的真实时间顺序。

```json
{
  "event_id": "EVT-0031",
  "collector_seq": 31,
  "event_time": "2026-08-24T10:32:11+08:00",
  "received_at": "2026-08-24T10:32:11.120+08:00",
  "causal_parent_ids": ["EVT-0030"],
  "is_late": false,
  "late_for_snapshot_id": null
}
```

接口：

```text
ingest_event(raw_event)
  → {
       event_id,
       collector_seq,
       accepted,
       duplicate,
       is_late,
       late_event_id,
       watermark
     }

apply_late_event(late_event_id)
  → {
       affected_chain_ids,
       new_snapshot_ids,
       preserved_snapshot_ids,
       recompute_status
     }
```

事件在某个 `snapshot_id` 之后才到达，且其 `event_time` 属于该 snapshot 的观察窗口时，标记为 `is_late=true`。系统可以产生新的 `LATE_RECOMPUTE` snapshot，但不得覆盖已经保存的 Selector decision、exposure 或 PRE/POST 快照。离线分析使用 `as_of_event_id` 选择当时可见的版本。

---

## 4. Occasion 与目标状态

### 4.1 Occasion 判定

检测当前是否出现 Occasion，只检查三个条件：

| 字段 | 判断问题 |
|---|---|
| `prior_instantiation` | 当前规则、结构、状态或决定此前是否已经进入项目 |
| `current_contact` | 是否出现使同一既有对象重新与当前处置相关的新接触 |
| `consequentiality` | 对该对象的不同判断是否会改变下一步修改、验证、授权、限制或回退 |

每项取值：

```text
CONFIRMED | NOT_CONFIRMED | UNCLEAR
```

判定：

```text
三项均 CONFIRMED
→ OCCASION_CONFIRMED

前两项已确认，consequentiality 尚不清楚
→ OCCASION_CANDIDATE，继续观察

任一项 NOT_CONFIRMED
→ NOT_OCCASION

对象无法绑定
→ UNKNOWN
```

Agent 刚完成实现、用户进入界面验收并发现结果不符合预期，也可以构成 Occasion。系统不要求对象必须离开注意中心一段时间。

### 4.2 冻结 decision chain

Occasion 确认后，系统冻结一个局部 decision object：

```json
{
  "occasion_id": "OCC-004",
  "chain_id": "PROJECT-01::OCC-004::FD-02",
  "focal_decision_id": "FD-02",
  "decision_object_profile_id": "FD-PROFILE-02",
  "claim_ids": ["CLAIM-FD02-01"],
  "decision_object": "删除原始照片时个人库、共享库与回忆卡片的关系",
  "anchor_event_id": "EVT-0031",
  "evidence_ids": ["EVID-0138", "EVID-0142"]
}
```

一个 chain 只处理一个局部 decision object。多目标 Episode 分成多个 chain，后续状态、策略和评测都绑定同一个 `chain_id`。

### 4.3 生成目标 C/S/A

Target Builder 根据当前 decision object、Occasion 后果和同链证据，输出用户为治理当前问题需要达到的最低目标状态：

```json
{
  "chain_id": "PROJECT-01::OCC-004::FD-02",
  "target_state": {
    "criteria": 2,
    "state": 3,
    "action": 2
  },
  "evidence_ids": ["EVID-0138", "EVID-0142"]
}
```

MVP 不在线自由生成 `decision_object` 或 target profile。Target Builder 必须先从冻结的 `decision_object_profile` 中匹配一个模板；模板包含唯一的 focal decision object、允许的证据类型、固定 target C/S/A 和 `rubric_version`。无法唯一匹配时返回 `CHAIN_UNASSESSABLE`，不能让 LLM 临时创造新的 decision object 或目标。

目标等级使用统一的 0–3 标尺：

| level | 目标含义 |
|---:|---|
| 0 | 该维与当前 decision object 无关 |
| 1 | 只需局部线索即可继续 |
| 2 | 需要形成可用于下一步判断的明确关系 |
| 3 | 该维是当前问题的关键治理条件，必须完整恢复 |

目标状态属于当前 chain，不是对用户一般能力的评价。

### 4.4 Occasion 后三维场景化基线评测

`OCCASION_CONFIRMED` 后、第一次 `INTERVENTION_EXPOSURE` 前，系统必须为当前 chain 发起一次三维场景化基线评测。它与 `submit_observer_probe` 的用途不同：

| 类型 | 目的 | 是否进入 Selector |
|---|---|---|
| `OCCASION_BASELINE` | 记录 Occasion 刚确认时的 C/S/A 基线，供 PRE/POST 和组间分析比较 | 不直接把回答转换成 C/S/A level；如用于 Observer，必须显式标记来源 |
| `OBSERVER_PROBE` | 在运行中补充当前状态判断所需的证据 | 可以作为带来源的补充证据 |
| `POST_EVALUATION` | 互动结束后的三维结果测量 | 不回填历史 Selector 或状态 |

基线评测固定包含 Criteria、State、Action 三道与当前 decision object 相关的情境题，使用冻结的 Likert 量表。题目应询问用户在当前项目关系中的判断、状态恢复和下一步行动边界，不能写成抽象的“你是否理解”。用户可以逐题回答、跳过或超时；跳过必须保存为缺失，不能阻塞事件采集，但必须在第一次 exposure 前完成一次“已回答/已跳过”的记录。

~~~text
OCCASION_CONFIRMED
  ↓ 冻结 decision object 与 target profile
OCCASION_BASELINE_PENDING
  ↓ 三道 C/S/A 情境题：回答、逐题跳过或超时
继续 Observer / Selector
  ↓
第一次 INTERVENTION_EXPOSURE
~~~

基线回答以 `USER_RESPONSE` 事件写入 Collector，并携带 `response_kind=OCCASION_BASELINE`、`question_set_version`、`as_of_event_id` 和 `evaluation_id`。它可以作为离线的 `baseline_CSA` 协变量，也可以作为 Observer 的带来源证据参与 assessability 判断，但不能直接覆盖行为证据或把 Likert 分数硬映射成 0–3 的状态 level。若基线评测尚未完成就发生 exposure，记录 `BASELINE_MISSED`，不得把 exposure 后回答冒充 Occasion 基线。

接口：

~~~text
submit_occasion_baseline(
  chain_id,
  snapshot_version,
  evaluation_id,
  question_set_version,
  responses,
  skipped_dimensions,
  interaction_id,
  as_of_event_id
)
  → {
       event_ids,
       evaluation_id,
       measurement_point: "OCCASION_BASELINE",
       as_of_event_id,
       accepted
     }
~~~

---

## 5. State Observer：持续估计当前 C/S/A

### 5.1 当前状态结构

Observer 在 Occasion 确认后随事件流持续更新：

```json
{
  "chain_id": "PROJECT-01::OCC-004::FD-02",
  "current_state": {
    "criteria": {
      "level": 1,
      "assessability": "SUFFICIENT",
      "evidence_ids": ["EVID-0141"]
    },
    "state": {
      "level": 1,
      "assessability": "LIMITED",
      "evidence_ids": ["EVID-0142"]
    },
    "action": {
      "level": 2,
      "assessability": "SUFFICIENT",
      "evidence_ids": ["EVID-0143"]
    }
  },
  "recent_exposure_count": 0,
  "recent_exposure_burden": 0.0,
  "active_verification": false
}
```

### 5.2 当前等级的统一含义

| level | 可观察行为含义 |
|---:|---|
| 0 | 同链行为中尚未形成该维相关关系或行动依据 |
| 1 | 出现零散线索，但仍不足以支撑当前局部判断 |
| 2 | 已形成可用于下一步处置的主要关系，但仍有局部缺口 |
| 3 | 已形成当前 decision object 所需的明确、可操作关系 |
| `UNKNOWN` | 当前日志不足以判断，不能按 0 处理 |

`level` 是同链可观察覆盖程度，不是心理掌握分数。详细行为锚点使用已经由真实数据标定的 C/S/A 标准；在线方案只规定统一输入输出，不重新建立一套状态分类。

### 5.3 序列如何更新三维状态

Observer 不把用户行为限制为固定过程状态，而是根据每个新行为及其上下文持续更新三维：

| 行为证据 | 主要提供的信息 |
|---|---|
| 用户恢复目标、规则、不变量或验收条件 | 更新 Criteria |
| 用户追问实现、历史、来源、依赖或对象关系 | 更新 State |
| 用户提出验证、修正、授权、范围、回退或停止边界 | 更新 Action |
| 测试、工具结果或工件检查 | 提高相关判断的 evidence support |
| Agent 自己的完成声明或解释 | 只作为 Agent claim，不能直接证明用户已获得该关系 |

同一行为可以同时更新多个维度。每次更新必须绑定当前 chain 的 `evidence_ids`。

`active_verification` 表示当前是否已有验证过程正在进行，它作为更新 Action 状态的行为证据使用，不再作为独立优化维度重复计分。

### 5.4 可选的 Observer probe

这里的 Observer probe 不等同于 4.4 的 `OCCASION_BASELINE`。前者是在线推理需要时的补充证据，后者是 Occasion 确认后的固定基线测量；两者必须使用不同的 `response_kind` 和不同的 `evaluation_id`。

在 OCCASION_BASELINE 已记录后，或运行中 Observer 发现证据不足时，插件可以另外展示三个与当前情境对应、可跳过的问题，分别补充 C/S/A 证据。问题不能写成抽象自评或知识考试，而应围绕当前局部项目关系。

用户回答作为新的 `USER_RESPONSE` 证据交给 Observer；它可以改变当前状态判断，但不直接覆盖行为证据。用户未回答时，系统继续仅根据行为序列估计。

接口：

```text
submit_observer_probe(
  chain_id,
  snapshot_version,
  probe_id,
  response_kind,
  response,
  skipped,
  interaction_id,
  as_of_event_id
)
  → {
       event_id,
       accepted,
       current_state_snapshot_id
     }
```

回答必须作为新的 `USER_RESPONSE` 事件进入同一 Collector，并记录回答时的 `as_of_event_id`。它与 chain 结束时的 `submit_evaluation` 分开；后者只用于评测，不回填 Selector 的历史状态。

---

## 6. Strategy Registry：每个候选的五维参数

### 6.1 候选单位

每个“策略类型 × 强度”是一个独立候选。例如，某策略的 L1、L2、L3 分别配置参数，不能只为整个策略类型配置一个平均值。

每个候选只需要一组五维选择参数：

```json
{
  "strategy_id": "STATE_CONTEXT_RECOVERY_L2",
  "parameters": {
    "criteria": 0.10,
    "state": 0.65,
    "action": 0.10,
    "evidence": 0.70,
    "workflow": 0.75
  }
}
```

记为：

\[
\mathbf x_c=(C_c,S_c,A_c,E_c,W_c)
\]

| 参数 | 含义 |
|---|---|
| \(C_c\) | 对 Criteria 缺口的支持能力 |
| \(S_c\) | 对 State 缺口的支持能力 |
| \(A_c\) | 对 Action 缺口的支持能力 |
| \(E_c\) | 策略内容的证据锚定与可追溯程度 |
| \(W_c\) | 工作流连续性，越高表示打断越小 |

所有参数归一化到 `[0,1]`。`strategy_id`、强度名称和渲染模板属于实现标识，不属于五维优化参数。

### 6.2 已确认的四类交互策略族

四类交互已经冻结为 MVP 的策略族。Selector 只能从下表登记的 family × intensity 候选中选择；LLM 可以填充当前 decision object、同链证据和文本内容，但不能临时创造新的交互类型、按钮语义或评测维度。

| `strategy_family` | 交互模板 | 主要 C/S/A 支持维度 | 固定交付物 | 用户可执行动作 |
|---|---|---|---|---|
| `STATE_CONTEXT_RECOVERY` | `PROJECT_STATUS_BRIEF_V1` 项目状态简报 | State + Criteria | 当前任务、最近 3–5 个关键动作、影响范围、异常信号、时间线、轻量影响图 | `GENERATE_CONFIRMATION_PROMPT`、`VIEW_CHANGES_ONLY`、`RETURN_TO_PRIOR_CHAT` |
| `RULE_CLARIFICATION` | `RULE_CLARIFIER_V1` 规则澄清器 | Criteria + Action | 不满语句、抽取规则、保护项、验收标准、可编辑规则表 | `CONVERT_TO_RULE`、`ADD_PROTECTED_ITEM`、`ADD_ACCEPTANCE_CRITERIA`、`EDIT_RULE` |
| `CLAIM_EVIDENCE_CALIBRATION` | `DECLARATION_EVIDENCE_MATRIX_V1` 声明—证据矩阵 | State（Evidence 作为证据槽位，不是 C/S/A 维度） | Agent 声明、当前证据、证据实际证明的内容、未证明项、待验证项 | `REQUEST_RUN_OR_TEST`、`LIST_MODIFIED_FILES`、`LIST_IMPACT_SCOPE`、`REQUEST_MISSING_EVIDENCE`、`MARK_VERIFIED` |
| `GOVERNANCE_ACTION_PLANNING` | `NEXT_STEP_GOVERNANCE_CENTER_V1` 下一步治理中心 | Action + Criteria | 当前问题、下一步期望、允许继续与否、分阶段治理计划、风险控制动作、回退条件 | `PAUSE_AND_EXPLAIN`、`EVIDENCE_ONLY`、`ALLOW_NARROW_CONTINUATION`、`RETURN_TO_PRIOR_STEP`、`GENERATE_GOVERNANCE_PROMPT` |

候选 ID 使用固定命名：`{strategy_family}_L1`、`{strategy_family}_L2`、`{strategy_family}_L3`。每个 family 的强度可以有不同五维参数，但同一 family 的模板和动作枚举不随强度改变。单次 exposure 只能绑定一个 family；`PRESENT_CHOICES` 最多展示两个不同 family，不能把四类面板拼成一个无边界的综合干预。

最小 Registry 条目如下：

```json
{
  "strategy_id": "CLAIM_EVIDENCE_CALIBRATION_L2",
  "strategy_family": "CLAIM_EVIDENCE_CALIBRATION",
  "intensity": "L2",
  "template_id": "DECLARATION_EVIDENCE_MATRIX_V1",
  "allowed_action_codes": ["REQUEST_RUN_OR_TEST", "LIST_MODIFIED_FILES", "LIST_IMPACT_SCOPE", "REQUEST_MISSING_EVIDENCE", "MARK_VERIFIED"],
  "parameters": {"criteria": 0.20, "state": 0.70, "action": 0.20, "evidence": 0.90, "workflow": 0.60},
  "registry_status": "APPROVED"
}
```

四类模板的标题、字段、动作和 evidence slot 属于版本化 UI/策略契约；改变其中任一项必须提升 `template_version` 或 `registry_version`，不能在运行中静默替换。

声明—证据矩阵中的 `E0/E1` 是针对某条 Agent claim 的证据状态显示；它不等于 Selector 的 `E_c` 能力参数，也不增加一个用户心理维度。

### 6.2.1 上游语义提示与硬约束

Selector 不仅接收数值化 C/S/A，还接收由上游 Prompt/Support Profile 生成的结构化 selector_hint。该提示必须来自当前用户轮次、同链证据和冻结的 decision object，不能只由关键词或 episode 总结猜测：

~~~json
{
  "support_family": "CLAIM_EVIDENCE_CALIBRATION",
  "allowed_families": ["CLAIM_EVIDENCE_CALIBRATION"],
  "confidence": "HIGH",
  "max_intensity": 2,
  "cognitive_gap_detected": true,
  "execution_request_detected": false,
  "reason": "用户用可复核的观察反驳 Agent 声明",
  "evidence_ids": ["CRE-0003::T004::M001"]
}
~~~

四类 family 的默认路由约束为：

| 语义信号 | 首选 family |
|---|---|
| 当前症状、历史版本、项目状态需要重建 | STATE_CONTEXT_RECOVERY |
| 反证、数据源、Agent 声明需要核验 | CLAIM_EVIDENCE_CALIBRATION |
| 阈值、条件、业务规则或验收标准需要澄清 | RULE_CLARIFICATION |
| 修改边界、验证顺序、回退条件需要安排 | GOVERNANCE_ACTION_PLANNING |

`max_intensity` 是强度硬上限。Family routing 先按当前轮次的主要认知任务判断：追问为什么/怎么算/依据/差异或完成证据时优先 `CLAIM_EVIDENCE_CALIBRATION`；命名、分层、复制关系、条件、边界和验收规则优先 `RULE_CLARIFICATION`；整理、设计、计划、归档、回退和下一步安排优先 `GOVERNANCE_ACTION_PLANNING`；不理解、状态不明或怀疑卡死才优先 `STATE_CONTEXT_RECOVERY`。普通执行、直接查看文件和简短确认不自动构成认知缺口。

MEDIUM family hint 只作为软排序偏好，不排除其他 family；只有 HIGH 且 `evidence_ids` 能回链到当前 chain 或当前 C/S/A 观测时，才启用 family 硬门槛。Selector 不改变 Registry 中候选的固有能力，也不把语义提示当成新的 C/S/A 维度。用户“要求 Agent 执行任务”与“存在认知缺口”分别由 `execution_request_detected` 和 `cognitive_gap_detected` 表达；前者为 true 不会自动抑制干预。

### 6.3 正式 MVP 参数（Pilot Freeze）

正式 MVP 使用以下 Registry 和 Selector config：

```text
retrace_selector/config/strategy_registry.formal.v1.json
retrace_selector/config/selection_policy.formal.v1.json
```

下表中的元组顺序固定为 `(C, S, A, E, W)`。这些值是基于四类交互的职责边界、强度单调性和工作流代价做出的正式初始配置；它们已经可以用于固定回放和 Selector POC，但不是干预效果的因果估计。`registry_status=APPROVED` 仅表示实现配置已冻结，不表示效果已经被真实参与者实验验证。

| `strategy_family` | L1 | L2 | L3 |
|---|---|---|---|
| `STATE_CONTEXT_RECOVERY` | `(0.20, 0.45, 0.10, 0.72, 0.92)` | `(0.30, 0.70, 0.15, 0.84, 0.82)` | `(0.40, 0.88, 0.20, 0.92, 0.68)` |
| `RULE_CLARIFICATION` | `(0.45, 0.10, 0.20, 0.72, 0.92)` | `(0.68, 0.16, 0.34, 0.84, 0.82)` | `(0.86, 0.24, 0.48, 0.92, 0.68)` |
| `CLAIM_EVIDENCE_CALIBRATION` | `(0.12, 0.38, 0.10, 0.82, 0.92)` | `(0.20, 0.68, 0.18, 0.92, 0.82)` | `(0.28, 0.86, 0.25, 0.97, 0.68)` |
| `GOVERNANCE_ACTION_PLANNING` | `(0.25, 0.12, 0.38, 0.76, 0.92)` | `(0.45, 0.18, 0.70, 0.88, 0.82)` | `(0.65, 0.28, 0.90, 0.95, 0.68)` |

正式对照全局参数固定为 `β=0.75`、`η=0.05`、`ε=0.03`、`evidence_floor_when_limited=0.60`、`τ=300s`、`λW=0.05`、`semantic_hint_soft_margin=0`、`same_chain_cooldown_seconds=300`。这些仍是可运行的 Pilot 参数，不是经过参与者行为校准的正式实验参数。正式 Registry 包含 12 个候选，候选 ID 使用 `FAMILY_L1/L2/L3`。POC pilot 在同一 Registry 上把 `ε` 调到 `0.005`，并把 `semantic_hint_soft_margin` 调到 `0.12`；该 margin 只影响 MEDIUM hint 的候选排序，不删除候选，也不改变 `PRESENT_CHOICES` 的 epsilon 平局判定。

### 6.4 参数从哪里来

第一阶段由交互设计团队和研究团队根据策略内容人工标定。策略类型尚未确定时，可以使用测试 Registry 完成接口和算法验证；正式实验前替换为真实策略参数。

有真实干预数据后，离线分析可以校准参数，但同一次实验运行中 Registry 版本保持冻结，不能在线随结果临时修改。

---

## 7. 运行时五维向量

候选的 C/S/A/E 是 Registry 中的固定能力。`E_c` 表示候选本身能否提供有同链证据锚定、可回指的支持内容，不是用户当前状态的第四个目标维度。当前证据的 `evidence_ids` 只作为审计和模板填充依据；`assessability=LIMITED` 时，Selector 使用全局 `evidence_floor_when_limited` 做候选资格检查，不把证据质量重复乘到 C/S/A 能力上。Workflow 根据近期真实 exposure 动态更新：

\[
B(t)=\sum_{e\in ExposureHistory,\ e.chain=session}
       \exp\left(-\frac{t-t_e}{\tau}\right)
\]

\[
W(c,t)=\max(0,W_c-\lambda_W B(t))
\]

其中只有实际 INTERVENTION_EXPOSURE 才进入 ExposureHistory；推荐、Selector 重算和 UI 轮询不增加负担。MVP 固定 τ 和 λW 到 Selector config，并在实验运行期间冻结。

因此当前候选向量为：

\[
\mathbf x(c,t)=
(C_c,S_c,A_c,E_c,W(c,t))
\]

当前状态不会重写策略的 C/S/A/E 固有能力。状态通过当前—目标差距和近期打断改变候选的实际比较结果。

`strategy_family` 是候选的行为语义身份，不是第五维参数。它决定 UI 使用哪一个固定模板、允许哪些 `INTERVENTION_ACTION`，也决定离线分析按哪一类干预解释结果；Selector 不能仅依据五维向量把一个 family 渲染成另一个 family。

---

## 8. Skyline 筛选

将所有已注册、当前可渲染且通过证据资格检查的干预候选加入集合 \(F_t\)，并显式加入不干预候选：

\[
\mathbf x_0=(0,0,0,0,1)
\]

不干预不提供 Criteria/State/Action 支持，也不提供证据锚定内容，但不产生新的工作流负担；因此它的 \(E_0=0\)，不能被解释为“证据支撑完美”。

如果候选 \(a\) 在五维上均不差于 \(b\)，且至少一维更好，则 \(a\) 支配 \(b\)。保留非支配候选：

\[
P_t=Skyline(F_t\cup\{c_0\})
\]

Skyline 只删除全面更差的候选，不直接决定唯一最佳策略。它不是行为到策略的规则映射。

---

## 9. 当前到目标的残余差距

### 9.1 当前缺口

将 0–3 等级归一化，得到当前目标缺口：

\[
g_j(t)=
\max\left(0,\frac{z_j^*(t)-z_j(t)}{3}\right),
\quad j\in\{C,S,A\}
\]

其中 \(z_j(t)\) 是当前状态，\(z_j^*(t)\) 是 Occasion 对应目标状态。

当某一维为 `UNKNOWN` 时，本次计算暂时忽略该维，并对剩余已知维度重新求平均；三维全部 `UNKNOWN` 时继续观察，不进行策略选择。`TARGET_REACHED` 是更严格的状态：只有 Criteria、State、Action 三维全部有 level、每维 `assessability=SUFFICIENT`，且每个维度都有来自当前用户轮次、同链事件或系统事件的语义 `evidence_ref`（该 ref 必须明确 `supports_dimensions` 命中该维度），并且三维缺口都为 0 时才允许输出。后验 `OUTCOME_ANNOTATION` evidence 不属于在线证据源，不能参与该判断。

### 9.2 候选的有效支持

候选实施后仍未覆盖的 C/S/A 目标差距为：

\[
d_j(c,t)=\max(0,g_j(t)-a_j(c))
\]

E_c 不进入当前目标差距的欧氏距离。它在 Skyline 阶段用于删除同等或更差的证据锚定候选，在近似平局时作为质量优先级，并在 assessability=LIMITED 时受全局 evidence floor 约束。这样不会把“策略是否有证据”错误地当成用户 C/S/A 的第四个心理维度。

这些计算表示策略设计上能够覆盖多少当前缺口，不解释为已经证明会使用户状态产生相同幅度的因果变化。

### 9.3 客观残余差距

设 \(K_t\) 为当前可判断的 C/S/A 维度集合：

\[
D_{obj}(c,t)=
\sqrt{
\frac{
\sum_{j\in K_t}d_j(c,t)^2
}{|K_t|}
}
\]

仅当 K_t 非空时计算该距离；三维都为 UNKNOWN 或没有目标维度时继续观察。数值越小，表示该候选对当前 C/S/A 目标缺口的覆盖越完整。

### 9.4 用户侧负担

\[
D_{user}(c,t)=1-W(c,t)
\]

对不干预候选固定定义：

~~~text
D_obj(c0,t) = sqrt(sum(g_j(t)^2) / |K_t|)
D_user(c0,t) = 0
~~~

数值越小，表示对当前工作流的打断越小。

---

## 10. 最终优化与是否干预

对 Skyline 候选使用一个统一目标函数：

\[
L(c,t)=D_{obj}(c,t)+\beta D_{user}(c,t)
\]

先在 P_t 中计算干预候选和不干预候选的损失。设 c1 为损失最低的干预候选，c2 为不同策略路径中损失第二低的候选，η 为最小改善幅度，ε 为近似平局容差：

~~~text
L(c1) >= L(c0) - η
→ NO_INTERVENTION

L(c1) < L(c0) - η
且不存在 c2，或 L(c2) > L(c1) + ε
→ INTERVENE(c1)

L(c1) < L(c0) - η
且 L(c2) <= L(c1) + ε
且 strategy_family(c1) != strategy_family(c2)
→ PRESENT_CHOICES(c1, c2)
~~~

c0 不作为用户可选择的第二条支持路径；如果两个路径都没有超过不干预的最小改善幅度，仍然输出 NO_INTERVENTION。

`PRESENT_CHOICES` 的两个选项必须来自不同 `strategy_family`，并分别保留自己的 `strategy_id`、`template_id` 和动作枚举；用户的单选结果不会把两个 family 合并成新的候选。

 \(\beta\) 是整个 Selector 共用的目标改善—工作流负担交换参数，不为每条策略单独配置。当前 Pilot Freeze 暂用 `β=0.75`，但该值没有行为数据校准依据。参数敏感性审计显示，在固定 target `(2,3,2)` 的 64 个合成状态上，`β=0.50/0.75/1.00` 的决策分布分别为 `26/34/4`、`29/31/4`、`29/31/4`（顺序为 `INTERVENE/PRESENT_CHOICES/NO_INTERVENTION`）；因此不能再声称 `β=1.0` 会使 L3 不可达，也不能把 `β=0.75` 称为正式校准值。真实 pilot 后只能通过新版本 Registry/Policy 调整。

MVP Selector config 至少冻结以下全局参数：

```json
{
  "beta": 0.75,
  "eta": 0.05,
  "epsilon": 0.03,
  "evidence_floor_when_limited": 0.60,
  "workflow_decay_tau_seconds": 300,
  "workflow_exposure_lambda": 0.05,
  "semantic_hint_soft_margin": 0.0,
  "same_chain_cooldown_seconds": 300,
  "long_no_response_seconds": 900,
  "selection_rule_version": "SELECTOR-V2-FORMAL-3-SEMANTIC-SOFT-GATE",
  "semantic_hint_min_confidence": "MEDIUM",
  "enforce_family_gate": true,
  "enforce_intensity_cap": true
}
```

参数敏感性回放的正式对照仍保留 `epsilon=0.03`；在同一批 20 个冻结 Episode 上，POC
pilot candidate 使用 `epsilon=0.005`、`semantic_hint_soft_margin=0.12`，其余 `beta`、`eta`、workflow lambda 和 tau
保持不变。`epsilon` 只控制不同 family 的近似平局是否进入 `PRESENT_CHOICES`；soft margin
只在 MEDIUM hint 下把语义更匹配的 family 提到排序前面，并在该排序已解决选择时抑制由重排制造的双选项。两者都不是正式行为校准；pilot 与 formal 结果必须分开记录。

当任一参与计算的维度为 `assessability=LIMITED` 时，只有 \(E_c\ge evidence\_floor\_when\_limited\) 的候选进入干预集合；如果没有候选满足条件，则输出 `NO_INTERVENTION` 并记录 `INSUFFICIENT_EVIDENCE`。三维全部 `UNKNOWN` 时仍然只观察，不进入 Selector。

在证据资格检查之后，Selector 再应用 selector_hint 的 family gate、强度上限和认知缺口判断。这一步只会在 HIGH+语义支持有效的证据时缩小候选集合；MEDIUM 只按 `semantic_hint_soft_margin` 影响候选排序，不排除其他 family；如果 soft preference 已经解决了 family 顺序，则不再用重排后的近邻候选制造新的 `PRESENT_CHOICES`。`epsilon` 仍只用于原始候选 frontier 的近似平局判定。这样 family 语义路由、双选项阈值和强度上限可以分别审计。真实 exposure 会更新 workflow burden；cooldown 绑定到当前 decision-chain 中实际曝光的 candidate ID（双选项在用户分叉后只绑定实际选中的 candidate ID），不会阻塞同一 chain 的其他候选。用户响应或干预动作后解除已曝光候选的 cooldown；旧 exposure 没有 candidate ID 时才回退为 chain 级兼容模式。曝光后超过 `long_no_response_seconds` 仍无用户响应时，Selector 输出 `NO_RESPONSE_TIMEOUT`，不继续重复推送。

NO_INTERVENTION 必须记录原因，不再把不同情况合并：`TARGET_REACHED`、`BELOW_ETA`、`INTERVENTION_NOT_ELIGIBLE`、`INSUFFICIENT_EVIDENCE`、`UNKNOWN_STATE`、`NO_ELIGIBLE_CANDIDATE`、`COOLDOWN_ACTIVE` 和 `NO_RESPONSE_TIMEOUT`。

### 10.1 不干预

不干预仍作为 Skyline 的基准候选，但其语义是“零支持、零新增负担”：

```text
capability = (0,0,0)
evidence   = 0
workflow   = 1
```

它不需要证据锚定，因为系统没有向用户作出新的支持性陈述。当前已经接近目标时，不干预可以胜出；某策略只有在实际缩小 C/S/A 缺口、且改善超过 η 后才会胜出。不需要额外计算 Gain，也不需要固定斜率边界或 max-gap 惩罚。

### 10.2 平局

- 同一策略不同强度近似相同时，选择工作流连续性更高的低强度候选；
- 不干预与干预近似相同时，选择不干预；
- 两个不同策略路径近似相同时，最多展示两个选项，由用户决定；
- 仍需单选时，依次按较低 L、较高 W、较高 E、较低强度排序。

epsilon、eta、evidence_floor_when_limited、τ、λW、semantic_hint_soft_margin 和 tie-break 规则属于全局 Selector config，不属于单条策略参数；同一 config、Registry 和输入必须产生相同结果。

### 10.3 用户可调策略与三维结果自适应

Selector 的全局参数、Registry 和证据约束仍然冻结；用户可以通过 ReTrace 面板调整自己的干预偏好。该偏好不是新的策略类型，也不允许用户绕过证据、family gate、`TARGET_REACHED` 或 `PRESENT_CHOICES` 规则。

面板提供三个操作点：

| 控件 | 范围 | 运行时含义 |
|---|---:|---|
| 干预频率 | `0.0–1.0` | 影响最小改善容差 `eta` 和同链 cooldown；值越高，越容易在小缺口下得到支持，且 cooldown 越短 |
| 干预力度 | `0.0–1.0` | 映射为最高允许强度：`[0, 1/3)`=`L1`、`[1/3, 2/3)`=`L2`、`[2/3, 1]`=`L3` |
| 零干预模式 | `AUTO` / `PAUSED` | `PAUSED` 优先输出 `NO_INTERVENTION(USER_PAUSED)`，即使当前状态未知或已达到目标；不删除事件、不改变历史选择；UI 可将其显示为“零干预模式” |

用户调整通过 `set_user_preferences` 保存为版本化 `subjective_preference`，并写入 `POLICY_PREFERENCE_UPDATED`。在线服务在 `online_user_profiles` 中持久化三层用户干预 Profile，并保留 `online_user_preferences` 作为兼容性的当前生效策略索引：`subjective_preference` 是用户主动表达的频率、力度、AUTO/PAUSED 和手动锁定；`assessed_need` 是由完整 C/S/A baseline→POST、反馈和负担推断出的需要信号；`effective_policy` 是当前真正交给 Selector 的策略。用户主动设置会更新 subjective/effective 两层，但不会删除既有 assessed_need；Adaptive Controller 更新 assessed_need 和 effective_policy，不覆盖 subjective_preference。频率只在有限范围内调整运行时 `eta` 与 cooldown；力度只增加候选强度上限。`beta`、`epsilon`、evidence floor、候选五维能力、语义 family gate 和历史 decision/exposure 均不被滑块改写。每次 Selector 输出都带有 `user_preference_version`，便于离线按当时偏好回放。

三道场景化 C/S/A 题的结果用于学习用户需要的支持程度，但不直接修改当前状态，也不读取后验 outcome annotation。每次 chain 结束并提交 `POST_EVALUATION` 后，Adaptive Controller 按以下条件工作：

1. 必须同时有完整的 `OCCASION_BASELINE` 和 `POST_EVALUATION` 三维回答；跳过或缺失任一维度时记录 `INCOMPLETE_PRE/POST`，不更新参数。
2. Likert `1–5` 回答先归一化到冻结的 `0–3` 目标量表；已有 `0–3` 回放输入可以显式按原量表传入。
3. “完成 1 个 chain”严格指该 chain 已关闭，且同时存在同一用户、同一 chain 的完整 `OCCASION_BASELINE` 与 `POST_EVALUATION`；三维均已回答、无跳过。post-only、baseline-only、重复提交和未关闭 chain 均不计入历史。至少完成 3 个这样的 chain 后才开始更新；频率和力度分别限制在每轮 `[-0.08, +0.08]`，并各自截断在 `[0,1]`。
4. 残余三维缺口越大，后续支持偏好越高；观察到的进步、用户 dismiss/ignore 和干预负担会抵消该增幅。用户打开“手动锁定”后不再自动更新。
5. 更新写入 `ADAPTATION_UPDATE` 事件，并把本次 `csa_gap_after`、`csa_progress`、反馈、负担和历史数量写入 `assessed_need`；只影响后续 Selector 调用。Registry、选择规则、已保存的选择和已发生的 exposure 保持不可变。

POC 的可复现更新式为：

~~~text
gap_after = mean(max(0, target_d - post_d) / 3)
progress  = mean((post_d - pre_d) / 3)
max_progress = max(0, progress)
frequency_delta = clip(0.08 * (0.5 * gap_after - 0.5 * max_progress)
                       + feedback_adjustment - 0.04 * burden, -0.08, +0.08)
intensity_delta = clip(0.08 * (gap_after - max_progress)
                       + 0.5 * feedback_adjustment - 0.02 * burden, -0.08, +0.08)
frequency_next = clip(frequency + frequency_delta, 0, 1)
intensity_next = clip(intensity + intensity_delta, 0, 1)
~~~

其中 `feedback_adjustment` 为 `ACCEPTED=+0.02`、`DISMISSED=-0.04`、`IGNORED=-0.02`、`UNSPECIFIED=0`。`0.08`、`0.04`、`0.02`、`0.5` 和“至少 3 个完整 chain”是当前 POC 的冻结起始值，不应描述为已由参与者数据校准的正式参数；正式实验需在不读取后验 outcome 的前提下另行预注册和校准。

Likert 输入必须是整数 `1–5`；`0`、`6`、小数、布尔值和非有限数值直接拒绝，不做静默截断。显式的冻结 `0–3` 回放输入仍可在 Controller 层按原量表传入。

核心接口：

~~~text
get_user_preferences(user_id)
  → { user_id, frequency_preference, intensity_preference, mode, version, manual_lock }

get_user_profile(user_id)
  → { user_id, version,
       subjective_preference,
       assessed_need,
       effective_policy }

set_user_preferences(user_id, frequency_preference, intensity_preference,
                     mode, manual_lock)
  → { event_id, accepted, preference }

submit_evaluation(..., intervention_feedback, burden_score)
  → { post_snapshot_id, adaptation: { update_id, changed, reason, preference, metrics } }

get_chain_outcome_linkage(chain_id)
  → { policy_preference: <preference_used_for_selection>,
       current_policy_preference: <latest_persisted_preference>,
       adaptation_preference: <preference_written_by_latest_adaptation_or_null>,
       subjective_preference,
       assessed_need,
       effective_policy,
       user_profile_version }
~~~

自适应的 `reason` 至少包括 `INSUFFICIENT_HISTORY`、`INCOMPLETE_PRE`、`INCOMPLETE_POST`、`INCOMPLETE_TARGET`、`MANUAL_LOCK` 和 `UPDATED`。这些 reason 用于审计“为什么本次没有学习”，不能被当作干预效果标签。在线系统仍不读取 `governance_outcome_ref`、`functional_outcome_ref` 或 outcome annotation 的后验 evidence。

---

## 11. Selector 输出

```json
{
  "decision_id": "SEL-0018",
  "chain_id": "PROJECT-01::OCC-004::FD-02",
  "focal_decision_id": "FD-02",
  "decision_object_profile_id": "FD-PROFILE-02",
  "claim_ids": ["CLAIM-FD02-01"],
  "snapshot_event_id": "EVT-0045",
  "as_of_event_id": "EVT-0045",
  "snapshot_kind": "LIVE",
  "decision": "INTERVENE",
  "selected": ["STATE_CONTEXT_RECOVERY_L2"],
  "strategy_family": "STATE_CONTEXT_RECOVERY",
  "interaction_type": "PROJECT_STATUS_BRIEF",
  "render_template_id": "PROJECT_STATUS_BRIEF_V1",
  "allowed_action_codes": ["GENERATE_CONFIRMATION_PROMPT", "VIEW_CHANGES_ONLY", "RETURN_TO_PRIOR_CHAT"],
  "render_payload_ref": "PAYLOAD-SEL-0018",
  "current_state": {"criteria": 1, "state": 1, "action": 2},
  "target_state": {"criteria": 2, "state": 3, "action": 2},
  "objective": {
    "d_obj": 0.18,
    "d_user": 0.25,
    "beta": 0.75,
    "loss": 0.43
  },
  "evidence_ids": ["EVID-0141", "EVID-0142"],
  "registry_version": "STRATEGY-REGISTRY-V1",
  "selection_rule_version": "SELECTOR-V2"
}
```

Renderer 只根据选中的候选 ID 加载交互设计模板，并填入当前 decision object 和允许显示的同链证据。Selector 不自由生成新的策略类型。

当 `decision=PRESENT_CHOICES` 时，输出改用 `options` 数组（长度为 2），每个 option 各自携带 `strategy_id`、`strategy_family`、`template_id`、`allowed_action_codes`、`branch_condition_code`、`branch_condition` 和对应 evidence refs；不要用一个 singular `strategy_family` 字段覆盖两个候选。输出同时带 `choice_contract.type=EXPLICIT_USER_BRANCH`，要求 UI 在 exposure 前提交 `selected_candidate_id`、匹配该 option 的 `choice_condition` 和用户依据 `choice_basis`。未提交分叉条件、提交了不属于该 option 的条件，或跨 option 曝光，均拒绝写入 exposure。

### 11.1 固定渲染与动作契约

```text
render_intervention(selection_decision_id, chain_id, as_of_event_id)
  → {
       strategy_id,
       strategy_family,
       template_id,
       payload,
       allowed_action_codes,
       evidence_refs,
       render_version
     }
```

`payload` 只能来自当前 frozen chain、允许的同链 evidence refs 和模板字段。UI 产生动作时必须回传 `strategy_family`、`template_id`、`action_code`、`selection_decision_id` 和 `as_of_event_id`；非法动作或跨 family 动作返回 `ACTION_NOT_ALLOWED`，不写入研究结果。四类面板可以共用面板容器，但不能共用未区分的“确认/继续”按钮语义。

双选项必须先记录用户分叉，再记录 exposure：

```text
record_choice(chain_id, selection_decision_id, selected_candidate_id,
              choice_condition, choice_basis)
  → USER_RESPONSE(response_kind=CHOICE_SELECTION)
```

---

## 12. 动态更新

Occasion 确认后，每个新的同链事件都可能改变当前状态、证据或工作流连续性：

```text
新事件
  ↓
更新当前 C/S/A、recent_exposure_burden
  ↓
重算 W(c,t) 与 evidence eligibility
  ↓
Skyline
  ↓
重算 D_obj、D_user 和 L
  ↓
输出当前选择
```

系统只在选择结果发生变化或需要展示新干预时更新 UI。未发生真实 exposure 时，不增加近期 exposure burden。

如果新事件在已有 snapshot 之后才到达，先写入 LATE_EVENT 审计记录，再按 apply_late_event 生成新的 LATE_RECOMPUTE snapshot。历史 Selector decision、真实 exposure 和 PRE/POST 快照保持不可变。真实 exposure 后立即冻结 PRE；没有 exposure 的 chain 在 Selector 决策点写入 pseudo-cutoff。

---

## 13. 互动结束与三维评测

### 13.1 生命周期

在线 chain 保留基线评测、观察和结果评测状态：

```text
ACTIVE
  ↓ OCCASION_CONFIRMED
BASELINE_EVALUATION_PENDING
  ↓ answered / skipped / timeout
OBSERVING
  ↓ interaction ends
EVALUATION_PENDING
  ↓ POST_EVALUATION answered / skipped
CLOSED
```

### 13.2 Occasion 后进入基线评测

当 Occasion 已确认、decision object 和 target profile 已冻结时，立即进入 BASELINE_EVALUATION_PENDING。系统向用户呈现当前局部项目情境下的 Criteria、State、Action 三道问题，并通过 submit_occasion_baseline 保存一次完整的 OCCASION_BASELINE 记录。

基线评测可以在用户逐题跳过或超时后结束；此时保存缺失维度和 BASELINE_MISSED（若在 exposure 前未完成），然后继续 OBSERVING。事件采集、Occasion chain 和 target profile 不因用户跳过而丢失。Selector 不得把基线 Likert 分数直接当成 0–3 的 current state level。

### 13.3 进入结果评测

满足以下条件时进入 `EVALUATION_PENDING`：

```text
用户刚完成一次验证、修正、授权或行动边界
+
Agent 或工具已经返回结果
+
短窗口内没有新的同链行为
```

如果用户继续讨论同一问题，恢复 `ACTIVE`；如果转向新的 decision object，关闭旧 chain，并为新对象单独判断 Occasion。

### 13.4 场景化问题

本节定义的是互动结束后的 POST_EVALUATION。它与 4.4 的 OCCASION_BASELINE 使用同一 C/S/A 三维结构和题型规范，但必须通过 evaluation_kind、measurement_point 和 evaluation_id 区分，不能把基线回答覆盖成结果回答。两者的问题都必须针对当前局部项目情境生成，而不是直接询问“你是否理解”。

每题使用预先冻结的 Likert 量表。用户可以全部回答、部分回答或跳过。系统保存：

```json
{
  "evaluation_id": "EVAL-POST-001",
  "evaluation_kind": "POST_EVALUATION",
  "measurement_point": "POST",
  "chain_id": "PROJECT-01::OCC-004::FD-02",
  "questions": {
    "criteria": "QUESTION-C",
    "state": "QUESTION-S",
    "action": "QUESTION-A"
  },
  "responses": {
    "criteria": 4,
    "state": 3,
    "action": null
  },
  "skipped": false,
  "scale_version": "LIKERT-V1"
}
```

跳过保存为缺失，不能按低分处理。评测回答不回填已经发生的 Selector 选择。

### 13.5 PRE/POST 快照接口

在线系统为每个 chain 提供三类测量快照：

| measurement_point | 定义 |
|---|---|
| PRE | 第一次真实 exposure 之前最后一个稳定快照；无干预时使用预定义 pseudo-cutoff |
| POST | 干预窗口结束后、短窗口内没有新的同链行为时的首个稳定快照 |
| CLOSE | chain 关闭时的最终快照，可选 |

接口：

~~~text
capture_measurement_snapshot(
  chain_id,
  measurement_point,
  as_of_event_id,
  trigger_event_id,
  reason
)
  → {
       snapshot_id,
       chain_id,
       measurement_point,
       as_of_event_id,
       current_state,
       target_state,
       rubric_version,
       immutable: true
     }

get_measurement_snapshot_pair(chain_id)
  → {
       pre: Snapshot | null,
       post: Snapshot | null,
       close: Snapshot | null
     }
~~~

PRE、POST、CLOSE 都必须绑定同一 chain_id、target profile 和 C/S/A rubric。后续 late event 只能新增 revision，不得修改原始测量点；离线分析按 as_of_event_id 回放。

---

## 14. 最小存储与审计

每次运行只需完整保存：

- 原始事件与 evidence refs；
- Occasion、chain 和目标 C/S/A；
- 每次当前 C/S/A 快照；
- 使用的策略五维参数和 Registry 版本；
- strategy family、渲染模板、允许动作以及实际 `action_code`；
- Skyline 候选、\(D_{obj}\)、\(D_{user}\)、\(L\) 与最终选择；
- 干预是否真实展示以及用户选择、关闭或跳过；
- Occasion 后的 OCCASION_BASELINE、互动结束后的 POST_EVALUATION、每题原始回答和跳过记录。
- 用户版本化偏好、`POLICY_PREFERENCE_UPDATED` 和 `ADAPTATION_UPDATE`，以及更新使用的三维分数、反馈和负担指标。

这些记录足以回放在线决策。插件运行错误和连接恢复由插件技术方案负责，不在在线推理方案中重复展开。

### 14.1 离线结果关联

在线系统只负责提供稳定的连接键、测量快照和 exposure 引用，不在运行时计算或消费 R/E/B、DGR/BGR/OGCR 或 Functional Outcome。离线分析通过以下 envelope 把在线记录与 outcome artifact 关联：

~~~json
{
  "chain_id": "PROJECT-01::OCC-004::FD-02",
  "occasion_id": "OCC-004",
  "focal_decision_id": "FD-02",
  "decision_object_profile_id": "FD-PROFILE-02",
  "claim_ids": ["CLAIM-FD02-01"],
  "csa_measurements": {
    "occasion_baseline_evaluation_id": "EVAL-BASE-001",
    "pre_snapshot_id": "SNAP-PRE-001",
    "post_snapshot_id": "SNAP-POST-001",
    "close_snapshot_id": "SNAP-CLOSE-001"
  },
  "exposure": {
    "selection_decision_id": "SEL-0018",
    "strategy_id": "STATE_CONTEXT_RECOVERY_L2",
    "strategy_family": "STATE_CONTEXT_RECOVERY",
    "template_id": "PROJECT_STATUS_BRIEF_V1",
    "action_codes": ["GENERATE_CONFIRMATION_PROMPT"],
    "exposure_id": "EXP-0018",
    "exposure_timestamp": "2026-08-24T10:32:15+08:00"
  },
  "policy_preference": {
    "version": "PREF-7d8e",
    "mode": "AUTO",
    "frequency_preference": 0.65,
    "intensity_preference": 0.40
  },
  "preference_used_for_selection": {
    "version": "PREF-7d8e",
    "mode": "AUTO",
    "frequency_preference": 0.65,
    "intensity_preference": 0.40
  },
  "current_policy_preference": {
    "version": "PREF-a91c",
    "mode": "AUTO",
    "frequency_preference": 0.68,
    "intensity_preference": 0.46
  },
  "adaptation_preference": {
    "version": "PREF-a91c",
    "mode": "AUTO",
    "frequency_preference": 0.68,
    "intensity_preference": 0.46
  },
  "user_profile_version": "PROFILE-2a91",
  "subjective_preference": {
    "version": "PREF-7d8e",
    "mode": "AUTO",
    "frequency_preference": 0.65,
    "intensity_preference": 0.40
  },
  "assessed_need": {
    "frequency_need": 0.31,
    "intensity_need": 0.62,
    "csa_gap_after": 0.62,
    "csa_progress": 0.08,
    "completed_chain_count": 4,
    "last_reason": "UPDATED",
    "last_update_id": "ADAPT-0031"
  },
  "effective_policy": {
    "version": "PREF-a91c",
    "mode": "AUTO",
    "frequency_preference": 0.68,
    "intensity_preference": 0.46
  },
  "adaptation_update_id": "ADAPT-0031",
  "governance_outcome_ref": null,
  "functional_outcome_ref": null,
  "as_of_event_id": "EVT-0055",
  "linkage_status": "READY_FOR_OFFLINE_LINKAGE"
}
~~~

接口：

~~~text
get_chain_outcome_linkage(chain_id, as_of_event_id)
  → {
       chain_id,
       focal_decision_id,
       claim_ids,
       csa_measurements,
       exposure,
       strategy_id,
       strategy_family,
       template_id,
       action_codes,
       policy_preference,
       preference_used_for_selection,
       current_policy_preference,
       adaptation_preference,
       user_profile_version,
       subjective_preference,
       assessed_need,
       effective_policy,
       adaptation_update_id,
       governance_outcome_ref: null,
       functional_outcome_ref: null,
       linkage_status
     }
~~~

`governance_outcome_ref` 和 `functional_outcome_ref` 由离线导入/审议流程填充；在线 Selector 不读取这两个字段，也不会因为离线 outcome 改变历史选择。

---

## 15. 实施顺序

### M1：事件与状态接口

- 接入插件规范化事件；
- 实现 Occasion 三项判断和 chain 冻结；
- 输出目标与当前 C/S/A，包括 `UNKNOWN` 和 evidence IDs。

### M2：固定离线回放

- 固定 decision-object profile、C/S/A rubric、Registry 和 `as_of_event_id`；
- 用同一 trace 验证 chain、目标、当前状态和 measurement input 可重复；
- 将 `CHAIN_UNASSESSABLE`、`UNKNOWN` 和 late-event revision 纳入回放基线。
- 使用 `retrace_selector/tools/run_formal_registry_experiment.py` 对 12 个候选运行 canonical cases 和两个 64 格点 C/S/A 网格；该实验只验证参数几何、family 可达性和双选项契约，不作为干预效果证据。
- 当前结果写入 `retrace_selector/artifacts/formal_registry_v1_experiment.json`：默认 target `(2,3,2)` 网格产生 `NO_INTERVENTION=4`、`INTERVENE=29`、`PRESENT_CHOICES=31`；四类 family 和 L3 均可达，双选项始终为两个不同 family，未知三维状态回退为 `NO_INTERVENTION`。

### M3：策略 Registry 与 Selector

- 冻结 `STATE_CONTEXT_RECOVERY`、`RULE_CLARIFICATION`、`CLAIM_EVIDENCE_CALIBRATION`、`GOVERNANCE_ACTION_PLANNING` 四类策略族、模板 ID 和动作枚举；
- 建立“策略族 × 强度”的五维参数表；
- 使用测试候选完成读取、版本和校验；
- 实现运行时 W 更新、五维 Skyline、`D_obj + βD_user` 和固定 `NO_INTERVENTION`/`PRESENT_CHOICES` 规则；
- 用固定回放结果作为 Selector 回归基线。

### M4：测量接口

- 接入 `submit_occasion_baseline`、`USER_RESPONSE`、真实 exposure、PRE/POST/CLOSE 和 late-event revision；
- 增加 `get_user_preferences` / `set_user_preferences` 和 PIP 中的频率、力度、零干预控件；
- 在 `submit_evaluation` 后运行有最小历史、完整三维回答、步长上限和手动锁定保护的 `AdaptiveController`，输出 `ADAPTATION_UPDATE`；
- 输出 `get_chain_outcome_linkage`，包含 `focal_decision_id`、`claim_ids`、profile、exposure 和 C/S/A 测量引用；
- 明确在线不计算或读取 R/E/B、DGR/BGR/OGCR 和 Functional Outcome；
- 验证推荐不等于 exposure，历史快照和 Selector decision 不被覆盖；
- 先通过 MCP 工具 fixture 验证状态读写，不接入 PIP 或轮询 UI。

### M5：PIP/轮询 UI

- 将选择结果按四类固定模板送入插件 UI：项目状态简报、规则澄清器、声明—证据矩阵或下一步治理中心；
- 最后接入 inline / picture-in-picture Resource 和版本化 `get_retrace_state` 轮询；
- 记录真实 exposure、用户操作、关闭和跳过，并验证 UI 失败不影响 Codex；
- 实现简单结束检测与三个可跳过问题。

---

## 16. 验收标准

### Occasion 与状态

- 首次普通委托不会被误判为 Re-entry Occasion；
- Agent 完成后用户验收发现不一致可以形成 Occasion；
- Occasion 不会被当成用户已经进行 Re-entry；
- 一个 chain 只绑定一个 decision object；
- Occasion 确认并冻结 target profile 后，首次 exposure 前必须发起 OCCASION_BASELINE；
- baseline 的回答、逐题跳过、超时和 BASELINE_MISSED 均可查询，且不直接覆盖 current state level；
- Observer 随行为序列更新 C/S/A，而不是使用固定过程状态；
- `UNKNOWN` 不会被当成 0。

### 策略与优化

- 每个“策略 × 强度”只有一组五维参数；
- Registry 固定包含四类已确认的 `strategy_family`，每个 family 有稳定的 template ID 和 action code 集合；
- Selector 不在运行时修改候选的固定 C/S/A 能力；
- 被全面支配的候选不会进入最终结果；
- 当前状态或证据变化后，选择结果可以动态变化；
- `NO_INTERVENTION` 的 evidence=0、workflow=1，且不产生 evidence penalty；
- `assessability=LIMITED` 遵守全局 evidence floor；
- Workflow 只由真实 exposure 的衰减负担更新；
- `PRESENT_CHOICES` 只在两个不同 strategy family 均超过不干预改善阈值时产生；
- `PRESENT_CHOICES` 的两个选项不能共用同一 family，且 UI 动作不能跨 family；
- `PRESENT_CHOICES` 必须给每个 option 提供冻结的 branch condition；exposure 前必须记录用户选中的 candidate、匹配的 condition 和 choice basis；
- 每个真实 exposure 都能回指 `strategy_family`、`template_id`、`selection_decision_id` 和 `action_code`；
- 不再使用参考点 `J`、`max-gap`、单独 `Gain` 或等惩罚线平移；
- 同一输入、Registry 和 \(\beta\) 产生相同选择。

此外，语义约束验收还必须满足：

- HIGH 且有效 evidence 的 selector_hint.allowed_families 生效时，最终候选必须属于允许 family；MEDIUM hint 不得直接排除其他 family；
- selector_hint.max_intensity 生效时，最终候选强度不得超过上限；
- cognitive_gap_detected=false 时必须输出 NO_INTERVENTION，并记录 INTERVENTION_NOT_ELIGIBLE；
- execution_request_detected=true 且 cognitive_gap_detected=true 时不得仅因执行请求而抑制干预；
- LIMITED 状态下所有候选低于 evidence floor 时必须记录 INSUFFICIENT_EVIDENCE；
- TARGET_REACHED 只有三维全部已知、证据充分且三维缺口为 0 时才允许出现；其中“证据充分”必须是语义上支持对应 C/S/A 维度的 `evidence_ref`，不能只凭可回链 ID，也不能接受后验 outcome annotation evidence；
- 真实 exposure 后已曝光 candidate ID 必须进入 cooldown，其他候选不得被连带阻塞；用户响应或动作后才解除。
- 曝光后超过 `long_no_response_seconds` 没有用户响应时必须输出 `NO_RESPONSE_TIMEOUT`；

### 评测

- Occasion baseline 和 POST evaluation 都包含同一 chain 的 Criteria、State、Action 三维问题；
- 两类评测通过 `evaluation_kind`、`measurement_point` 和 `evaluation_id` 分开保存；
- 问题针对具体情境，不是抽象理解度自评；
- 用户可以部分回答或跳过；
- 缺失回答不会被保存为低分；
- 每条回答可以回指当时的 current state、target state 和 intervention；
- 每条 chain 可以导出 `focal_decision_id`、`claim_ids`、baseline/PRE/POST 引用和 `linkage_status`；
- 在线输出不包含未经离线审议的 DGR/BGR/OGCR 或 Functional Outcome 标签。
- 用户可以保存频率、力度和零干预模式，且每次选择记录当时的偏好版本；
- 三维 baseline/post 完整且同一用户已有至少 3 个已关闭、同时具备完整 baseline 与 POST 的 chain 时才允许自动更新；post-only、baseline-only、重复提交、未关闭 chain、缺失题目、手动锁定和后验 outcome evidence 都不能触发更新；
- 自适应只改变后续选择的频率容差、cooldown 和最高强度，不改变 Registry、证据门槛、历史选择或 exposure。

---

## 17. 当前冻结的技术结构

ReTrace 在确认 Occasion 后从固定 decision-object profile 中冻结一个局部 decision chain，并使用冻结的 C/S/A rubric 生成 target profile；首次 exposure 前必须完成或明确记录 OCCASION_BASELINE 三维场景化评测。State Observer 使用既有行为序列、同链证据和可选 USER_RESPONSE 持续估计当前 C/S/A；late event 只产生新 revision，不覆盖既有 Selector decision、exposure 或 PRE/POST 快照。交互设计团队将项目状态简报、规则澄清器、声明—证据矩阵和下一步治理中心冻结为四类 `strategy_family`，再为每个 family × intensity 注册 Criteria、State、Action、Evidence、Workflow 五维参数。E 用于证据资格、Skyline 和平局排序，D_obj 只计算 C/S/A 残余差距；NO_INTERVENTION 作为零支持、零新增负担的基准候选参与比较，Workflow 使用真实 exposure 的时间衰减。Selector 依据全局 eta/epsilon 规则输出 NO_INTERVENTION、一个候选或最多两个不同 strategy family；UI 最后通过 MCP 工具按固定模板渲染并记录 baseline、真实 exposure、PRE/POST 快照、用户动作和 POST_EVALUATION，再导出只含连接键的 outcome linkage envelope 供离线分析。
