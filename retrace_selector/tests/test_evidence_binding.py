from __future__ import annotations

import hashlib
import unittest

from retrace_selector.constraints import evaluate_constraints
from retrace_selector.evidence import candidate_evidence_completeness, supporting_evidence
from retrace_selector.models import (
    DecisionBrief,
    DecisionState,
    Level,
    Primitive,
    ValidationError,
)
from retrace_selector.rendering import render_brief

from common import engine, state_dict


def bound_evidence(
    evidence_id: str,
    *,
    dimensions: list[str] | None = None,
    primitives: list[str] | None = None,
    source: str = "OBSERVED",
):
    return {
        "evidence_id": evidence_id,
        "source": source,
        "locator": f"case/transcript.jsonl#{evidence_id}",
        "sequence_index": 1,
        "content_sha256": hashlib.sha256(evidence_id.encode()).hexdigest(),
        "supports_dimensions": dimensions or [],
        "supports_primitives": primitives or [],
        "available_at_decision": True,
    }


def v2_state(*, evidence, **overrides):
    raw = state_dict(
        schema_version="retrace-state-v2",
        evidence=evidence,
        evidence_completeness="sufficient" if evidence else "none",
        **overrides,
    )
    return DecisionState.from_dict(raw)


class EvidenceBindingTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine()

    def test_v2_requires_prefix_metadata_and_binding(self):
        raw = state_dict(
            schema_version="retrace-state-v2",
            evidence=[{"evidence_id": "E1", "source": "OBSERVED"}],
        )
        with self.assertRaisesRegex(ValidationError, "requires locator"):
            DecisionState.from_dict(raw)
        evidence = bound_evidence("E1", dimensions=["evidence_action_governance"])
        evidence["available_at_decision"] = False
        with self.assertRaisesRegex(ValidationError, "available_at_decision=true"):
            v2_state(evidence=[evidence])

    def test_primitive_binding_takes_priority_over_need_binding(self):
        decision_state = v2_state(
            evidence=[
                bound_evidence(
                    "E1",
                    dimensions=["project_state_reconstruction"],
                    primitives=["PROVENANCE"],
                )
            ],
            support_needs={"criteria_basis_reconstruction": 0, "project_state_reconstruction": 3, "evidence_action_governance": 0},
        )
        provenance = DecisionBrief.intervention(Primitive.PROVENANCE, Level.L1)
        causal = DecisionBrief.intervention(Primitive.CAUSAL_EXPLANATION, Level.L1)
        self.assertEqual(
            [item.evidence_id for item in supporting_evidence(provenance, decision_state, self.engine.policy)],
            ["E1"],
        )
        self.assertEqual(
            supporting_evidence(causal, decision_state, self.engine.policy), ()
        )

    def test_action_boundary_binding_prefers_disposition_over_verification(self):
        decision_state = v2_state(
            evidence=[
                bound_evidence(
                    "E-boundary",
                    primitives=["DISPOSITION_COORDINATION"],
                )
            ],
            support_needs={
                "criteria_basis_reconstruction": 0,
                "project_state_reconstruction": 0,
                "evidence_action_governance": 3,
            },
        )
        result = self.engine.select(decision_state)
        self.assertEqual(result.selected_ids, ("DISPOSITION_COORDINATION-L2",))

    def test_verification_binding_still_prefers_verification(self):
        decision_state = v2_state(
            evidence=[bound_evidence("E-test", primitives=["VERIFICATION"])],
            support_needs={
                "criteria_basis_reconstruction": 0,
                "project_state_reconstruction": 0,
                "evidence_action_governance": 3,
            },
        )
        result = self.engine.select(decision_state)
        self.assertEqual(result.selected_ids, ("VERIFICATION-L2",))

    def test_unrelated_observation_does_not_unlock_causal_explanation(self):
        decision_state = v2_state(
            evidence=[bound_evidence("E1", dimensions=["evidence_action_governance"])],
            support_needs={"criteria_basis_reconstruction": 0, "project_state_reconstruction": 3, "evidence_action_governance": 3},
        )
        causal = DecisionBrief.intervention(Primitive.CAUSAL_EXPLANATION, Level.L2)
        failed = {
            item.rule_id
            for item in evaluate_constraints(causal, decision_state, self.engine.policy)
            if not item.allowed
        }
        self.assertIn("C030_MINIMUM_EVIDENCE", failed)
        self.assertIn("C035_CAUSAL_EXPLANATION_REQUIRES_OBSERVATION", failed)

    def test_rendered_brief_contains_only_candidate_supporting_evidence(self):
        decision_state = v2_state(
            evidence=[
                bound_evidence("E-criteria", dimensions=["criteria_basis_reconstruction"]),
                bound_evidence("E-action", dimensions=["evidence_action_governance"]),
            ],
            support_needs={"criteria_basis_reconstruction": 3, "project_state_reconstruction": 0, "evidence_action_governance": 3},
        )
        brief = DecisionBrief.intervention(Primitive.VERIFICATION, Level.L1)
        rendered = render_brief(
            brief, decision_state, self.engine.policy, self.engine.templates
        )
        self.assertEqual(rendered.evidence_ids, ("E-action",))

    def test_candidate_cannot_inherit_sufficient_from_unrelated_evidence(self):
        decision_state = v2_state(
            evidence=[
                bound_evidence("E-criteria", dimensions=["criteria_basis_reconstruction"]),
                bound_evidence("E-action", dimensions=["evidence_action_governance"]),
            ],
            support_needs={"criteria_basis_reconstruction": 3, "project_state_reconstruction": 0, "evidence_action_governance": 3},
        )
        brief = DecisionBrief.intervention(Primitive.VERIFICATION, Level.L2)
        self.assertEqual(
            candidate_evidence_completeness(brief, decision_state, self.engine.policy).value,
            "partial",
        )


if __name__ == "__main__":
    unittest.main()
