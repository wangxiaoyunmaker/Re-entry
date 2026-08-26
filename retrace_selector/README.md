# ReTrace Skyline Selector

一个离线、确定性、可审计的 Cognitive Re-entry 干预选择器 MVP。当前同时保留两条入口：
旧 `select` 用于历史回放；`select-v06` 实现技术方案 v0.6 的精简核心对象、外置策略注册、
五维 Skyline、目标缺口和最终三步决策。

v0.6 接收由旧状态投影得到的 10 字段 `DecisionState`，从外部 Strategy Registry 读取
`StrategyCandidate`，执行硬约束、一次 Skyline 筛选，再用状态参考点目标函数 `J(c)` 与
`NO_INTERVENTION` 比较，返回：

- `INTERVENE`
- `NO_INTERVENTION`
- `PRESENT_CHOICES`
- `REQUEST_CLARIFICATION`
- `SAFE_HOLD`

选择器不自动识别原始对话中的 Re-entry，也不声称产生因果最优干预。当前
`config/strategy_registry.v0.6.json` 明确标为 `TEST_ONLY`，只验证接口和算法，不能作为正式
交互策略 taxonomy。交互设计团队提交策略后，只需替换 registry 与人工校准参数，不修改
v0.6 主流程。

v0.3 输入边界位于 `src/retrace_selector/v03.py`：它要求完整的
`criteria_basis_reconstruction`、`project_state_reconstruction`、
`evidence_action_governance`、`evidence_basis` 和 `trace_coverage` 字段，完成严格校验后再进入确定性 selector。当前 selector 内核仍保留兼容层，便于与既有 v0.2 配置和测试对照。

Support Profile 的上游抽取遵循 evidence-first 两阶段协议：先记录现有行为字段形成的事件级用户证据，再判断判断依据是否形成、是否被使用，最后才聚合为 selector-facing profile。实现与规范见 `src/retrace_selector/support_profile.py` 和 `docs/SUPPORT_PROFILE_EXTRACTION_SPEC.md`；关键词和 Episode fallback 不得直接生成机制维度。

## Quick start

```bash
cd /Users/wy/Desktop/HCI/retrace_selector
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m retrace_selector.cli select \
  --state examples/state_reentry_verification.json \
  --policy config/policy.v0.2.json \
  --templates config/templates.v0.2.json
PYTHONPATH=src python3 -m retrace_selector.cli select-v03 \
  --state path/to/retrace-state-v3.json \
  --policy config/policy.v0.2.json \
  --templates config/templates.v0.2.json
PYTHONPATH=src python3 -m retrace_selector.cli select-v06 \
  --state artifacts/pilot_annotation_20260820/SRE-0017.state.json \
  --policy config/selection_policy.v0.6.json \
  --registry config/strategy_registry.v0.6.json
PYTHONPATH=src python3 -m retrace_selector.cli replay \
  --states examples/canonical_scenarios.json \
  --policy config/policy.v0.2.json \
  --templates config/templates.v0.2.json
```

v0.6 实时接入与持久化（当前 `TEST_ONLY` registry 只能使用 `SHADOW`）：

```bash
PYTHONPATH=src python3 -m retrace_selector.cli runtime-select \
  --database var/retrace-runtime.sqlite3 \
  --request examples/runtime_request_v06.json \
  --policy config/selection_policy.v0.6.json \
  --registry config/strategy_registry.v0.6.json \
  --mode SHADOW
```

运行时接口直接接受 Observer/Skill 的内存状态，也支持 `--request -` 从标准输入读取。
`recent_intervention_count` 只在宿主上报 `INTERVENTION_PRESENTED` 后增加；验证开始/结束、
用户接受/拒绝和会话重置都作为幂等事件写入 SQLite。完整接口与安全回退说明见
[v0.6 runtime integration](docs/RUNTIME_INTEGRATION_V06.md)。

Selector sub-agent（仅 `DRY_RUN`）：

```bash
PYTHONPATH=src python3 -m retrace_selector.cli subagent \
  --request examples/subagent_request_pilot.json \
  --policy config/policy.v0.2.json \
  --templates config/templates.v0.2.json
```

状态 Skill 位于 `skills/retrace-state-builder/`，负责生成并校验 `retrace-state-v2`；`subagents/retrace-selector/` 是受限的 selector 子 Agent 封装。两者都不执行项目写操作，宿主 Agent 必须在用户确认后才执行干预。

真实 episode 接入：

```bash
PYTHONPATH=src python3 -m retrace_selector.cli build-prefixes \
  --core-inventory ../outputs/reentry_strict_final_20260820/core_reentry_episodes.csv \
  --edge-inventory ../outputs/reentry_strict_final_20260820/edge_reentry_episodes.csv \
  --excluded-inventory ../outputs/reentry_strict_final_20260820/excluded_episodes.csv \
  --annotations ../outputs/reentry_annotation_v22_existing_20260819_v2/annotation_results.jsonl \
  --output-dir artifacts/real_prefix_20260820
```

人工复核 `calibration_review_template.jsonl` 中的 prefix state 后，只有 `review.status=APPROVED`、属于 core、通过泄漏检查且证据引用与 prefix 完全一致的案例才会进入参数搜索：

```bash
PYTHONPATH=src python3 -m retrace_selector.cli calibrate \
  --reviews artifacts/real_prefix_20260820/calibration_review_template.jsonl \
  --targets artifacts/real_prefix_20260820/calibration_targets.jsonl \
  --prefix-manifest artifacts/real_prefix_20260820/prefix_manifest.jsonl \
  --policy config/policy.v0.2.json \
  --templates config/templates.v0.2.json \
  --output artifacts/real_prefix_20260820/calibration_result.json
```

## Project documents

- [PRD](docs/PRD.md)
- [Technical design](docs/TECHNICAL_DESIGN.md)
- [Specification](docs/SPECIFICATION.md)
- [Implementation issues](docs/IMPLEMENTATION_ISSUES.md)
- [Real prefix and calibration specification](docs/REAL_PREFIX_CALIBRATION_SPEC.md)
- [v0.6 runtime integration](docs/RUNTIME_INTEGRATION_V06.md)
- [Skill and Selector subagent technical design](../0820-ReTrace-Skill与Selector-子Agent技术方案-v0.1.md)
- [Observer LLM technical design](../0820-ReTrace-Observer-LLM技术方案-v0.1.md)

## Package boundary

v0.6 主路径：

```text
legacy/pilot state
→ state_adapter（10 字段 DecisionState）
→ external Strategy Registry
→ hard constraints
→ five-dimensional vector + state reference point
→ one Skyline
→ J(c) + Gain vs NO_INTERVENTION
→ INTERVENE / PRESENT_CHOICES / NO_INTERVENTION / SAFE_HOLD
→ registered template + sealed audit record
```

旧版研究与校准路径：

```text
raw episode + frozen onset
→ leakage-guarded prefix manifest
→ human-reviewed bound state
→ first-stage behavior evidence
→ runtime observer
→ basis-relevant signal / delegation-failure gate
→ second-stage Support Profile
→ structured state
→ candidate generation
→ hard constraints
→ scoring
→ one Skyline
→ target point + J(c) minimization
→ baseline comparison
→ frozen-policy selection
→ audit record and rendered Decision Brief
```

自动状态识别器、LLM 文案生成器和在线 Agent/IDE 集成不属于当前包。

Canonical replay 仍是规则验收，不是历史 episode 效果评估。真实 prefix 产物不复制原始敏感对话正文，只保存定位符、顺序号和内容哈希；完整 episode 标注仅作为不可见校准目标。

## Online inference v2

收敛版在线链路位于 `src/retrace_selector/online_inference_v2/`，不替换旧 v0.6
回放入口；`online_v2.py` 只是兼容导入入口。`OnlineInferenceService` 提供 Occasion/chain/target、规范化事件与
collector 顺序、late-event revision、C/S/A Observer、`D_obj + beta*D_user`
Selector、baseline/probe、真实 exposure、PRE/POST/CLOSE、linkage 和版本化
`get_retrace_state` 接口。另有 `UserPolicyPreference` 和 `AdaptiveController`：UI 可分别调整干预频率、力度和零干预模式；只有同一用户至少完成 3 个已关闭、同时具备完整 baseline 与 POST 三维回答的 chain 后，才以小步长更新偏好。Likert 非法值会被拒绝而不是截断。插件 UI 只需把 Hook/MCP 事件转成 `OnlineEvent`，并在
实际呈现成功后调用 `expose`；Selector recommendation 本身不会增加 exposure burden。

偏好接口为 `get_user_preferences(user_id)` 和
`set_user_preferences(user_id, frequency_preference, intensity_preference, mode, manual_lock)`；
`get_user_profile(user_id)` 返回 `subjective_preference`、`assessed_need` 和
`effective_policy` 三层用户干预画像。自适应不会修改 Registry、证据门槛、
`beta`/`epsilon` 或历史选择；每次选择和结果都保留偏好/profile 版本，便于离线回放和审计。

配置模板：

```text
config/strategy_registry.v2.json
config/selection_policy.v2.json
```

正式 MVP/Pilot 参数使用：

```text
config/strategy_registry.formal.v1.json
config/selection_policy.formal.v1.json
```

基于 20 个冻结 Episode 的参数敏感性回放，POC pilot 当前使用
`config/selection_policy.v2.json`（`epsilon=0.005`、`semantic_hint_soft_margin=0.12`）；正式 v1 配置仍保留
`epsilon=0.03`、`semantic_hint_soft_margin=0` 作为对照，直到完成逐轮人工校准。前者只减少近似平局双选项，后者只增强 MEDIUM hint 的语义排序，不是新的硬规则。

该 Registry 包含四类已确认交互的 12 个候选（每类 L1/L2/L3）。正式 Selector Policy 还启用 selector_hint 的 HIGH+语义支持证据硬 family gate、MEDIUM 软排序、强度上限、认知缺口判断、候选级 cooldown 和长时间无响应保护。可运行确定性的参数几何实验：

```bash
PYTHONPATH=src python3 tools/run_formal_registry_experiment.py \
  --output artifacts/formal_registry_v1_experiment.json
```

该实验验证 family 可达性、未知状态回退、候选注册和双选项约束，不代表真实干预效果。

5 个冻结 Episode 的逐轮语义约束回放：

```bash
PYTHONPATH=src python3 tools/run_real_episode_round_replay.py
```

结果写入 artifacts/real_episode_round_replay_v2.json 和
artifacts/real_episode_round_replay_v2.md。其中 C/S/A、selector_hint 和 evidence refs
只基于当前冻结用户轮次的 pilot coding，不读取后验 outcome annotation 的 evidence，
也不是金标准干预效果标签；回放同时覆盖 UNKNOWN、INSUFFICIENT_EVIDENCE、
INTERVENTION_NOT_ELIGIBLE 和 NO_RESPONSE_TIMEOUT。

CLI 也提供 `online-ingest`、`online-replay`、`online-select`、`online-baseline`、`online-expose`、
`online-action`、`online-evaluate`、`online-state`、`online-linkage` 和 `online-preferences` 命令；其中
`online-preferences` 可读取或写入用户的频率、力度和零干预模式。它们
共享同一个 SQLite 数据库，可与旧 v0.6 表共存。
