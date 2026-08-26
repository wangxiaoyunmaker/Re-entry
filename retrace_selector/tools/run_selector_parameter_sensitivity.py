"""Audit how the deterministic v2 selector changes under pilot parameters.

This is a synthetic geometry audit, not a calibration or efficacy estimate.
It makes the current POC sensitivity visible before any participant data is
used to freeze a formal experiment policy.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
import itertools
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_formal_registry_experiment import (  # noqa: E402
    evaluate_case,
    load_registry_v2,
    load_selector_config_v2,
    POLICY_PATH,
    REGISTRY_PATH,
)


OUTPUT = ROOT / "artifacts/selector_parameter_sensitivity_v1.json"
OUTPUT_MD = ROOT / "artifacts/selector_parameter_sensitivity_v1.md"


def _counts(registry: Any, config: Any, *, assessability: str = "SUFFICIENT") -> dict[str, int]:
    results = [
        evaluate_case(
            registry,
            config,
            case_id=f"SENS-{index}",
            current=current,
            target=(2, 3, 2),
            assessability=assessability,
        )
        for index, current in enumerate(itertools.product(range(4), repeat=3))
    ]
    return dict(sorted(Counter(item["decision"] for item in results).items()))


def run() -> dict[str, Any]:
    registry = load_registry_v2(REGISTRY_PATH)
    base = load_selector_config_v2(POLICY_PATH)
    result: dict[str, Any] = {
        "schema_version": "retrace-selector-parameter-sensitivity-v1",
        "synthetic": True,
        "interpretation": "parameter sensitivity only; not calibration or intervention efficacy",
        "base_policy": base.to_dict(),
        "grid": {"target": [2, 3, 2], "case_count": 64},
        "variants": {},
    }
    for field, values in {
        "beta": (0.50, 0.75, 1.00),
        "eta": (0.03, 0.05, 0.10),
        "epsilon": (0.01, 0.03, 0.10),
    }.items():
        result["variants"][field] = {
            str(value): _counts(registry, replace(base, **{field: value}))
            for value in values
        }
    result["variants"]["evidence_floor_when_limited"] = {
        str(value): _counts(
            registry,
            replace(base, evidence_floor_when_limited=value),
            assessability="LIMITED",
        )
        for value in (0.60, 0.72, 0.80, 0.95)
    }
    return result


def _write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Selector 参数敏感性审计",
        "",
        "> 这是固定 Registry、固定 target `(2,3,2)` 的 64 个合成状态网格；用于识别 POC 对参数的敏感程度，不是参与者校准，也不是干预效果评估。",
        "",
        "| 参数 | 取值 | INTERVENE | PRESENT_CHOICES | NO_INTERVENTION |",
        "|---|---:|---:|---:|---:|",
    ]
    for field, variants in payload["variants"].items():
        for value, counts in variants.items():
            lines.append(
                f"| {field} | {value} | {counts.get('INTERVENE', 0)} | "
                f"{counts.get('PRESENT_CHOICES', 0)} | {counts.get('NO_INTERVENTION', 0)} |"
            )
    lines.extend([
        "",
        "解释：`epsilon` 对双选项数量最敏感；当前 `beta=0.75` 与 `beta=1.00` 在该网格上相同，不能据此证明 beta 已被行为数据校准。`evidence_floor_when_limited=0.60/0.72/0.80` 在当前 Registry 上没有改变分布，说明需要低证据候选或真实 LIMITED 轨迹继续测试。",
        "",
    ])
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    payload = run()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(OUTPUT)
    print(OUTPUT_MD)


if __name__ == "__main__":
    main()
