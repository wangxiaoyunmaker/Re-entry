from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from retrace_selector.online_v2 import (
    DecisionObjectProfile,
    OnlineInferenceService,
    RegistryV2,
    SelectorConfigV2,
    ValidationError,
)


def profile_config():
    return {
        "FD-PROFILE-01": {
            "profile_id": "FD-PROFILE-01",
            "decision_object": "照片删除关系",
            "target_state": {"criteria": 2, "state": 3, "action": 2, "rubric_version": "CSA-RUBRIC-V1"},
            "allowed_evidence_types": ["RO04"],
        }
    }


def registry_config():
    return {
        "registry_version": "STRATEGY-REGISTRY-V2-TEST",
        "registry_status": "APPROVED",
        "candidates": [
            {"strategy_id": "STATE_TRACE", "strategy_family": "STATE_TRACE", "intensity": "L2",
             "parameters": {"criteria": 0.1, "state": 0.7, "action": 0.1, "evidence": 0.8, "workflow": 0.8}, "template_id": "STATE_TRACE_L2"},
            {"strategy_id": "RULE_CHECK", "strategy_family": "RULE_CHECK", "intensity": "L2",
             "parameters": {"criteria": 0.6, "state": 0.2, "action": 0.3, "evidence": 0.8, "workflow": 0.7}, "template_id": "RULE_CHECK_L2"},
        ],
        "templates": {"STATE_TRACE_L2": {"title": "trace", "message": "trace", "next_step": "check"},
                      "RULE_CHECK_L2": {"title": "rule", "message": "rule", "next_step": "check"}},
    }


def semantic_registry_config():
    return {
        "registry_version": "STRATEGY-REGISTRY-V2-SEMANTIC-TEST",
        "registry_status": "APPROVED",
        "candidates": [
            {"strategy_id": "STATE_CONTEXT_RECOVERY", "strategy_family": "STATE_CONTEXT_RECOVERY", "intensity": "L1",
             "parameters": {"criteria": 0.2, "state": 0.45, "action": 0.1, "evidence": 0.72, "workflow": 0.92}, "template_id": "STATE_L1"},
            {"strategy_id": "STATE_CONTEXT_RECOVERY", "strategy_family": "STATE_CONTEXT_RECOVERY", "intensity": "L2",
             "parameters": {"criteria": 0.3, "state": 0.7, "action": 0.15, "evidence": 0.84, "workflow": 0.82}, "template_id": "STATE_L2"},
            {"strategy_id": "RULE_CLARIFICATION", "strategy_family": "RULE_CLARIFICATION", "intensity": "L1",
             "parameters": {"criteria": 0.45, "state": 0.1, "action": 0.2, "evidence": 0.72, "workflow": 0.92}, "template_id": "RULE_L1"},
            {"strategy_id": "RULE_CLARIFICATION", "strategy_family": "RULE_CLARIFICATION", "intensity": "L2",
             "parameters": {"criteria": 0.68, "state": 0.16, "action": 0.34, "evidence": 0.84, "workflow": 0.82}, "template_id": "RULE_L2"},
        ],
        "templates": {
            "STATE_L1": {"title": "state l1"},
            "STATE_L2": {"title": "state l2"},
            "RULE_L1": {"title": "rule l1"},
            "RULE_L2": {"title": "rule l2"},
        },
    }


def occasion_event(**payload):
    return {
        "event_id": payload.pop("event_id", "EVT-OCC-1"),
        "session_id": "S1",
        "event_type": "USER_PROMPT",
        "actor": "USER",
        "project_id": "P1",
        "observed_at": "2026-08-24T10:00:00+00:00",
        "source": "CODEX_HOOK",
        "payload": {
            "occasion_signals": {"prior_instantiation": "CONFIRMED", "current_contact": "CONFIRMED", "consequentiality": "CONFIRMED"},
            "decision_object_profile_id": "FD-PROFILE-01",
            "occasion_id": "OCC-1",
            "focal_decision_id": "FD-1",
            "claim_ids": ["CLAIM-1"],
            "evidence_ids": ["E1"],
            **payload,
        },
    }


class OnlineV2Tests(unittest.TestCase):
    def service(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = OnlineInferenceService(
            database_path=Path(temp.name) / "online.sqlite3",
            profiles=profile_config(),
            registry=registry_config(),
            config=SelectorConfigV2(beta=1.0, eta=0.05, epsilon=0.03, evidence_floor_when_limited=0.6),
        )
        return service

    def test_occasion_freezes_chain_and_unknown_state_does_not_select(self):
        service = self.service()
        result = service.ingest_event(occasion_event())
        self.assertEqual(result["occasion"], "OCCASION_CONFIRMED")
        chain_id = result["chain"]["chain_id"]
        selection = service.select(chain_id)
        self.assertEqual(selection["decision"], "NO_INTERVENTION")
        self.assertEqual(selection["objective"]["reason"], "UNKNOWN_STATE")

    def test_full_baseline_exposure_post_and_linkage_flow(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-STATE-1", csa_updates={
            "criteria": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
            "state": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }, chain_id=chain_id))
        baseline = service.submit_occasion_baseline(chain_id, evaluation_id="EVAL-BASE-1", responses={"criteria": 3, "state": 2}, skipped_dimensions=["action"])
        self.assertEqual(baseline["measurement_point"], "OCCASION_BASELINE")
        selection = service.select(chain_id)
        self.assertIn(selection["decision"], {"INTERVENE", "PRESENT_CHOICES", "NO_INTERVENTION"})
        exposure = service.expose(chain_id, exposure_id="EXP-1", selection_decision_id=selection["decision_id"])
        self.assertFalse(exposure["baseline_missed"])
        self.assertIsNotNone(exposure["pre_snapshot_id"])
        post = service.submit_evaluation(chain_id, evaluation_id="EVAL-POST-1", responses={"criteria": 4, "state": 4}, skipped_dimensions=["action"])
        self.assertIsNotNone(post["post_snapshot_id"])
        state = service.get_retrace_state(chain_id)
        self.assertEqual(state["chain"]["status"], "CLOSED")
        linkage = service.get_chain_outcome_linkage(chain_id)
        self.assertEqual(linkage["focal_decision_id"], "FD-1")
        self.assertEqual(linkage["csa_measurements"]["occasion_baseline_evaluation_id"], "EVAL-BASE-1")
        self.assertEqual(linkage["linkage_status"], "READY_FOR_OFFLINE_LINKAGE")

    def test_exposure_before_baseline_is_recorded_as_missed(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        exposure = service.expose(chain_id, exposure_id="EXP-MISSED")
        self.assertTrue(exposure["baseline_missed"])
        self.assertIsNotNone(exposure["baseline_missed_event_id"])
        self.assertTrue(service.get_chain(chain_id).baseline_missed)

    def test_late_event_creates_revision_without_replacing_pre_snapshot(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        service.submit_occasion_baseline(chain_id, evaluation_id="EVAL-BASE-2", skipped_dimensions=["criteria", "state", "action"])
        pre = service.capture_measurement_snapshot(chain_id, "PRE", reason="test")
        result = service.ingest_event(occasion_event(event_id="EVT-LATE", chain_id=chain_id, observed_at="2020-01-01T00:00:00+00:00"))
        self.assertTrue(result["is_late"])
        revision = service.apply_late_event("EVT-LATE")
        self.assertEqual(revision["recompute_status"], "COMPLETED")
        snapshots = service.store.snapshots(chain_id)
        self.assertEqual(snapshots["pre"]["snapshot_id"], pre["snapshot_id"])
        self.assertTrue(any(item["measurement_point"] == "LATE_RECOMPUTE" for item in snapshots.values()))

    def test_limited_assessability_requires_evidence_floor(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-LIMITED", chain_id=chain_id, csa_updates={
            "criteria": {"level": 1, "assessability": "LIMITED", "evidence_ids": ["E-C"]},
            "state": {"level": 1, "assessability": "LIMITED", "evidence_ids": ["E-S"]},
            "action": {"level": 1, "assessability": "LIMITED", "evidence_ids": ["E-A"]},
        }))
        service.submit_occasion_baseline(chain_id, evaluation_id="EVAL-BASE-3", skipped_dimensions=["criteria", "state", "action"])
        selection = service.select(chain_id)
        self.assertTrue(selection["skyline_ids"])

    def test_fixed_trace_replay_is_deterministic(self):
        first = self.service()
        second = self.service()
        trace = [occasion_event(), occasion_event(event_id="EVT-STATE-REPLAY", csa_updates={
            "criteria": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
            "state": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }, chain_id="P1::OCC-1::FD-1")]
        one = first.replay_trace(trace)
        two = second.replay_trace(trace)
        self.assertEqual(one["replay_hash"], two["replay_hash"])

    def test_observer_probe_adds_evidence_without_treating_baseline_as_state(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        service.submit_occasion_baseline(chain_id, evaluation_id="EVAL-BASE-PROBE", responses={"criteria": 5, "state": 5, "action": 5})
        before = service.observe(chain_id)
        self.assertIsNone(before.criteria.level)
        service.submit_observer_probe(chain_id, evaluation_id="EVAL-PROBE", responses={"criteria": 5}, evidence_updates={"criteria": {"level": 2, "assessability": "SUFFICIENT", "evidence_ids": ["PROBE-C"]}})
        after = service.observe(chain_id)
        self.assertEqual(after.criteria.level, 2)
        self.assertEqual(after.criteria.evidence_ids, ("PROBE-C",))

    def test_semantic_hint_gates_family_and_caps_intensity(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = OnlineInferenceService(
            database_path=Path(temp.name) / "online.sqlite3",
            profiles=profile_config(),
            registry=semantic_registry_config(),
            config=SelectorConfigV2(beta=0.75, eta=0.05, epsilon=0.03),
        )
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-SEMANTIC", chain_id=chain_id, csa_updates={
            "criteria": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
            "state": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }, evidence_refs=[{
            "evidence_id": "E-C",
            "source": "CURRENT_USER_TURN",
            "semantic_role": "COUNTEREVIDENCE",
            "supports_families": ["RULE_CLARIFICATION"],
            "supports_dimensions": ["criteria", "state", "action"],
        }], selector_hint={
            "support_family": "RULE_CLARIFICATION",
            "allowed_families": ["RULE_CLARIFICATION"],
            "confidence": "HIGH",
            "max_intensity": 1,
            "cognitive_gap_detected": True,
            "execution_request_detected": True,
            "evidence_ids": ["E-C"],
        }))
        selection = service.select(chain_id)
        self.assertEqual(selection["decision"], "INTERVENE")
        self.assertEqual(selection["selected"], ["RULE_CLARIFICATION_L1"])
        self.assertEqual(selection["objective"]["semantic_constraints"]["hint_enforced"], True)
        self.assertEqual(selection["objective"]["semantic_constraints"]["family_gate_mode"], "HARD")
        self.assertTrue(selection["objective"]["semantic_constraints"]["hint_evidence_valid"])
        self.assertEqual(selection["objective"]["semantic_constraints"]["max_intensity"], 1)

    def test_medium_family_hint_is_soft_and_does_not_exclude_other_family(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = OnlineInferenceService(
            database_path=Path(temp.name) / "online.sqlite3",
            profiles=profile_config(),
            registry=semantic_registry_config(),
            config=SelectorConfigV2(beta=0.75, eta=0.05, epsilon=0.03),
        )
        chain_id = service.ingest_event(occasion_event())[
            "chain"
        ]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-SEMANTIC-MEDIUM", chain_id=chain_id, csa_updates={
            "criteria": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
            "state": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }, selector_hint={
            "support_family": "RULE_CLARIFICATION",
            "allowed_families": ["RULE_CLARIFICATION"],
            "confidence": "MEDIUM",
            "max_intensity": 1,
            "cognitive_gap_detected": True,
            "execution_request_detected": False,
            "evidence_ids": ["E-C"],
        }))
        selection = service.select(chain_id)
        constraints = selection["objective"]["semantic_constraints"]
        self.assertEqual(constraints["family_gate_mode"], "SOFT")
        self.assertFalse(constraints["hint_enforced"])
        self.assertEqual(selection["selected"], ["STATE_CONTEXT_RECOVERY_L1"])

    def test_semantic_soft_margin_reorders_without_creating_a_choice(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = OnlineInferenceService(
            database_path=Path(temp.name) / "online.sqlite3",
            profiles=profile_config(),
            registry=semantic_registry_config(),
            config=SelectorConfigV2(beta=0.75, eta=0.05, epsilon=0.005, semantic_hint_soft_margin=0.20),
        )
        chain_id = service.ingest_event(occasion_event())[
            "chain"
        ]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-SEMANTIC-MARGIN", chain_id=chain_id, csa_updates={
            "criteria": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
            "state": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }, selector_hint={
            "support_family": "RULE_CLARIFICATION",
            "allowed_families": ["RULE_CLARIFICATION"],
            "confidence": "MEDIUM",
            "max_intensity": 1,
            "cognitive_gap_detected": True,
            "execution_request_detected": False,
            "evidence_ids": ["E-C"],
        }))
        selection = service.select(chain_id)
        constraints = selection["objective"]["semantic_constraints"]
        self.assertEqual(selection["decision"], "INTERVENE")
        self.assertEqual(selection["selected"], ["RULE_CLARIFICATION_L1"])
        self.assertTrue(constraints["soft_preference_applied"])
        self.assertTrue(constraints["choice_suppressed_by_soft_preference"])

    def test_execution_request_does_not_suppress_a_cognitive_gap(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event())[
            "chain"
        ]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-EXECUTION-WITH-GAP", chain_id=chain_id, csa_updates={
            "criteria": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
            "state": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }, selector_hint={
            "support_family": "STATE_TRACE",
            "allowed_families": ["STATE_TRACE"],
            "confidence": "HIGH",
            "max_intensity": 2,
            "cognitive_gap_detected": True,
            "execution_request_detected": True,
            "evidence_ids": ["E-S"],
        }))
        selection = service.select(chain_id)
        self.assertNotEqual(selection["objective"].get("reason"), "INTERVENTION_NOT_ELIGIBLE")
        self.assertTrue(selection["objective"]["semantic_constraints"]["execution_request_detected"])

    def test_semantic_hint_can_suppress_cognitive_intervention(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-INELIGIBLE", chain_id=chain_id, csa_updates={
            "criteria": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
            "state": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }, selector_hint={
            "support_family": "STATE_TRACE",
            "allowed_families": ["STATE_TRACE"],
            "confidence": "HIGH",
            "max_intensity": 1,
            "cognitive_gap_detected": False,
            "execution_request_detected": True,
        }))
        selection = service.select(chain_id)
        self.assertEqual(selection["decision"], "NO_INTERVENTION")
        self.assertEqual(selection["objective"]["reason"], "INTERVENTION_NOT_ELIGIBLE")

    def test_high_hint_requires_semantically_supporting_evidence(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = OnlineInferenceService(
            database_path=Path(temp.name) / "online.sqlite3",
            profiles=profile_config(),
            registry=semantic_registry_config(),
            config=SelectorConfigV2(beta=0.75, eta=0.05, epsilon=0.03),
        )
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-SEMANTIC-MISMATCH", chain_id=chain_id, csa_updates={
            "criteria": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
            "state": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }, evidence_refs=[{
            "evidence_id": "E-C",
            "source": "CURRENT_USER_TURN",
            "semantic_role": "RULE_STATEMENT",
            "supports_families": ["STATE_CONTEXT_RECOVERY"],
            "supports_dimensions": ["criteria"],
        }], selector_hint={
            "support_family": "RULE_CLARIFICATION",
            "allowed_families": ["RULE_CLARIFICATION"],
            "confidence": "HIGH",
            "max_intensity": 2,
            "cognitive_gap_detected": True,
            "execution_request_detected": False,
            "evidence_ids": ["E-C"],
        }))
        selection = service.select(chain_id)
        constraints = selection["objective"]["semantic_constraints"]
        self.assertFalse(constraints["hint_evidence_valid"])
        self.assertNotEqual(constraints["family_gate_mode"], "HARD")

    def test_posterior_outcome_evidence_is_rejected_at_ingest(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event(evidence_ids=["POSTERIOR-EID"]))["chain"]["chain_id"]
        self.assertEqual(service.select(chain_id)["evidence_ids"], [])
        with self.assertRaises(ValidationError):
            service.ingest_event(occasion_event(
                evidence_refs=[{
                    "evidence_id": "POSTERIOR-E1",
                    "source": "OUTCOME_ANNOTATION",
                    "semantic_role": "OUTCOME_LABEL",
                    "supports_dimensions": ["state"],
                }]
            ))

    def test_no_intervention_reason_codes_cover_target_threshold_and_evidence(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event())[
            "chain"
        ]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-TARGET", chain_id=chain_id, csa_updates={
            "criteria": {"level": 2, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
            "state": {"level": 3, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 2, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }, evidence_refs=[
            {"evidence_id": "E-C", "source": "CURRENT_USER_TURN", "semantic_role": "STATE_OBSERVATION", "supports_dimensions": ["criteria"]},
            {"evidence_id": "E-S", "source": "CURRENT_USER_TURN", "semantic_role": "STATE_OBSERVATION", "supports_dimensions": ["state"]},
            {"evidence_id": "E-A", "source": "CURRENT_USER_TURN", "semantic_role": "STATE_OBSERVATION", "supports_dimensions": ["action"]},
        ]))
        target = service.select(chain_id)
        self.assertEqual(target["objective"]["reason"], "TARGET_REACHED")

        low_gap_chain = service.ingest_event(occasion_event(
            event_id="EVT-OCC-LOW-GAP",
            chain_id="P1::OCC-LOW-GAP::FD-LOW",
            occasion_id="OCC-LOW-GAP",
            focal_decision_id="FD-LOW",
        ))[
            "chain"
        ]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-LOW-GAP", chain_id=low_gap_chain, csa_updates={
            "criteria": {"level": 2, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
            "state": {"level": 3, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }))
        below = service.select(low_gap_chain)
        self.assertIn(below["objective"]["reason"], {"BELOW_ETA", "TARGET_REACHED"})

    def test_target_reached_requires_three_sufficient_evidenced_dimensions(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-TARGET-LIMITED", chain_id=chain_id, csa_updates={
            "criteria": {"level": 2, "assessability": "LIMITED", "evidence_ids": []},
            "state": {"level": 3, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 2, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }))
        selection = service.select(chain_id)
        self.assertNotEqual(selection["objective"].get("reason"), "TARGET_REACHED")
        self.assertIn(selection["objective"].get("reason"), {"BELOW_ETA", "INSUFFICIENT_EVIDENCE"})

    def test_limited_state_reports_insufficient_evidence_when_all_candidates_fail_floor(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        registry = registry_config()
        for candidate in registry["candidates"]:
            candidate["parameters"]["evidence"] = 0.5
        service = OnlineInferenceService(
            database_path=Path(temp.name) / "online.sqlite3",
            profiles=profile_config(),
            registry=registry,
            config=SelectorConfigV2(evidence_floor_when_limited=0.6),
        )
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-LIMITED-FLOOR", chain_id=chain_id, csa_updates={
            "criteria": {"level": 1, "assessability": "LIMITED", "evidence_ids": ["E-C"]},
            "state": {"level": 1, "assessability": "LIMITED", "evidence_ids": ["E-S"]},
            "action": {"level": 1, "assessability": "LIMITED", "evidence_ids": ["E-A"]},
        }))
        selection = service.select(chain_id)
        self.assertEqual(selection["decision"], "NO_INTERVENTION")
        self.assertEqual(selection["objective"]["reason"], "INSUFFICIENT_EVIDENCE")

    def test_exposure_updates_workflow_and_same_chain_cooldown(self):
        service = self.service()
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-COOLDOWN-STATE", chain_id=chain_id, csa_updates={
            "criteria": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
            "state": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 1, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }))
        first = service.select(chain_id)
        service.expose(chain_id, exposure_id="EXP-COOLDOWN", selection_decision_id=first["decision_id"])
        state = service.observe(chain_id)
        self.assertEqual(state.recent_exposure_count, 1)
        self.assertGreater(state.recent_exposure_burden, 0.0)
        self.assertTrue(state.cooldown_active)
        second = service.select(chain_id)
        skipped = second["objective"]["semantic_constraints"]["cooldown_skipped_candidate_ids"]
        self.assertIn(first["selected"][0], skipped)
        # Candidate-level cooldown leaves other registered paths available;
        # therefore the whole chain need not become NO_INTERVENTION.
        self.assertNotEqual(second["objective"]["semantic_constraints"]["cooldown_scope"], "CHAIN")
        service.record_action(chain_id, action="ACKNOWLEDGE")
        resumed = service.select(chain_id)
        self.assertFalse(resumed["objective"]["semantic_constraints"]["cooldown_active"])
        self.assertEqual(resumed["objective"]["semantic_constraints"]["cooldown_skipped_candidate_ids"], [])

    def test_present_choices_require_explicit_frozen_branch_choice_before_exposure(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        registry = {
            "registry_version": "STRATEGY-REGISTRY-V2-CHOICE-TEST",
            "registry_status": "TEST_ONLY",
            "candidates": [
                {"strategy_id": "STATE_CONTEXT_RECOVERY", "strategy_family": "STATE_CONTEXT_RECOVERY", "intensity": "L1",
                 "parameters": {"criteria": 1.0, "state": 1.0, "action": 1.0, "evidence": 0.8, "workflow": 0.9}, "template_id": "STATE"},
                {"strategy_id": "RULE_CLARIFICATION", "strategy_family": "RULE_CLARIFICATION", "intensity": "L1",
                 "parameters": {"criteria": 1.0, "state": 1.0, "action": 1.0, "evidence": 0.8, "workflow": 0.9}, "template_id": "RULE"},
            ],
            "templates": {
                "STATE": {"choice_condition_code": "STATE_FIRST", "choice_condition": "先恢复状态"},
                "RULE": {"choice_condition_code": "RULE_FIRST", "choice_condition": "先澄清规则"},
            },
        }
        service = OnlineInferenceService(
            database_path=Path(temp.name) / "online.sqlite3",
            profiles=profile_config(),
            registry=registry,
            config=SelectorConfigV2(beta=0.75, eta=0.05, epsilon=0.03),
        )
        chain_id = service.ingest_event(occasion_event())["chain"]["chain_id"]
        service.ingest_event(occasion_event(event_id="EVT-CHOICE-STATE", chain_id=chain_id, csa_updates={
            "criteria": {"level": 0, "assessability": "SUFFICIENT", "evidence_ids": ["E-C"]},
            "state": {"level": 0, "assessability": "SUFFICIENT", "evidence_ids": ["E-S"]},
            "action": {"level": 0, "assessability": "SUFFICIENT", "evidence_ids": ["E-A"]},
        }))
        selection = service.select(chain_id)
        self.assertEqual(selection["decision"], "PRESENT_CHOICES")
        self.assertEqual(selection["choice_contract"]["type"], "EXPLICIT_USER_BRANCH")
        selected_candidate = selection["selected"][0]
        expected_condition = next(
            option["branch_condition_code"]
            for option in selection["options"]
            if option["strategy_id"] == selected_candidate
        )
        with self.assertRaises(ValidationError):
            service.expose(chain_id, exposure_id="EXP-CHOICE-NO-BRANCH", selection_decision_id=selection["decision_id"])
        self.assertNotIn("pre", service.store.snapshots(chain_id))
        with self.assertRaises(ValidationError):
            service.record_choice(
                chain_id,
                selection_decision_id=selection["decision_id"],
                selected_candidate_id=selected_candidate,
                choice_condition="WRONG_BRANCH",
                choice_basis="用户明确偏好",
            )
        choice = service.record_choice(
            chain_id,
            selection_decision_id=selection["decision_id"],
            selected_candidate_id=selected_candidate,
            choice_condition=expected_condition,
            choice_basis="用户明确偏好",
        )
        exposure = service.expose(
            chain_id,
            exposure_id="EXP-CHOICE-WITH-BRANCH",
            selection_decision_id=selection["decision_id"],
            selected_candidate_id=selected_candidate,
        )
        self.assertEqual(exposure["choice_event_id"], choice["event_id"])
        self.assertEqual(exposure["selected_candidate_id"], selected_candidate)


if __name__ == "__main__":
    unittest.main()
