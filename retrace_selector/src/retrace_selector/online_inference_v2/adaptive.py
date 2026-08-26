"""User-controlled and outcome-informed intervention preferences.

This module deliberately keeps personalization separate from the frozen
Selector policy.  It produces a bounded user-level preference profile; the
online service applies that profile only to future selections.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Mapping

from ..models import ValidationError


_DIMENSIONS = ("criteria", "state", "action")
_MODES = {"AUTO", "PAUSED"}
_SOURCES = {"DEFAULT", "USER_UI", "ADAPTIVE"}
_NEED_SOURCES = {"DEFAULT", "ADAPTIVE"}
_FEEDBACK_ADJUSTMENTS = {"ACCEPTED": 0.02, "DISMISSED": -0.04, "IGNORED": -0.02, "UNSPECIFIED": 0.0}


def _bounded(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValidationError(f"{name} must be finite")
    if not 0.0 <= result <= 1.0:
        raise ValidationError(f"{name} must be within [0, 1]")
    return result


def _score(value: Any, name: str, *, scale: str = "AUTO") -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be numeric or missing")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValidationError(f"{name} must be finite")
    scale = str(scale).upper()
    if scale in {"CSA-LIKERT-V1", "LIKERT-V1"}:
        # The three-question evaluation is a 1..5 scenario Likert scale;
        # adaptation compares it with the frozen 0..3 C/S/A target scale.
        if not numeric.is_integer() or not 1.0 <= numeric <= 5.0:
            raise ValidationError(f"{name} must be an integer Likert value within [1, 5]")
        return (numeric - 1.0) * 0.75
    # The online evaluation contract also permits callers that already provide
    # the frozen 0..3 scale (used by deterministic replay fixtures).
    if not 0.0 <= numeric <= 3.0:
        raise ValidationError(f"{name} must be within the frozen [0, 3] scale")
    return numeric


def _optional_unit(value: Any, name: str) -> float | None:
    if value is None:
        return None
    return _bounded(value, name)


def _optional_progress(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not -1.0 <= result <= 1.0:
        raise ValidationError(f"{name} must be finite and within [-1, 1]")
    return result


@dataclass(frozen=True)
class UserPolicyPreference:
    user_id: str
    frequency_preference: float = 0.5
    intensity_preference: float = 0.5
    mode: str = "AUTO"
    version: str = "PREF-DEFAULT"
    source: str = "DEFAULT"
    explicit: bool = False
    manual_lock: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise ValidationError("user_id must be a non-empty string")
        _bounded(self.frequency_preference, "frequency_preference")
        _bounded(self.intensity_preference, "intensity_preference")
        if self.mode not in _MODES:
            raise ValidationError("mode must be AUTO or PAUSED")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValidationError("preference version must be non-empty")
        if self.source not in _SOURCES:
            raise ValidationError("preference source is invalid")
        if not isinstance(self.explicit, bool) or not isinstance(self.manual_lock, bool):
            raise ValidationError("explicit and manual_lock must be boolean")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "UserPolicyPreference":
        if not isinstance(raw, Mapping):
            raise ValidationError("user policy preference must be an object")
        explicit = raw.get("explicit", False)
        manual_lock = raw.get("manual_lock", False)
        if not isinstance(explicit, bool) or not isinstance(manual_lock, bool):
            raise ValidationError("explicit and manual_lock must be boolean")
        return cls(
            user_id=str(raw.get("user_id", "")),
            frequency_preference=_bounded(raw.get("frequency_preference", 0.5), "frequency_preference"),
            intensity_preference=_bounded(raw.get("intensity_preference", 0.5), "intensity_preference"),
            mode=str(raw.get("mode", "AUTO")).upper(),
            version=str(raw.get("version", "PREF-DEFAULT")),
            source=str(raw.get("source", "DEFAULT")).upper(),
            explicit=explicit,
            manual_lock=manual_lock,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "frequency_preference": self.frequency_preference,
            "intensity_preference": self.intensity_preference,
            "mode": self.mode,
            "version": self.version,
            "source": self.source,
            "explicit": self.explicit,
            "manual_lock": self.manual_lock,
        }


@dataclass(frozen=True)
class UserAssessedNeed:
    """Persisted need signal inferred from online C/S/A evaluation history."""

    user_id: str
    frequency_need: float | None = None
    intensity_need: float | None = None
    csa_gap_after: float | None = None
    csa_progress: float | None = None
    completed_chain_count: int = 0
    last_reason: str = "NO_DATA"
    last_update_id: str | None = None
    last_feedback: str | None = None
    last_burden: float | None = None
    version: str = "NEED-DEFAULT"
    source: str = "DEFAULT"

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise ValidationError("user_id must be a non-empty string")
        _optional_unit(self.frequency_need, "frequency_need")
        _optional_unit(self.intensity_need, "intensity_need")
        _optional_unit(self.csa_gap_after, "csa_gap_after")
        _optional_progress(self.csa_progress, "csa_progress")
        if isinstance(self.completed_chain_count, bool) or not isinstance(self.completed_chain_count, int) or self.completed_chain_count < 0:
            raise ValidationError("completed_chain_count must be a non-negative integer")
        if not isinstance(self.last_reason, str) or not self.last_reason.strip():
            raise ValidationError("last_reason must be non-empty")
        if self.last_update_id is not None and (not isinstance(self.last_update_id, str) or not self.last_update_id.strip()):
            raise ValidationError("last_update_id must be a non-empty string or null")
        if self.last_feedback is not None:
            feedback = str(self.last_feedback).upper()
            if feedback not in _FEEDBACK_ADJUSTMENTS:
                raise ValidationError("last_feedback is invalid")
            object.__setattr__(self, "last_feedback", feedback)
        _optional_unit(self.last_burden, "last_burden")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValidationError("assessed need version must be non-empty")
        if self.source not in _NEED_SOURCES:
            raise ValidationError("assessed need source is invalid")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "UserAssessedNeed":
        if not isinstance(raw, Mapping):
            raise ValidationError("assessed need must be an object")
        return cls(
            user_id=str(raw.get("user_id", "")),
            frequency_need=raw.get("frequency_need"),
            intensity_need=raw.get("intensity_need"),
            csa_gap_after=raw.get("csa_gap_after"),
            csa_progress=raw.get("csa_progress"),
            completed_chain_count=raw.get("completed_chain_count", 0),
            last_reason=str(raw.get("last_reason", "NO_DATA")),
            last_update_id=raw.get("last_update_id"),
            last_feedback=raw.get("last_feedback"),
            last_burden=raw.get("last_burden"),
            version=str(raw.get("version", "NEED-DEFAULT")),
            source=str(raw.get("source", "DEFAULT")).upper(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "frequency_need": self.frequency_need,
            "intensity_need": self.intensity_need,
            "csa_gap_after": self.csa_gap_after,
            "csa_progress": self.csa_progress,
            "completed_chain_count": self.completed_chain_count,
            "last_reason": self.last_reason,
            "last_update_id": self.last_update_id,
            "last_feedback": self.last_feedback,
            "last_burden": self.last_burden,
            "version": self.version,
            "source": self.source,
        }


@dataclass(frozen=True)
class UserProfile:
    """The durable three-layer user intervention profile."""

    user_id: str
    subjective_preference: UserPolicyPreference
    assessed_need: UserAssessedNeed
    effective_policy: UserPolicyPreference
    version: str = "PROFILE-DEFAULT"

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise ValidationError("user_id must be a non-empty string")
        if self.subjective_preference.user_id != self.user_id or self.assessed_need.user_id != self.user_id or self.effective_policy.user_id != self.user_id:
            raise ValidationError("all user profile layers must use the same user_id")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValidationError("profile version must be non-empty")

    @classmethod
    def default(cls, user_id: str) -> "UserProfile":
        subjective = UserPolicyPreference(user_id=user_id)
        return cls(user_id, subjective, UserAssessedNeed(user_id=user_id), subjective)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "UserProfile":
        if not isinstance(raw, Mapping):
            raise ValidationError("user profile must be an object")
        user_id = str(raw.get("user_id", ""))
        subjective = UserPolicyPreference.from_dict(raw.get("subjective_preference", {}))
        assessed = UserAssessedNeed.from_dict(raw.get("assessed_need", {}))
        effective = UserPolicyPreference.from_dict(raw.get("effective_policy", {}))
        return cls(user_id, subjective, assessed, effective, str(raw.get("version", "PROFILE-DEFAULT")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "version": self.version,
            "subjective_preference": self.subjective_preference.to_dict(),
            "assessed_need": self.assessed_need.to_dict(),
            "effective_policy": self.effective_policy.to_dict(),
        }


@dataclass(frozen=True)
class AdaptiveUpdate:
    preference: UserPolicyPreference
    changed: bool
    reason: str
    metrics: Mapping[str, float | str | int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "preference": self.preference.to_dict(),
            "changed": self.changed,
            "reason": self.reason,
            "metrics": dict(self.metrics),
        }


class AdaptiveController:
    """Bounded, delayed adaptation from the three-question C/S/A result.

    The controller is intentionally conservative: it needs complete PRE/POST
    scores and a minimum history, then moves both sliders by a small step. It
    never changes the global Registry or Selector constants.
    """

    def __init__(self, *, min_history: int = 3, learning_rate: float = 0.08):
        if min_history < 1:
            raise ValidationError("min_history must be positive")
        self.min_history = min_history
        self.learning_rate = _bounded(learning_rate, "learning_rate")

    @staticmethod
    def validate_feedback(value: Any) -> str:
        feedback = str(value or "UNSPECIFIED").upper()
        if feedback not in _FEEDBACK_ADJUSTMENTS:
            raise ValidationError("feedback must be ACCEPTED, DISMISSED, IGNORED, or UNSPECIFIED")
        return feedback

    @staticmethod
    def validate_burden(value: Any) -> float:
        return _bounded(value, "burden")

    def update(
        self,
        preference: UserPolicyPreference,
        *,
        pre_scores: Mapping[str, Any],
        post_scores: Mapping[str, Any],
        target_scores: Mapping[str, Any],
        completed_chain_count: int,
        feedback: str = "UNSPECIFIED",
        burden: float = 0.0,
        pre_score_scale: str = "AUTO",
        post_score_scale: str = "AUTO",
    ) -> AdaptiveUpdate:
        if completed_chain_count < self.min_history:
            return AdaptiveUpdate(preference, False, "INSUFFICIENT_HISTORY", {"completed_chain_count": completed_chain_count})
        if preference.manual_lock:
            return AdaptiveUpdate(preference, False, "MANUAL_LOCK", {"completed_chain_count": completed_chain_count})
        burden = self.validate_burden(burden)
        feedback = self.validate_feedback(feedback)
        if any(_score(pre_scores.get(dimension), f"pre_scores.{dimension}", scale=pre_score_scale) is None for dimension in _DIMENSIONS):
            return AdaptiveUpdate(preference, False, "INCOMPLETE_PRE", {"completed_chain_count": completed_chain_count})
        if any(_score(post_scores.get(dimension), f"post_scores.{dimension}", scale=post_score_scale) is None for dimension in _DIMENSIONS):
            return AdaptiveUpdate(preference, False, "INCOMPLETE_POST", {"completed_chain_count": completed_chain_count})
        if any(_score(target_scores.get(dimension), f"target_scores.{dimension}") is None for dimension in _DIMENSIONS):
            return AdaptiveUpdate(preference, False, "INCOMPLETE_TARGET", {"completed_chain_count": completed_chain_count})

        pre = {d: _score(pre_scores[d], f"pre_scores.{d}", scale=pre_score_scale) or 0.0 for d in _DIMENSIONS}
        post = {d: _score(post_scores[d], f"post_scores.{d}", scale=post_score_scale) or 0.0 for d in _DIMENSIONS}
        target = {d: _score(target_scores[d], f"target_scores.{d}") or 0.0 for d in _DIMENSIONS}
        gap_before = sum(max(0.0, target[d] - pre[d]) / 3.0 for d in _DIMENSIONS) / len(_DIMENSIONS)
        gap_after = sum(max(0.0, target[d] - post[d]) / 3.0 for d in _DIMENSIONS) / len(_DIMENSIONS)
        progress = sum((post[d] - pre[d]) / 3.0 for d in _DIMENSIONS) / len(_DIMENSIONS)
        feedback_adjustment = _FEEDBACK_ADJUSTMENTS[feedback]
        # Persistent residual need raises support tolerance; observed progress
        # and burden counterbalance it. Frequency and intensity are deliberately
        # decoupled: frequency responds more to willingness/overload signals,
        # while intensity responds more to the residual C/S/A gap.
        max_progress = max(0.0, progress)
        frequency_need = max(0.0, min(1.0, 0.5 * gap_after - 0.5 * max_progress))
        intensity_need = max(0.0, min(1.0, gap_after - max_progress))
        frequency_delta = self.learning_rate * (0.5 * gap_after - 0.5 * max_progress)
        frequency_delta += feedback_adjustment - 0.04 * burden
        frequency_delta = max(-0.08, min(0.08, frequency_delta))
        intensity_delta = self.learning_rate * (gap_after - max_progress)
        intensity_delta += 0.5 * feedback_adjustment - 0.02 * burden
        intensity_delta = max(-0.08, min(0.08, intensity_delta))
        next_frequency = max(0.0, min(1.0, preference.frequency_preference + frequency_delta))
        next_intensity = max(0.0, min(1.0, preference.intensity_preference + intensity_delta))
        changed = next_frequency != preference.frequency_preference or next_intensity != preference.intensity_preference
        updated = replace(
            preference,
            frequency_preference=next_frequency,
            intensity_preference=next_intensity,
            source="ADAPTIVE" if changed else preference.source,
            explicit=True if changed else preference.explicit,
        )
        return AdaptiveUpdate(
            updated,
            changed,
            "UPDATED" if changed else "NO_CHANGE",
            {
                "completed_chain_count": completed_chain_count,
                "gap_before": gap_before,
                "gap_after": gap_after,
                "progress": progress,
                "frequency_need": frequency_need,
                "intensity_need": intensity_need,
                "burden": burden,
                "feedback": feedback,
                # `delta` remains the frequency step for replay/API
                # compatibility. New consumers should use the explicit fields.
                "delta": frequency_delta,
                "frequency_delta": frequency_delta,
                "intensity_delta": intensity_delta,
            },
        )
