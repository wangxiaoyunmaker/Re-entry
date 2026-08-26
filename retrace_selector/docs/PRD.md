# PRD：ReTrace Skyline Selector MVP

## 1. Product goal

为研究者提供一个离线选择器：输入人工编码的 Cognitive Re-entry 状态，输出可审计的干预选择。MVP 验证选择机制能否稳定执行，而不是验证自动识别或干预因果效果。

## 2. Primary user

主要用户是研究者和系统设计者。若后续接入交互层，最终 vibe coding 用户只应看到渲染后的最小 Decision Brief，不接触数学分数或完整审计记录。

## 3. Functional requirements

### FR-1 Strict state input

输入必须包含：

- `process_state`
- `support_opportunity`：`NONE`、`EARLY_SUPPORT`、`REENTRY_SUPPORT` 或 `ABSTAIN`
- `support_needs` 下三个完整支持维度的整数等级 `0–3`
- 带来源的 evidence references
- `consequence`
- `reversibility`
- `authorization_risk`
- `evidence_completeness`
- `state_confidence`，范围 `[0,1]`
- `recent_interventions`
- `active_verification`
- independent runtime signals when available: `basis_relevant_signal`,
  `delegation_failure_signal`, `repeated_unresolved`
- optional process memory: `target_key`, `delegation_attempt_count`,
  `last_confirmed_progress`, `failure_window`, `cooldown_until`,
  `recent_intervention_ids`

未知字段、缺失字段和非法枚举必须产生结构化错误。

### FR-2 Versioned frozen policy

五个原语、三个强度、能力、负担、证据门槛、权重、目标缺口改善门槛和规则全部来自版本化 JSON 配置。配置加载失败时 fail closed。

### FR-3 Candidate generation

MVP 生成五个单原语的 L1–L3 候选和 `NO_INTERVENTION`。过程状态限制候选强度。双原语组合不属于 MVP。

### FR-4 Constraint engine

每条约束具有唯一 ID、优先级和可审计原因。安全/不可逆 > 授权 > 证据 > 状态置信度 > 过程状态 > 重复和负担。约束不允许被目标函数抵消。

### FR-5 Multi-criteria scoring

对可行候选计算三个支持维度、`evidence_quality` 和 `workflow_continuity`。所有分数有限且位于 `[0,1]`。`NO_INTERVENTION` 的基线为三个支持维度为 `0`、证据质量和工作流连续性为 `1`。

### FR-6 Skyline and selection

系统必须计算 dominance、一次 Skyline、`FrontierRatio`、参考点目标函数 `J(c)`、相对不干预的目标缺口改善和 near-tie。`SAFE_HOLD` 与 `REQUEST_CLARIFICATION` 是终止结果，不参加 Skyline。`utility` 不属于最终选择输入。

### FR-7 Auditable output

输出必须包含输入证据、配置版本与哈希、生成和删除的候选、约束结果、分数、dominance、Skyline、`J(c)`、目标缺口改善、最终选择和原因。

### FR-8 Frozen rendering

用户侧 Decision Brief 由版本化模板渲染。模板不能改变算法选择，也不能自由生成新的原语。

### FR-9 CLI and replay

CLI 支持单状态选择和批量 canonical scenario replay。历史 episode replay 的输入必须由上游截止在 Occasion 当时；当前选择器不自动验证时序泄漏。

## 4. Outcomes

| Outcome | Meaning |
|---|---|
| `INTERVENE` | 选择 `J(c)` 相对 B0 改善并越过门槛的候选；或在 B0 因安全/授权约束不可行时，选择最小 `J(c)` 的安全候选并标记 forced governance |
| `NO_INTERVENTION` | 目标缺口改善不足或直接委托仍有效 |
| `PRESENT_CHOICES` | 两个实质不同候选近似并列 |
| `REQUEST_CLARIFICATION` | 高风险状态与低置信度冲突 |
| `SAFE_HOLD` | 硬约束后没有安全可行候选 |

## 5. Non-goals

- 从原始对话自动识别 Occasion 或支持需求；
- 建立 Occasion 穷尽分类；
- 训练权重、个性化或估计成功概率；
- LLM 自由生成干预正文；
- 在线 IDE/Agent 接入；
- 自动修改、批准、回滚或暂停真实项目；
- 声称 Top-1 是普遍或因果最优。

## 6. Acceptance criteria

- 合法状态能够端到端选择并输出审计记录；
- 非法输入和配置均 fail closed；
- `DELEGATION_PROGRESSING` 返回 `NO_INTERVENTION`；
- `EARLY_SUPPORT_OPPORTUNITY` 不产生 L2/L3；
- 低置信度不产生高强度候选；
- 低置信度与高风险冲突返回 `REQUEST_CLARIFICATION`；
- 证据不足过滤 `CAUSAL_EXPLANATION-L2/L3`；
- `DESIGN_ASSUMPTION` 不能单独支撑 partial/sufficient，且高强度因果解释必须有 `OBSERVED` 证据；
- 高授权风险只允许 `DISPOSITION_COORDINATION-L2/L3`；
- 高后果且低可逆时不允许普通不干预或 L1；
- 低风险且高可逆时禁止 L3；
- `active_verification=true` 时不重复 Verification；
- 空可行集返回 `SAFE_HOLD`；
- 被支配候选不进入 Skyline，所有非支配候选均进入；
- 普通 Re-entry 候选未越过目标缺口改善门槛时返回 `NO_INTERVENTION`；`EARLY_SUPPORT` 使用独立的低负担改善门槛，但仍只允许 L1；
- near-tie 触发冻结的选择规则；
- 同一状态和 policy 产生确定性等价结果；
- CLI select 与 replay 均有端到端测试。
