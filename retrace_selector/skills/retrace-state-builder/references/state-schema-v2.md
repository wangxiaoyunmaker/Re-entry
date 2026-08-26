# retrace-state-v2 contract

Required fields:

```text
schema_version, decision_id, process_state, support_opportunity, support_needs, evidence,
consequence, reversibility, authorization_risk, evidence_completeness,
state_confidence, recent_interventions, active_verification
```

Closed values:

- process state: `DELEGATION_PROGRESSING`, `EARLY_SUPPORT_OPPORTUNITY`, `REENTRY_OCCASION_OBSERVED`, `GOVERNANCE_RECOVERING`
- risk: `low`, `medium`, `high`
- completeness: `none`, `partial`, `sufficient`
- evidence source: `OBSERVED`, `INFERRED`, `DESIGN_ASSUMPTION`
- support needs: integer levels in `[0,3]` for the three full support-dimension names

Each v2 evidence item requires:

```text
evidence_id, source, locator, sequence_index, content_sha256,
available_at_decision=true, and supports_dimensions or supports_primitives
```

`decision_id` must identify the current decision window. It must not be a future episode outcome ID.

Optional runtime signals are stored independently when available:

```text
basis_relevant_signal, delegation_failure_signal, repeated_unresolved
```

Optional process memory is also carried explicitly rather than being inferred
from support scores:

```text
target_key, delegation_attempt_count, last_confirmed_progress,
failure_window, cooldown_until, recent_intervention_ids
```

`basis_relevant_signal` only routes to second-stage Support Profile analysis;
it does not itself select a support dimension or intervention. Failure,
repetition, cooldown, and duplicate suppression remain independent runtime
inputs.
