# Real Episode Prefix、候选级证据绑定与参数校准规格

## 1. 目的与非目标

本模块把真实 Re-entry episode 接入离线干预选择器，用于检验和校准“在当时可获得的信息下应选择何种干预”。它不使用事件发生后的用户行动预测同一时刻的选择，也不估计干预的因果效果。

## 2. 时间边界

每个 episode 的输入边界为冻结的 `reentry_onset`，prefix 包含 onset 事件本身，不包含其后的回应、恢复行为和结果。

边界解析优先级：

1. `proposed_start` 或 `reentry_onset` 中的 `context_xxxx:Rn`；
2. 在整份 transcript 中唯一的数字 `record_index`；
3. 其他情况标记 `REVIEW_REQUIRED`，禁止猜测。

Transcript 可以包含跨行消息对象。解析器允许字符串内的未转义换行，但仍严格要求每个事件是对象，并包含 `source_context`、`record_index` 和 `role`。

## 3. Prefix manifest

`retrace-prefix-manifest-v1` 对 READY 案例保存：

- episode、匿名 participant group 与 stratum；
- onset 的 context、record、全局 sequence index 和 locator；
- transcript/prefix SHA-256；
- prefix/future event 数量；
- 每个可用事件的 evidence ID、locator、sequence index、timestamp、role 和内容哈希。

Manifest 不保存原始 `text/audit_text`。`leakage_check=PASS` 要求所有导出引用的 `sequence_index <= onset.sequence_index`。

## 4. 候选级证据绑定

真实状态使用 `retrace-state-v2`。每条 `EvidenceRef` 必须包含：

- `locator`、`sequence_index`、`content_sha256`；
- `available_at_decision=true`；
- 至少一个 `supports_needs` 或 `supports_primitives` 绑定；
- `source ∈ {OBSERVED, INFERRED, DESIGN_ASSUMPTION}`。

若设置 `supports_primitives`，证据只支持这些原语；否则按 `supports_needs` 支持以该 need 为 primary need 的候选。硬约束、E 分数和 Decision Brief 均只使用当前候选的支持证据。

Calibration ingestion 会把人工 state 中每条证据的 ID、locator、sequence index 与内容哈希和 prefix manifest 逐项核对，防止替换引用或借用 post-onset 证据。

## 5. 标签隔离与人工复核

完整 episode 的 v2.2 编码仅用于构造 `calibration_target`：

- RD01 映射为 expected `INTERVENE`，RD02 映射为 `NO_INTERVENTION`；
- RO/RA/EV/DR 编码映射为一个或多个可接受原语；
- onset 后的 source pointers 单独列为 `post_onset_target_pointers`；
- 整个 target 固定标记 `selector_visible=false`。

Reviewer 应在不查看 calibration target 的界面或分离文件中，只依据 prefix 编码 state、证据来源和候选绑定。只有 `review.status=APPROVED`、core、READY、泄漏 PASS 的案例进入主校准；edge 仅用于敏感性分析，excluded 不参与拟合。

## 6. 参数搜索与评估

当前离线搜索对象为：

- 五个正权重 `O/S/D/E/W`，总和为 1；
- `gain` 阈值；
- `near_tie` 阈值。

目标优先级依次为：整体匹配率、目标原语召回、较低过度干预率、较低不足干预率、与原设计参数的较小偏移。评估按匿名 participant group 分组，最多五折交叉验证，避免同一参与者的 episode 同时进入训练和测试。

输出是版本化参数建议与 held-out 指标，不自动覆盖 policy，也不解释为干预效果或因果最优。

## 7. 2026-08-20 实际接入结果

- 严格复核清单：271 例（core 196、edge 42、excluded 33）；
- READY prefix：265；边界歧义待复核：6；原始正文导出：0；泄漏失败：0；
- 已有 v2.2 annotation：20；其中 core 7、edge 10、excluded 3；
- onset 后 target pointers：38；
- 已批准 prefix state：0，因此参数拟合未运行。

完成真实参数校准前，至少还需：修正 6 个歧义 onset；增加至不少于 10 个可用 core 标注；由人工完成只看 prefix 的 state/evidence binding 复核，并确保至少覆盖 3 个参与者组。
