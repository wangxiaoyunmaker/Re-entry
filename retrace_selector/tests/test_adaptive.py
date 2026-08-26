from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from retrace_selector.online_inference_v2 import (
    AdaptiveController,
    OnlineInferenceService,
    UserPolicyPreference,
)
from retrace_selector.models import ValidationError


def profile_config():
    return {
        "FD-PROFILE-ADAPT": {
            "profile_id": "FD-PROFILE-ADAPT",
            "decision_object": "测试决策对象",
            "target_state": {"criteria": 2, "state": 3, "action": 2, "rubric_version": "CSA-RUBRIC-V1"},
            "allowed_evidence_types": ["RO04"],
        }
    }


def registry_config():
    return {
        "registry_version": "STRATEGY-REGISTRY-ADAPT-TEST",
        "registry_status": "TEST_ONLY",
        "candidates": [
            {
                "strategy_id": "STATE_CONTEXT_RECOVERY",
                "strategy_family": "STATE_CONTEXT_RECOVERY",
                "intensity": "L1",
                "parameters": {"criteria": 0.2, "state": 0.45, "action": 0.1, "evidence": 0.8, "workflow": 0.9},
                "template_id": "STATE-L1",
            },
            {
                "strategy_id": "STATE_CONTEXT_RECOVERY",
                "strategy_family": "STATE_CONTEXT_RECOVERY",
                "intensity": "L2",
                "parameters": {"criteria": 0.3, "state": 0.7, "action": 0.15, "evidence": 0.84, "workflow": 0.8},
                "template_id": "STATE-L2",
            },
        ],
        "templates": {"STATE-L1": {"title": "state l1"}, "STATE-L2": {"title": "state l2"}},
    }


def occasion_event(event_id: str = "EVT-ADAPT-OCC", *, chain_id: str | None = None):
    return {
        "event_id": event_id,
        "session_id": "S-ADAPT",
        "event_type": "USER_PROMPT",
        "actor": "USER",
        "project_id": "P-ADAPT",
        "observed_at": "2026-08-26T10:00:00+00:00",
        "source": "CODEX_HOOK",
        "payload": {
            "user_id": "USER-ADAPT",
            "occasion_signals": {"prior_instantiation": "CONFIRMED", "current_contact": "CONFIRMED", "consequentiality": "CONFIRMED"},
            "decision_object_profile_id": "FD-PROFILE-ADAPT",
            "occasion_id": event_id,
            "focal_decision_id": event_id.replace("EVT-", "FD-"),
            "claim_ids": ["CLAIM-ADAPT"],
            "evidence_ids": ["E-ADAPT"],
            **({"chain_id": chain_id} if chain_id else {}),
        },
    }


class AdaptiveControllerTests(unittest.TestCase):
    def test_requires_complete_three_dimensional_result_and_history(self):
        controller = AdaptiveController(min_history=3)
        preference = UserPolicyPreference(user_id="U1")
        incomplete = controller.update(
            preference,
            pre_scores={"criteria": 0, "state": 0, "action": 0},
            post_scores={"criteria": 1, "state": 1},
            target_scores={"criteria": 2, "state": 3, "action": 2},
            completed_chain_count=3,
        )
        self.assertFalse(incomplete.changed)
        self.assertEqual(incomplete.reason, "INCOMPLETE_POST")

        no_history = controller.update(
            preference,
            pre_scores={"criteria": 0, "state": 0, "action": 0},
            post_scores={"criteria": 1, "state": 1, "action": 1},
            target_scores={"criteria": 2, "state": 3, "action": 2},
            completed_chain_count=2,
        )
        self.assertFalse(no_history.changed)
        self.assertEqual(no_history.reason, "INSUFFICIENT_HISTORY")

    def test_residual_gap_increases_future_support_but_only_by_small_steps(self):
        update = AdaptiveController().update(
            UserPolicyPreference(user_id="U1"),
            pre_scores={"criteria": 0, "state": 0, "action": 0},
            post_scores={"criteria": 0, "state": 0, "action": 0},
            target_scores={"criteria": 2, "state": 3, "action": 2},
            completed_chain_count=3,
        )
        self.assertTrue(update.changed)
        self.assertEqual(update.reason, "UPDATED")
        self.assertGreater(update.preference.frequency_preference, 0.5)
        self.assertGreater(update.preference.intensity_preference, 0.5)
        self.assertLessEqual(update.metrics["delta"], 0.08)

    def test_good_result_and_burden_can_reduce_future_support(self):
        update = AdaptiveController().update(
            UserPolicyPreference(user_id="U1"),
            pre_scores={"criteria": 1, "state": 1, "action": 1},
            post_scores={"criteria": 3, "state": 3, "action": 3},
            target_scores={"criteria": 2, "state": 3, "action": 2},
            completed_chain_count=3,
            feedback="DISMISSED",
            burden=1.0,
        )
        self.assertTrue(update.changed)
        self.assertLess(update.preference.frequency_preference, 0.5)
        self.assertLess(update.preference.intensity_preference, 0.5)

    def test_likert_scores_are_normalized_to_the_frozen_zero_to_three_target(self):
        update = AdaptiveController().update(
            UserPolicyPreference(user_id="U1"),
            pre_scores={"criteria": 1, "state": 1, "action": 1},
            post_scores={"criteria": 5, "state": 5, "action": 5},
            target_scores={"criteria": 2, "state": 3, "action": 2},
            completed_chain_count=3,
            pre_score_scale="CSA-LIKERT-V1",
            post_score_scale="CSA-LIKERT-V1",
        )
        self.assertAlmostEqual(update.metrics["gap_after"], 0.0)
        self.assertLess(update.preference.frequency_preference, 0.5)

    def test_invalid_likert_values_are_rejected_instead_of_clipped(self):
        controller = AdaptiveController()
        for invalid in (0, 6):
            with self.assertRaises(ValidationError):
                controller.update(
                    UserPolicyPreference(user_id="U1"),
                    pre_scores={"criteria": invalid, "state": 1, "action": 1},
                    post_scores={"criteria": 1, "state": 1, "action": 1},
                    target_scores={"criteria": 2, "state": 3, "action": 2},
                    completed_chain_count=3,
                    pre_score_scale="CSA-LIKERT-V1",
                    post_score_scale="CSA-LIKERT-V1",
                )

    def test_frequency_and_intensity_have_separate_steps(self):
        update = AdaptiveController().update(
            UserPolicyPreference(user_id="U1"),
            pre_scores={"criteria": 1, "state": 1, "action": 1},
            post_scores={"criteria": 1, "state": 1, "action": 1},
            target_scores={"criteria": 2, "state": 3, "action": 2},
            completed_chain_count=3,
        )
        self.assertGreater(update.metrics["intensity_delta"], update.metrics["frequency_delta"])
        self.assertEqual(update.metrics["delta"], update.metrics["frequency_delta"])

    def test_manual_lock_prevents_adaptation(self):
        preference = UserPolicyPreference(user_id="U1", manual_lock=True, explicit=True, source="USER_UI")
        update = AdaptiveController().update(
            preference,
            pre_scores={"criteria": 0, "state": 0, "action": 0},
            post_scores={"criteria": 0, "state": 0, "action": 0},
            target_scores={"criteria": 2, "state": 3, "action": 2},
            completed_chain_count=10,
        )
        self.assertFalse(update.changed)
        self.assertEqual(update.reason, "MANUAL_LOCK")

    def test_preference_validation_is_bounded(self):
        with self.assertRaises(ValidationError):
            UserPolicyPreference(user_id="U1", frequency_preference=1.1)
        with self.assertRaises(ValidationError):
            UserPolicyPreference(user_id="U1", mode="ALWAYS")
        with self.assertRaises(ValidationError):
            UserPolicyPreference.from_dict({"user_id": "U1", "explicit": "false"})


class AdaptiveServiceTests(unittest.TestCase):
    def service(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return OnlineInferenceService(
            database_path=Path(temp.name) / "online.sqlite3",
            profiles=profile_config(),
            registry=registry_config(),
        )

    def test_invalid_likert_is_rejected_before_baseline_is_saved(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        with self.assertRaises(ValidationError):
            service.submit_occasion_baseline(
                chain_id,
                evaluation_id="EVAL-BASE-INVALID",
                responses={"criteria": 0, "state": 1, "action": 1},
            )
        self.assertIsNone(service.store.get_evaluation("EVAL-BASE-INVALID"))

    def test_paused_precedes_unknown_state(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        service.set_user_preferences("USER-ADAPT", frequency_preference=0.5, intensity_preference=0.5, mode="PAUSED")
        selection = service.select(chain_id)
        self.assertEqual(selection["decision"], "NO_INTERVENTION")
        self.assertEqual(selection["objective"]["reason"], "USER_PAUSED")

    def test_post_only_rows_do_not_count_as_completed_chain_history(self):
        service = self.service()
        for index in range(1, 4):
            chain_id = f"P-ADAPT::POST-ONLY-{index}::FD-{index}"
            service.ingest_event(occasion_event(f"EVT-POST-ONLY-{index}", chain_id=chain_id))
            result = service.submit_evaluation(
                chain_id,
                evaluation_id=f"EVAL-POST-ONLY-{index}",
                responses={"criteria": 1, "state": 1, "action": 1},
            )
            self.assertEqual(result["adaptation"]["reason"], "INSUFFICIENT_HISTORY")

        chain_id = "P-ADAPT::REAL-COMPLETE::FD-REAL"
        service.ingest_event(occasion_event("EVT-REAL-COMPLETE", chain_id=chain_id))
        service.submit_occasion_baseline(
            chain_id,
            evaluation_id="EVAL-BASE-REAL",
            responses={"criteria": 1, "state": 1, "action": 1},
        )
        result = service.submit_evaluation(
            chain_id,
            evaluation_id="EVAL-POST-REAL",
            responses={"criteria": 1, "state": 1, "action": 1},
        )
        self.assertEqual(service.store.count_user_complete_chains("USER-ADAPT"), 1)
        self.assertEqual(result["adaptation"]["reason"], "INSUFFICIENT_HISTORY")

    def test_user_controls_are_persisted_and_applied_to_future_selection(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event()) ["chain"]["chain_id"]
        service.ingest_event({
            **occasion_event("EVT-ADAPT-STATE", chain_id=chain_id),
            "payload": {
                **occasion_event("EVT-ADAPT-STATE", chain_id=chain_id)["payload"],
                "csa_updates": {
                    "criteria": {"level": 0, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
                    "state": {"level": 0, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
                    "action": {"level": 0, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
                },
            },
        })

        paused = service.set_user_preferences("USER-ADAPT", frequency_preference=0.1, intensity_preference=0.1, mode="PAUSED")
        self.assertTrue(paused["accepted"])
        self.assertEqual(service.get_user_preferences("USER-ADAPT")["mode"], "PAUSED")
        selection = service.select(chain_id)
        self.assertEqual(selection["decision"], "NO_INTERVENTION")
        self.assertEqual(selection["objective"]["reason"], "USER_PAUSED")
        self.assertEqual(selection["objective"]["semantic_constraints"]["user_preference_mode"], "PAUSED")

        service.set_user_preferences("USER-ADAPT", frequency_preference=1.0, intensity_preference=0.1, mode="AUTO")
        selected = service.select(chain_id)
        self.assertNotEqual(selected["objective"].get("reason"), "USER_PAUSED")
        self.assertEqual(selected["objective"]["semantic_constraints"]["max_intensity"], 1)
        self.assertEqual(selected["objective"]["semantic_constraints"]["intensity_cap_applied"], True)
        self.assertLess(selected["objective"]["semantic_constraints"]["effective_eta"], service.config.eta)

    def test_top_level_user_id_is_used_for_anchor_chain_preference_lookup(self):
        service = self.service()
        event = occasion_event("EVT-ADAPT-TOP-USER")
        event["user_id"] = "USER-TOP"
        event["payload"] = {key: value for key, value in event["payload"].items() if key != "user_id"}
        chain_id = service.ingest_event(event)["chain"]["chain_id"]
        service.set_user_preferences("USER-TOP", frequency_preference=0.8, intensity_preference=0.8)
        state = service.observe(chain_id)
        self.assertEqual(state.user_preference_version.startswith("PREF-"), True)
        self.assertEqual(state.frequency_preference, 0.8)

    def test_preference_event_can_be_ingested_and_replayed(self):
        service = self.service()
        service.ingest_event({
            "event_id": "EVT-PREFERENCE-RAW",
            "session_id": "S-ADAPT",
            "event_type": "POLICY_PREFERENCE_UPDATED",
            "actor": "USER",
            "project_id": "P-ADAPT",
            "observed_at": "2026-08-26T10:00:00+00:00",
            "source": "MCP_UI",
            "user_id": "USER-RAW",
            "payload": {
                "user_id": "USER-RAW",
                "preference": {
                    "user_id": "USER-RAW",
                    "frequency_preference": 0.7,
                    "intensity_preference": 0.4,
                    "mode": "AUTO",
                    "version": "PREF-RAW",
                    "source": "USER_UI",
                    "explicit": True,
                    "manual_lock": False,
                },
            },
        })
        self.assertEqual(service.get_user_preferences("USER-RAW")["version"], "PREF-RAW")

    def test_third_complete_chain_applies_adaptation_from_baseline_and_post(self):
        service = self.service()
        results = []
        for index in range(1, 4):
            chain_id = f"P-ADAPT::OCC-{index}::FD-{index}"
            event = occasion_event(f"EVT-ADAPT-OCC-{index}", chain_id=chain_id)
            service.ingest_event(event)
            service.submit_occasion_baseline(
                chain_id,
                evaluation_id=f"EVAL-BASE-{index}",
                responses={"criteria": 1, "state": 1, "action": 1},
            )
            results.append(
                service.submit_evaluation(
                    chain_id,
                    evaluation_id=f"EVAL-POST-{index}",
                    responses={"criteria": 1, "state": 1, "action": 1},
                )
            )

        self.assertEqual(results[0]["adaptation"]["reason"], "INSUFFICIENT_HISTORY")
        self.assertEqual(results[1]["adaptation"]["reason"], "INSUFFICIENT_HISTORY")
        self.assertEqual(results[2]["adaptation"]["reason"], "UPDATED")
        self.assertEqual(results[2]["adaptation"]["preference"]["source"], "ADAPTIVE")
        self.assertGreater(service.get_user_preferences("USER-ADAPT")["frequency_preference"], 0.5)
        linkage = service.get_chain_outcome_linkage("P-ADAPT::OCC-3::FD-3")
        self.assertEqual(linkage["adaptation_update_id"], results[2]["adaptation"]["update_id"])
        self.assertEqual(linkage["policy_preference"]["version"], "PREF-DEFAULT")
        self.assertEqual(linkage["preference_used_for_selection"], linkage["policy_preference"])
        self.assertEqual(
            linkage["current_policy_preference"]["version"],
            results[2]["adaptation"]["preference"]["version"],
        )
        self.assertEqual(
            linkage["adaptation_preference"]["version"],
            results[2]["adaptation"]["preference"]["version"],
        )

    def test_profile_separates_subjective_preference_from_assessed_need(self):
        service = self.service()
        service.set_user_preferences(
            "USER-ADAPT",
            frequency_preference=0.2,
            intensity_preference=0.3,
        )
        for index in range(1, 4):
            chain_id = f"P-ADAPT::PROFILE-{index}::FD-{index}"
            service.ingest_event(occasion_event(f"EVT-PROFILE-{index}", chain_id=chain_id))
            service.submit_occasion_baseline(
                chain_id,
                evaluation_id=f"EVAL-PROFILE-BASE-{index}",
                responses={"criteria": 1, "state": 1, "action": 1},
            )
            service.submit_evaluation(
                chain_id,
                evaluation_id=f"EVAL-PROFILE-POST-{index}",
                responses={"criteria": 1, "state": 1, "action": 1},
            )

        profile = service.get_user_profile("USER-ADAPT")
        self.assertEqual(profile["subjective_preference"]["source"], "USER_UI")
        self.assertEqual(profile["subjective_preference"]["frequency_preference"], 0.2)
        self.assertEqual(profile["subjective_preference"]["intensity_preference"], 0.3)
        self.assertEqual(profile["effective_policy"]["source"], "ADAPTIVE")
        self.assertEqual(profile["assessed_need"]["last_reason"], "UPDATED")
        self.assertEqual(profile["assessed_need"]["completed_chain_count"], 3)
        self.assertIsNotNone(profile["assessed_need"]["last_update_id"])

        chain_state = service.get_retrace_state("P-ADAPT::PROFILE-3::FD-3")
        self.assertEqual(chain_state["user_profile"], profile)

        reopened = OnlineInferenceService(
            database_path=service.store.path,
            profiles=profile_config(),
            registry=registry_config(),
        )
        self.assertEqual(reopened.get_user_profile("USER-ADAPT"), profile)


if __name__ == "__main__":
    unittest.main()
