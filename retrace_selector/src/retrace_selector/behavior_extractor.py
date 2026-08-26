"""End-to-end bounded call for event-level behavior evidence extraction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .llm_extraction import (
    BEHAVIOR_EVIDENCE_SYSTEM_PROMPT,
    build_behavior_evidence_prompt,
    parse_behavior_evidence_response,
)
from .llm_provider import ProviderConfig, call_chat_completion


def extract_behavior_evidence(
    events: Iterable[Mapping[str, Any]],
    *,
    config: ProviderConfig | None = None,
) -> list[dict[str, Any]]:
    """Extract and validate evidence for one bounded event batch."""

    event_list = list(events)
    prompt = build_behavior_evidence_prompt(event_list)
    response = call_chat_completion(
        BEHAVIOR_EVIDENCE_SYSTEM_PROMPT,
        prompt,
        config=config,
    )
    return parse_behavior_evidence_response(response, event_list)
