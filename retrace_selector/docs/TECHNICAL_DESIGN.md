# Technical Design

## 1. Architecture

```text
JSON state ─┐
            ├→ strict loaders → candidate generator → constraint engine
policy JSON ┘                                      ↓
                         audit ← selector ← skyline ← scorer
                                      ↓
                              frozen template renderer
```

状态识别、选择算法和内容渲染严格分层。算法内核使用纯函数，不调用网络、不读取原始对话、不写项目文件。

## 2. Modules

| Module | Responsibility |
|---|---|
| `models.py` | 枚举、状态、候选、分数和结果类型 |
| `config.py` | 严格加载 policy/templates，计算 SHA-256 |
| `candidates.py` | 生成 canonical 单原语候选和 B0 |
| `constraints.py` | 逐候选硬约束与全局冲突检查 |
| `scoring.py` | O/S/D/E/W 和冻结效用 |
| `skyline.py` | epsilon dominance、前沿与支配证据 |
| `selector.py` | 端到端编排、gain、tie 和 outcome |
| `rendering.py` | 冻结模板渲染 |
| `audit.py` | canonical JSON、JSONL 和幂等审计 ID |
| `replay.py` | 批量运行与汇总指标 |
| `cli.py` | select/replay 命令 |

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
```

## 4. Decision rules

### 4.1 Global preflight

低状态置信度与高授权风险，或低状态置信度与“高后果+低可逆”同时出现时，直接返回 `REQUEST_CLARIFICATION`。这避免 L1 限制与强制 L2/L3 发生不可解冲突。

### 4.2 Candidate constraints

`NO_INTERVENTION` 是普通候选和 gain 基线；高风险时可以被约束删除。即使被删除，其基线分数仍用于诊断 gain。`SAFE_HOLD` 不参与候选比较。

### 4.3 Scoring

对 `k∈{O,S,D}`：

```text
score_k = intrinsic_capability_k
          × level_multiplier
          × normalized_need_k
```

`E` 由证据门槛和当前 evidence completeness 计算；`W=1-burden-cooldown_penalty`。

### 4.4 Skyline and ranking

所有维度越大越好。Dominance 使用统一 epsilon。权重只用于 Skyline 内排序。强制治理场景绕过普通 gain gate，但仍记录相对 B0 gain。

## 5. Determinism and audit

- 输入、policy、templates 使用 canonical JSON；
- policy/templates 的 SHA-256 写入结果；
- 候选和输出按 canonical ID 排序；
- 相同输入和配置产生相同 `audit_id` 与决策内容；
- 运行时间不进入确定性决策对象；
- 审计 JSONL 检测重复 `audit_id`。

## 6. Extension boundary

双原语 Brief、自动状态识别、学习策略和生成式 renderer 必须作为独立版本进入，不能静默改变 MVP policy。
