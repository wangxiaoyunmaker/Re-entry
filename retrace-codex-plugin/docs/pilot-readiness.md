# ReTrace Pilot Readiness

Date: 2026-08-29  
Scope: M2 / M3-A / M3-B / M3-C / M3-D only; no M4 functionality.

Overall status: **PARTIAL_WITH_VALIDATED_FALLBACK**

The logical mechanism and the installed runtime are ready for a controlled internal pilot. The remaining uncertainty is the desktop host UI lifecycle, not the M2/M3 state machine. The recommended pilot uses synchronous prompt interception, deterministic local reconstruction, explicit/manual investigation association, and COPY-first resume. It does not depend on async hooks, `ui/message` SEND, or PIP.

## Supported configuration

```text
Host:                  Codex CLI/Desktop 0.145.0 with installed retrace plugin
Invitation mode:       RETRACE_INVITATION_MODE=soft_gate
Prompt hook:           synchronous UserPromptSubmit
Re-entry surface:      standard MCP app UI when available
Resume:                COPY-first, then participant manually pastes into Codex
Investigation:         COPY → participant runs → explicit/manual result association
Async capture:         deferred; not required for this pilot protocol
Reconstruction:        deterministic local adapter
Composer:              deterministic local adapter for internal mechanism pilot only
Formal-study live LLM: separate validation required; no quality claim here
```

## Readiness matrix

| Capability | Required for pilot | Installed validation | Fallback | Final status |
|---|---:|---|---|---|
| Cold MCP startup | yes | Fresh Git-backed marketplace install starts MCP; 15 tools and UI resource available with no hydration. | none | PASS |
| Sync prompt hook | yes | Real `codex exec` loads the installed hook and completes the prompt. | none | PASS |
| Soft gate | yes | Installed assessor blocks the confirmed third low-information corrective prompt and preserves `pre_prompt_text`. | fail-open on error/timeout/LOW | PASS |
| PRE barrier | yes | Installed run reaches PRE, stores responses, and exposes no reconstruction before submission. | none | PASS |
| Reconstruction | yes | Installed run reaches `USER_REVIEW`; frozen trigger and snapshot are recoverable. | deterministic local adapter | PASS |
| Review | yes | Installed run persists goal/uncertainty/evidence reviews and rebuilds reviewed state. | none | PASS |
| Governance constraint | yes | Installed run persists a first-class `GOVERNANCE_CONSTRAINT` and carries it into generated constraints. | explicit user add/edit | PASS |
| Candidate promotion | yes | Installed investigation result remains pending until candidate `CONFIRM`, then appears in accepted evidence with provenance. | explicit/manual association | PASS |
| Next Prompt | yes | Installed run generates a structured prompt from `ReviewedReentryState`. | deterministic local composer | PASS |
| COPY resume | yes | Installed run preserves edited prompt and ends `RESUMABLE` with `completion_reason=COPIED`. | manual paste into Codex | PASS |
| SEND | no if COPY works | Adapter contract is tested; actual host `ui/message` delivery is not required by supported protocol. | COPY | NOT_REQUIRED_FOR_PILOT |
| Async capture | TBD | CLI 0.145.0 skips async hooks. Sync prompt path and explicit evidence fallback are validated. | sync prompt + manual evidence association | PARTIAL_WITH_VALIDATED_FALLBACK |
| Investigation auto-capture | no if manual association works | Automatic host association is not validated; explicit result-event association is validated in installed runtime. | manual association | NOT_REQUIRED_FOR_PILOT |
| Standard MCP UI lifecycle | yes for ordinary participant operation | UI resource and standalone rendering pass; actual host click/refresh/close-reopen lifecycle is not tested. | operator-assisted installed MCP tool path | PARTIAL_WITH_VALIDATED_FALLBACK |
| PIP lifecycle | no if standard UI works | Not tested and not used by the protocol. | standard MCP UI | NOT_REQUIRED_FOR_PILOT |
| Live LLM composer | no for internal mechanism pilot | Deterministic adapter passed mechanism replay and installed full episode; live quality is not validated. | deterministic local adapter | NOT_REQUIRED_FOR_PILOT |

## Installed end-to-end data integrity gate

A fresh installed runtime episode was reconstructed from the local database after completion. It contained:

```text
reentry_run_id              PASS
trigger_event_id            PASS
issue_chain / stall input   PASS
pre_prompt_text             PASS
Invitation action           PASS
PRE responses               PASS
frozen reconstruction       PASS
review actions              PASS
governance constraints     PASS
investigation actions       PASS
ReviewedReentryState        PASS
generated prompt            PASS
edited prompt               PASS
completion_reason=COPIED    PASS
runtime failures/timestamps PASS (no failures in clean run; failure policy covered separately)
```

The run ended in `RESUMABLE`, with `stateVersion=6`, `completion_reason=COPIED`, and an edited prompt equal to the prompt passed to `complete_reentry`. No prompt was sent by the COPY path.

## Blockers and defer decisions

### Remaining integration risk

The actual desktop MCP app host has not yet been observed clicking the full Invitation → PRE → Reconstruction → Review → Next Prompt → COPY path, or restoring it after reload/close-reopen. This should be checked before an unsupervised participant session. It does not require a state-machine change.

### Deferred by validated fallback

- Async hooks: not required for the supported sync-gate/manual-evidence protocol.
- `ui/message` SEND: not required because COPY-first returns the exact edited prompt.
- Automatic investigation capture: not required because explicit/manual result association preserves provenance.
- PIP-specific lifecycle: not required if the standard MCP UI is available.
- Live LLM: not required for an internal mechanism pilot; no formal generation-quality claim is allowed.

## Decision

**1–2 person internal pilot: conditionally yes, using the supported COPY-first/manual-association protocol and operator oversight of the standard MCP UI.**

**Unsupervised participant pilot: not yet.** First close the desktop UI lifecycle check and record whether the host can open, persist, refresh, and release the standard MCP app. Do not enter M4 as part of that closure.

