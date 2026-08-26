from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from retrace_selector.llm_support_profile import (
    BASIS_ASSESSMENT_SYSTEM_PROMPT,
    build_basis_assessment_prompt,
    extract_support_profile,
    observe_then_extract_support_profile,
    parse_basis_assessment_response,
)
from retrace_selector.models import ValidationError


BEHAVIOR = [
    {
        "evidence_id": "U10",
        "actor": "USER",
        "text_span": "不同账号不能互相看到数据",
        "dialogue_act": ["AD-K"],
        "task_intent": ["CODE.REPAIR"],
        "target_object": ["TO05"],
        "input_type": ["IN00"],
        "validation_strategy": ["VS00"],
        "temporal_position": "BEFORE_OR_AT_TRIGGER",
        "source": "OBSERVED",
        "action_focus": "NONE",
        "supports_primitives": [],
        "action_focus_rationale": "用户提出业务约束。",
    },
    {
        "evidence_id": "U11",
        "actor": "USER",
        "text_span": "先用两个真实账号测试隔离，再改登录模块",
        "dialogue_act": ["AD-K"],
        "task_intent": ["CODE.REPAIR"],
        "target_object": ["TO05"],
        "input_type": ["IN00"],
        "validation_strategy": ["VS01"],
        "temporal_position": "AFTER_TRIGGER",
        "source": "OBSERVED",
        "behavior_change_from_prior": "CHANGED",
        "behavior_change_basis": "用户从提出规则转为安排验证和修改范围。",
        "action_focus": "BOTH",
        "supports_primitives": ["VERIFICATION", "DISPOSITION_COORDINATION"],
        "action_focus_rationale": "用户同时安排验证和修改范围。",
    },
]


def assessment() -> dict:
    empty = {
        "basis_status": "NOT_OBSERVED",
        "formation_evidence_ids": [],
        "use_evidence_ids": [],
        "support_need": "NONE",
        "need_evidence_ids": [],
        "confidence": "HIGH",
        "rationale": "没有对应用户证据。",
        "need_rationale": "没有对应支持需求证据。",
    }
    return {
        "criteria_basis_reconstruction": {
            "basis_status": "USED",
            "formation_evidence_ids": ["U10"],
            "use_evidence_ids": ["U11"],
            "support_need": "MEDIUM",
            "need_evidence_ids": ["U11"],
            "confidence": "HIGH",
            "rationale": "用户提出规则并据此安排后续行动。",
            "need_rationale": "用户需要先验证隔离规则。",
        },
        "project_state_reconstruction": dict(empty),
        "evidence_action_governance": {
            "basis_status": "FORMED",
            "formation_evidence_ids": ["U11"],
            "use_evidence_ids": [],
            "support_need": "MEDIUM",
            "need_evidence_ids": ["U11"],
            "confidence": "HIGH",
            "rationale": "用户安排验证顺序和修改范围。",
            "need_rationale": "同一事件说明需要行动治理支持。",
        },
    }


class SupportProfilePromptTests(unittest.TestCase):
    def test_prompt_contains_isolated_trace_and_stage_one_evidence(self):
        prompt = build_basis_assessment_prompt(
            [{"event_id": "U10", "actor": "USER", "text": "不同账号不能互相看到数据"}],
            BEHAVIOR[:1],
            trigger_event_id="U10",
        )
        self.assertIn("<trace_events>", prompt)
        self.assertIn("<behavior_evidence>", prompt)
        self.assertIn("U10", prompt)
        self.assertIn("three dimensions", prompt)
        self.assertIn("basis formation", BASIS_ASSESSMENT_SYSTEM_PROMPT)

    def test_valid_assessment_is_parsed_and_validated(self):
        result = parse_basis_assessment_response(
            json.dumps({"basis_assessment": assessment()}, ensure_ascii=False),
            BEHAVIOR,
        )
        self.assertEqual(result["criteria_basis_reconstruction"]["basis_status"], "USED")
        self.assertEqual(
            result["criteria_basis_reconstruction"]["use_evidence_ids"], ["U11"]
        )

    def test_used_without_changed_user_behavior_is_rejected(self):
        bad = assessment()
        bad["criteria_basis_reconstruction"]["use_evidence_ids"] = ["U10"]
        with self.assertRaisesRegex(
            ValidationError, "USED requires a changed later USER behavior"
        ):
            parse_basis_assessment_response(
                json.dumps({"basis_assessment": bad}, ensure_ascii=False),
                BEHAVIOR,
            )

    @patch("retrace_selector.llm_support_profile.call_chat_completion")
    def test_end_to_end_calls_both_stages_and_aggregates_profile(self, call):
        call.side_effect = [
            json.dumps({"behavior_evidence": BEHAVIOR}, ensure_ascii=False),
            json.dumps({"basis_assessment": assessment()}, ensure_ascii=False),
        ]
        result = extract_support_profile(
            [
                {"event_id": "U10", "actor": "USER", "text": "不同账号不能互相看到数据"},
                {
                    "event_id": "U11",
                    "actor": "USER",
                    "text": "先用两个真实账号测试隔离，再改登录模块",
                },
            ],
            trigger_event_id="U11",
        )
        self.assertEqual(call.call_count, 2)
        self.assertEqual(
            result["support_profile"]["criteria_basis_reconstruction"]["observed_work"],
            "OBSERVED",
        )
        self.assertEqual(
            result["basis_assessment"]["criteria_basis_reconstruction"]["basis_status"],
            "USED",
        )

    @patch("retrace_selector.llm_support_profile.call_chat_completion")
    def test_runtime_orchestrator_skips_second_stage_without_support_signal(self, call):
        call.return_value = json.dumps({"behavior_evidence": []}, ensure_ascii=False)
        result = observe_then_extract_support_profile(
            [{"event_id": "A1", "actor": "AGENT", "text": "继续运行。"}],
            progress_observed=True,
        )
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result["observation"]["support_opportunity"], "NONE")
        self.assertIsNone(result["basis_assessment"])
        self.assertIsNone(result["support_profile"])


if __name__ == "__main__":
    unittest.main()
