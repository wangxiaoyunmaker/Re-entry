# ReTrace Codex Plugin v0.2

M0–M1 implementation of the ReTrace research prototype described in:

`../0828新主线/技术方案/retrace-codex-plugin-technical-design-v0.2-implementation-ready.md`

This milestone provides lifecycle event capture, atomic local spool ingestion, SQLite persistence, a stdio MCP server, a runtime-status MCP UI, deterministic M2 issue-chain/stall assessment, a research-only Invitation soft gate, and the M3-A/M3-B/M3-C Re-entry workflow. M3-A persists PRE responses and freezes an immutable M2 context snapshot. M3-B reconstructs a structured context from that frozen history and keeps evidence, explanations, uncertainties, and Agent Claims separate for user review. M3-C derives a reviewed state, creates bounded investigations, composes a reviewed-state-only delegation, and requires explicit resume authorization.

## Development

```bash
pnpm install
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm test:fixtures
pnpm test:smoke
```

For a real Codex installation, review and trust the bundled hooks, then install the plugin through the local plugin workflow. Set `PLUGIN_DATA` to the writable research data directory and `RETRACE_PARTICIPANT_ID` to the study participant identifier; the latter is never taken from the Codex account identity.

Invitation behavior is controlled by `RETRACE_INVITATION_MODE`:

- `observe_only` (default): the asynchronous event capture path remains non-blocking.
- `soft_gate`: `UserPromptSubmit` runs the local bounded assessor synchronously; only a HIGH-confidence confirmed stall candidate is blocked. Timeout, runtime error, or LOW confidence fails open and sends the original prompt normally.

When the participant chooses `CONTINUE_DIRECT`, the UI sends the saved `pre_prompt_text` verbatim through the host follow-up-message capability. `ENTER_REENTRY` only advances to `PRE_SURVEY`; it does not yet show survey questions or Re-entry content.

M3-A permits only this ordered transition after `ENTER_REENTRY`:

```text
PRE_SURVEY → REENTRY_CONTEXT → USER_REVIEW → NEXT_PROMPT_READY → RESUMABLE
```

The `NEXT_PROMPT_READY` self-transition is reserved for later editing. `reconstruct_reentry_context` reads only events at or before the frozen `trigger_event_id`, persists a schema-constrained reconstruction plus independent `agent_claims`, and advances to `USER_REVIEW` only on success. Reconstruction errors fail open and leave the run in `REENTRY_CONTEXT`. `record_context_review` supports `CONFIRM`, `EDIT`, `REJECT`, and `ADD` as an immutable review overlay; it does not generate or send a prompt. The current implementation uses a deterministic local extractor so replay remains offline; a live LLM adapter is intentionally not part of M3-B.

M3-C adds the five-item PRE Likert set, `create_investigation`, `record_investigation_result`, `generate_next_prompt`, `edit_next_prompt`, and `complete_reentry`. Investigation remains inside `USER_REVIEW`; returned evidence candidates remain pending review. Next Prompt keeps generated and edited versions separate. COPY, SENT, CANCEL, and FAILED_OPEN are explicit release actions; no M3 path falls back to M2's verbatim resend.

The current M3-C composer and investigation builder are deterministic local adapters for offline replay. A live LLM adapter and installed-host send confirmation remain deferred integration work.

The current platform limitations and live-host checks are recorded in `platform-poc-report.md`.
