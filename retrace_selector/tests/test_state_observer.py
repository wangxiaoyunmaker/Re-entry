import unittest

from retrace_selector.state_observer import observe_runtime_support_state


class RuntimeSupportStateTests(unittest.TestCase):
    def test_progressing_without_signal_does_not_intervene(self):
        result = observe_runtime_support_state(progress_observed=True)
        self.assertEqual(result["support_opportunity"], "NONE")
        self.assertEqual(result["observation_state"], "DELEGATION_PROGRESSING")

    def test_one_failed_delegation_gets_early_low_burden_support(self):
        result = observe_runtime_support_state(
            direct_delegation_failures=1,
            progress_observed=False,
        )
        self.assertEqual(result["support_opportunity"], "EARLY_SUPPORT")
        self.assertEqual(result["observation_state"], "EARLY_SUPPORT_OPPORTUNITY")

    def test_repeated_failure_does_not_require_user_reentry_behavior(self):
        result = observe_runtime_support_state(direct_delegation_failures=2)
        self.assertTrue(result["repeated_unresolved"])
        self.assertEqual(result["support_opportunity"], "EARLY_SUPPORT")

    def test_user_support_action_routes_to_deeper_profile_analysis(self):
        result = observe_runtime_support_state(
            behavior_evidence=[
                {
                    "actor": "USER",
                    "action_focus": "DISPOSITION",
                    "supports_primitives": ["DISPOSITION_COORDINATION"],
                }
            ],
        )
        self.assertEqual(result["support_opportunity"], "ANALYSIS_NEEDED")
        self.assertTrue(result["should_generate_support_profile"])

    def test_basis_signal_routes_without_assigning_a_mechanism(self):
        result = observe_runtime_support_state(
            behavior_evidence=[
                {
                    "actor": "USER",
                    "basis_relevant_signal": True,
                    "action_focus": "NONE",
                }
            ]
        )
        self.assertTrue(result["basis_relevant_signal"])
        self.assertEqual(result["support_opportunity"], "ANALYSIS_NEEDED")
        self.assertEqual(result["explicit_user_support_action"], False)

    def test_high_risk_routes_independently_of_behavior_evidence(self):
        result = observe_runtime_support_state(
            consequence="high",
            reversibility="low",
            behavior_evidence=[],
        )
        self.assertTrue(result["high_risk_signal"])
        self.assertEqual(result["support_opportunity"], "ANALYSIS_NEEDED")

    def test_failure_and_process_memory_are_independent_outputs(self):
        result = observe_runtime_support_state(
            direct_delegation_failures=1,
            target_key="same-task",
            delegation_attempt_count=2,
            last_confirmed_progress=False,
            failure_window=1,
            cooldown_until="2026-08-23T12:00:00Z",
            recent_intervention_ids=["VERIFICATION-L1"],
        )
        self.assertTrue(result["delegation_failure_signal"])
        self.assertFalse(result["repeated_unresolved"])
        self.assertEqual(result["target_key"], "same-task")
        self.assertEqual(result["recent_intervention_ids"], ["VERIFICATION-L1"])

    def test_inadequate_trace_abstains_without_independent_signal(self):
        result = observe_runtime_support_state(trace_coverage="INADEQUATE")
        self.assertEqual(result["support_opportunity"], "ABSTAIN")


if __name__ == "__main__":
    unittest.main()
