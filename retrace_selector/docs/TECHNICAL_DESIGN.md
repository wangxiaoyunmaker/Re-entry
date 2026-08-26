# Technical Design

## 1. Architecture

```text
episode inventory → onset resolver → prefix manifest → human prefix review ┐
                                                                           ├→ strict state loader
policy JSON ────────────────────────────────────────────────────────────────┘
          → first-stage observer → support-profile analysis
          → candidate generator → hard constraints → scorer → one skyline
          → reference-point objective J(c) → baseline gate
          → selector → frozen renderer → audit
```

状态识别、选择算法和内容渲染严格分层。算法内核使用纯函数，不调用网络、不读取原始对话、不写项目文件。

## 2. Modules

| Module | Responsibility |
|---|---|
| `models.py` | 枚举、状态、候选、分数和结果类型 |
| `real_prefix.py` | 解析真实 transcript、冻结 onset prefix、生成无正文证据清单和泄漏报告 |
| `evidence.py` | 将证据按 support dimension/primitive 绑定到候选并计算候选级证据完整度 |
| `calibration.py` | 隔离完整 episode 标签、校验人工批准状态、搜索参数并按参与者分组交叉验证 |
| `config.py` | 严格加载 policy/templates，计算 SHA-256 |
| `candidates.py` | 生成 canonical 单原语候选和 B0 |
| `constraints.py` | 逐候选硬约束与全局冲突检查 |
| `scoring.py` | 生成五维候选分数；不负责最终选择目标 |
| `objective.py` | 根据状态目标点计算 `J(c)` 与相对 B0 的目标缺口改善 |
| `skyline.py` | epsilon dominance、前沿与支配证据 |
| `selector.py` | 端到端编排、一次 Skyline、`J(c)`、baseline gate、tie 和 outcome |
| `rendering.py` | 冻结模板渲染 |
| `audit.py` | canonical JSON、JSONL 和幂等审计 ID |
| `replay.py` | 批量运行与汇总指标 |
| `cli.py` | select/replay/build-prefixes/calibrate 命令 |

## 3. Core API

```python
engine = SelectionEngine(policy, templates)
result = engine.select(state)
```

阶段函数保持独立可测试：

```python
generate_candidates(state, policy)
evaluate_constraints(brief, state, policy)
score_brief(brief, state, policy)
compute_skyline(scored, epsilon)
objective_value(score, state, policy)  # J(c), lower is better
```

## 4. Decision rules

### 4.1 Global preflight

低状态置信度与高授权风险，或低状态置信度与“高后果+低可逆”同时出现时，直接返回 `REQUEST_CLARIFICATION`。这避免 L1 限制与强制 L2/L3 发生不可解冲突。

### 4.2 Candidate constraints

`NO_INTERVENTION` 是普通候选和目标缺口改善基线；高风险时可以被约束删除。即使被删除，其基线 `J(B0)` 仍用于诊断目标缺口改善。`SAFE_HOLD` 不参与候选比较。

`DESIGN_ASSUMPTION` 只标识设计依据，不能单独支撑 partial/sufficient 的项目证据完整度。`CAUSAL_EXPLANATION-L2/L3` 还必须有至少一条与该候选绑定的 `OBSERVED` 证据。候选级绑定优先于宽泛的 need 绑定；一个候选不能借用只支持其他 need/primitive 的证据。

### 4.3 Scoring and objective

对三个支持维度：

```text
score_k = intrinsic_capability_k
          × level_multiplier
          × normalized_need_k
```

`minimum_evidence` 是硬约束门槛；`evidence_quality` 根据该候选所绑定证据计算，不会因候选无最低证据要求而自动置 1。`workflow_continuity=1-burden-cooldown_penalty`。

候选通过硬约束后只计算一次 Skyline。对 Skyline 前沿中的每个候选构造状态相关的目标点 `r`，并计算：

```text
gap_j(c) = max(0, r_j - x_j(c))
J(c) = Σ_j w_j × gap_j(c) + λ × max_j gap_j(c)
improvement(c) = J(B0) - J(c)
```

其中 `x(c)` 是五维候选分数，`w` 是冻结的客观因子权重，`λ` 惩罚最大单维缺口，目标是让 `J(c)` 越小越好。普通 Re-entry 使用 `improvement` 门槛；`EARLY_SUPPORT` 使用独立的低负担门槛。`utility` 不再是选择目标，也不参与排序。

### 4.4 Skyline and ranking

所有维度越大越好。Dominance 使用统一 epsilon。Skyline 只删除被支配候选；最终排序由 `J(c)` 完成，而不是再次调用 Skyline。强制治理场景绕过普通 improvement gate，但仍记录相对 B0 的目标缺口改善。

运行时观察器与选择器分开：`basis_relevant_signal` 只负责启动第二阶段分析，不直接生成干预；`delegation_failure_signal`、`repeated_unresolved` 和过程记忆字段独立保存在状态中。`target_key` 用于识别同一目标，失败计数/窗口用于判断重复未解决，`cooldown_until` 和 `recent_intervention_ids` 用于抑制重复打扰。

## 5. Determinism and audit

- 输入、policy、templates 使用 canonical JSON；
- policy/templates 的 SHA-256 写入结果；
- engine version 由代码拥有，policy 版本不兼容时 fail closed；
- 候选和输出按 canonical ID 排序；
- 相同输入和配置产生相同 `audit_id` 与决策内容；
- 决策内容另有 `decision_digest`，写入前校验封存后变化；
- 运行时间不进入确定性决策对象；
- 审计 JSONL 检测重复 `audit_id`，同 ID 异内容必须报冲突。

## 6. Extension boundary

双原语 Brief、自动状态识别和生成式 renderer 必须作为独立版本进入，不能静默改变当前 offline policy。
在线推理 v2 已作为 `src/retrace_selector/online_inference_v2/` 独立边界实现，并由仓库根目录的在线推理
技术方案描述；它可以持久化三层 User Profile（主观偏好、评估需要、当前生效策略），但不能反向修改本离线
engine 的 Registry、证据门槛、`beta`、`epsilon` 或历史选择。离线 calibration 仍只能输出版本化建议参数，
不能自动覆盖生产配置。

## 7. Online inference v2 implementation boundary

在线 v2 的场景化评测是固定的两轮三题协议：`submit_occasion_baseline` 在首次 exposure 前保存
Criteria/State/Action 基线，`submit_evaluation` 在 chain 结束时保存同三维 POST 结果并生成 POST 快照。
两轮之间的 `PRE` 只由真实 `expose()` 产生；`OBSERVER_PROBE` 不属于 baseline/post 评测。

用户自主调节通过 `get_user_preferences` / `set_user_preferences` 提供频率、力度、`AUTO/PAUSED` 和
`manual_lock`；Profile 通过 `get_user_profile` 返回三层持久化对象。完整 baseline+POST chain 达到至少 3 个
已关闭 chain 后，`AdaptiveController` 才以每次最多 `±0.08` 的小步长更新 assessed need 和 effective policy。
