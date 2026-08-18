# P0 Gate v2.5 评估报告

## 结论

当前推荐将 **v2.5 完整 Few-Shot 版**作为 P0 高召回候选筛选的冻结候选，而不是 v2.4 或 Compact-6 版。

v2.5 不把 P0 当作最终 Re-entry 裁决。它只判断当前事件是否值得进入后续验证；直接委托是否足够、是否形成治理活动以及是否构成完整 Re-entry，仍由下游阶段判断。

本报告后续根据严格理论边界修正了两个参考标签：`V25H-013` 与 `V25H-014` 均改为 `DO_NOT_RETAIN`。这次只改变人工参考标签，没有改变 Prompt、Few-Shot 或模型输出。

## 前瞻留出集

20 条事件在 v2.5 Prompt 执行前完成抽样，之后按严格理论边界完成单人参考编码：8 条 `RETAIN_STRONG`、1 条 `RETAIN_POSSIBLE`、11 条 `DO_NOT_RETAIN`。

| Run | Valid | Precision | Recall | F1 | Strong Recall | Possible Recall | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| v2.5 full r1 | 20/20 | 0.8182 | 1.0000 | 0.9000 | 1.0000 | 1.0000 | 2 | 0 |
| v2.5 full r2 | 20/20 | 0.6923 | 1.0000 | 0.8182 | 1.0000 | 1.0000 | 4 | 0 |

- Binary agreement：0.9000
- Exact three-label agreement：0.9000
- 两次运行均未漏掉 `RETAIN_STRONG`。
- 不稳定项集中在 `RETAIN_POSSIBLE` 与普通视觉迭代的边界。

需要第二编码员重点复核：

- `V25H-005`：规划阶段提出语音入口是否真的暴露现有架构限制。
- `V25H-017`：在已完成按钮上增加浅色模式是否只是新功能。

## 与 v2.4 的关系

v2.4 在 24 条前瞻样本上的结果为 Precision 0.8182、Recall 0.7500、F1 0.7826。它减少了部分普通承接误报，但同时漏掉了明确的原产物不一致和运行期崩溃，因此不适合作为 P0 高召回入口。

v2.5 回到 v2.3 的宽松 Gate，只保留三类稳定排除：

1. 直接回答 Agent 所需输入；
2. 选择或提供用户已有的外部材料；
3. 明确认可当前结果后转入文档、复用或执行。

同时保留两项召回保护：Agent 产物与原物不一致，以及 Agent 正在测试/打包当前修改时用户报告的运行期失败。

## Few-Shot 成本消融

| Variant | Dataset / Run | Precision | Recall | F1 | Prompt tokens / event |
|---|---|---:|---:|---:|---:|
| Full anchors | v2.5 holdout r1 | 0.8182 | 1.0000 | 0.9000 | 5,988.8 |
| Full anchors | v2.5 holdout r2 | 0.6923 | 1.0000 | 0.8182 | 5,988.8 |
| Compact-6 | v2.5 holdout r1 | 0.7273 | 0.8889 | 0.8000 | 4,939.8 |
| Compact-6 | v2.5 holdout r2 | 0.6667 | 0.8889 | 0.7619 | 4,939.8 |

Compact-6 节省约 17.5% 输入 token，但稳定降低召回，因此不替代完整版本。后续若继续降成本，应尝试按事件类型动态检索 2–4 个锚点，而不是静态删除一半示例。

## 工程回归

- 研究者编写的边界集：24/24 二元与三标签均正确。
- 安全回归：8/8 API cases 通过。
- 伪造 XML 闭合标签已转义。
- Prompt injection 样本未导致越权保留。
- 未知 Event ID 被拒绝。
- Wrapper 可归一化 `verdicts`、`results`、事件 ID map 和 singleton array 等常见 JSON 包装漂移；归一化后仍执行完整 Event ID 与字段白名单校验。

## 使用边界

当前结果仍是 **single-coder provisional reference**，不能称为 Gold Standard。建议在全量运行前完成：

1. 对上述四个边界案例做双人独立复核；
2. 从全量 Trace 随机抽取一批无关键词事件做人类漏报审计；
3. 报告 P0 与后续直接委托充分性 Gate 的串联召回，而不是只报告 P0 指标。
