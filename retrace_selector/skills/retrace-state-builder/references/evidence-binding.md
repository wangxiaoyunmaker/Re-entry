# Evidence binding

Use `supports_primitives` when the evidence supports a specific intervention primitive. Use `supports_dimensions` only when the evidence supports the corresponding primary support dimension but not one particular primitive.

Specific primitive binding takes priority over need binding. A candidate may use only evidence that supports its primitive or its primary need. Do not use an unrelated observed event to satisfy a candidate's evidence threshold.

Use:

- `OBSERVED` for directly locatable user, Agent, tool, runtime, or project evidence;
- `INFERRED` for a conclusion supported by multiple observations;
- `DESIGN_ASSUMPTION` only for policy or interpretation assumptions.

`DESIGN_ASSUMPTION` cannot by itself make project evidence partial or sufficient. For high-strength causal explanation, an applicable `OBSERVED` reference is required.

When the current evidence is a user request for future verification, bind it to `VERIFICATION` as a request/need signal, but do not claim that verification has already succeeded.
