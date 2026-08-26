---
name: retrace-state-builder
description: Build a bounded, evidence-linked retrace-state-v2 for Cognitive Re-entry intervention selection when a user questions an Agent-instantiated project, its evidence, or its subsequent handling.
---

# ReTrace State Builder

Use this skill only when the host Agent has a bounded current context and a plausible Re-entry signal. It converts the available context into a state for the deterministic ReTrace selector; it does not select an intervention, edit project files, or infer post-onset outcomes.

## Required workflow

1. Read the supplied context boundary. Treat only events at or before `boundary.sequence_index` as available.
2. If the host cannot establish a reliable boundary, return a clarification request and do not guess.
3. Distinguish observed evidence from inferred interpretation and design assumptions.
4. Code the current process state, support opportunity, and full support-dimension needs using the definitions in [references/process-state-rules.md](references/process-state-rules.md).
5. Bind each evidence item to the need or primitive it actually supports. Use the evidence rules in [references/evidence-binding.md](references/evidence-binding.md).
6. Set confidence and evidence completeness conservatively. Later success, repair, acceptance, or user action must not be used to justify the current state.
7. Emit only the `retrace-state-v2` object described in [references/state-schema-v2.md](references/state-schema-v2.md). Validate it before returning.

## Hard boundaries

- Never read or request calibration targets, full-episode annotations, or future events.
- Never copy unnecessary sensitive message text into rationale or evidence metadata.
- Never change policy weights, thresholds, templates, or candidate definitions.
- Never call project mutation, deployment, rollback, or approval tools.
- If evidence is insufficient, lower confidence/completeness or return clarification; do not invent facts.

## Handoff

Pass the validated state to the trusted ReTrace Selector sub-agent with `execution_mode=DRY_RUN`. The sub-agent owns candidate selection. The host Agent owns user-facing explanation and must obtain user confirmation before executing any selected action.

For local validation, run:

```bash
PYTHONPATH=src python3 skills/retrace-state-builder/scripts/validate_state.py state.json
```
