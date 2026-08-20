from __future__ import annotations

import hashlib
import json
from typing import Iterable

from .candidates import generate_candidates
from .config import canonical_json
from .constraints import evaluate_constraints, intervention_burden
from .models import (
    CandidateEvaluation,
    DecisionState,
    Outcome,
    PolicySpec,
    Primitive,
    Risk,
    SelectionResult,
    TemplateCatalog,
)
from .rendering import render_brief
from .scoring import NO_INTERVENTION_SCORE, score_brief, utility
from .skyline import compute_skyline


def _audit_id(
    state: DecisionState, policy: PolicySpec, templates: TemplateCatalog
) -> str:
    material = {
        "state": state.to_dict(),
        "policy_hash": policy.config_hash,
        "template_hash": templates.config_hash,
        "engine_version": policy.engine_version,
    }
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def _rank_key(item: CandidateEvaluation) -> tuple[float, float, float, int, str]:
    assert item.utility is not None and item.score is not None
    level = int(item.brief.level) if item.brief.level is not None else 0
    return (-item.utility, -item.score.E, -item.score.W, level, item.brief.brief_id)


class SelectionEngine:
    def __init__(self, policy: PolicySpec, templates: TemplateCatalog):
        self.policy = policy
        self.templates = templates

    def _terminal_result(
        self,
        state: DecisionState,
        outcome: Outcome,
        reason_code: str,
        *,
        warnings: Iterable[str] = (),
    ) -> SelectionResult:
        return SelectionResult(
            audit_id=_audit_id(state, self.policy, self.templates),
            outcome=outcome,
            reason_codes=(reason_code,),
            selected_ids=(),
            generated=(),
            feasible_ids=(),
            skyline_ids=(),
            dominance_witnesses={},
            frontier_ratio=None,
            warnings=tuple(warnings),
            rendered_briefs=(),
            metadata=self._metadata(state, forced_governance=False),
        )

    def _metadata(self, state: DecisionState, forced_governance: bool) -> dict:
        return {
            "state": state.to_dict(),
            "policy_version": self.policy.policy_version,
            "policy_hash": self.policy.config_hash,
            "template_version": self.templates.template_version,
            "template_hash": self.templates.config_hash,
            "engine_version": self.policy.engine_version,
            "record_only": True,
            "forced_governance": forced_governance,
        }

    def select(self, state: DecisionState) -> SelectionResult:
        warnings: list[str] = []
        needs = state.governance_needs
        if (
            state.process_state.value == "DELEGATION_PROGRESSING"
            and any(getattr(needs, key) > 0 for key in ("O", "S", "D"))
        ):
            warnings.append("W001_PROGRESSING_WITH_NONZERO_GOVERNANCE_NEEDS")
        if state.authorization_risk is Risk.HIGH and needs.D == 0:
            warnings.append("W002_HIGH_AUTHORIZATION_WITH_ZERO_D_NEED")

        high_risk = state.authorization_risk is Risk.HIGH or (
            state.consequence is Risk.HIGH and state.reversibility is Risk.LOW
        )
        if (
            state.state_confidence < self.policy.thresholds.low_confidence
            and high_risk
        ):
            return self._terminal_result(
                state,
                Outcome.REQUEST_CLARIFICATION,
                "T001_LOW_CONFIDENCE_HIGH_RISK_CONFLICT",
                warnings=warnings,
            )

        briefs = generate_candidates(state, self.policy)
        evaluations: list[CandidateEvaluation] = []
        for brief in briefs:
            constraints = evaluate_constraints(brief, state, self.policy)
            score = score_brief(brief, state, self.policy)
            evaluations.append(
                CandidateEvaluation(
                    brief=brief,
                    constraints=constraints,
                    score=score,
                )
            )

        feasible = [item for item in evaluations if item.allowed]
        if not feasible:
            result = self._terminal_result(
                state,
                Outcome.SAFE_HOLD,
                "T002_EMPTY_FEASIBLE_SET",
                warnings=warnings,
            )
            result.generated = tuple(evaluations)
            return result

        frontier, witnesses = compute_skyline(
            feasible, self.policy.thresholds.dominance_epsilon
        )
        baseline_utility = utility(NO_INTERVENTION_SCORE, self.policy)
        for item in frontier:
            assert item.score is not None
            item.utility = utility(item.score, self.policy)
            item.gain_vs_no_intervention = item.utility - baseline_utility

        ranked = sorted(frontier, key=_rank_key)
        feasible_ids = tuple(sorted(item.brief.brief_id for item in feasible))
        skyline_ids = tuple(item.brief.brief_id for item in ranked)
        frontier_ratio = len(frontier) / len(feasible)
        if frontier_ratio >= 0.9 and len(feasible) > 1:
            warnings.append("W003_SKYLINE_FRONTIER_RATIO_HIGH")

        no_intervention_eval = next(
            (item for item in evaluations if item.brief.is_no_intervention), None
        )
        b0_allowed = bool(no_intervention_eval and no_intervention_eval.allowed)
        forced_governance = not b0_allowed
        interventions = [item for item in ranked if not item.brief.is_no_intervention]

        if not interventions:
            if b0_allowed:
                return self._build_result(
                    state,
                    Outcome.NO_INTERVENTION,
                    ("S001_NO_FEASIBLE_INTERVENTION",),
                    (),
                    evaluations,
                    feasible_ids,
                    skyline_ids,
                    witnesses,
                    frontier_ratio,
                    warnings,
                    forced_governance,
                    baseline_utility,
                )
            result = self._terminal_result(
                state,
                Outcome.SAFE_HOLD,
                "T003_NO_SAFE_INTERVENTION",
                warnings=warnings,
            )
            result.generated = tuple(evaluations)
            return result

        top = interventions[0]
        assert top.gain_vs_no_intervention is not None
        if (
            not forced_governance
            and top.gain_vs_no_intervention <= self.policy.thresholds.gain
        ):
            return self._build_result(
                state,
                Outcome.NO_INTERVENTION,
                ("S002_GAIN_NOT_ABOVE_THRESHOLD",),
                (),
                evaluations,
                feasible_ids,
                skyline_ids,
                witnesses,
                frontier_ratio,
                warnings,
                forced_governance,
                baseline_utility,
            )

        selected = [top]
        reason_codes = [
            "S004_FORCED_GOVERNANCE" if forced_governance else "S003_POSITIVE_GAIN"
        ]
        outcome = Outcome.INTERVENE
        if len(interventions) >= 2:
            second = interventions[1]
            assert top.utility is not None and second.utility is not None
            if top.utility - second.utility <= self.policy.thresholds.near_tie:
                top_need = self.policy.primitive_profiles[top.brief.primitive].primary_need
                second_need = self.policy.primitive_profiles[
                    second.brief.primitive
                ].primary_need
                if top_need != second_need:
                    selected = [top, second]
                    outcome = Outcome.PRESENT_CHOICES
                    reason_codes.append("S005_NEAR_TIE_DIFFERENT_PATHS")
                else:
                    selected = [
                        min(
                            (top, second),
                            key=lambda item: (
                                intervention_burden(item.brief, state, self.policy),
                                int(item.brief.level),
                                item.brief.brief_id,
                            ),
                        )
                    ]
                    reason_codes.append("S006_NEAR_TIE_LOWER_BURDEN")

        return self._build_result(
            state,
            outcome,
            tuple(reason_codes),
            tuple(selected),
            evaluations,
            feasible_ids,
            skyline_ids,
            witnesses,
            frontier_ratio,
            warnings,
            forced_governance,
            baseline_utility,
        )

    def _build_result(
        self,
        state: DecisionState,
        outcome: Outcome,
        reason_codes: tuple[str, ...],
        selected: tuple[CandidateEvaluation, ...],
        evaluations: list[CandidateEvaluation],
        feasible_ids: tuple[str, ...],
        skyline_ids: tuple[str, ...],
        witnesses: dict[str, tuple[str, ...]],
        frontier_ratio: float,
        warnings: list[str],
        forced_governance: bool,
        baseline_utility: float,
    ) -> SelectionResult:
        rendered = tuple(
            render_brief(item.brief, state, self.templates) for item in selected
        )
        metadata = self._metadata(state, forced_governance)
        metadata["baseline_utility"] = baseline_utility
        return SelectionResult(
            audit_id=_audit_id(state, self.policy, self.templates),
            outcome=outcome,
            reason_codes=reason_codes,
            selected_ids=tuple(item.brief.brief_id for item in selected),
            generated=tuple(evaluations),
            feasible_ids=feasible_ids,
            skyline_ids=skyline_ids,
            dominance_witnesses=witnesses,
            frontier_ratio=frontier_ratio,
            warnings=tuple(warnings),
            rendered_briefs=rendered,
            metadata=metadata,
        )
