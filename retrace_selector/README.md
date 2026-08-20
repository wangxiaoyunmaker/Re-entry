# ReTrace Skyline Selector

一个离线、确定性、可审计的 Cognitive Re-entry 干预选择器 MVP。

它接收人工编码的结构化状态，生成五类干预原语的 L1–L3 候选，执行硬约束、`O/S/D/E/W` 评分、Skyline/Pareto 筛选和冻结策略排序，并返回：

- `INTERVENE`
- `NO_INTERVENTION`
- `PRESENT_CHOICES`
- `REQUEST_CLARIFICATION`
- `SAFE_HOLD`

MVP 不自动识别原始对话中的 Re-entry，不学习权重，也不声称产生因果最优干预。

## Quick start

```bash
cd /Users/wy/Desktop/HCI/retrace_selector
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m retrace_selector.cli select \
  --state examples/state_reentry_verification.json \
  --policy config/policy.v0.2.json \
  --templates config/templates.v0.2.json
PYTHONPATH=src python3 -m retrace_selector.cli replay \
  --states examples/canonical_scenarios.json \
  --policy config/policy.v0.2.json \
  --templates config/templates.v0.2.json
```

## Project documents

- [PRD](docs/PRD.md)
- [Technical design](docs/TECHNICAL_DESIGN.md)
- [Specification](docs/SPECIFICATION.md)
- [Implementation issues](docs/IMPLEMENTATION_ISSUES.md)

## Package boundary

```text
structured state
→ candidate generation
→ constraints
→ scoring
→ skyline
→ frozen-policy selection
→ audit record and rendered Decision Brief
```

状态识别器、LLM 文案生成器和在线 Agent/IDE 集成不属于当前包。

当前示例与 canonical replay 均为人工构造状态，不是历史 episode 效果评估。审计记录仅保存 evidence reference，不应复制原始敏感对话内容。
