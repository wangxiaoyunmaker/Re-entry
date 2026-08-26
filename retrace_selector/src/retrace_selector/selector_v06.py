"""Strict v0.6 StrategyCandidate selector.

This engine is intentionally separate from the legacy fixed-Primitive engine
so historical replays remain reproducible while the new registry contract is
adopted.
"""

from __future__ import annotations

import hashlib
from typing import Iterable

from .config import canonical_json, content_hash
from .coverage_calibration import condition_candidate_capability
from .models import (
    ConstraintRecord,
    EvidenceCompleteness,
    Level,
    Outcome,
    ScoreVector,
    ValidationError,
)
from .reference_point import reference_point
from .strategy_registry import StrategyRegistry
from .target_optimization import gain_vs_no_intervention, objective_value
from .v06_models import (
    CoreRisk,
    SelectionPolicy,
    SelectorDecisionState,
    StrategyCandidate,
    V06CandidateEvaluation,
    V06RenderedBrief,
    V06SelectionResult,
)
from .version import V06_ENGINE_VERSION


LOW_CONFIDENCE_THRESHOLD = 0.6
COOLDOWN_COUNT = 3
COOLDOWN_PENALTY_PER_INTERVENTION = 0.05
MAX_EFFECTIVE_WORKFLOW_COST = 0.8
POLICY_VERSION = "selection-policy-v0.6"

ALLOWED_LEVELS = {
    "DELEGATION_PROGRESSING": (),
    "EARLY_SUPPORT_OPPORTUNITY": (Level.L1,),
    "REENTRY_OCCASION_OBSERVED": (Level.L1, Level.L2, Level.L3),
    "GOVERNANCE_RECOVERING": (Level.L1,),
}

NO_INTERVENTION_SCORE = ScoreVector(0.0, 0.0, 0.0, 1.0, 1.0)


def effective_workflow_cost(
    candidate: StrategyCandidate,
    state: SelectorDecisionState,
) -> float:
    repetition_adjustment = min(
        COOLDOWN_PENALTY_PER_INTERVENTION * state.recent_intervention_count,
        1.0,
    )
    return min(1.0, candidate.workflow_cost + repetition_adjustment)


def score_candidate(
    candidate: StrategyCandidate,
    state: SelectorDecisionState,
    *,
    state_conditioned: bool = False,
    authorization_capable: bool = False,
) -> ScoreVector:
    workflow_cost = effective_workflow_cost(candidate, state)
    capability = candidate.capability
    if state_conditioned:
        capability = condition_candidate_capability(
            capability,
            state.support_needs,
            authorization_capable=authorization_capable,
            authorization_required=state.authorization_required,
        )
    return ScoreVector(
        criteria_basis_reconstruction=capability[0],
        project_state_reconstruction=capability[1],
        evidence_action_governance=capability[2],
        evidence_quality=min(candidate.evidence_quality, state.evidence_level),
        workflow_continuity=1.0 - workflow_cost,
    )


def _record(rule_id: str, allowed: bool, reason: str, priority: int) -> ConstraintRecord:
    return ConstraintRecord(
        rule_id=rule_id,
        allowed=allowed,
        reason=reason,
        priority=priority,
    )


def _evidence_rank(value: float) -> int:
    if value >= 1.0:
        return EvidenceCompleteness.SUFFICIENT.rank
    if value >= 0.5:
        return EvidenceCompleteness.PARTIAL.rank
    return EvidenceCompleteness.NONE.rank


def evaluate_candidate_constraints(
    candidate: StrategyCandidate,
    state: SelectorDecisionState,
    registry: StrategyRegistry,
) -> tuple[ConstraintRecord, ...]:
    catalog = registry.catalog_entry(candidate)
    level = candidate.intensity
    cost = effective_workflow_cost(candidate, state)
    records = [
        _record(
            "MINIMUM_EVIDENCE",
            _evidence_rank(state.evidence_level) >= candidate.minimum_evidence.rank,
            "current evidence must meet the candidate minimum",
            10,
        ),
        _record(
            "OBSERVED_EVIDENCE_FOR_CAUSAL_CLAIM",
            not (catalog.deterministic_causal_claim and level >= Level.L2)
            or state.has_observed_evidence,
            "deterministic causal support at L2/L3 requires observed evidence",
            20,
        ),
        _record(
            "AUTHORIZATION_CAPABLE",
            not state.authorization_required
            or (catalog.authorization_capable and level >= Level.L2),
            "authorization-required states need an authorization-capable L2/L3 strategy",
            30,
        ),
        _record(
            "HIGH_RISK_MINIMUM_INTENSITY",
            state.risk_level is not CoreRisk.HIGH or level >= Level.L2,
            "high-risk states require at least L2 protection",
            40,
        ),
        _record(
            "LOW_RISK_MAXIMUM_INTENSITY",
            state.risk_level is not CoreRisk.LOW or level is not Level.L3,
            "low-risk states do not permit L3 interruption",
            50,
        ),
        _record(
            "LOW_CONFIDENCE_INTENSITY_CAP",
            state.confidence >= LOW_CONFIDENCE_THRESHOLD or level is Level.L1,
            "low-confidence states permit only L1",
            60,
        ),
        _record(
            "PROCESS_STATE_INTENSITY_CAP",
            level in ALLOWED_LEVELS[state.process_state.value],
            "intensity must be allowed in the current process state",
            70,
        ),
        _record(
            "AVOID_DUPLICATE_VERIFICATION",
            not (state.active_verification and catalog.verification_support),
            "do not add verification support while effective verification is active",
            80,
        ),
        _record(
            "RECENT_INTERVENTION_COOLDOWN",
            state.recent_intervention_count < COOLDOWN_COUNT or level is Level.L1,
            "cooldown permits only L1 after repeated interventions",
            90,
        ),
        _record(
            "MAX_WORKFLOW_COST",
            cost <= MAX_EFFECTIVE_WORKFLOW_COST,
            "effective workflow cost must not exceed 0.8",
            100,
        ),
    ]
    return tuple(records)


def _dominates(
    left: V06CandidateEvaluation,
    right: V06CandidateEvaluation,
    epsilon: float = 1e-12,
) -> bool:
    if left.score is None or right.score is None:
        raise ValueError("Skyline requires scored candidates")
    no_worse = all(
        left_value >= right_value - epsilon
        for left_value, right_value in zip(left.score.vector(), right.score.vector())
    )
    strictly_better = any(
        left_value > right_value + epsilon
        for left_value, right_value in zip(left.score.vector(), right.score.vector())
    )
    return no_worse and strictly_better


def compute_skyline(
    candidates: Iterable[V06CandidateEvaluation],
) -> tuple[tuple[V06CandidateEvaluation, ...], dict[str, tuple[str, ...]]]:
    ordered = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    frontier: list[V06CandidateEvaluation] = []
    witnesses: dict[str, tuple[str, ...]] = {}
    for candidate in ordered:
        dominators = tuple(
            other.candidate_id
            for other in ordered
            if other is not candidate and _dominates(other, candidate)
        )
        if dominators:
            witnesses[candidate.candidate_id] = tuple(sorted(dominators))
        else:
            frontier.append(candidate)
    return tuple(frontier), witnesses


class V06SelectionEngine:
    def __init__(
        self,
        registry: StrategyRegistry,
        policy: SelectionPolicy,
        *,
        policy_hash: str | None = None,
    ):
        self.registry = registry
        self.policy = policy
        expected_policy_hash = content_hash(policy.to_dict())
        if policy_hash is not None and policy_hash != expected_policy_hash:
            raise ValidationError("policy_hash does not match the supplied v0.6 policy")
        self.policy_hash = expected_policy_hash

    def _audit_id(self, state: SelectorDecisionState) -> str:
        payload = {
            "state": state.to_dict(),
            "registry_hash": self.registry.config_hash,
            "policy_hash": self.policy_hash,
            "engine_version": V06_ENGINE_VERSION,
            "contract_version": "v0.6",
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def _state_hash(self, state: SelectorDecisionState) -> str:
        return hashlib.sha256(
            canonical_json(state.to_dict()).encode("utf-8")
        ).hexdigest()

    def _metadata(
        self,
        state: SelectorDecisionState,
        *,
        forced_governance: bool,
        baseline_objective: float,
        best_objective: float | None,
        gain: float | None,
    ) -> dict:
        target = reference_point(state)
        return {
            "state": state.to_dict(),
            "decision_state_hash": self._state_hash(state),
            "derived_state": {
                "support_opportunity": state.support_opportunity,
                "safe_to_continue": state.safe_to_continue,
                "has_observed_evidence": state.has_observed_evidence,
            },
            "registry_version": self.registry.registry_version,
            "registry_status": self.registry.registry_status,
            "capability_mode": self.registry.capability_mode,
            "registry_hash": self.registry.config_hash,
            "policy": self.policy.to_dict(),
            "policy_version": POLICY_VERSION,
            "policy_hash": self.policy_hash,
            "engine_version": V06_ENGINE_VERSION,
            "forced_governance": forced_governance,
            "reference_point": target.to_dict(),
            "J_no_intervention": baseline_objective,
            "J_best_intervention": best_objective,
            "Gain": gain,
            "audit_record_ready": True,
        }

    def _result(
        self,
        state: SelectorDecisionState,
        outcome: Outcome,
        reason_codes: tuple[str, ...],
        selected: tuple[V06CandidateEvaluation, ...],
        generated: tuple[V06CandidateEvaluation, ...],
        feasible_ids: tuple[str, ...],
        skyline_ids: tuple[str, ...],
        witnesses: dict[str, tuple[str, ...]],
        frontier_ratio: float | None,
        *,
        forced_governance: bool,
        baseline_objective: float,
        best_objective: float | None,
        gain: float | None,
    ) -> V06SelectionResult:
        rendered = []
        for item in selected:
            assert item.candidate is not None
            template = self.registry.template(item.candidate)
            rendered.append(
                V06RenderedBrief(
                    candidate_id=item.candidate_id,
                    strategy_id=item.candidate.strategy_id,
                    intensity=item.candidate.intensity.name,
                    title=template.title,
                    message=template.message,
                    evidence_ids=tuple(ref.evidence_id for ref in state.evidence_refs),
                    next_step=template.next_step,
                )
            )
        return V06SelectionResult(
            audit_id=self._audit_id(state),
            outcome=outcome,
            reason_codes=reason_codes,
            selected_ids=tuple(item.candidate_id for item in selected),
            generated=generated,
            feasible_ids=feasible_ids,
            skyline_ids=skyline_ids,
            dominance_witnesses=witnesses,
            frontier_ratio=frontier_ratio,
            rendered_briefs=tuple(rendered),
            metadata=self._metadata(
                state,
                forced_governance=forced_governance,
                baseline_objective=baseline_objective,
                best_objective=best_objective,
                gain=gain,
            ),
        )

    def select(self, state: SelectorDecisionState) -> V06SelectionResult:
        baseline_objective, _, _ = objective_value(
            NO_INTERVENTION_SCORE, state, self.policy
        )
        baseline_constraint = _record(
            "SAFE_TO_CONTINUE",
            state.safe_to_continue,
            "no intervention is feasible only when continuing is safe",
            0,
        )
        baseline = V06CandidateEvaluation(
            candidate=None,
            constraints=(baseline_constraint,),
            score=NO_INTERVENTION_SCORE,
            effective_workflow_cost=0.0,
            objective_value=baseline_objective,
            gain=0.0,
        )

        if not state.support_signal and state.safe_to_continue:
            return self._result(
                state,
                Outcome.NO_INTERVENTION,
                ("NO_SUPPORT_SIGNAL",),
                (),
                (baseline,),
                ("NO_INTERVENTION",),
                ("NO_INTERVENTION",),
                {},
                1.0,
                forced_governance=False,
                baseline_objective=baseline_objective,
                best_objective=None,
                gain=None,
            )

        evaluated: list[V06CandidateEvaluation] = []
        for candidate in self.registry.candidates:
            constraints = evaluate_candidate_constraints(candidate, state, self.registry)
            allowed = all(item.allowed for item in constraints)
            catalog = self.registry.catalog_entry(candidate)
            score = (
                score_candidate(
                    candidate,
                    state,
                    state_conditioned=(
                        self.registry.capability_mode == "STATE_CONDITIONED"
                    ),
                    authorization_capable=catalog.authorization_capable,
                )
                if allowed
                else None
            )
            if score is None:
                evaluated.append(
                    V06CandidateEvaluation(
                        candidate=candidate,
                        constraints=constraints,
                        score=None,
                        effective_workflow_cost=effective_workflow_cost(candidate, state),
                    )
                )
                continue
            value, _, _ = objective_value(score, state, self.policy)
            evaluated.append(
                V06CandidateEvaluation(
                    candidate=candidate,
                    constraints=constraints,
                    score=score,
                    effective_workflow_cost=effective_workflow_cost(candidate, state),
                    objective_value=value,
                    gain=gain_vs_no_intervention(value, baseline_objective),
                )
            )

        generated = (baseline, *evaluated)
        feasible = tuple(
            item for item in generated if item.allowed and item.score is not None
        )
        frontier, witnesses = compute_skyline(feasible) if feasible else ((), {})
        frontier_ratio = len(frontier) / len(feasible) if feasible else None
        feasible_ids = tuple(sorted(item.candidate_id for item in feasible))
        skyline_ids = tuple(sorted(item.candidate_id for item in frontier))
        interventions = [item for item in frontier if item.candidate is not None]
        interventions.sort(
            key=lambda item: (
                item.objective_value if item.objective_value is not None else float("inf"),
                item.effective_workflow_cost,
                int(item.candidate.intensity) if item.candidate else 0,
                item.candidate_id,
            )
        )
        forced_governance = not state.safe_to_continue

        if not interventions:
            outcome = Outcome.SAFE_HOLD if forced_governance else Outcome.NO_INTERVENTION
            return self._result(
                state,
                outcome,
                ("NO_SAFE_CANDIDATE",),
                (),
                generated,
                feasible_ids,
                skyline_ids,
                witnesses,
                frontier_ratio,
                forced_governance=forced_governance,
                baseline_objective=baseline_objective,
                best_objective=None,
                gain=None,
            )

        top = interventions[0]
        if not forced_governance and (
            top.gain is None or top.gain < self.policy.tau
        ):
            return self._result(
                state,
                Outcome.NO_INTERVENTION,
                ("GAIN_BELOW_THRESHOLD",),
                (),
                generated,
                feasible_ids,
                skyline_ids,
                witnesses,
                frontier_ratio,
                forced_governance=False,
                baseline_objective=baseline_objective,
                best_objective=top.objective_value,
                gain=top.gain,
            )

        selected = (top,)
        outcome = Outcome.INTERVENE
        reason_codes = (
            ("FORCED_GOVERNANCE",)
            if forced_governance
            else ("INTERVENTION_SELECTED",)
        )

        if not forced_governance and len(interventions) >= 2:
            # tau gates the best intervention.  c2 remains the second-best safe
            # path from the same frontier, as specified by the v0.6 Margin rule.
            second = interventions[1]
            assert top.objective_value is not None and second.objective_value is not None
            if abs(second.objective_value - top.objective_value) <= self.policy.epsilon_tie:
                assert top.candidate is not None and second.candidate is not None
                if top.candidate.strategy_id != second.candidate.strategy_id:
                    selected = (top, second)
                    outcome = Outcome.PRESENT_CHOICES
                    reason_codes = ("DISTINCT_PATH_NEAR_TIE",)
                else:
                    selected = (
                        min(
                            (top, second),
                            key=lambda item: (
                                item.effective_workflow_cost,
                                int(item.candidate.intensity) if item.candidate else 0,
                                item.candidate_id,
                            ),
                        ),
                    )

        best = selected[0]
        return self._result(
            state,
            outcome,
            reason_codes,
            selected,
            generated,
            feasible_ids,
            skyline_ids,
            witnesses,
            frontier_ratio,
            forced_governance=forced_governance,
            baseline_objective=baseline_objective,
            best_objective=best.objective_value,
            gain=best.gain,
        )


# Public v0.6 name used by integration code.
SelectionEngine = V06SelectionEngine
