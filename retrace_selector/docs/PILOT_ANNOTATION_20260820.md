# Real Prefix Annotation Pilot — 2026-08-20

这是一次由 Codex 辅助完成、用于跑通流程的 pilot，不是双研究者最终编码，也不是参数有效性结论。所有 state 只依据 onset 及之前的 prefix；每个 state 的证据均绑定到 onset 事件，完整 episode target 保持在独立文件中。文件中的 `APPROVED` 仅用于通过校准 API 的审批门槛，不代表正式人工批准。

## Pilot states

| Episode | Prefix-side interpretation | Needs (O/S/D) | Evidence completeness | Selector result |
|---|---|---:|---|---|
| SRE-0012 | 用户要求对已修复的网站进行真实网页验证；此前已经历多次干预 | 1/2/3 | partial | `NO_INTERVENTION`（cooldown） |
| SRE-0017 | 用户重新规定交付内容与范围，要求实现回到明确规则 | 3/2/1 | partial | `RULE_ALIGNMENT-L2` |
| SRE-0061 | 用户说明 SPS 工作数据与游戏化奖励之间需要建立连接规则 | 2/3/3 | partial | `DISPOSITION_COORDINATION-L3` |
| SRE-0112 | 用户质疑账号绑定解释，需要重建原因与状态依据 | 2/3/2 | partial | `PROVENANCE-L2` |

证据没有使用后续的“已经检查”“已经修复”“最终怎么处理”等事件。SRE-0012 的 `NO_INTERVENTION` 是一个有意义的 pilot 结果：当前冻结策略把近期干预次数达到 cooldown 阈值视为不宜继续增加干预，而不是因为没有识别到用户痛点。

## Pilot calibration

使用 4 个 pilot review、3 个参与者组和现有隔离 target ledger 运行校准：

- 推荐权重：`O=0.2, S=0.3, D=0.2, E=0.1, W=0.2`；
- `gain=0.025`，`near_tie=0.03`；
- 训练集匹配率：`1.00`；
- 分组交叉验证准确率：`0.75`；
- 分组交叉验证目标原语召回率：`0.75`。

由于样本只有 4 个，推荐参数不能替换当前 policy，也不能作为正式实证结果。正式校准仍需扩充 core episode、完成 bounded blind review，并达到预设的最小样本量。

## Artifacts

- `artifacts/pilot_annotation_20260820/pilot_reviews.jsonl`
- `artifacts/pilot_annotation_20260820/*.state.json`
- `artifacts/pilot_annotation_20260820/*.selection.json`
- `artifacts/pilot_annotation_20260820/pilot_calibration.json`
- `tools/build_pilot_reviews.py`
