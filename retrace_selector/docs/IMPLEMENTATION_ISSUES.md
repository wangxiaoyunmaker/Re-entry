# Implementation Issues Log

本文件记录实施过程中遇到的歧义、决策、风险和后续事项。已解决问题仍保留，以便论文和系统审计。

## 2026-08-20 — I-001：低置信度与高风险规则冲突

- **问题：**低置信度只允许 L1，但高授权或高后果低可逆要求 L2/L3。
- **决策：**在候选生成前返回 `REQUEST_CLARIFICATION`，不允许任一约束静默覆盖另一约束。
- **状态：**Frozen。

## 2026-08-20 — I-002：SAFE_HOLD 的算法位置

- **问题：**若将 SAFE_HOLD 当普通候选，会混淆治理收益与安全终止。
- **决策：**SAFE_HOLD 是空可行集的终止 outcome，不参与评分或 Skyline。
- **状态：**Frozen。

## 2026-08-20 — I-003：NO_INTERVENTION 与 RECORD_ONLY 混写

- **问题：**前者是用户侧动作选择，后者是内部日志行为。
- **决策：**B0 ID 只使用 `NO_INTERVENTION`；可持久化的决策对象单独输出 `audit_record_ready`，不声称 CLI 已经写入文件。
- **状态：**Frozen。

## 2026-08-20 — I-004：双原语范围不可测试

- **问题：**组合白名单、协同和 IntegrationCost 尚无经验基础。
- **决策：**MVP 只实现单原语；双原语保留为后续 policy 版本。
- **状态：**Deferred。

## 2026-08-20 — I-005：运行环境没有第三方验证库

- **问题：**系统 Python 没有 Pydantic、PyYAML、pytest 或 Hypothesis。
- **决策：**MVP 使用标准库 dataclass、严格 JSON loader 和 unittest，保持零运行依赖；性质不变量通过确定性生成测试覆盖。
- **状态：**Resolved。

## 2026-08-20 — I-006：CLI 全局参数位置与文档示例不一致

- **问题：**首次 smoke test 发现 `--policy/--templates` 只能放在子命令前，而 README 将其放在 `select/replay` 后，导致 argparse 拒绝。
- **决策：**将配置参数定义在两个子命令上，使调用顺序与常用 CLI 习惯和文档一致。
- **状态：**Resolved by smoke test。

## 2026-08-20 — I-007：典型场景中的 Skyline 退化

- **问题：**首批 5 个 canonical scenarios 的 `FrontierRatio` 均为 1，Skyline 没有删除候选。扩展至 13 个规则场景后，`mean_frontier_ratio=0.9333`、`skyline_deletion_rate=0.0698`，仍说明当前维度、离散档位和候选画像容易形成全面 trade-off。
- **决策：**不为制造删除率而调整参数。MVP 保留 `W003_SKYLINE_FRONTIER_RATIO_HIGH`、支配见证和批量退化指标；用合成测试验证 Skyline 算法本身，并把真实状态回放下的删除率作为 policy calibration gate。
- **状态：**Open empirical calibration risk，不阻断离线研究原型。

## 2026-08-20 — I-008：证据来源只记录、未约束选择

- **问题：**初版允许单个 `DESIGN_ASSUMPTION` 与 `evidence_completeness=sufficient` 并存，会把设计设定误当项目事实；高强度因果解释也没有直接观测门槛。
- **决策：**partial/sufficient 必须至少有 `OBSERVED` 或 `INFERRED`；`CAUSAL_EXPLANATION-L2/L3` 必须至少有一条 `OBSERVED`。
- **状态：**Resolved by validation, hard constraint and regression tests。

## 2026-08-20 — I-009：forced governance 与“优于不干预”表述冲突

- **问题：**高后果/授权场景中 B0 可能因硬约束被删除，此时安全候选即使冻结效用低于 B0 也必须被选择。
- **决策：**PRD 明确区分普通 gain-gated intervention 与 forced-governance intervention；后者保留负 gain 作为诊断，并写入原因码与 metadata。
- **状态：**Resolved in semantics and audit output。

## 2026-08-20 — I-010：冻结配置可被嵌套 dict 绕过

- **问题：**frozen dataclass 内的普通 dict 仍可修改，会在 config hash 不变时改变约束和结果。
- **决策：**loader 对所有嵌套 policy/template mapping 做防御性复制和只读封装；engine version 改由代码拥有并校验 policy 兼容性。
- **状态：**Resolved with mutation and compatibility regression tests。

## 2026-08-20 — I-011：Replay oracle 拼写错误被静默忽略

- **问题：**scenario 顶层未做严格 schema 校验，拼错断言字段仍可显示 PASS。
- **决策：**Replay 拒绝未知/缺失字段，每个 scenario 至少有一个 oracle，并强制 scenario_id/decision_id 唯一。
- **状态：**Resolved with negative regression tests。

## 2026-08-20 — I-012：当前 Replay 不是历史 episode 效果验证

- **问题：**13 个 canonical scenarios 是规则验收样例，尚未运行真实 episode prefix replay；当前 state schema 也无法自动证明输入排除了 Occasion 之后的信息。
- **决策：**只声称完成选择机制验证。历史 replay 必须由上游提供截止时间和 evidence manifest，并另行报告泄漏审计、专家一致性和过度干预率。
- **状态：**Deferred empirical validation，不阻断离线机制 MVP。

## 2026-08-20 — I-013：参数与候选级证据关联尚属设计假设

- **问题：**权重、能力、负担和阈值尚未经双研究者标注或真实效果数据校准；当前 Brief 携带状态中的 evidence IDs，尚未建立 claim/primitive 级支持关系。
- **决策：**将当前 policy 统一定位为 `DESIGN_ASSUMPTION`；候选级 evidence linkage 和参数 calibration 作为进入用户评估前的 gate。
- **状态：**Open research calibration task。
