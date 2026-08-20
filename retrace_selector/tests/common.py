from __future__ import annotations

import json
from pathlib import Path

from retrace_selector.config import load_policy, load_templates
from retrace_selector.models import DecisionState
from retrace_selector.selector import SelectionEngine


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "policy.v0.2.json"
TEMPLATES_PATH = ROOT / "config" / "templates.v0.2.json"


def engine() -> SelectionEngine:
    return SelectionEngine(load_policy(POLICY_PATH), load_templates(TEMPLATES_PATH))


def state_dict(**overrides):
    data = {
        "schema_version": "retrace-state-v1",
        "decision_id": "test-state",
        "process_state": "REENTRY_OCCASION_OBSERVED",
        "governance_needs": {"O": 0, "S": 2, "D": 3},
        "evidence": [{"evidence_id": "E1", "source": "OBSERVED"}],
        "consequence": "medium",
        "reversibility": "medium",
        "authorization_risk": "low",
        "evidence_completeness": "partial",
        "state_confidence": 0.8,
        "recent_interventions": 0,
        "active_verification": False,
    }
    data.update(overrides)
    return data


def state(**overrides) -> DecisionState:
    return DecisionState.from_dict(state_dict(**overrides))


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
