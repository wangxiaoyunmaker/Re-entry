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
- **决策：**将当前 policy 统一定位为 `DESIGN_ASSUMPTION`；v0.2.0 已实现 need/primitive 级 evidence linkage 和人工批准后 calibration 管线。
- **状态：**候选级绑定 Resolved；经验参数仍等待批准样本。

## 2026-08-20 — I-014：真实 transcript 并非全部是严格单行 JSONL

- **问题：**SRE-0068 的消息正文包含未转义换行，逐行 `json.loads` 会把一个对象误拆成多行。
- **决策：**使用 `JSONDecoder(strict=False).raw_decode` 顺序解析对象流；仍严格检查对象类型与 `source_context/record_index/role`，且错误信息不输出正文。
- **状态：**Resolved with regression test。

## 2026-08-20 — I-015：context-local record 编号无法唯一定位 onset

- **问题：**271 个 episode 中有 6 个的数字 onset 在多个 context 重复。若任选一个匹配点，可能把未来事件纳入 prefix 或提前截断。
- **决策：**优先使用 `context_xxxx:Rn` 边界；仅当数字 record 在整份 transcript 中唯一时自动解析。歧义案例标记 `REVIEW_REQUIRED`，不进入 calibration。
- **状态：**265 READY，6 REVIEW_REQUIRED；等待人工补充 context-qualified onset。

## 2026-08-20 — I-016：完整 episode 编码会造成结果泄漏

- **问题：**v2.2 的 recovery/action/evidence/decision 编码及 source pointers 覆盖 onset 之后的用户行动，不能直接生成选择器输入。
- **决策：**物理分离 `calibration_review_template.jsonl` 与 `calibration_targets.jsonl`。前者不含 target；选择器只能看到 onset 及之前的引用。拟合时才按 case ID 连接标记为 `selector_visible=false` 的完整编码标签，并以独立 `prefix_manifest.jsonl` 为权威源重新校验 stratum、participant group、泄漏状态和全部证据元数据。人工 state 中的 evidence ID、locator、sequence index、content hash 必须与 manifest 完全一致。
- **状态：**Resolved by schema and fail-closed validation。

## 2026-08-20 — I-017：现有样本不足以诚实完成经验校准

- **问题：**现有 v2.2 标注只有 20 例，其中 core 7 例；20 例均未完成只看 prefix 的人工状态复核。默认 calibration gate 要求至少 10 个批准案例和 3 个参与者组。
- **决策：**已生成 20 例 review template，但保持 `PENDING`。当前 CLI 应返回 `approved cases; got 0`，不得输出“已校准”参数。需先扩充 core 编码并完成盲于 post-onset 标签的 prefix review。
- **状态：**Open empirical data gate；代码管线已完成。

## 2026-08-20 — I-018：当前 review template 不能单独支持盲审

- **问题：**为避免敏感正文复制，prefix manifest/review template 只保存定位符和哈希。研究者仅打开该文件无法理解事件内容；若直接打开完整 transcript，又可能看到 onset 后标签信息。
- **决策：**本版本不把正文写入可提交产物。正式复核前需增加一个本地只读 review UI：按权威 manifest 读取源 transcript、只显示 `sequence_index <= onset` 的内容，并把 target 文件保持不可访问。该 UI 不是参数搜索内核的一部分。
- **状态：**Open human-review tooling gate；不影响 prefix 构建与泄漏审计，但阻断人工 state 批准。

## 2026-08-20 — I-019：校准入口不能信任自报 PASS 或空审批

- **问题：**外部文件可手工把 manifest 标成 PASS、把 review 标成 APPROVED，或把 participant-group CV 降成无训练集的一折。
- **决策：**calibrate 重新验证 manifest 的时序/唯一性/onset/哈希格式；APPROVED 强制 reviewer、reviewed_at、tool_version；state decision ID 必须匹配 case；group gate 不得低于 3。trial hash 绑定基础 policy hash，输出记录 engine 与全部输入文件 hash。
- **状态：**Resolved with negative regression tests。
