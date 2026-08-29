# ReTrace v0.2 M2 platform POC report

Date: 2026-08-28

## Scope

This report records what was verified while implementing M0–M2. It distinguishes local host-equivalent checks from a live participant trial; the latter has not been claimed unless explicitly noted.

## POC-1 — lifecycle Hooks

- Verified against the current official Hooks reference: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `PreCompact`, `PostCompact`, and `SessionEnd` are supported lifecycle points.
- `UserPromptSubmit.prompt` and `turn_id`, `PreToolUse/PostToolUse.tool_use_id`, `tool_input/tool_response`, and `Stop.last_assistant_message` are captured by the normalizer.
- In `observe_only` (the default), all capture hooks remain asynchronous; the emitter performs no blocking, approval, rewrite, project-file access, or LLM work.
- In research-only `soft_gate`, `UserPromptSubmit` additionally invokes a local assessor synchronously with a 1.5-second subprocess timeout. It emits a block decision only for a HIGH-confidence confirmed stall; the submitted prompt is never rewritten. The other seven hooks remain asynchronous.
- `SessionEnd` is synchronous with a 3-second timeout and only emits one event.
- Local subprocess smoke verifies stdin JSON → atomic `.tmp` write → rename → spool drain → SQLite.

## POC-1b — UserPromptSubmit soft gate

- Local integration fixture observed the intended sequence: the third prompt in an initial-request + two low-information corrective sequence is blocked before assessment completes, and its exact text is stored as `reentry_runs.pre_prompt_text`.
- `CONTINUE_DIRECT` transitions the invitation to `DISMISSED` and returns the exact saved prompt to the UI. The UI serializes that text as a host follow-up message with `{ role: "user", content: [{ type: "text", text }] }`; no LLM rewrite occurs.
- `ENTER_REENTRY` transitions to `PRE_SURVEY` without creating survey responses, snapshots, or displaying Re-entry content. `PAUSE` dismisses without sending.
- LOW confidence, assessor runtime error, and assessor timeout were exercised locally; each produced no blocking stdout, so the original prompt remains eligible to send.
- **Pending live-host check:** the current Codex desktop build has not yet been used to confirm the actual pre-send block, desktop Invitation rendering, or follow-up delivery end to end. The checks above are deterministic runtime/MCP protocol tests.

## POC-2 — bundled MCP

- `.mcp.json` uses the implementation-contract command `node ./server/dist/index.js` inside the wrapped `mcpServers` form accepted by the current plugin ingestion validator. The OpenAI plugin docs allow both direct and wrapped server maps.
- The server is stdio-based and exposes `open_retrace_panel`, `get_retrace_state`, `get_runtime_status`, and `record_invitation_choice`.
- Local MCP process startup and tool protocol smoke are covered by the build/smoke commands.
- **Pending host check:** the exact relative-argument working-directory resolution must be tested by installing this plugin into the target Codex desktop build. If that host does not resolve from plugin root, use the documented registered `.app.json` or install-time launcher fallback and record the result here.

## POC-3 — MCP UI

- The server registers a `text/html;profile=mcp-app` resource and links it through `_meta.ui.resourceUri`.
- The UI uses the portable `tools/call` postMessage bridge with a `window.openai.callTool` compatibility fallback, plus the host follow-up-message capability with a `ui/message` fallback.
- The standalone Vite build produces the resource used by the MCP server.
- **Pending host check:** opening the resource from the target Codex desktop build and recovering after UI reload.

## POC-4 — PIP / refresh

- The M1 UI has a 2-second read-only refresh loop when hosted in an MCP Apps iframe and a visible manual retry fallback on error.
- **Pending host check:** whether PIP is available, whether 2-second polling is throttled or approval-gated, and whether the host preserves the iframe during continued conversation. Until measured, `uiSyncMode` is reported as `manual-or-polling-pending-m0` and no product behavior depends on PIP.

## Host version used for local checks

```text
codex-cli 0.145.0
node v24.16.0
pnpm 11.19.0
```

The CLI version is recorded for reproducibility; it is not treated as evidence that the target desktop UI/PIP behavior passed.

## Real Codex CLI smoke — 2026-08-28

- A temporary git-backed local marketplace install was exercised with `codex exec --ephemeral --skip-git-repo-check --dangerously-bypass-hook-trust` and a real one-turn prompt. The host returned the expected model response (`OK`) and loaded the ReTrace `UserPromptSubmit` hook.
- The same run logged `async hooks are not supported yet` and skipped the asynchronous hooks, including the capture emitter. This is a limitation of CLI 0.145.0 observed in the host, not a passing result for the intended asynchronous capture path; desktop-host validation remains open.
- The temporary install also could not start the MCP server because the marketplace clone did not contain the local `node_modules`/release runtime dependencies. Source-tree MCP startup remains green; distributable dependency packaging is a separate release prerequisite.
- The temporary plugin and marketplace registrations were removed after the smoke. The temporary test directories are outside the project workspace.

## M3-A data boundary — 2026-08-29

- `ENTER_REENTRY` now atomically stores an M2-only snapshot containing `reentry_run_id`, `issue_chain`, `stall_assessment`, immutable `pre_prompt_text`, and `trigger_event_id`.
- PRE responses advance the run only from `PRE_SURVEY` to `REENTRY_CONTEXT`; direct state skipping is rejected. Subsequent transitions are constrained to `USER_REVIEW → NEXT_PROMPT_READY → RESUMABLE`, with an explicit prompt-edit self-transition at `NEXT_PROMPT_READY`.
- M3-A tests verify state-version checks, interaction idempotency, the PRE information barrier, and that later Codex events do not mutate the frozen snapshot.
- M3-A does not generate Re-entry content or send any prompt. LLM reconstruction, investigation, next-prompt composition, and POST survey remain out of scope.

## M3-B context reconstruction and user review — 2026-08-29

- `reconstruct_reentry_context` reads the immutable M3 snapshot and only `raw_events` whose collector sequence is at or before the snapshot's `trigger_event_id`. Events arriving after PRE entry are unavailable to the reconstruction.
- The public reconstruction is structured as `goal`, `evidence_items`, `explanations`, `uncertainties`, and `agent_claims`. Observable evidence keeps event IDs and source type; agent statements are stored separately in `agent_claims` with claim kind, source event, supporting evidence references, and verification status.
- Provenance validation rejects event IDs outside the frozen range and evidence references that do not exist in the same reconstruction. Reconstruction failure is recorded in `reentry_reconstructions`, leaves the run in `REENTRY_CONTEXT`, and does not block or send a Codex prompt.
- `record_context_review` persists `CONFIRM`, `EDIT`, `REJECT`, and `ADD` in an independent `context_reviews` overlay. Review actions do not mutate the frozen snapshot, advance to `NEXT_PROMPT_READY`, or create a prompt draft.
- The M3-B extractor is deterministic and local for fixture replay. At that milestone, no live LLM request or automatic prompt generation was included.

## M3-C — Investigation, Next Delegation & Resume — 2026-08-29

### Implemented

- `ReviewedReentryState` is a derived representation built from the immutable M3-B reconstruction plus the review overlay. It separates accepted/rejected evidence and explanations, unresolved uncertainties, added observations, evidence requirements, and rejected Agent Claims. System reconstruction and review rows remain unchanged.
- Bounded Investigation is a `USER_REVIEW` interaction. It targets an uncertainty or evidence requirement and stores `question_to_verify`, `evidence_requirement`, reviewed context, constraints, expected observable result, generated prompt, edited prompt, and status. Results can be associated with real event IDs and evidence candidates, which remain `RESULT_PENDING_REVIEW` rather than becoming accepted evidence automatically.
- `generate_next_prompt` consumes only `ReviewedReentryState`. The structured draft contains objective, known facts, open questions, evidence requirements, constraints, requested action, verification criteria, and prompt text. Rejected Agent Claims are not included as known facts.
- `edit_next_prompt` preserves `generated_text` and stores `user_edited_text` plus edit timestamp and review version. `complete_reentry` releases control only through explicit `COPY`, `SENT`, `CANCEL`, or `FAILED_OPEN`, with completion reason persisted. `SENT` is confirmed by the UI after the host follow-up call succeeds.
- PRE now exposes a centralized five-item 1–7 Likert question set only while the run is in `PRE_SURVEY`; no reconstruction is included in that state.

### Validated

- Local integration tests cover reviewed-state precedence, edited goal/evidence requirements, rejected claims, bounded investigation, result candidates pending review, generated-vs-edited prompt persistence, prompt mismatch protection, COPY release, and FAILED_OPEN release.
- MCP/UI smoke covers the expanded tool registry and rebuilt app resource. Existing M1/M2/M3-A/M3-B checks remain green.

### Integration-pending

- Real Codex CLI/Desktop async hooks, installed-state MCP startup, and host follow-up delivery remain deployment/integration debt. The source-tree SEND path is exercised, but the installed host has not been promoted to evidence of end-to-end delivery.

### M3 end-to-end mechanism validation — 2026-08-29

Status: **logical-complete / mechanism-replay-validated / integration-pending / participant-pilot-pending**.

- Five high-fidelity local episode replays covered premature completion, unsupported causal explanation, goal drift, investigation-generated evidence, and user-added governance rules. The full trace is recorded in `docs/m3-mechanism-validation.md`; this is mechanism evidence, not participant or installed-desktop evidence.
- All five episodes reached a HIGH-confidence, zero-information-gain stall with two unmet reports. The frozen `pre_prompt_text`, PRE information barrier, M2 snapshot provenance, reviewed state, and explicit completion boundary held across the replay.
- Five of five added evidence requirements reached generated `Evidence required` and `Verification condition`; the edited C goal reached `objective`; no rejected Agent Claim became a `knownFact`; and no uncertainty was collapsed into an established cause or completion.
- M3-D fixed the two material review-contract gaps from the original audit. D’s result remains `RESULT_PENDING_REVIEW` until review, then `CONFIRM`/`EDIT` enters `ReviewedReentryState.acceptedEvidence` with candidate, investigation, result, source-event, original-value, reviewed-value, review-action, review-id, and timestamp provenance; `REJECT` enters rejected evidence. Unknown review target IDs fail before persistence.
- C/E governance additions now use first-class `GOVERNANCE_CONSTRAINT` items with explicit kinds (`SCOPE`, `PROCESS`, `EVIDENCE`, `AUTHORITY`, `DO_NOT_ASSUME`, `OTHER`). They remain separate from uncertainties and map to generated `Constraints`; no automatic inference was added.
- New M3-D regression coverage validates candidate `CONFIRM`/`EDIT`/`REJECT`, membership validation, idempotent derivation, and C/E governance propagation. Existing M1/M2/M3-A/M3-B/M3-C checks remain green.
- Investigation D was **BOUNDED**: one epistemic gap, observable evidence requirement, no unrelated modifications, and explicit non-assumption of resolution.
- No M4 functionality, state-machine rewrite, stall-detector change, Invitation change, or PRE-barrier change was made during this validation.

### Deferred

- Live LLM Composer/Investigation adapters, automatic investigation-result capture, and post-result review UI are deferred. No M3-C error path sends either `pre_prompt_text` or a generated prompt automatically.

## Pre-Pilot Host Integration Validation — 2026-08-29

Status: **host-integration-partial**; M2/M3-D logic remains frozen. The cold-install blocker is resolved; remaining host-surface validation is separate from the intervention mechanism. No deployed-plugin claim is made.

- The first Git-backed install exposed two packaging issues: build output was ignored, and runtime dependencies were not available in a cold clone. The plugin now retains `server/dist`/`web/dist` and carries a flat production-only `server/node_modules` runtime generated with `pnpm install --prod --node-linker=hoisted`.
- A fresh Git-backed marketplace install now starts MCP with no source-worktree symlink, package-manager command, or manual hydration. It exposes 15 tools, reports healthy runtime status, and serves `text/html;profile=mcp-app`. Cold installed MCP is **PASS**.
- A real `codex exec` on CLI `0.145.0` loaded the installed hook configuration and completed a real one-turn prompt. The sync UserPromptSubmit path is **PASS**. The host still reports `async hooks are not supported yet`; async capture is therefore deferred under the supported sync/manual-evidence pilot configuration. The host also reports an MCP initialize failure during shutdown, so the CLI host lifecycle remains **PARTIAL_WITH_VALIDATED_FALLBACK** despite direct cold MCP startup passing.
- In the cold installed-runtime action check, a confirmed stalled corrective prompt produced Invitation; CONTINUE_DIRECT returned the exact stored `pre_prompt_text`, ENTER_REENTRY produced `PRE_SURVEY`, and PAUSE produced `IDLE`. A complete cold installed episode reached `RESUMABLE` with `completion_reason=COPIED`; PRE responses, frozen reconstruction, review actions, governance constraints, investigation candidate provenance, generated prompt, edited prompt, and timestamps were recoverable.
- COPY-first is the recommended pilot resume path. `ui/message` SEND, automatic investigation capture, async hooks, and PIP are deferred where the validated fallback is sufficient. Standard UI resource/rendering is available, but desktop host click/refresh/close-reopen lifecycle remains **NOT_TESTED**.
- The supported configuration and exact readiness decision are recorded in [`docs/pilot-readiness.md`](docs/pilot-readiness.md); the installed reproduction and blocker taxonomy are in [`docs/installed-host-validation.md`](docs/installed-host-validation.md). This validation does not authorize M4.
