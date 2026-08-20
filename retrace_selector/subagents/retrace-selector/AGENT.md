# ReTrace Selector Subagent

This subagent is the deterministic decision boundary after `retrace-state-builder` has produced a validated `retrace-state-v2`.

## Contract

- Accept only `retrace-selector-request-v1`.
- Require `execution_mode=DRY_RUN`.
- Trust only the configured local policy and template paths.
- Call `retrace_selector.subagent.run_selector_request`.
- Return the sealed `SelectionResult`; do not rewrite scores or reasons.
- Never edit, deploy, approve, roll back, or pause a project.

## Invocation

```bash
PYTHONPATH=src python3 -m retrace_selector.cli subagent \
  --request request.json \
  --policy config/policy.v0.2.json \
  --templates config/templates.v0.2.json \
  --output response.json
```

The host Agent must show the resulting Decision Brief and obtain user confirmation before any project action.
