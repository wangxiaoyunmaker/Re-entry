# ReTrace v0.6 Runtime Integration

该运行时层把 Observer/State Skill 的状态直接连接到 v0.6 Selector，并在 Selector 外部负责
会话记忆、幂等、配置固定、超时、安全回退和交互事件持久化。它不修改五维评分与最终决策
公式，也不会自动执行项目修改。

## 1. 运行模式

- `SHADOW`：可以使用 `TEST_ONLY` registry；记录本来会选择什么，但不生成可展示的
  `interaction_id`，不能累计真实干预次数。
- `LIVE`：只接受 `registry_status=APPROVED`；当结果为 `INTERVENE` 或
  `PRESENT_CHOICES` 时生成 `interaction_id`，等待宿主确认实际展示。

当前仓库的 `strategy_registry.v0.6.json` 是 `TEST_ONLY`，因此目前只能安全运行
`SHADOW`。这是一道强制门，不是使用说明约定。

## 2. 实时请求

宿主可以直接调用 Python API，不需要把状态先写入文件：

```python
from retrace_selector import RuntimeSelectorService

service = RuntimeSelectorService.from_paths(
    database_path="var/retrace-runtime.sqlite3",
    policy_path="config/selection_policy.v0.6.json",
    registry_path="config/strategy_registry.v0.6.json",
    execution_mode="SHADOW",
)

response = service.select({
    "schema_version": "retrace-runtime-request-v0.6",
    "request_id": "REQ-001",
    "session_id": "SESSION-001",
    "state": observer_or_skill_state,
})
```

`state` 可以是精简的 v0.6 十字段状态，也可以是现有 `retrace-state-v1/v2` 或 pilot
`governance_needs.O/S/D` 状态。运行时调用 `adapt_state()`，不会再次调用 LLM。
这里的 Observer/Skill 输入指已经完成证据绑定和风险字段构建的最终状态对象；仅包含
`observation_state` 的第一阶段路由结果信息不足，必须先交给 State Skill 补成合法状态。

如宿主需要固定配置，可在请求中增加：

```json
{
  "expected_registry_hash": "64位SHA-256",
  "expected_policy_hash": "64位SHA-256"
}
```

任一哈希与当前配置不一致都会持久化为 `CONFIG_VERSION_MISMATCH / SAFE_HOLD`。

## 3. 会话记忆

SQLite 是运行时字段的权威来源：

- `recent_intervention_count` 只统计已收到 `INTERVENTION_PRESENTED` 的交互；仅完成选择不计数。
- `active_verification` 由 `VERIFICATION_STARTED` 和 `VERIFICATION_COMPLETED` 更新。
- 在会话尚无运行时事件时，保留 Observer/Skill 上送的两个字段，兼容历史状态。
- 一旦运行时接管相应事件流，后续请求使用持久化值，避免上游陈旧字段覆盖最新事件。
- `SESSION_RESET` 开启新的会话记忆区间，但不删除历史审计记录。

支持的事件类型：

```text
INTERVENTION_PRESENTED
USER_ACCEPTED
USER_REJECTED
USER_DISMISSED
USER_SUPPLIED_INFO
VERIFICATION_STARTED
VERIFICATION_COMPLETED
SESSION_RESET
```

用户反应必须引用已经展示的 `interaction_id`；选择但未展示的干预不能记录为接受或拒绝。

## 4. 幂等与配置切换

- `request_id` 是选择幂等键；相同请求重试返回原结果，不重复创建干预。
- 相同 `request_id` 携带不同状态时返回 `IDEMPOTENCY_CONFLICT / SAFE_HOLD`，原记录不覆盖。
- `event_id` 是事件幂等键；相同事件重试不重复累计。
- 每条请求保存实际使用的 registry/policy 哈希。
- `reload_configuration()` 先完整校验新配置，再原子切换未来请求；旧请求仍可按原结果重放。

## 5. 异常回退

| 情况 | 行为 |
|---|---|
| 状态无效且风险明确安全 | `REQUEST_CLARIFICATION` |
| 状态无效且安全性未知 | `SAFE_HOLD` |
| 上游明确 `ABSTAIN`，且风险明确安全 | `REQUEST_CLARIFICATION` |
| 上游明确 `ABSTAIN`，且风险不安全或未知 | `SAFE_HOLD` |
| 配置哈希不匹配 | `SAFE_HOLD` |
| `LIVE` 使用未批准 registry | `SAFE_HOLD` |
| 选择超时或内部异常 | `SAFE_HOLD` |
| SQLite 无法持久化 | 不释放原选择，返回 `PERSISTENCE_FAILURE / SAFE_HOLD` |

回退响应与正常 `V06SelectionResult` 分开：正常结果放在 `result`，运行时回退放在
`fallback`。宿主不能把 fallback 当成可展示策略。

## 6. CLI 与标准输入

```bash
PYTHONPATH=src python3 -m retrace_selector.cli runtime-select \
  --database var/retrace-runtime.sqlite3 \
  --policy config/selection_policy.v0.6.json \
  --registry config/strategy_registry.v0.6.json \
  --request examples/runtime_request_v06.json \
  --mode SHADOW

cat examples/runtime_request_v06.json | \
PYTHONPATH=src python3 -m retrace_selector.cli runtime-select \
  --database var/retrace-runtime.sqlite3 \
  --policy config/selection_policy.v0.6.json \
  --registry config/strategy_registry.v0.6.json \
  --request - \
  --mode SHADOW

PYTHONPATH=src python3 -m retrace_selector.cli runtime-event \
  --database var/retrace-runtime.sqlite3 \
  --event examples/runtime_verification_started_v06.json

PYTHONPATH=src python3 -m retrace_selector.cli runtime-history \
  --database var/retrace-runtime.sqlite3 \
  --session-id SESSION-example

PYTHONPATH=src python3 -m retrace_selector.cli runtime-health \
  --database var/retrace-runtime.sqlite3 \
  --policy config/selection_policy.v0.6.json \
  --registry config/strategy_registry.v0.6.json \
  --mode LIVE
```

生产代码应直接调用 Python API；标准输入 CLI 主要用于本地联调和进程间原型接入。

## 7. 持久化边界

数据库记录精简状态、证据 ID/来源、选择结果、配置哈希、展示状态和结构化反应事件。它不
保存原始对话正文。`metadata` 只应放结构化代码或定位符，不应复制敏感用户消息。
数据库文件创建后会限制为当前用户读写（`0600`）；生产部署仍应把它放在受控数据目录，
并按研究数据管理方案配置备份、保留期限和磁盘加密。
