# 基于 Skyline 的状态条件化多准则干预选择

> v0.2：Cognitive Re-entry 过程模型对齐版
> 日期：2026-08-20
> 状态：讨论稿

## 1. 方案目标

本方案解决一个具体问题：

> 当系统发现用户可能需要 Cognitive Re-entry 支持时，如何从多种交互干预中选择当前最合适的一种？

我们将它建模为带硬约束的状态条件化多准则选择：

```text
交互与项目证据
→ Re-entry 状态与治理需求
→ 生成干预候选
→ 硬约束过滤
→ Skyline/Pareto 非支配集
→ 冻结策略排序
→ 干预 / 不干预 / 安全保持
```

当前没有真实干预 exposure 和反事实结果，因此这里的“最优”是：

> **给定状态估计、人工定义准则和冻结策略下的条件最优，而不是已经被用户效果数据证明的最优干预。**

## 2. 上游过程模型

### 2.1 三层理论输入

| 理论层 | 技术作用 |
|---|---|
| 双侧项目模型缺口 | 提供用户侧或 Agent 侧理解偏离的结构背景 |
| Re-entry Occasion | 判断用户何时意识到直接委托已经不足 |
| 治理重构实践 | 判断用户当前需要恢复什么治理能力 |

双侧 Gap 不直接决定干预。真正进入候选生成的是当前治理需求。

### 2.2 三类治理需求

| ID | 治理需求 | 内容 |
|---|---|---|
| `O` | Criteria Operationalization | 把目标转化为规则、边界案例和验收标准 |
| `S` | Project-state Reconstruction | 重建当前状态、历史修改、因果、依赖和影响范围 |
| `D` | Verification–Disposition Reconfiguration | 重组证据、验证责任、行动顺序、范围、批准、回退和结束条件 |

三类需求可以同时出现，不构成固定阶段。

### 2.3 Occasion window

系统不根据单个 Prompt 直接选择干预，而是观察完整事件窗口：

```text
前置项目状态
→ 用户遇到的近端线索
→ 用户如何解释该线索
→ 为什么直接委托已经不足
→ 用户采取的第一个 Re-entry 行为
```

Occasion 的最终类别仍应由全部 episode 的开放编码产生，本系统不预设穷尽 taxonomy。

### 2.4 Re-entry 与提前支持

| 过程状态 | 默认处理 |
|---|---|
| `DELEGATION_PROGRESSING` | `NO_INTERVENTION` |
| `EARLY_SUPPORT_OPPORTUNITY` | 仅生成可忽略的 L1 候选 |
| `REENTRY_OCCASION_OBSERVED` | 根据 `O/S/D` 生成干预候选 |
| `GOVERNANCE_RECOVERING` | 降低干预或退出 |

提前支持机会可能出现在用户主动 Re-entry 之前，但不能被写成用户已经发生 Re-entry。

### 2.5 证据来源

| 来源 | 含义 |
|---|---|
| `OBSERVED` | 可在对话、工具事件或项目材料中直接定位 |
| `INFERRED` | 根据多个事件推断出的 Gap、Occasion 或治理需求 |
| `DESIGN_ASSUMPTION` | 干预原语、准则和参数等设计设定 |

三个来源必须分别记录，不能混写成同等强度的经验事实。

`DESIGN_ASSUMPTION` 不能单独支撑 partial/sufficient 的项目证据完整度；高强度因果解释还必须至少有一条 `OBSERVED` 证据。

## 3. 最小状态输入

```json
{
  "process_state": "REENTRY_OCCASION_OBSERVED",
  "governance_needs": {"O": 0, "S": 2, "D": 3},
  "evidence_ids": ["R1434", "R1442"],
  "consequence": "high",
  "reversibility": "medium",
  "authorization_risk": "low",
  "evidence_completeness": "partial",
  "state_confidence": 0.82,
  "recent_interventions": 1
}
```

`O/S/D` 第一版只使用 `0/1/2/3` 序数等级，不解释为精确心理测量。

| 状态字段 | 用途 |
|---|---|
| `process_state` | 决定是否介入和允许的最高强度 |
| `governance_needs` | 判断候选与当前需求的兼容性 |
| `evidence_completeness` | 过滤无证据断言 |
| `consequence/reversibility` | 设置安全和强度约束 |
| `authorization_risk` | 判断是否必须进行处置协商 |
| `state_confidence` | 决定正常选择、降级或 abstain |
| `recent_interventions` | 避免短时间重复打断 |

## 4. 干预候选

### 4.1 五个设计原语

| 原语 | 系统动作 | 主要支持需求 |
|---|---|---|
| `RULE_ALIGNMENT` | 对齐目标、历史规则和当前实现 | `O` |
| `PROVENANCE` | 展示文件、版本、修改和来源 | `S` |
| `CAUSAL_EXPLANATION` | 提供有证据的现象—原因—影响关系 | `S` |
| `VERIFICATION` | 提供或协商与目标直接相关的验证 | `D` |
| `DISPOSITION_COORDINATION` | 协商责任、顺序、范围、批准、回退和结束条件 | `D` |

这些是系统设计组件，不是从日志直接发现的五类用户或五类 Occasion。

`DISPOSITION_COORDINATION` 取代原来的 `CONTROL_RETURN`，因为用户需要的不只是收回 Agent 控制权，而是重新组织双方如何验证和处置项目。

### 4.2 强度

| 强度 | 含义 |
|---|---|
| `L1` | 轻量、可忽略的提示或入口 |
| `L2` | 明确建议用户判断、验证或选择 |
| `L3` | 高后果情况下暂停行动并等待处置 |

不展示干预表示为 `NO_INTERVENTION`。`RECORD_ONLY` 是内部审计行为，不是候选干预。

### 4.3 Decision Brief

最终候选不是抽象原语名称，而是一个可展示的 Decision Brief，例如：

```text
PROVENANCE-L1
RULE_ALIGNMENT-L2
CAUSAL_EXPLANATION-L2
DISPOSITION_COORDINATION-L2
NO_INTERVENTION
```

MVP 只生成单原语 Brief。双原语需要额外冻结组合白名单、协同效用和整合负担，留待后续 policy 版本。

## 5. 多准则属性

每个 Brief 在当前状态下具有五维属性：

$$
x(B,s)=[O(B,s),S(B,s),D(B,s),E(B,s),W(B,s)]
$$

| 准则 | 含义 |
|---|---|
| `O` | 对判断标准操作化的支持程度 |
| `S` | 对项目状态及关系重建的支持程度 |
| `D` | 对验证与处置重构的支持程度 |
| `E` | 输出的证据基础和可追溯程度 |
| `W` | 工作流连续性，即低负担、低重复和低打断 |

所有准则统一为“越高越好”。安全、授权和最低证据要求作为硬约束，不允许被其他收益抵消。

### 5.1 动态属性

组件的当前收益由固有能力与状态兼容性共同决定：

$$
g_k(c,s)=b_k(c)\cdot\rho_k(c,s),\quad k\in\{O,S,D\}
$$

- `b_k(c)`：组件在该维度上的固有能力上限；
- `ρ_k(c,s)`：该组件与当前治理需求的兼容性；
- `ρ` 不是干预成功概率。

兼容性使用 `0/0.25/0.5/0.75/1` 等有限等级初始化。

### 5.2 Brief 聚合

MVP 不设置未经验证的协同加分。同一维度采用保守聚合：

$$
K(B,s)=\max_{c\in B}g_K(c,s),\quad K\in\{O,S,D\}
$$

证据采用短板原则：

$$
E(B,s)=\min_{c\in B}E(c,s)
$$

工作流连续性为：

$$
W(B,s)=1-Clip\left(\sum_{c\in B}Burden(c,s)+IntegrationCost(B,s),0,1\right)
$$

### 5.3 不干预基线

$$
x(B_0,s)=[0,0,0,1,1]
$$

`NO_INTERVENTION` 不提供治理支持，但不产生新断言和新打断。它应进入普通比较，而不是强制系统每次选择一种干预。

## 6. 硬约束

| 条件 | 约束 |
|---|---|
| 状态置信度低 | 普通风险只允许 L1；与高风险冲突时返回 `REQUEST_CLARIFICATION` |
| 证据不足 | 禁止高强度因果解释和确定性完成声明 |
| 授权风险高 | 必须包含 `DISPOSITION_COORDINATION-L2/L3` |
| 后果高且不可逆 | 不允许普通不干预或仅 L1 |
| 低风险且可逆 | 默认禁止 L3 |
| 用户正在有效验证 | 不重复提供同一 Verification |
| 短时间连续干预 | 降级、延迟或不干预 |
| Brief 负担过高 | 删除、降级或拆分候选 |

可行集为：

$$
F(s)=\{B\in G(s)\mid Constraints(B,s)=true\}
$$

如果硬约束后没有安全可行候选，返回 `SAFE_HOLD`，不能放宽硬约束制造答案。`REQUEST_CLARIFICATION` 仅用于全局预检阶段的低置信度—高风险冲突。

## 7. Skyline 与最终选择

### 7.1 Pareto 支配

如果 `B_i` 在全部准则上不差于 `B_j`，且至少一项更好，则：

$$
B_i\succ_s B_j
$$

Skyline 为：

$$
Sky(F(s))=\{B\in F(s)\mid \nexists B'\in F(s),B'\succ_s B\}
$$

Skyline 只产生非支配候选集，不产生唯一第一名。

### 7.2 前沿内排序

使用研究前冻结的策略参数 `θ`：

$$
U_\theta(B\mid s)=w_\theta(s)^Tx(B,s)
$$

定义相对不干预增益：

$$
Gain_\theta(B\mid s)=U_\theta(B\mid s)-U_\theta(B_0\mid s)
$$

当 `B_0` 通过硬约束时，只有最佳候选的增益超过 `τ_gain` 才展示干预：

$$
B_\theta^*(s)=
\begin{cases}
\arg\max_{B\in Sky(F(s))}U_\theta(B\mid s), & \max Gain_\theta>\tau_{gain}\\
B_0, & \text{otherwise}
\end{cases}
$$

该结果是 **policy-relative conditional optimum**。

当 `B_0` 因安全或授权硬约束不可行时，系统在剩余安全候选中排序并标记 `forced_governance=true`，不将这一路径表述为“已证明优于不干预”。若没有安全候选，返回 `SAFE_HOLD`。

### 7.3 不稳定时不假装唯一最优

如果前两名差距过小：

1. 优先选择更短、负担更低、强度更低的 Brief；
2. 两个候选代表不同治理路径时，向用户展示选择；
3. 状态置信度也低时 abstain。

### 7.4 Skyline 退化检查

高维、小候选集下，很多候选可能都不被支配。必须报告：

$$
FrontierRatio=\frac{|Sky(F(s))|}{|F(s)|}
$$

如果 `FrontierRatio` 长期接近 1，Skyline 只能被定位为 Pareto 检查，不能声称它完成了有效筛选。

## 8. 端到端执行流程

```text
1. 从 Trace 和项目材料提取可观察事件
2. 构造 Occasion window
3. 记录双侧 Gap 证据，但不直接映射干预
4. 估计过程状态和 O/S/D 治理需求
5. 根据状态、证据和强度生成 Decision Brief
6. 应用安全、授权、证据和负担硬约束
7. 计算 O/S/D/E/W
8. 计算 Skyline 非支配集
9. 按冻结策略排序，并与 NO_INTERVENTION 比较
10. 输出干预、并列选择、NO_INTERVENTION、REQUEST_CLARIFICATION 或 SAFE_HOLD
11. 记录用户后续判断、验证、处置和项目结果
```

每次选择至少保存：

- 原始事件和项目证据 ID；
- Occasion 与治理需求的推断来源；
- 生成及删除的候选；
- 硬约束；
- 五维属性和 Skyline；
- 最终选择及相对不干预增益；
- policy version；
- 用户后续行为。

用户侧只展示干预理由、关键证据和一个最小下一步，不展示完整数学过程。

## 9. 参数初始化与验证

### 9.1 初始化顺序

1. 对全部 Occasion window 完成开放编码；
2. 检查 `O/S/D` 是否覆盖用户真实的治理重构工作；
3. 由至少两名研究者独立标注原语能力、兼容性和负担；
4. 冻结硬约束、权重、强度和候选生成规则；
5. 所有参数使用有限序数等级，不伪装成精确心理测量。

历史日志只能形成设计先验，不能估计干预因果效果。

### 9.2 当前可做的验证

- dominance、Skyline 和硬约束单元测试；
- 历史 episode 前缀 replay；
- 专家 Top-1/Top-k 一致性；
- 不安全或无证据候选进入 Top-1 的比例；
- `NO_INTERVENTION` 适当性和过度干预率；
- `FrontierRatio` 与 Skyline 实际删除率；
- 权重扰动下的排名稳定性；
- 与固定映射、直接加权排序的对照。

Replay 只能验证建议的合理性和可追溯性，不能证明真实用户效果。

### 9.3 未来用户研究

产生真实干预 exposure 后，分别测量：

- 用户能否形成任务相关的项目状态理解；
- 能否提出或执行诊断性验证；
- 能否合理限定责任、修改、批准和回退；
- 是否减少无进展委托循环；
- perceived control；
- information overload 与 workflow interruption；
- 项目功能结果。

点击、展开或“好的”不能单独作为理解和治理恢复的证据。

## 10. 可声称边界

### 可以声称

- 将 Cognitive Re-entry 干预选择形式化为状态条件化、带硬约束的多准则决策；
- 使用 `O/S/D` 描述当前治理支持需求；
- 从五个设计原语生成 Decision Brief；
- 使用 `O/S/D/E/W` 和 Skyline 删除被全面支配的候选；
- 在允许不干预和 abstention 的前提下选择策略相对的条件最优方案；
- 每次选择都可追溯到证据、推断、规则和候选比较。

### 不能声称

- Occasion 的最终分类已经完成；
- 五个干预原语是日志中直接发现的穷尽分类；
- 权重或效用由真实用户效果学习得到；
- Skyline 自身产生唯一最优答案；
- 当前 Top-1 干预具有已证实的因果效果；
- 用户接受干预即代表理解或治理恢复。

## 11. 论文技术表述

> **我们将 Cognitive Re-entry 中的干预选择建模为状态条件化、带硬约束的多准则决策问题。系统依据 Re-entry 过程形成判断标准操作化、项目状态重建以及验证—处置重构三类治理需求，从设计原语生成候选 Decision Brief，经 Pareto 筛选后，在允许不干预和弃权的前提下选择相对于冻结策略的条件最优方案。**

英文：

> **We formulate intervention selection during Cognitive Re-entry as a state-conditioned, constrained multi-criteria decision problem. The system derives three governance-support needs from the re-entry process, generates candidate Decision Briefs from design primitives, applies Pareto filtering, and selects a policy-relative conditional optimum with explicit no-intervention and abstention options.**

## 12. 参考文献

Börzsönyi, S., Kossmann, D., & Stocker, K. (2001). [The Skyline Operator](https://doi.org/10.1109/ICDE.2001.914855). *Proceedings of the 17th International Conference on Data Engineering*, 421–430.
