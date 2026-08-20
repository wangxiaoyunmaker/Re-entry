# ReTrace Skill 与 Selector 子 Agent 技术方案

> 版本：v0.1
> 日期：2026-08-20
> 状态：实现前方案

## 1. 方案目标

将现有 ReTrace Skyline Selector 接入 Agent 工作流，使系统能够在用户可能重新进入项目状态时：

1. 从当前可见的对话、项目和运行证据中形成结构化 `retrace-state-v2`；
2. 使用确定性的 Skyline selector 选择干预、并列选项、不干预或安全保持；
3. 把选择结果转换为用户可理解的 Decision Brief；
4. 在用户确认后才执行后续修改、验证或处置。

核心原则是：

> Skill 负责形成当前状态，Selector 子 Agent 负责执行冻结策略下的选择，主 Agent 负责与用户协商和执行。

状态识别、策略选择和项目执行不能合并为一个自由生成的 prompt。

## 2. 系统边界

### 2.1 本方案包含

- `ReTrace State Skill`：受约束的状态构建 Skill；
- `ReTrace Selector Subagent`：调用现有确定性选择器的子 Agent；
- v2 state schema、evidence binding 和审计接口；
- Decision Brief 返回协议；
- 失败关闭、用户确认和权限边界。

### 2.2 本方案不包含

- 从全部历史对话自动识别 Re-entry Occasion 的最终模型；
- 自动推断不可观察的用户心理状态；
- 自动修改、批准、回滚或暂停真实项目；
- 在线学习权重或根据单次结果修改 policy；
- 让 LLM 自由创造未在模板中定义的新干预原语。

## 3. 总体架构

```text
当前 Agent 工作流
        │
        │ potential Re-entry signal
        ▼
ReTrace State Skill
        │  retrace-state-v2
        ▼
State validator + evidence binding
        │
        ▼
ReTrace Selector Subagent
        │  SelectionResult
        ▼
Decision Brief renderer
        │
        ▼
用户确认 / 拒绝 / 补充信息
        │
        ▼
主 Agent 执行验证、修改、解释或处置
        │
        ▼
Audit record
```

现有 `retrace_selector` 包已经实现下半部分：state 校验、候选生成、硬约束、评分、Skyline、排序、渲染和审计。新增工作主要是 Skill 输入协议、子 Agent 封装和宿主 Agent 的确认循环。

## 4. 组件职责

### 4.1 ReTrace State Skill

Skill 接收一个由宿主明确截断的当前上下文窗口，输出结构化状态。它需要完成：

- 判断当前过程状态是继续委托、提前支持、Re-entry occasion 还是治理恢复；
- 将用户目标转化为 `O/S/D` 三类治理需求；
- 标记风险、可逆性、授权风险和当前验证状态；
- 为每个判断附上证据来源和 locator；
- 识别不确定性，并在证据不足时降低 `state_confidence`；
- 严格输出 JSON，不输出未被 schema 接受的自由字段。

Skill 不计算 Skyline，不选择原语，不修改项目。

### 4.2 ReTrace Selector Subagent

子 Agent 接收合法的 `retrace-state-v2`，调用本地选择器：

```python
result = SelectionEngine(policy, templates).select(state)
```

它只负责：

- 使用版本化 policy 和 template；
- 返回候选、约束、分数、Skyline、增益和 outcome；
- 返回与当前候选绑定的 evidence IDs；
- 将结果写入可审计的 SelectionResult；
- 在 state 或 policy 非法时 fail closed。

子 Agent 不应重新解释 state、不应自行改权重、不应跳过硬约束、不应直接调用代码编辑或部署工具。

### 4.3 宿主 Agent

宿主 Agent 负责：

- 识别是否值得调用 State Skill；
- 提供 bounded context；
- 向用户展示 Decision Brief；
- 接收用户确认、拒绝或补充信息；
- 只有在确认后执行实际项目操作。

宿主 Agent 不能把用户尚未看到的 post-onset 结果回填到本次 state。

## 5. Skill 调用时机

第一版不要求 Skill 每轮对话都运行。宿主 Agent 在出现以下信号时调用：

- 用户质疑结果是否符合目标或业务规则；
- 用户要求解释修改范围、历史原因、依赖或影响；
- 用户不接受“已完成”“测试通过”等代理证据；
- 用户要求真实环境、完整流程或特定边界验证；
- 用户重新规定责任、范围、批准、回退或交付条件。

这些信号只是调用候选，不是固定的 Occasion 分类。最终是否构成 Re-entry 由状态 Skill 根据当前窗口和证据判断。

## 6. Skill 输入协议

宿主向 Skill 传入的上下文必须已经完成时间边界截断：

```json
{
  "schema_version": "retrace-skill-context-v1",
  "decision_id": "turn-or-episode-id",
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
      "observed_at": "2026-06-26T09:38:03.807Z",
      "text": "...",
      "locator": "transcript.jsonl#context_0018:R23",
      "content_sha256": "..."
    }
  ],
  "project_state": {
    "summary": "宿主提供的当前项目状态摘要",
    "changed_files": [],
    "runtime_observations": []
  },
  "recent_interventions": 0
}
```

### 输入安全要求

- `future_events_included` 必须为 `false`；
- 每个事件必须有 sequence index、locator 和内容哈希；
- Skill 不接收 calibration target、完整 episode 标签或未来用户行动；
- 不能通过省略 boundary 来让 Skill 读取全量历史；
- 宿主无法提供可靠边界时，Skill 返回 `REQUEST_CLARIFICATION`，不猜测。

## 7. Skill 输出协议

Skill 输出必须能够直接被现有 `DecisionState.from_dict` 校验：

```json
{
  "schema_version": "retrace-state-v2",
  "decision_id": "turn-or-episode-id",
  "process_state": "REENTRY_OCCASION_OBSERVED",
  "governance_needs": {"O": 2, "S": 3, "D": 2},
  "evidence": [
    {
      "evidence_id": "context_0018:R23",
      "source": "OBSERVED",
      "locator": "transcript.jsonl#context_0018:R23",
      "observed_at": "2026-06-26T09:38:03.807Z",
      "sequence_index": 22,
      "content_sha256": "...",
      "supports_primitives": ["RULE_ALIGNMENT"],
      "available_at_decision": true
    }
  ],
  "consequence": "medium",
  "reversibility": "medium",
  "authorization_risk": "low",
  "evidence_completeness": "partial",
  "state_confidence": 0.82,
  "recent_interventions": 0,
  "active_verification": false
}
```

Skill 可在内部保留 rationale，但传给 Selector 的正式对象只能包含 schema 允许的字段。Rationale 中不得复制不必要的敏感正文。

## 8. Evidence binding 规则

每条 v2 evidence 必须包含：

- `locator`；
- `sequence_index`；
- `content_sha256`；
- `available_at_decision=true`；
- `supports_needs` 或 `supports_primitives` 至少一个。

绑定优先级为：

```text
supports_primitives > supports_needs > 无绑定的 v1 legacy evidence
```

Selector 的硬约束、E 分数和 Decision Brief 只使用当前候选支持的 evidence。Skill 不能把一个只支持 `RULE_ALIGNMENT` 的证据标记为 `VERIFICATION` 证据。

## 9. Selector 子 Agent 协议

### 请求

```json
{
  "schema_version": "retrace-selector-request-v1",
  "state": {"schema_version": "retrace-state-v2"},
  "policy_ref": "config/policy.v0.2.json",
  "template_ref": "config/templates.v0.2.json",
  "execution_mode": "DRY_RUN"
}
```

`execution_mode` 第一版固定为 `DRY_RUN`。它只能产生选择结果，不能执行项目写操作。

### 响应

响应直接复用现有 `SelectionResult`，至少包含：

- `outcome`；
- `selected_ids`；
- rendered Decision Brief；
- feasible/rejected candidates；
- constraint records；
- score vectors、Skyline 和 dominance witnesses；
- gain、near-tie 和 reason codes；
- policy/template hash；
- audit ID 和 decision digest。

### 失败关闭

| 情况 | 返回 |
|---|---|
| state schema 非法 | `REQUEST_CLARIFICATION` 或结构化 validation error |
| boundary 不可靠 | `REQUEST_CLARIFICATION` |
| 证据 locator/hash 不一致 | 拒绝选择并记录 validation error |
| 低置信度与高风险冲突 | `REQUEST_CLARIFICATION` |
| 没有安全可行候选 | `SAFE_HOLD` |
| gain 不足 | `NO_INTERVENTION` |

## 10. 用户交互循环

```text
State Skill 形成 state
→ Selector 返回候选
→ 主 Agent 展示最小 Decision Brief
→ 用户确认 / 修改规则 / 要求证据 / 拒绝
→ 若 state 改变，重新调用 Skill
→ 若用户确认，主 Agent 执行已批准动作
→ 保存 audit record
```

`INTERVENE` 不等于“自动执行”。它只表示当前冻结 policy 认为需要向用户展示干预。`PRESENT_CHOICES` 必须让用户在候选之间作出选择。

## 11. 建议的 Skill 包结构

```text
skills/retrace-state-builder/
├── SKILL.md
├── references/
│   ├── state-schema-v2.md
│   ├── evidence-binding.md
│   └── process-state-rules.md
└── scripts/
    ├── validate_state.py
    └── redact_context.py

subagents/retrace-selector/
├── AGENT.md
└── run_selector.py
```

`SKILL.md` 只描述何时调用、允许读取什么、必须输出什么；状态 schema 和硬约束放在 references 中；确定性选择继续由 Python 包实现，不在 Skill prompt 中重写评分逻辑。

## 12. 实施阶段

### Phase 1：离线 Skill contract

- 固定 `retrace-skill-context-v1`；
- 固定 state 输出 schema；
- 用 4 个 pilot episode 验证 state 能被 selector 接收；
- 保留人工提供 state 的 fallback。

### Phase 2：Selector 子 Agent 封装

- 将 CLI 调用封装为 Python/API 工具；
- 固定 `DRY_RUN` 权限；
- 统一 SelectionResult 和 audit 输出；
- 增加非法 state、未来证据和策略篡改测试。

### Phase 3：宿主 Agent 接入

- 增加 potential Re-entry trigger；
- 建立用户确认界面；
- 将用户补充规则重新送回 State Skill；
- 记录用户接受、拒绝和修改候选的结果。

### Phase 4：真实评估

- 使用 bounded blind-review 产生正式 prefix states；
- 扩充至少 10 个 core 案例和 3 个参与者组；
- 完成参数校准和 grouped cross-validation；
- 在真实 Agent workflow 中评估治理恢复、负担和过度干预。

## 13. 验收标准

- Skill 输出 100% 能通过 `retrace-state-v2` 校验；
- Skill 无法引用 boundary 之后的 evidence；
- 证据 ID、locator、sequence index 和 hash 与权威 manifest 完全一致；
- Selector 子 Agent 不修改 policy，不执行项目写操作；
- 相同 state、policy 和 template 产生相同 SelectionResult；
- `REQUEST_CLARIFICATION`、`SAFE_HOLD` 和 `NO_INTERVENTION` 均能正确返回；
- 用户拒绝候选后不会自动执行该候选；
- 所有选择都能通过 audit ID 和 decision digest 重放；
- Pilot 之外的正式校准仍需人工批准，不得把 Skill 自己生成的标签当作 ground truth。

## 14. 当前状态与主要风险

现有 selector、证据绑定、prefix manifest、calibration 和审计模块已经完成。尚未完成的是：

1. 正式 `SKILL.md`；
2. bounded context 的宿主接口；
3. Selector 子 Agent 的运行封装；
4. 用户确认后的执行权限层；
5. bounded blind-review 工具。

因此，当前可以先做成“Skill 生成 state + 子 Agent 选择”的离线/DRY_RUN 原型，但还不能把它描述为已经接入真实 Agent 的自动干预系统。
