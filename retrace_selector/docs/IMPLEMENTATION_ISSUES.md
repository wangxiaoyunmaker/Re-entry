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
- **决策：**B0 ID 只使用 `NO_INTERVENTION`；所有运行默认记录审计，单独输出 `record_only`。
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

- **问题：**首批 5 个 canonical scenarios 的 `FrontierRatio` 均为 1，Skyline 没有删除候选。这说明当前维度、离散档位和候选画像容易形成全面 trade-off，Skyline 在这些场景中只是正确性检查，尚未证明具有实际筛选增益。
- **决策：**不为制造删除率而调整参数。MVP 保留 `W003_SKYLINE_FRONTIER_RATIO_HIGH`、支配见证和批量退化指标；用合成测试验证 Skyline 算法本身，并把真实状态回放下的删除率作为 policy calibration gate。
- **状态：**Open empirical calibration risk，不阻断离线研究原型。
