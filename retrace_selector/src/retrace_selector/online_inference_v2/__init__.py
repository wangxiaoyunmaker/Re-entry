"""Public API for the ReTrace converged online inference implementation.

Importing from this package is preferred for new code.  The sibling
``retrace_selector.online_v2`` module remains a compatibility facade for
existing callers.
"""

from .core import (
    DecisionChain,
    DecisionObjectProfile,
    DimensionState,
    EvidenceRef,
    EventSource,
    OccasionSignals,
    OnlineEvent,
    OnlineEventType,
    OnlineInferenceService,
    OnlineStore,
    ObserverState,
    RegistryV2,
    SelectorConfigV2,
    SkylineSelectorV2,
    StrategyV2,
    TargetBuilder,
    TargetState,
    V2Selection,
    load_registry_v2,
    load_selector_config_v2,
)
from .adaptive import AdaptiveController, AdaptiveUpdate, UserAssessedNeed, UserPolicyPreference, UserProfile

__all__ = [
    "OnlineEvent", "OnlineEventType", "EventSource", "OccasionSignals",
    "DecisionObjectProfile", "DecisionChain", "TargetState", "TargetBuilder",
    "DimensionState", "EvidenceRef", "ObserverState", "StrategyV2", "RegistryV2",
    "SelectorConfigV2", "SkylineSelectorV2", "V2Selection", "OnlineStore",
    "OnlineInferenceService", "load_registry_v2", "load_selector_config_v2",
    "UserPolicyPreference", "UserAssessedNeed", "UserProfile", "AdaptiveUpdate", "AdaptiveController",
]
