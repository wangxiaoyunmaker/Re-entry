# ReTrace Skyline Selector

一个离线、确定性、可审计的 Cognitive Re-entry 干预选择器 MVP。

它接收人工编码的结构化状态，生成五类干预原语的 L1–L3 候选，执行硬约束、`O/S/D/E/W` 评分、Skyline/Pareto 筛选和冻结策略排序，并返回：

- `INTERVENE`
- `NO_INTERVENTION`
- `PRESENT_CHOICES`
- `REQUEST_CLARIFICATION`
- `SAFE_HOLD`

选择器不自动识别原始对话中的 Re-entry，也不声称产生因果最优干预。当前版本增加了真实 episode prefix 构建、候选级证据绑定，以及基于人工批准 prefix state 的参数校准管线。

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
- [Skill and Selector subagent technical design](../0820-ReTrace-Skill与Selector-子Agent技术方案-v0.1.md)

## Package boundary

```text
raw episode + frozen onset
→ leakage-guarded prefix manifest
→ human-reviewed bound state
→ structured state
→ candidate generation
→ constraints
→ scoring
→ skyline
→ frozen-policy selection
→ audit record and rendered Decision Brief
```

自动状态识别器、LLM 文案生成器和在线 Agent/IDE 集成不属于当前包。

Canonical replay 仍是规则验收，不是历史 episode 效果评估。真实 prefix 产物不复制原始敏感对话正文，只保存定位符、顺序号和内容哈希；完整 episode 标注仅作为不可见校准目标。
