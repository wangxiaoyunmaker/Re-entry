# ReTrace Codex 插件技术方案 v0.1

**用途**：定义 ReTrace 如何作为 Codex 插件接入真实对话与工具事件、运行在线推理，并在 Codex 内提供持续可见的交互界面。
**适用范围**：Codex in ChatGPT desktop app；第一阶段不支持浏览器扩展和 IDE 插件。
**关联方案**：[ReTrace 在线干预推理技术方案](./retrace-online-inference-technical-design.md)负责 Occasion、C/S/A、Skyline 与评测逻辑；本文只定义宿主接入、运行容器、UI 和数据接口。

---

## 1. 目标与边界

ReTrace 插件需要完成四件事：

1. 被动接收 Codex 中的用户消息、工具调用、工具结果和轮次结束事件；
2. 将事件转换为在线推理模块可消费的统一事件流；
3. 持续运行 Occasion Detector、State Observer 与 Skyline Selector；
4. 在 Codex 内显示干预、策略选择和可跳过的场景化评测问题。

插件不修改 Codex 主 Agent 的生成逻辑，不替代 Codex 的权限系统，也不自动修改项目。ReTrace 的干预通过独立 UI 展示，用户可以查看、选择、回答、跳过或关闭。

本方案不包含：

- C/S/A 等级的行为锚点和推理提示词；
- Skyline 的目标函数和参数标定；
- DGR、BGR、OGCR 等离线结果编码；
- 浏览器 DOM、屏幕 OCR 或输入法监听。

这些内容分别由在线推理方案和离线分析方案定义。

---

## 2. 平台可行性依据

Codex 插件可以同时包含 Skills、MCP Server、可选 UI 和仅在 Codex 中运行的 Hooks。ChatGPT 与 Codex 使用统一的插件目录，但具体能力可以是宿主特定的。[OpenAI Plugin architecture](https://developers.openai.com/plugins/concepts/plugins)

Codex Hooks 可以在会话、用户提交、工具执行和轮次结束等生命周期点运行，并向 Hook 命令提供 `session_id`、`turn_id`、`cwd`、`model` 和 `transcript_path` 等上下文。[OpenAI Codex Hooks](https://learn.chatgpt.com/docs/hooks)

MCP Server 可以为选定工具返回 UI Resource。UI 运行在隔离 iframe 中，通过 MCP Apps bridge 接收工具数据、调用工具和提交交互；宿主支持 inline、fullscreen 和 picture-in-picture 等呈现方式。[OpenAI Plugin UI](https://developers.openai.com/plugins/build/chatgpt-ui)

因此，本方案采用：

```text
Codex Hooks + 本地状态服务 + MCP Server + MCP UI
```

而不是读取不稳定的 Codex transcript 文件，也不依赖 Codex 窗口的 DOM。

---

## 3. 总体架构

```text
┌──────────────────── Codex Desktop ────────────────────┐
│                                                       │
│  用户消息 / Agent 回复 / 工具调用与结果               │
│                 │                                     │
│                 ▼                                     │
│          ReTrace Lifecycle Hooks                      │
│                 │                                     │
│                 ▼                                     │
│          Local Event Collector                        │
│                 │                                     │
│                 ▼                                     │
│   Occasion Detector → State Observer → Skyline        │
│                 │                                     │
│                 ▼                                     │
│          Shared State Store                           │
│                 ▲                                     │
│                 │ MCP tools                           │
│          ReTrace MCP Server                           │
│                 │                                     │
│                 ▼                                     │
│   ReTrace MCP UI（inline / picture-in-picture）       │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### 3.1 组件职责

| 组件 | 职责 | 不负责 |
|---|---|---|
| Hooks | 捕获 Codex 生命周期事件并快速投递 | 运行耗时 LLM 推理、渲染 UI |
| Event Collector | 校验、去重、排序、规范化事件 | 判断干预策略 |
| Online Inference Runtime | Occasion、chain、C/S/A、Skyline | Codex 宿主适配 |
| Shared State Store | 保存事件、快照、干预和回答 | 直接生成界面 |
| MCP Server | 向 UI 提供状态查询和交互提交工具 | 被动监听 Codex 事件 |
| MCP UI | 呈现状态、干预和评测，接收用户操作 | 作为业务数据的唯一真值来源 |

---

## 4. 插件包结构

```text
retrace-codex-plugin/
├── .codex-plugin/
│   └── plugin.json
├── hooks/
│   └── hooks.json
├── scripts/
│   └── emit_event
├── server/
│   ├── mcp_server
│   ├── event_collector
│   └── inference_runtime
├── web/
│   ├── src/
│   │   └── retrace_panel
│   └── dist/
│       └── retrace_panel.js
└── schemas/
    ├── raw_event.schema.json
    ├── snapshot.schema.json
    └── interaction.schema.json
```

插件 manifest 负责声明插件名称、版本、MCP Server 和资源位置。Hooks 独立放在 `hooks/hooks.json`。MCP UI 作为 `text/html;profile=mcp-app` 资源由 MCP Server 返回。

第一阶段通过个人 marketplace 安装和测试，不需要提交公共插件目录。插件升级时必须同时记录：

- `plugin_version`；
- `event_schema_version`；
- `inference_config_version`；
- `strategy_registry_version`；
- `ui_resource_version`。

---

## 5. Codex 事件接入

### 5.1 使用的 Hooks

| Hook | 捕获内容 | ReTrace 用途 |
|---|---|---|
| `SessionStart` | 会话 ID、工作目录、模型等 | 建立或恢复运行会话 |
| `UserPromptSubmit` | 即将发送的 prompt、turn ID | 用户行为和 Occasion 检测 |
| `PreToolUse` | 工具名、参数、tool use ID | 记录即将执行的操作 |
| `PostToolUse` | 工具输入、输出和 tool use ID | 获取测试、文件、命令等证据 |
| `Stop` | 本轮最终 Agent 消息 | 记录 Agent 回复并触发轮次快照 |
| `PreCompact` / `PostCompact` | 上下文压缩边界 | 标记上下文连续性风险 |
| `SessionEnd` | 会话结束 | 触发有限时长的刷盘；不依赖它完成复杂推理 |

Hook 命令默认是同步执行的。为避免 ReTrace 阻塞 Codex，除 `SessionEnd` 外的采集 Hook 必须在 `hooks/hooks.json` 中显式设置 `"async": true`；Hook 只负责校验并投递原始 JSON，目标执行时间不超过两秒。`SessionEnd` 始终同步执行，只做短时刷盘和关闭标记，超时或失败时由 Event Collector 在下次启动恢复未提交事件。

MVP 的 Hook 配置约束如下：

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "scripts/emit_event",
        "async": true,
        "timeout": 2
      }]
    }],
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "command",
        "command": "scripts/emit_event",
        "async": true,
        "timeout": 2
      }]
    }],
    "PreToolUse": [{
      "hooks": [{
        "type": "command",
        "command": "scripts/emit_event",
        "async": true,
        "timeout": 2
      }]
    }],
    "PostToolUse": [{
      "hooks": [{
        "type": "command",
        "command": "scripts/emit_event",
        "async": true,
        "timeout": 2
      }]
    }],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "scripts/emit_event",
        "async": true,
        "timeout": 2
      }]
    }],
    "PreCompact": [{
      "hooks": [{
        "type": "command",
        "command": "scripts/emit_event",
        "async": true,
        "timeout": 2
      }]
    }],
    "PostCompact": [{
      "hooks": [{
        "type": "command",
        "command": "scripts/emit_event",
        "async": true,
        "timeout": 2
      }]
    }],
    "SessionEnd": [{
      "hooks": [{
        "type": "command",
        "command": "scripts/emit_event",
        "timeout": 3
      }]
    }]
  }
}
```

`scripts/emit_event` 不运行 LLM 推理、不访问项目文件、不向 stdout 输出会改变 Agent 行为的内容，只把 stdin 原样写入带有 `hook_event_name` 的本地采集队列。`Stop` 事件必须保存 `last_assistant_message` 和 `stop_hook_active`；如果同一 turn 因 Stop continuation 再次触发，只记录事件，不把它自动当作第二个独立轮次。

示例中的 scripts/emit_event 是插件安装器解析后的稳定入口名。实际打包时必须把它解析为已安装插件目录中的绝对路径，不能依赖当前 session cwd；Hook 文档中的相对路径只作为示意。

### 5.2 可获得与不可获得的信息

稳定输入：

- `UserPromptSubmit` 中即将发送的完整 prompt；
- 工具调用名称、参数和结果；
- 一轮完成后的最终 Agent 消息；
- `session_id`、`turn_id`、`tool_use_id` 和 `cwd`；
- Hook 发生类型和当前模型。

不能依赖：

- Agent 逐 token 输出；
- Codex 界面的滚动、点击、选区或焦点；
- 插件启用前的完整历史事件；
- transcript 文件的长期稳定格式；
- 非 Codex 工具导致的所有外部文件变化。

需要逐 token 事件时，应另行评估 Codex App Server；它不属于本 MVP。

### 5.3 统一原始事件

```json
{
  "schema_version": "retrace-raw-event-v1",
  "event_id": "EVT-0031",
  "observed_at": "2026-08-24T10:32:11+08:00",
  "receiver_order": 31,
  "session_id": "SESSION-01",
  "user_id": "PARTICIPANT-0031",
  "turn_id": "TURN-04",
  "tool_use_id": null,
  "actor": "USER",
  "event_type": "USER_PROMPT",
  "content_ref": "CONTENT-0031",
  "project_id": "PROJECT-01",
  "cwd": "/path/to/project",
  "hook_event_name": "UserPromptSubmit",
  "source": "CODEX_HOOK",
  "plugin_version": "0.1.0"
}
```

允许的 Codex Hook 派生 `event_type`：

```text
SESSION_START
USER_PROMPT
TOOL_CALL
TOOL_RESULT
AGENT_FINAL
COMPACTION_START
COMPACTION_END
SESSION_END
```

插件交互和 Collector 派生事件使用同一事件表，但 `source` 必须标为 `MCP_UI` 或 `COLLECTOR`，不能伪装成 Codex Hook：

```text
USER_RESPONSE              Observer probe、Occasion baseline 或 POST evaluation 的用户回答
INTERVENTION_EXPOSURE      干预内容已由 UI 成功呈现
INTERVENTION_ACTION        用户选择、关闭、跳过或提交固定动作
POLICY_PREFERENCE_UPDATED  用户保存频率、力度或零干预模式
ADAPTATION_UPDATE          三维 baseline/post 结果触发的版本化小步长偏好更新
LATE_EVENT                 已提交快照之后才到达的事件审计记录
SNAPSHOT_PRE               exposure 前的不可变 PRE 快照
SNAPSHOT_POST              干预窗口后的不可变 POST 快照
```

`INTERVENTION_EXPOSURE` 不是 Selector 的推荐结果。只有 UI 成功收到并渲染某个干预 snapshot 后，才允许写入 exposure；用户没有看到、UI 加载失败或仅存在于后台状态库中的推荐，都不能记为 exposure。

`FILE_CHANGE` 不作为一级 Hook 事件。文件变化优先从 `apply_patch`、相关工具调用和结果中派生；需要完整文件审计时，再增加独立 workspace watcher。

### 5.4 排序与去重

异步 Hook 可能乱序完成，因此不能把脚本完成顺序当作真实行为顺序。Collector 不声称从 `tool_use_id` 恢复并行工具之间不存在的总顺序，而是保存偏序关系、为每条事件分配单调的 `collector_seq`，并用 turn watermark 决定何时生成可比较快照：

```text
session_id
  └── turn_id
       └── tool_use_id
            ├── PreToolUse
            └── PostToolUse
```

每个事件至少保存：

```json
{
  "event_id": "EVT-0031",
  "collector_seq": 31,
  "event_time": "2026-08-24T10:32:11+08:00",
  "received_at": "2026-08-24T10:32:11.120+08:00",
  "causal_parent_ids": ["EVT-0030"],
  "turn_id": "TURN-04",
  "tool_use_id": null,
  "is_late": false,
  "late_for_snapshot_id": null
}
```

Collector 对相同 `session_id + hook_event_name + turn_id + tool_use_id + payload_hash` 做幂等去重，但保留重复到达审计记录。一个 turn 在收到 `Stop` 后可以生成轮次快照；在此之后到达、且其事件时间属于该 turn 的事件，必须通过 late-event 接口追加处理，不得覆盖已有选择、exposure 或 PRE/POST 快照。

采集接口：

```text
ingest_event(raw_hook_or_ui_event)
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

`apply_late_event` 可以生成新的 `LATE_RECOMPUTE` snapshot，但必须保留原始 snapshot、Selector decision 和 exposure 记录；离线分析按 `as_of_event_id` 选择当时可见的版本。

---

## 6. 在线推理运行方式

### 6.1 触发时点

Event Collector 接收新事件后，根据事件类型触发不同工作：

| 事件 | 立即执行 | 可延后到轮次结束 |
|---|---|---|
| `USER_PROMPT` | Occasion 候选更新、chain 绑定 | 完整 C/S/A 重估 |
| `TOOL_CALL` | 记录行动和验证状态 | Selector 重算 |
| `TOOL_RESULT` | 证据绑定、验证状态更新 | 较重的 LLM 分析 |
| `AGENT_FINAL` | 当前轮快照、C/S/A 与 Selector | 边界检测 |
| `SESSION_END` | 关闭状态、持久化 | 无 |
| `USER_RESPONSE` | 保存 Observer probe 回答并更新补充证据 | Selector 重算 |
| `INTERVENTION_EXPOSURE` | 冻结 PRE，开启真实 exposure 窗口 | 暂不延迟 exposure 事实 |
| `INTERVENTION_ACTION` | 保存选择、关闭、跳过或提交动作 | 行为状态更新 |
| `LATE_EVENT` | 写入迟到事件审计并标记受影响 snapshot | `LATE_RECOMPUTE` |
| `SNAPSHOT_PRE` / `SNAPSHOT_POST` | 持久化不可变测量点 | 离线导出与回放 |

插件交互事件也进入同一运行队列：USER_RESPONSE、INTERVENTION_EXPOSURE、INTERVENTION_ACTION 和 LATE_EVENT。USER_RESPONSE 更新补充证据；INTERVENTION_EXPOSURE 冻结 PRE 并开始 exposure 窗口；INTERVENTION_ACTION 保存用户操作；LATE_EVENT 只生成新 revision，不覆盖历史快照。

第一阶段以“事件后更新、轮次结束完成”为准，不要求对 Agent 生成过程逐 token 分析。

### 6.2 推理接口

插件不内嵌新的 Occasion、C/S/A 或 Skyline 定义，只调用在线推理模块：

```text
adapt_event(raw_event)
  → normalized_event

update_context(normalized_event, previous_context)
  → DecisionContext

assess_occasion(DecisionContext)
  → OccasionResult

observe_support(DecisionContext, active_chain)
  → DecisionState

select_intervention(DecisionState, strategy_registry)
  → SelectionDecision
```

具体字段和计算规则以在线干预推理技术方案为准。

### 6.3 最新状态快照

UI 只读取已提交的快照，不读取推理过程中的半成品：

```json
{
  "snapshot_version": 18,
  "session_id": "SESSION-01",
  "chain_id": "PROJECT-01::OCC-004::FD-02",
  "lifecycle": "ACTIVE",
  "snapshot_kind": "LIVE",
  "measurement_point": null,
  "as_of_event_id": "EVT-0045",
  "late_event_revision": 0,
  "occasion_status": "CONFIRMED",
  "support_needs": {
    "criteria": {"level": 1, "assessability": "SUFFICIENT"},
    "state": {"level": 3, "assessability": "SUFFICIENT"},
    "action": {"level": 2, "assessability": "SUFFICIENT"}
  },
  "selection": {
    "decision": "INTERVENE",
    "strategy_id": "STATE_CONTEXT_RECOVERY_L2",
    "strategy_family": "STATE_CONTEXT_RECOVERY",
    "template_id": "PROJECT_STATUS_BRIEF_V1",
    "allowed_action_codes": ["GENERATE_CONFIRMATION_PROMPT", "VIEW_CHANGES_ONLY", "RETURN_TO_PRIOR_CHAT"]
  },
  "updated_at": "2026-08-24T10:32:14+08:00"
}
```

每次成功提交快照时递增 `snapshot_version`。UI 只有在版本变化时才重新渲染主要内容。

测量快照使用同一存储接口，但不可覆盖：

```text
capture_measurement_snapshot(
  chain_id,
  measurement_point,       // PRE | POST | CLOSE
  as_of_event_id,
  trigger_event_id,
  reason
)
  → MeasurementSnapshot

get_measurement_snapshot_pair(chain_id)
  → {
       pre: MeasurementSnapshot | null,
       post: MeasurementSnapshot | null,
       close: MeasurementSnapshot | null
     }
```

`PRE` 是第一次真实 `INTERVENTION_EXPOSURE` 之前最后一个稳定快照；如果本链最终不干预，则使用同一 Selector 决策点的预定义 `pseudo_cutoff`。`POST` 是干预窗口结束后、短窗口内没有新同链行为时的首个稳定快照。接口必须保存 `as_of_event_id`，后续 late event 只能产生新 revision，不能改写原始 PRE/POST。

---

## 7. Shared State Store

### 7.1 存储内容

MVP 使用本地 SQLite，至少包含：

| 表 | 内容 |
|---|---|
| `runtime_sessions` | Codex 会话、项目和插件版本 |
| `raw_events` | 规范化前后的事件引用 |
| `decision_chains` | Occasion 与 focal decision chain |
| `decision_snapshots` | 每次 Observer/Selector 快照 |
| `measurement_snapshots` | PRE/POST/CLOSE 不可变测量快照及 revision |
| `interventions` | recommendation、真实 exposure、跳过、选择和关闭记录 |
| `observer_probe_responses` | USER_RESPONSE 与补充问题回答 |
| `late_event_audits` | 迟到事件、受影响 chain 和重算 revision |
| `evaluation_responses` | Occasion baseline 与 POST evaluation 的三维场景化问题及 Likert 回答 |
| `outcome_linkage_records` | chain、focal decision、claim、C/S/A 快照、exposure 与离线 outcome 引用 |
| `strategy_registry_snapshots` | strategy family、强度、template、允许动作和 Registry 版本 |
| `runtime_errors` | 超时、重复、缺失和降级记录 |
SQLite 使用 WAL、事务和 busy timeout。Hook 进程不直接执行推理，只向单写入 Event Collector 投递；MCP Server 与 Collector 通过同一个本地服务或受控写入队列访问数据库，避免多个 Hook 进程同时更新推理状态。

### 7.2 真值边界

- Event Collector 是原始事件真值入口；
- Online Inference Runtime 是计算状态真值入口；
- SQLite 是跨 Hook、MCP Server 和 UI 的共享业务状态；
- iframe 的 widget state 只保存展开、选中、滚动等界面状态，不能作为研究数据的唯一存储。

### 7.3 内容与隐私

研究运行前必须取得参与者知情同意。存储层支持两种配置：

```text
FULL_CONTENT：保存原始文本和工具输出
REFERENCE_ONLY：只保存哈希、引用和最小分析字段
```

原始内容、推理结果和用户回答必须带有 `session_id`、`chain_id` 和配置版本。导出数据时使用研究参与者 ID 替换 Codex 账户信息。

---

## 8. MCP Server

### 8.1 工具接口

MVP 暴露以下工具：

| 工具 | 调用者 | 作用 | 是否改变状态 |
|---|---|---|---|
| `open_retrace_panel` | 用户或 Codex | 返回 ReTrace UI Resource 和当前快照 | 否 |
| `get_retrace_state` | UI | 获取高于指定版本的新快照 | 否 |
| `get_measurement_snapshot_pair` | UI/离线导出 | 获取同一 chain 的 PRE/POST/CLOSE 快照 | 否 |
| `get_chain_outcome_linkage` | 离线导出/调试 | 获取稳定连接键、测量引用和 exposure 引用，不返回 R/E/B 结果 | 否 |
| `export_chain_outcome_bundle` | 离线导出 | 导出同一 chain 的事件引用、C/S/A 测量、exposure 和 provenance | 否 |
| `render_intervention` | UI | 按固定 strategy family/template 返回可渲染 payload 和允许动作 | 否 |
| `choose_intervention_path` | UI | 保存用户在最多两个路径中的选择 | 是 |
| `record_intervention_action` | UI | 保存四类干预模板允许的固定动作并生成 `INTERVENTION_ACTION` | 是 |
| `dismiss_intervention` | UI | 跳过或关闭当前干预 | 是 |
| `submit_observer_probe` | UI | 保存可选的当前 C/S/A 补充回答并生成 `USER_RESPONSE` | 是 |
| `submit_occasion_baseline` | UI | Occasion 确认后、首次 exposure 前保存三维场景化基线并生成带 `response_kind` 的 `USER_RESPONSE` | 是 |
| `record_intervention_exposure` | UI | 记录干预已由 UI 成功呈现并触发 PRE/POST 生命周期 | 是 |
| `submit_evaluation` | UI | 保存三个场景化问题的 Likert 回答 | 是 |
| `skip_evaluation` | UI | 保存跳过及原因 | 是 |
| `get_runtime_status` | UI/调试 | 返回 Hook、Collector、推理和数据库状态 | 否 |
| `get_user_preferences` | UI | 获取当前用户的频率、力度和零干预模式 | 否 |
| `set_user_preferences` | UI | 保存用户滑块设置并生成版本化偏好事件 | 是 |
| `get_user_profile` | UI/离线导出 | 获取主观偏好、评估需要和当前生效策略三层 Profile | 否 |

所有写工具都必须接收：

```json
{
  "session_id": "SESSION-01",
  "chain_id": "PROJECT-01::OCC-004::FD-02",
  "snapshot_version": 18,
  "interaction_id": "INT-0012",
  "as_of_event_id": "EVT-0045"
}
```

`interaction_id` 用于幂等处理。旧快照提交需要记录为 `STALE_INTERACTION`，不能静默覆盖当前状态。

`get_user_preferences` / `set_user_preferences` 是用户级接口，不绑定单个 chain；写入时至少携带 `user_id` 和 `session_id`，并由服务自行生成 `preference.version` 与幂等事件 ID。

用户偏好面板的最小输入为：

```json
{
  "user_id": "PARTICIPANT-0031",
  "frequency_preference": 0.65,
  "intensity_preference": 0.40,
  "mode": "AUTO",
  "manual_lock": false,
  "session_id": "SESSION-01"
}
```

`frequency_preference` 和 `intensity_preference` 均为 `0.0–1.0`。UI 可将 `mode=AUTO/PAUSED` 显示为“自动/零干预模式”。频率仅影响在线 Selector 的触发容差和同链 cooldown，力度仅影响最高候选强度；UI 不得直接修改 Registry、`beta`、`epsilon`、evidence floor 或历史 decision。用户点击保存时写入 `POLICY_PREFERENCE_UPDATED`，在线服务在 Selector 输出和 `get_retrace_state` 中返回偏好版本。

用户 Profile 持久化为三层：`subjective_preference` 保存用户通过 UI 主动表达的偏好；`assessed_need` 保存由完整 C/S/A baseline→POST、反馈、负担和有效历史推断的需要信号；`effective_policy` 保存当前实际交给 Selector 的策略。用户主动调整只更新 subjective/effective，不删除 assessed_need；Adaptive Controller 只更新 assessed_need/effective，不覆盖 subjective。`get_user_preferences` 仍返回 effective_policy 以兼容旧调用，`get_user_profile` 返回三层完整对象。

三道场景化问题的完整 baseline/post 结果会在 chain 结束后触发小步长 Adaptive Controller；PRE 是干预前状态快照，不等同于 baseline 问卷。这里的“完成 1 个 chain”严格指同一用户、同一 chain 同时存在完整 `OCCASION_BASELINE` 与 `POST_EVALUATION`，三维均已回答、无跳过，且 chain 已关闭；post-only、baseline-only、重复提交和未关闭 chain 均不计入历史。Controller 至少等待 3 个这样的 chain，跳过题目、缺失维度、手动锁定或没有足够历史时只记录不更新；更新写入 `ADAPTATION_UPDATE`，同步保存 assessed_need，并只作用于后续推理。Outcome annotation 和后验 evidence 不进入这个更新路径。

Likert 回答必须是整数 `1–5`；`0`、`6`、小数、布尔值和非有限数值直接拒绝，不做静默截断。Controller 的离线回放接口可以显式接收冻结的 `0–3` 原量表输入。

`submit_occasion_baseline` 的最小输入为：

```json
{
  "session_id": "SESSION-01",
  "chain_id": "PROJECT-01::OCC-004::FD-02",
  "evaluation_id": "EVAL-BASE-001",
  "question_set_version": "CSA-LIKERT-V1",
  "responses": {
    "criteria": 4,
    "state": 3,
    "action": null
  },
  "skipped_dimensions": ["action"],
  "snapshot_version": 18,
  "as_of_event_id": "EVT-0045",
  "interaction_id": "INT-0014"
}
```

回答必须写入 `USER_RESPONSE`，并带有 `response_kind=OCCASION_BASELINE`。若首次 exposure 前未完成，记录 `BASELINE_MISSED`；exposure 后提交的回答不能伪装成 Occasion baseline。

`record_intervention_exposure` 的最小输入为：

```json
{
  "session_id": "SESSION-01",
  "chain_id": "PROJECT-01::OCC-004::FD-02",
  "selection_decision_id": "SEL-0018",
  "strategy_id": "STATE_CONTEXT_RECOVERY_L2",
  "strategy_family": "STATE_CONTEXT_RECOVERY",
  "template_id": "PROJECT_STATUS_BRIEF_V1",
  "snapshot_version": 18,
  "ui_instance_id": "UI-07",
  "render_status": "RENDERED",
  "rendered_at": "2026-08-24T10:32:15+08:00",
  "as_of_event_id": "EVT-0045",
  "interaction_id": "INT-0013"
}
```

只有 `render_status=RENDERED` 才生成 `INTERVENTION_EXPOSURE` 和 `exposure_id`；`SELECTED`、`RECOMMENDED` 或 UI 轮询到状态本身不算 exposure。

`record_intervention_action` 的最小输入为：

```json
{
  "session_id": "SESSION-01",
  "chain_id": "PROJECT-01::OCC-004::FD-02",
  "selection_decision_id": "SEL-0018",
  "exposure_id": "EXP-0018",
  "strategy_family": "STATE_CONTEXT_RECOVERY",
  "template_id": "PROJECT_STATUS_BRIEF_V1",
  "action_code": "GENERATE_CONFIRMATION_PROMPT",
  "snapshot_version": 18,
  "as_of_event_id": "EVT-0045",
  "interaction_id": "INT-0015"
}
```

服务端必须根据 Registry 校验 `strategy_family + template_id + action_code`。不在该模板动作集合中的请求返回 `ACTION_NOT_ALLOWED`，不能写入有效的 intervention action。

只读结果关联接口：

~~~text
get_chain_outcome_linkage(chain_id, as_of_event_id)
  → {
       chain_id,
       occasion_id,
       focal_decision_id,
       decision_object_profile_id,
       claim_ids,
       csa_measurements,
       exposure,
       strategy_id,
       strategy_family,
       template_id,
       action_codes,
       policy_preference,              // 本次最新 Selection 使用的偏好
       preference_used_for_selection,  // 与上字段相同，显式语义别名
       current_policy_preference,      // 当前持久化偏好，可能已被后续自适应更新
       adaptation_preference,          // 最新 ADAPTATION_UPDATE 写入的偏好或 null
       user_profile_version,
       subjective_preference,
       assessed_need,
       effective_policy,
       adaptation_update_id,
       governance_outcome_ref: null,
       functional_outcome_ref: null,
       linkage_status: "READY_FOR_OFFLINE_LINKAGE"
     }

export_chain_outcome_bundle(chain_id, as_of_event_id, redaction_mode)
  → {
       export_id,
       chain_manifest,
       event_refs,
       csa_measurements,
       exposure_refs,
       outcome_linkage,
       provenance
     }
~~~

导出 bundle 只提供离线关联所需的连接键、快照、事件引用和 provenance，并保留 `strategy_id`、`strategy_family`、`template_id`、推荐/呈现状态和实际 `action_code`；不在插件内生成 DGR、BGR、OGCR 或 Functional Outcome 标签。`redaction_mode` 沿用 `FULL_CONTENT` / `REFERENCE_ONLY`，导出必须记录 `as_of_event_id` 和 schema 版本。

### 8.2 UI Resource

`open_retrace_panel` 返回：

- `structuredContent`：当前会话和快照的最小数据；
- `_meta.ui.resourceUri`：ReTrace UI 模板；
- MCP Apps UI MIME type：`text/html;profile=mcp-app`。

资源 URI 作为 UI 版本缓存键。出现不兼容修改时发布新 URI，例如：

```text
ui://retrace/panel/v1.html
ui://retrace/panel/v2.html
```

---

## 9. Codex 内交互 UI

### 9.1 打开方式

用户在研究会话开始时执行一次：

```text
@retrace 打开面板
```

Codex 调用 `open_retrace_panel` 后渲染 UI。面板默认先以内嵌卡片打开；若宿主支持，则请求 picture-in-picture，使其在用户继续与 Codex 对话时保持可见。

必须进行能力检测：

- 支持 picture-in-picture：使用持续面板；
- 不支持：保持 inline card，并提供重新打开或刷新按钮；
- UI 无法加载：在线推理继续运行，记录 `UI_UNAVAILABLE`。

Hooks 负责更新后台状态，但不负责创建 UI。因此如果用户关闭面板，检测到 Occasion 后不能保证自动重新弹出。MVP 将“会话开始时打开一次并保持可见”作为正式交互前提；UI 重新打开后必须从 `get_retrace_state` 和 `get_measurement_snapshot_pair` 恢复，而不能依赖 iframe 内存状态。

### 9.2 面板状态

UI 只保留五种显示状态：

```text
IDLE
  尚未检测到 active Occasion

OBSERVING
  已开始观察某个 Occasion/chain，尚未决定干预

INTERVENTION
  展示一种策略或最多两个支持路径

EVALUATION
  展示三个可跳过的场景化 Likert 问题

ERROR
  当前数据或连接不可用，显示最近一次有效状态
```

这些是 UI 呈现状态，不替代在线方案中的 chain 生命周期。

### 9.3 面板内容

`IDLE`：

- ReTrace 正在运行；
- 当前项目和会话状态；
- 最近同步时间。

`OBSERVING`：

- 当前关注的局部 decision object；
- 系统正在观察 Criteria、State、Action 哪些方面；
- 不显示未经验证的“用户理解度”结论。

`INTERVENTION`：

- 策略内容、`strategy_family`、模板版本和所绑定的同链证据；
- 单一策略，或最多两个不同 `strategy_family` 支持路径；
- 只呈现 Registry 允许的动作，所有点击都通过 `record_intervention_action` 写入 `INTERVENTION_ACTION`。

四类固定面板的内容契约如下：

| `strategy_family` | `template_id` | 面板必须呈现 | 关键动作 |
|---|---|---|---|
| `STATE_CONTEXT_RECOVERY` | `PROJECT_STATUS_BRIEF_V1` | 当前任务、最近关键动作、涉及文件/模块/页面/接口/配置、异常信号、时间线和影响图 | 生成状态确认 Prompt、仅查看变化、回到先前对话 |
| `RULE_CLARIFICATION` | `RULE_CLARIFIER_V1` | 不满意语句、抽取规则、保护项和完成验收标准；规则可编辑 | 转成规则、加入禁止项、加入验收标准、编辑规则 |
| `CLAIM_EVIDENCE_CALIBRATION` | `DECLARATION_EVIDENCE_MATRIX_V1` | Agent 声明、已有证据、证据实际证明项、未证明项和待验证项；标注 E0/E1 等证据级别 | 要求运行/测试、列出修改文件、列出影响范围、补充证据、标记已验证 |
| `GOVERNANCE_ACTION_PLANNING` | `NEXT_STEP_GOVERNANCE_CENTER_V1` | 当前问题、下一步 Agent 行动、允许继续与否、分阶段治理计划、风险控制和回退条件 | 暂停并解释、只补证据、允许窄范围继续、回到上一步、生成治理 Prompt |

面板不得把四类内容合并为一个泛化的“继续执行”按钮；若 Selector 返回 `NO_INTERVENTION`，不渲染上述面板，也不生成 exposure。

`EVALUATION`：

- 针对当前情境生成的 Criteria、State、Action 三个问题；
- 每题使用预先定义的 Likert 量表；
- 允许部分回答、全部跳过或关闭；
- 保存题目文本、量表版本、回答和时间。

### 9.4 UI 与主 Agent 的关系

UI 的按钮默认只调用 ReTrace MCP 工具，不自动向 Codex 主对话发送消息。只有某种交互设计明确需要把用户选择转化为 Codex 指令时，才使用 MCP Apps 的 follow-up message 能力，并在发送前让用户确认。

这样可以避免 ReTrace 的研究操作无意改变主 Agent 的执行轨迹。

---

## 10. 实时同步

### 10.1 MVP 同步策略

MVP 使用版本化轮询：

```text
Hooks 写入事件
  → 推理运行
  → 原子提交 snapshot_version = N
  → UI 调用 get_retrace_state(after_version=N-1)
  → 有新版本时更新
```

建议：

- `IDLE`：每 5 秒检查一次；
- `OBSERVING`：每 2 秒检查一次；
- `INTERVENTION/EVALUATION`：用户操作后立即刷新；
- 页面不可见时降低频率；
- 连续失败三次后指数退避。

UI 的轮询必须通过 MCP Apps bridge 的 `tools/call` 调用只读 `get_retrace_state`，不得默认直接访问 localhost。若 Codex 宿主限制自动 MCP tool call，则降级为：

- 用户点击刷新；或
- UI 通过已声明的 `connectDomains` 连接 ReTrace 状态 API。

直接状态 API 只作为 POC fallback，必须使用独立的认证/会话校验和 CSP allowlist。是否支持无需确认的定时只读调用，必须在当前 Codex 桌面版本上通过 POC 实测，不能只依据通用 MCP UI 文档假设。

### 10.2 延迟目标

| 环节 | MVP 目标 |
|---|---:|
| Hook 到 Collector | P95 ≤ 1 秒 |
| Collector 写入事件 | P95 ≤ 200 毫秒 |
| 非 LLM 状态更新 | P95 ≤ 500 毫秒 |
| LLM Observer 更新 | P95 ≤ 8 秒 |
| 新快照到 UI 可见 | P95 ≤ 3 秒，不含 LLM 时间 |

超出目标时保留最近一次有效快照，并在新结果到达后替换，不能清空当前面板。

---

## 11. 异常与回退

| 异常 | 处理 |
|---|---|
| Hook 重复 | 幂等去重并保留重复审计 |
| Hook 乱序 | 按 session/turn/tool use 关系重排 |
| `PostToolUse` 缺失 | 工具调用标记为 `RESULT_MISSING`，不伪造结果 |
| Observer 超时 | 保留上一快照，本轮标记 `INFERENCE_TIMEOUT` |
| Selector 失败 | 本轮不展示新干预，保存错误 |
| UI 轮询失败 | 显示最近状态和连接提示 |
| UI 被关闭 | 后台继续观察；用户可重新打开 |
| Codex 上下文压缩 | 标记边界，继续依赖插件自己的 chain 存储 |
| 配置版本切换 | 当前推理完成后原子切换，新快照记录新版本 |
| SessionEnd | 停止新推理、等待短时刷盘、关闭会话 |

错误回退只影响本次 ReTrace 展示，不阻断 Codex 主 Agent。

---

## 12. 安全、权限与信任

1. 安装时明确展示并审核插件 Hooks；未被信任的 Hooks 不运行。
2. Hook 脚本只向 ReTrace 本地服务写事件，不执行项目修改命令。
3. MCP 查询工具标记为只读；提交回答等写工具只修改 ReTrace 数据库。
4. UI 通过 CSP 只允许连接声明过的 ReTrace 域名或本地服务。
5. 不在 UI 中执行来自 Codex 对话的任意 HTML；所有文本按不可信输入转义。
6. 研究日志提供删除、导出和参与者撤回机制。
7. API 密钥、身份令牌和原始敏感内容不进入 UI `structuredContent`。

---

## 13. MVP 实施顺序

### M0：Hook 与真实事件采集 POC

- 按 `hooks/hooks.json` 配置 `SessionStart`、`UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop`、`PreCompact`、`PostCompact` 和 `SessionEnd`；
- 实现无 LLM、无项目文件读取的 `scripts/emit_event`，将 Hook stdin 原样投递到本地采集队列；
- 实现 Event Collector、SQLite WAL、幂等去重和 `collector_seq`；
- 用真实 Codex 对话验证用户提交、并发工具、Stop continuation、SessionEnd 刷盘和重启恢复。

### M1：事件接口与共享状态

- 增加 `USER_RESPONSE`、`INTERVENTION_EXPOSURE`、`INTERVENTION_ACTION`、`LATE_EVENT` 和 `SNAPSHOT_PRE/POST` 事件契约；
- 实现统一 `ingest_event()`、`apply_late_event()`、`capture_measurement_snapshot()` 和 `get_measurement_snapshot_pair()`；
- 实现 `submit_occasion_baseline()`、`submit_observer_probe()`、`record_intervention_exposure()`、`record_intervention_action()` 等 MCP 写接口，并用固定 UI/事件 fixture 验证幂等、旧版本和 late-event 行为；
- 先不接入 PIP 或轮询 UI，直接检查数据库中的 PRE、POST 和修订记录。

### M2：固定离线回放

- 冻结 decision-object profile、`CSA-RUBRIC-V1`、`strategy_registry.formal.v1.json`、`selection_policy.formal.v1.json` 和 `as_of_event_id`；
- 对同一真实 trace 运行可重复的 replay manifest，输出 chain、PRE/POST/CLOSE 输入和 late-event audit；
- 无法唯一绑定对象或无法按 rubric 判断时输出 `CHAIN_UNASSESSABLE`/`UNKNOWN`，不得由回放器猜测；
- 确认离线 outcome 不回填在线 Selector 后，再进入 Selector 实现。

### M3：Selector 与在线推理

- 对接 `adapt_event()`、Observer 和固定策略 Registry；
- 冻结四类 `strategy_family`、模板 ID、证据槽位和动作枚举，并验证每次 exposure 只能绑定一个 family；
- 实现五维 Skyline、C/S/A 残余差距和 `D_obj + βD_user`；
- 修正 `NO_INTERVENTION` 的零支持/零新增负担语义、Evidence floor、Workflow exposure 衰减和 `PRESENT_CHOICES` 双路径判定；
- 用固定回放结果作为 Selector 回归基线，验证同一输入产生相同选择。

### M4：测量与评测接口（无 UI）

- 通过固定请求 fixture 验证 `submit_occasion_baseline`、`submit_observer_probe` 与最终 `submit_evaluation` 分离；
- 验证 Occasion 确认后、首次 exposure 前存在三维 baseline；真实 exposure 才冻结 PRE、窗口结束后生成 POST，late event 只产生新 revision；
- 保存三道情境化问题、Likert 回答和跳过记录；
- 验证四类模板的渲染 payload、允许动作和 `INTERVENTION_ACTION` 均能回指同一 selection/exposure；
- 通过 `export_chain_outcome_bundle` 导出同一 chain 的事件、状态、干预、C/S/A 测量和 provenance，仍不依赖 PIP 展示；
- 导出中只保留 `governance_outcome_ref` / `functional_outcome_ref` 连接位，不在插件内生成离线 outcome 标签。

### M5：PIP/轮询 UI

- 建立个人 marketplace 插件、MCP Server 和 `open_retrace_panel`；
- 按 Selector 返回的 family 渲染项目状态简报、规则澄清器、声明—证据矩阵或下一步治理中心；
- 最后接入 inline / picture-in-picture Resource，以及基于 `get_retrace_state` 的版本化轮询；
- 在 PIP 面板提供频率、力度滑块和“零干预模式”，保存后显示当前 preference version；完整的三维 baseline/post 结果由在线服务以小步长学习用户偏好；
- 只有 UI 成功渲染后调用 `record_intervention_exposure`，轮询本身不产生 exposure；
- 实测关闭、会话切换、刷新失败、旧 snapshot 提交和 Codex 重启恢复。

---

## 14. MVP 验收标准

### 14.1 事件接入

- 用户提交、工具调用、工具结果和 Agent 最终消息均可在数据库中找到；
- Observer probe 回答、真实 intervention exposure 和 late-event 审计均可在数据库中找到；
- 每条记录可回指 `session_id` 和 `turn_id`；
- 工具调用与结果可由 `tool_use_id` 配对；
- 重复或乱序不会生成重复干预。

### 14.2 UI

- 用户可以在新 Codex 会话中打开 ReTrace 面板；
- 支持的宿主中可以切换为 picture-in-picture；
- 用户继续与 Codex 对话时，面板仍可读取新快照；
- 四类已确认交互分别使用固定模板、字段和动作枚举，不能由 LLM 临时生成新面板类型；
- 双选项只展示两个不同 `strategy_family`，不会把 `NO_INTERVENTION` 当作支持路径；
- 用户可以选择、跳过、关闭和提交 Likert 回答；
- UI 关闭或失败不影响 Codex 正常工作。

### 14.3 在线推理接入

- 真实事件可以送入现有 `adapt_event()`；
- Occasion 确认后建立唯一 chain；
- Occasion 确认后、首次 exposure 前能够提交三维 OCCASION_BASELINE，且回答、跳过、超时和 BASELINE_MISSED 可审计；
- C/S/A 与 Selector 快照带有证据、`as_of_event_id` 和配置版本；
- PRE/POST 快照不可变，late event 只能生成新 revision；
- UI 展示的干预与数据库中的 SelectionDecision 一致。
- UI 展示的 `strategy_family`、`template_id` 和允许动作与 Registry 一致；非法或跨 family 动作被拒绝。

### 14.3.1 固定回放与测量

- 固定 decision-object profile 和 C/S/A rubric 能对同一 trace 产生相同回放结果；
- Observer probe 回答不会混入 chain 结束评测；
- Occasion baseline、Observer probe、真实 exposure、PRE、POST 和 late-event revision 可以分别查询；
- 真实 exposure 之前的 PRE 不会被后续事件覆盖；
- `get_chain_outcome_linkage` 和 `export_chain_outcome_bundle` 能保留 `focal_decision_id`、`claim_ids`、baseline/PRE/POST、exposure 和 `as_of_event_id`；
- 导出保留 `strategy_id`、`strategy_family`、`template_id` 和实际 `action_code`，并区分推荐、成功呈现和用户操作；
- 插件导出不包含未经离线审议的 DGR/BGR/OGCR 或 Functional Outcome 结论。

### 14.4 运行保障

- Observer 超时、状态缺失和 SessionEnd 均有确定回退；
- 重启 MCP Server 后能够恢复当前 session 和最新快照；
- 插件升级不会覆盖既有研究记录；
- 导出数据能够按 chain 串起事件、状态、干预和评测。

---

## 15. 第一阶段需要实测的三个平台问题

当前技术链路可行，但以下行为必须用最小 POC 在目标 Codex 桌面版本中验证：

1. MCP UI 在 Codex 中是否完整支持 picture-in-picture，以及关闭和会话切换后的保留行为；
2. 已渲染 UI 是否可以按固定间隔调用只读 `get_retrace_state`，是否出现重复审批或频率限制；
3. 插件 Hooks 与 MCP Server 是否能稳定访问同一个本地状态目录，并在 Codex 重启后恢复。

若第 1 项不支持，使用 inline card；若第 2 项不支持，使用声明过的状态 API 或手动刷新；若第 3 项不支持，使用单独的 localhost ReTrace service 作为共享状态进程。三项都不会推翻插件架构，只影响 UI 的刷新与呈现方式。

---

## 16. 核心技术表述

ReTrace 以 Codex 插件形式运行。插件利用生命周期 Hooks 被动采集用户提交、工具调用、工具结果和轮次完成事件，将其规范化后持续送入 Occasion Detector、State Observer 与 Skyline Selector。推理结果写入版本化本地状态库，并由插件的 MCP Server 通过只读和交互工具提供给 MCP UI。用户在会话开始时打开一次 ReTrace 面板；在宿主支持时，面板以 picture-in-picture 形式持续显示，并根据最新状态在观察、四类固定干预模板和评测界面之间切换。用户的选择、跳过、模板动作和场景化 Likert 回答通过 MCP 工具持久化，并与同一 decision chain、证据引用和配置版本绑定。该架构不修改 Codex 主 Agent，也不依赖 DOM 或不稳定 transcript 格式。
