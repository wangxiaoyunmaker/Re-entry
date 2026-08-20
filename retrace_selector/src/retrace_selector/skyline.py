from __future__ import annotations

from collections.abc import Iterable

from .models import CandidateEvaluation


def dominates(
    left: CandidateEvaluation,
    right: CandidateEvaluation,
    epsilon: float,
) -> bool:
    if left.score is None or right.score is None:
        raise ValueError("dominance requires scored candidates")
    left_values = left.score.vector()
    right_values = right.score.vector()
    no_worse = all(a >= b - epsilon for a, b in zip(left_values, right_values))
    strictly_better = any(a > b + epsilon for a, b in zip(left_values, right_values))
    return no_worse and strictly_better


def compute_skyline(
    candidates: Iterable[CandidateEvaluation], epsilon: float
) -> tuple[tuple[CandidateEvaluation, ...], dict[str, tuple[str, ...]]]:
    ordered = tuple(sorted(candidates, key=lambda item: item.brief.brief_id))
    witnesses: dict[str, tuple[str, ...]] = {}
    frontier: list[CandidateEvaluation] = []
    for candidate in ordered:
        dominators = tuple(
            other.brief.brief_id
            for other in ordered
            if other is not candidate and dominates(other, candidate, epsilon)
        )
        if dominators:
            witnesses[candidate.brief.brief_id] = tuple(sorted(dominators))
        else:
            frontier.append(candidate)
    return tuple(frontier), witnesses
