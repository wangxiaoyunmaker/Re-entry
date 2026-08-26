# ReTrace online inference v2

这个文件夹是 `retrace-online-inference-technical-design.md` 的可运行实现。
旧版 `retrace_selector.online_v2` 仍然保留为兼容入口，但新代码应从
`retrace_selector.online_inference_v2` 导入。

## 文件结构

`core.py` 包含完整的确定性实现：

- 事件契约与 Collector 顺序：`OnlineEvent`、`OnlineStore.ingest`
- M1 Occasion/chain/target：`OccasionSignals`、`TargetBuilder`
- M2 固定回放与 late revision：`replay_trace`、`apply_late_event`
- M3 Observer/Registry/Skyline：`StateObserverV2`、`SkylineSelectorV2`
- M4 baseline、probe、PRE/POST/CLOSE、linkage
- M5 exposure、action、`get_retrace_state` 轮询接口

`verify.py` 是不依赖 pytest 的端到端验证脚本。它会在临时目录创建 SQLite
数据库，执行：

```text
Occasion → chain → baseline → C/S/A + selector_hint update → semantic gate/cooldown → Selector

selector_hint 由上游 Support Profile/Prompt 生成，包含 support_family、
allowed_families、confidence、max_intensity、cognitive_gap_detected、
execution_request_detected、evidence_ids 和语义 `evidence_refs`。`evidence_ref`
必须来自当前用户轮次、同链事件或系统事件，并明确 `supports_families`/
`supports_dimensions`；后验 `OUTCOME_ANNOTATION` evidence 会在 Collector 边界拒绝。
MEDIUM 只做软排序，只有 HIGH 且 evidence ref 语义支持有效时才硬过滤 family；
它不改变 Registry 的五维能力参数。

`intervention_eligible` 只保留为 v1 输入/输出兼容字段，不再作为上游判断字段。
Selector 的 `TARGET_REACHED` 只有在 C/S/A 三维全部已知、每维 `SUFFICIENT` 且有
语义上支持该维度的 evidence ref、并且三维缺口都为 0 时才成立。`NO_INTERVENTION` 会区分 `TARGET_REACHED`、`BELOW_ETA`、
`INTERVENTION_NOT_ELIGIBLE`、`INSUFFICIENT_EVIDENCE`、`UNKNOWN_STATE`、
`NO_ELIGIBLE_CANDIDATE`、`COOLDOWN_ACTIVE` 和 `NO_RESPONSE_TIMEOUT`。真实 exposure 会更新
workflow burden，并只在同一 decision-chain 下抑制已曝光的候选 strategy；其他候选仍可竞争。
用户响应或干预动作后解除该候选的 cooldown。没有候选 ID 的旧 exposure 才回退为 chain 级兼容模式。

`PRESENT_CHOICES` 必须输出两个不同 family 的 option，并为每个 option 提供冻结的
`branch_condition_code`/`branch_condition`。UI 必须先调用 `record_choice`，提交
`selected_candidate_id`、匹配的 `choice_condition` 和 `choice_basis`，再调用 `expose`；
未完成分叉不能曝光。
         → exposure → PRE → POST → linkage
```

并检查 late event 只产生新的 revision，不覆盖 PRE 快照。

## 如何验证

在 `retrace_selector` 目录执行：

```bash
PYTHONPATH=src python3 src/retrace_selector/online_inference_v2/verify.py
PYTHONPATH=src python3 -m unittest tests.test_online_v2 -v
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

预期结果分别包含 `ONLINE_V2_VERIFY_OK`、专项测试通过，以及全量测试的
`OK`。如果要查看真实持久化结果，可以把 `verify.py` 中的临时数据库路径
替换为项目内的 `var/online-v2.sqlite3`，再用 CLI 的 `online-state` 和
`online-linkage` 查询。

## 最小 API 验证顺序

```python
from retrace_selector.online_inference_v2 import OnlineInferenceService

service = OnlineInferenceService(
    database_path="var/online-v2.sqlite3",
    profiles={...},
    registry={...},
    config={...},
)

chain = service.ingest_event(normalized_occasion_event())["chain"]
service.submit_occasion_baseline(chain["chain_id"], evaluation_id="EVAL-BASE")
selection = service.select(chain["chain_id"])
if selection["decision"] == "PRESENT_CHOICES":
    option = selection["options"][0]
    service.record_choice(
        chain["chain_id"],
        selection_decision_id=selection["decision_id"],
        selected_candidate_id=option["strategy_id"],
        choice_condition=option["branch_condition_code"],
        choice_basis="用户明确选择该分支的依据",
    )
    service.expose(
        chain["chain_id"],
        exposure_id="EXP-1",
        selection_decision_id=selection["decision_id"],
        selected_candidate_id=option["strategy_id"],
    )
else:
    service.expose(chain["chain_id"], exposure_id="EXP-1", selection_decision_id=selection["decision_id"])
service.submit_evaluation(chain["chain_id"], evaluation_id="EVAL-POST")
assert service.get_chain_outcome_linkage(chain["chain_id"])["linkage_status"] == "READY_FOR_OFFLINE_LINKAGE"
```

`INTERVENTION_EXPOSURE` 必须由宿主 UI 在真正成功呈现后触发；Selector 推荐
本身不会增加 exposure burden。
