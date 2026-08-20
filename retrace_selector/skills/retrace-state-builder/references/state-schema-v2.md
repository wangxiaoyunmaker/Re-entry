# retrace-state-v2 contract

Required fields:

```text
schema_version, decision_id, process_state, governance_needs, evidence,
consequence, reversibility, authorization_risk, evidence_completeness,
state_confidence, recent_interventions, active_verification
```

Closed values:

- process state: `DELEGATION_PROGRESSING`, `EARLY_SUPPORT_OPPORTUNITY`, `REENTRY_OCCASION_OBSERVED`, `GOVERNANCE_RECOVERING`
- risk: `low`, `medium`, `high`
- completeness: `none`, `partial`, `sufficient`
- evidence source: `OBSERVED`, `INFERRED`, `DESIGN_ASSUMPTION`
- needs: integer `O/S/D` in `[0,3]`

Each v2 evidence item requires:

```text
evidence_id, source, locator, sequence_index, content_sha256,
available_at_decision=true, and supports_needs or supports_primitives
```

`decision_id` must identify the current decision window. It must not be a future episode outcome ID.
