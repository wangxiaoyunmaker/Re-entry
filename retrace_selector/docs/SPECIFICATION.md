# Frozen MVP Specification

## 1. Versions

- State schema: `retrace-state-v1` (legacy) and `retrace-state-v2` (bound prefix evidence)
- Policy schema: `retrace-policy-v1`
- Template schema: `retrace-templates-v1`
- Engine: `0.2.0`

## 2. Closed enums

```text
ProcessState = DELEGATION_PROGRESSING | EARLY_SUPPORT_OPPORTUNITY |
               REENTRY_OCCASION_OBSERVED | GOVERNANCE_RECOVERING
Primitive = RULE_ALIGNMENT | PROVENANCE | CAUSAL_EXPLANATION |
            VERIFICATION | DISPOSITION_COORDINATION
Level = L1 | L2 | L3
Risk = low | medium | high
EvidenceCompleteness = none | partial | sufficient
EvidenceSource = OBSERVED | INFERRED | DESIGN_ASSUMPTION
```

`reversibility=low` 表示难以回退；`high` 表示容易回退。

## 3. Frozen thresholds

| Parameter | Value |
|---|---:|
| low state confidence | `< 0.60` |
| gain threshold | `0.05` |
| near-tie margin | `0.03` |
| dominance epsilon | `1e-9` |
| max burden | `0.80` |
| recent intervention cooldown count | `3` |
| cooldown window | 10 minutes, supplied upstream as count |

## 4. Constraint priority

```text
global confidence/risk conflict
> safety and irreversibility
> authorization
> evidence
> confidence intensity cap
> process-state intensity cap
> active verification
> cooldown and burden
```

## 5. Candidate policy

- `DELEGATION_PROGRESSING`: B0 only;
- `EARLY_SUPPORT_OPPORTUNITY`: L1 + B0;
- `REENTRY_OCCASION_OBSERVED`: L1/L2/L3 + B0;
- `GOVERNANCE_RECOVERING`: L1 + B0;
- candidate must have a non-zero matching primary governance need;
- high authorization risk generates `DISPOSITION_COORDINATION-L2/L3` even if D was coded zero when the process state permits those levels; otherwise the hard constraints yield `SAFE_HOLD`;
- `DESIGN_ASSUMPTION` does not substantiate state evidence completeness; `CAUSAL_EXPLANATION-L2/L3` requires at least one `OBSERVED` reference;
- no multi-component Brief in MVP.

## 6. Terminal semantics

- `NO_INTERVENTION` is a selectable candidate;
- `audit_record_ready=true` means the result can be persisted as an audit record; it is not a candidate ID or proof that a file write occurred;
- `REQUEST_CLARIFICATION` is a global preflight outcome;
- `SAFE_HOLD` is the empty-feasible-set outcome;
- neither terminal outcome participates in Skyline.

## 7. Near-tie

Near-tie is evaluated only between the top two intervention candidates after gain gating. If both cover different primary needs, return `PRESENT_CHOICES`; otherwise choose the lower-burden candidate using the deterministic tie-breaker.

## 8. Output invariants

- rejected candidates never enter feasible, skyline or selected sets;
- every dominated candidate records at least one dominator;
- every score is finite and in `[0,1]`;
- policy weights sum to 1;
- every result contains policy/template hashes and evidence references;
- every result contains a sealed `decision_digest`; same-audit-ID/different-content appends fail closed;
- forced-governance decisions record that gain gating was bypassed;
- rendering failure cannot substitute another primitive.
