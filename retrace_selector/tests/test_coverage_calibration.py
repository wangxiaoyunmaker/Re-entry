from __future__ import annotations

import unittest

from retrace_selector.coverage_calibration import (
    CoverageAbstained,
    condition_candidate_capability,
    derive_support_needs,
)
from retrace_selector.models import SupportNeeds, ValidationError


def dimension(level, assessability="SUFFICIENT", evidence_ids=None):
    return {
        "level": level,
        "assessability": assessability,
        "evidence_ids": evidence_ids if evidence_ids is not None else ["E1"],
    }


class CoverageCalibrationTests(unittest.TestCase):
    def test_numeric_coverage_is_compared_with_frozen_target(self):
        result = derive_support_needs(
            {"criteria": 3, "state": 2, "action": 2},
            {
                "criteria": dimension(2),
                "state": dimension(2),
                "action": dimension(1),
            },
        )
        self.assertEqual(result.support_needs, SupportNeeds(1, 0, 1))
        self.assertEqual(result.unknown_dimensions, ())
        self.assertEqual(result.confidence_cap, 0.9)

    def test_unknown_is_not_encoded_as_observed_zero(self):
        result = derive_support_needs(
            {"criteria": 2, "state": 3, "action": 1},
            {
                "criteria": dimension(2),
                "state": dimension("UNKNOWN", "LIMITED", ["E-question"]),
                "action": dimension(1),
            },
        )
        self.assertEqual(result.support_needs, SupportNeeds(0, 3, 0))
        self.assertEqual(result.unknown_dimensions, ("state",))
        self.assertEqual(result.confidence_cap, 0.5)

    def test_irrelevant_unknown_target_does_not_create_a_gap(self):
        result = derive_support_needs(
            {"criteria": 2, "state": 0, "action": 1},
            {
                "criteria": dimension(1),
                "state": dimension("UNKNOWN", "LIMITED", []),
                "action": dimension(1),
            },
        )
        self.assertEqual(result.support_needs, SupportNeeds(1, 0, 0))

    def test_zero_requires_direct_sufficient_evidence(self):
        with self.assertRaisesRegex(ValidationError, "level 0 requires"):
            derive_support_needs(
                {"criteria": 2, "state": 2, "action": 2},
                {
                    "criteria": dimension(0, "LIMITED", []),
                    "state": dimension(1),
                    "action": dimension(1),
                },
            )

    def test_unknown_requires_limited_assessability(self):
        with self.assertRaisesRegex(ValidationError, "UNKNOWN requires"):
            derive_support_needs(
                {"criteria": 2, "state": 2, "action": 2},
                {
                    "criteria": dimension("UNKNOWN", "SUFFICIENT", []),
                    "state": dimension(1),
                    "action": dimension(1),
                },
            )

    def test_abstain_stops_derivation(self):
        with self.assertRaises(CoverageAbstained):
            derive_support_needs(
                {"criteria": 2, "state": 2, "action": 2},
                {
                    "criteria": dimension(1),
                    "state": dimension(1, "ABSTAIN"),
                    "action": dimension(1),
                },
            )

    def test_candidate_capability_is_conditioned_without_prefiltering(self):
        conditioned = condition_candidate_capability(
            (0.20, 0.80, 0.30), SupportNeeds(0, 3, 1)
        )
        self.assertEqual(conditioned, (0.0, 0.8, 0.105))

    def test_authorization_requirement_activates_capable_action_support(self):
        conditioned = condition_candidate_capability(
            (0.20, 0.10, 0.65),
            SupportNeeds(0, 0, 0),
            authorization_capable=True,
            authorization_required=True,
        )
        self.assertEqual(conditioned, (0.0, 0.0, 0.65))


if __name__ == "__main__":
    unittest.main()
