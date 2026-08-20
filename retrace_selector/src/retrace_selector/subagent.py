from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .config import load_policy, load_templates
from .models import DecisionState, ValidationError
from .selector import SelectionEngine
from .version import ENGINE_VERSION


REQUEST_SCHEMA = "retrace-selector-request-v1"
RESPONSE_SCHEMA = "retrace-selector-response-v1"


def run_selector_request(
    request: Mapping[str, Any],
    *,
    policy_path: str,
    templates_path: str,
) -> dict[str, Any]:
    """Run the deterministic selector behind the DRY_RUN sub-agent boundary."""

    if not isinstance(request, Mapping):
        raise ValidationError("selector request must be an object")
    allowed = {"schema_version", "state", "policy_ref", "template_ref", "execution_mode"}
    unknown = set(request) - allowed
    missing = allowed - set(request)
    if unknown:
        raise ValidationError(f"selector request unknown fields: {sorted(unknown)}")
    if missing:
        raise ValidationError(f"selector request missing fields: {sorted(missing)}")
    if request["schema_version"] != REQUEST_SCHEMA:
        raise ValidationError(f"unsupported selector request schema: {request['schema_version']}")
    if request["execution_mode"] != "DRY_RUN":
        raise ValidationError("selector sub-agent only permits execution_mode=DRY_RUN")
    for field in ("policy_ref", "template_ref"):
        if not isinstance(request[field], str) or not request[field].strip():
            raise ValidationError(f"selector request {field} must be a non-empty string")
    if Path(str(request["policy_ref"])).resolve() != Path(policy_path).resolve():
        raise ValidationError("request policy_ref does not match trusted policy path")
    if Path(str(request["template_ref"])).resolve() != Path(templates_path).resolve():
        raise ValidationError("request template_ref does not match trusted template path")

    policy = load_policy(policy_path)
    templates = load_templates(templates_path)
    state = DecisionState.from_dict(request["state"])
    result = SelectionEngine(policy, templates).select(state)
    return {
        "schema_version": RESPONSE_SCHEMA,
        "engine_version": ENGINE_VERSION,
        "execution_mode": "DRY_RUN",
        "result": result.to_dict(),
    }
