# ReTrace Observer LLM 技术方案

> 版本：v0.1
> 日期：2026-08-20
> 状态：实现前方案

## 1. 方案目标

在主 Agent 持续编程和推进任务的过程中，引入一个独立的 Observer LLM，专门分析当前交互是否出现了 Cognitive Re-entry，以及用户此刻需要恢复哪类治理依据。

Observer 不负责写代码，不负责执行干预，也不直接决定项目处置。它只输出一个带证据和置信度的状态假设，随后由确定性的 ReTrace Skyline Selector 进行干预选择。

核心流程为：

```text
主 Agent 交互事件
→ bounded context broker
→ Observer LLM
→ 结构化状态假设
→ evidence/state validator
→ retrace-state-v2
→ Skyline selector
→ Decision Brief
→ 用户确认
→ 主 Agent 执行
```

## 2. 为什么需要独立 Observer

主 Agent 同时承担实现者和解释者角色，容易继续维护自己的实现叙事：

> “功能已经完成，测试也通过了。”

但用户真正关心的可能是：真实数据是否正确、业务规则是否满足、跨页面流程是否可用，或这次修改是否影响了之前的功能。

Observer 的作用不是替代主 Agent，而是提供一个分离的观察通道，检查：

- Agent 的声明是否有与用户目标直接对应的证据；
- 用户是否开始质疑结果、行动范围或验证方式；
- 当前问题属于目标标准、项目状态，还是验证—处置安排；
- 是否值得把当前状态交给干预选择器。

## 3. 系统架构

```text
┌────────────────────┐
│ Main Coding Agent  │
│ 写代码、解释、执行 │
└─────────┬──────────┘
          │ event stream
          ▼
┌────────────────────┐
│ Context Broker     │
│ 当前窗口、项目摘要 │
│ 时间边界、证据索引 │
└─────────┬──────────┘
          │ bounded context
          ▼
┌────────────────────┐
│ ReTrace Observer   │
│ 独立 LLM，只读分析 │
└─────────┬──────────┘
          │ observer-result-v1
          ▼
┌────────────────────┐
│ State Validator     │
│ schema、证据、边界 │
└─────────┬──────────┘
          │ retrace-state-v2
          ▼
┌────────────────────┐
│ Skyline Selector   │
│ 确定性策略选择     │
└─────────┬──────────┘
          │ Decision Brief
          ▼
┌────────────────────┐
│ User Confirmation   │
│ 接受、拒绝、补充规则│
└─────────┬──────────┘
          ▼
      Main Agent 执行
```

## 4. 组件职责

### 4.1 Main Coding Agent

主 Agent 继续负责正常的软件开发任务：理解需求、调用工具、修改代码、运行测试和向用户报告结果。

主 Agent 不应自行修改 Observer 的结果，也不能在 Observer 返回低置信度时强行选择干预。

### 4.2 Context Broker

Context Broker 为 Observer 组装当前可见上下文，包括：

- 当前用户消息；
- 最近若干轮 Agent—用户事件；
- 当前项目状态摘要；
- 文件、版本、运行结果和测试证据索引；
- 最近干预次数；
- 当前事件边界和内容哈希。

它必须负责时间边界控制。Observer 不能直接读取完整历史、未来事件或 calibration target。

### 4.3 ReTrace Observer LLM

Observer LLM 是只读旁观者，负责生成 `retrace-observer-result-v1`。

它回答四个问题：

1. 当前是否出现了值得关注的 Re-entry signal？
2. 这个信号更接近哪种错位：结果—标准、行动—历史、声明—证据或当前状态—后续后果？
3. 用户当前需要恢复哪类治理依据？
4. 哪些证据支持该判断，哪些地方仍不确定？

Observer 不直接输出最终干预，不直接修改 `policy`，也不执行项目工具。

### 4.4 State Validator

Validator 将 Observer 输出转换为可供 selector 使用的 state，并执行：

- 严格 schema 校验；
- evidence locator 和内容哈希校验；
- boundary 泄漏检查；
- evidence source 检查；
- `O/S/D` 范围检查；
- 置信度和完整度检查；
- observer-result 与 state 的一致性检查。

Validator 失败时不得继续选择，返回 `REQUEST_CLARIFICATION` 或结构化 validation error。

### 4.5 Skyline Selector

Selector 使用冻结 policy 进行确定性选择：

```text
retrace-state-v2
→ candidate generation
→ hard constraints
→ O/S/D/E/W scoring
→ Skyline/Pareto filtering
→ gain and near-tie
→ SelectionResult
```

当前已有的 `retrace_selector` 包直接承担这一层，不由 Observer LLM 重写。

### 4.6 User Confirmation Layer

Selector 的 `INTERVENE` 只表示“应该向用户展示干预建议”，不表示自动执行。

用户可以：

- 接受候选；
- 拒绝候选；
- 补充规则或验收标准；
- 要求更多证据；
- 选择并列候选中的一个；
- 要求恢复普通委托。

只有用户确认后，主 Agent 才能执行实际修改、验证或处置。

## 5. Observer 调用时机

Observer 不必每轮调用。第一版采用 trigger-based invocation，在以下事件之后调用：

- 用户质疑结果是否符合目标或业务规则；
- 用户要求解释修改范围、历史来源或影响；
- 用户拒绝“已完成”“测试通过”等声明；
- 用户要求真实环境、完整流程或边界条件验证；
- Agent 连续修复但问题仍重复出现；
- 用户重新规定范围、责任、批准、回退或交付条件；
- 新需求使既有实现的适配性变得不确定。

这些 trigger 只是唤起 Observer 的候选信号，不等于预先定义了完整的 Occasion taxonomy。

## 6. Observer 输入协议

```json
{
  "schema_version": "retrace-observer-context-v1",
  "decision_id": "turn-20260820-001",
  "boundary": {
    "sequence_index": 22,
    "boundary_reason": "current_user_turn",
    "future_events_included": false
  },
  "events": [
    {
      "event_id": "context_0018:R23",
      "sequence_index": 22,
      "role": "user",
      "text": "当前用户消息或受控摘要",
      "locator": "transcript.jsonl#context_0018:R23",
      "content_sha256": "...",
      "available_at_decision": true
    }
  ],
  "project_snapshot": {
    "summary": "当前项目状态摘要",
    "changed_files": [],
    "runtime_observations": [],
    "test_observations": []
  },
  "recent_interventions": 0
}
```

### 输入边界

- Observer 只能读取 `sequence_index <= boundary.sequence_index` 的事件；
- `future_events_included` 必须为 `false`；
- 不提供完整 episode target、未来用户行动或最终恢复结果；
- 项目摘要中的每个外部事实必须有 locator 或明确标记为 `INFERRED`；
- 无法建立可靠边界时，Observer 必须返回 `boundary_uncertain=true`。

## 7. Observer 输出协议

Observer 输出不直接等于 `DecisionState`，而是先输出观察假设：

```json
{
  "schema_version": "retrace-observer-result-v1",
  "decision_id": "turn-20260820-001",
  "should_trigger": true,
  "process_state": "REENTRY_OCCASION_OBSERVED",
  "occasion_signal": "DECLARATION_EVIDENCE_MISMATCH",
  "governance_needs": {
    "O": 1,
    "S": 2,
    "D": 3
  },
  "state_confidence": 0.82,
  "evidence_completeness": "partial",
  "evidence": [
    {
      "event_id": "context_0018:R23",
      "source": "OBSERVED",
      "supports_primitives": ["VERIFICATION"],
      "claim": "用户要求真实验证，而当前上下文只有 Agent 的完成声明"
    }
  ],
  "uncertainties": [
    "尚无真实环境运行结果"
  ],
  "recommended_handoff": "RUN_SELECTOR"
}
```

### 输出约束

- `should_trigger=false` 时不运行 selector，记录 Observer 结果即可；
- `state_confidence < 0.60` 时不能自动产生高强度 state；
- 没有 evidence 的 claim 必须删除或标为 `DESIGN_ASSUMPTION`；
- `recommended_handoff` 只能是 `NO_INTERVENTION`、`RUN_SELECTOR` 或 `REQUEST_CLARIFICATION`；
- Observer 不得输出新的 primitive、level、policy 参数或执行命令。

## 8. Observer 到 State 的转换

转换器将 Observer 结果映射为现有 `retrace-state-v2`：

| Observer 字段 | State 字段 |
|---|---|
| `process_state` | `process_state` |
| `governance_needs` | `governance_needs` |
| `evidence` | `evidence`，补充 locator/hash/sequence |
| `state_confidence` | `state_confidence` |
| `evidence_completeness` | `evidence_completeness` |
| 宿主项目风险 | `consequence/reversibility/authorization_risk` |
| 宿主历史计数 | `recent_interventions` |
| 宿主验证状态 | `active_verification` |

如果 Observer 的证据引用无法在 Context Broker 的权威清单中找到，转换失败，不允许选择。

## 9. 独立性与防自我合理化机制

### 9.1 上下文隔离

Observer 不接收主 Agent 的隐藏思维链，只接收事件、项目摘要和可定位证据。

### 9.2 工具隔离

Observer 无代码编辑、终端写入、部署、回滚和外部消息工具，只能产生结构化结果。

### 9.3 Policy 隔离

Observer 不能读取或修改 selector 的权重、阈值和候选画像。它只生成状态假设。

### 9.4 证据隔离

Observer 的每个关键判断必须能够回指当前窗口的 evidence。未来事件只能在下一次观察中出现，不能回填当前判断。

### 9.5 不一致处理

当 Main Agent 的摘要与 Observer 可见证据冲突时：

1. 保留冲突记录；
2. 降低 state confidence；
3. 必要时返回 `REQUEST_CLARIFICATION`；
4. 不让任一 LLM 静默覆盖另一方的事实。

## 10. 触发与运行模式

### 10.1 Trigger-based 模式（MVP）

只在潜在 Re-entry signal 出现时调用 Observer。优点是成本和打断较低，缺点是可能漏掉未被规则 trigger 捕获的 Occasion。

### 10.2 Turn-based 模式（研究对照）

每轮或每个 Agent action 后调用 Observer，但默认不展示结果。只在置信度和风险达到门槛时进入 selector。该模式适合测量 Occasion detection recall，但成本更高。

### 10.3 Shadow 模式

Observer 和 selector 在后台运行，结果不展示给用户，只记录预测。用于估计过度触发率、漏检率和与人工编码的一致性。

正式用户实验前，先使用 Shadow 模式校准 trigger 和 state mapping。

## 11. 失败关闭策略

| 失败情形 | 系统处理 |
|---|---|
| Observer 输出非法 JSON | 丢弃本轮观察并记录错误 |
| evidence 无法定位 | 不运行 selector |
| boundary 不确定 | `REQUEST_CLARIFICATION` |
| state confidence 过低 | 仅展示澄清请求，不展示高强度干预 |
| 当前已有 active verification | 禁止重复 Verification |
| 无安全可行候选 | `SAFE_HOLD` |
| gain 不足 | `NO_INTERVENTION` |
| 用户拒绝干预 | 返回主 Agent，不能自动重试同一干预 |

## 12. 审计记录

一次 Observer-assisted decision 至少记录：

```text
observer_context_hash
observer_result_hash
state_digest
policy_hash
template_hash
selection_audit_id
selection_decision_digest
user_response
execution_status
```

审计记录不复制完整敏感正文，只保留 evidence locator、sequence index 和内容哈希。

## 13. 实施方案

### Phase 1：Observer contract

- 固定 `retrace-observer-context-v1`；
- 固定 `retrace-observer-result-v1`；
- 编写 observer prompt 和 JSON schema；
- 使用已有 4 个 pilot episode 做离线输出。

### Phase 2：Observer adapter

- 新增 `observer_adapter.py`；
- 将 observer evidence 映射到 `EvidenceRef`；
- 复用现有 state validator；
- 对未来证据、缺失 locator 和矛盾字段添加 fail-closed 测试。

### Phase 3：Selector integration

- Observer 只在 `RUN_SELECTOR` 时调用 selector 子 Agent；
- selector 保持 `DRY_RUN`；
- Decision Brief 交给主 Agent 展示；
- 用户确认状态进入 audit。

### Phase 4：Shadow evaluation

- 先不向用户展示 Observer 结果；
- 与人工开放编码的 Occasion、O/S/D 和 evidence binding 对比；
- 评估 precision、recall、false trigger、state confidence calibration。

### Phase 5：交互实验

- 比较 Main Agent-only、Observer-assisted 和 Observer+Selector 三种条件；
- 测量治理恢复、用户判断准确性、验证行为、干预负担和过度打断。

## 14. 验收标准

- Observer 只读取 bounded context；
- Observer 不具备项目修改和部署权限；
- 所有关键判断都有 evidence locator；
- 非法或未来 evidence 无法进入 state；
- 同一输入和 observer prompt 版本产生可记录的结构化结果；
- Observer 不直接决定 primitive 或 level；
- Selector 仍由冻结 policy 确定性执行；
- 用户拒绝干预后不会自动执行或无限重试；
- Observer、state 和 selector 三层结果都可通过 hash 重放；
- Shadow 模式下不影响主 Agent 的实际行为。

## 15. 当前系统对应关系

| 方案组件 | 当前实现状态 |
|---|---|
| bounded prefix manifest | 已实现 |
| `retrace-state-v2` | 已实现 |
| evidence binding | 已实现 |
| Skyline selector | 已实现 |
| Selector sub-agent DRY_RUN wrapper | 已实现 |
| Observer LLM prompt/adapter | 待实现 |
| Context Broker | 待实现 |
| 用户确认 UI | 待实现 |
| Shadow evaluation | 待实现 |

当前可以先实现 Observer 的离线/Shadow 版本，再接入主 Agent。正式在线干预前，必须完成 bounded context、证据校验和用户确认层。
