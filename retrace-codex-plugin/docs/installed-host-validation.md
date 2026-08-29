# Pre-Pilot Installed Host Validation

Date: 2026-08-29  
Status: **host-integration-partial; M2/M3-D logic frozen**

This document records pre-pilot validation of the installed-plugin boundary. It is not a participant pilot and does not claim desktop deployment. The M2 stall detector, soft gate, Invitation state machine, M3-A/B/C/D semantics, PRE information barrier, and fail-open policy were not changed for this validation.

## Environment

```text
codex-cli 0.145.0
node v24.16.0
pnpm 11.19.0
macOS / Codex desktop in-app browser
```

The installed plugin was registered as `retrace@retrace-local` from a temporary Git-backed copy, because the local source directory is not itself a Git repository. The cache path used by the validation was:

```text
/Users/wy/.codex/plugins/cache/retrace-local/retrace/0.2.0+codex.20260829021316
```

## Reproduction

From the plugin root:

```sh
pnpm build
python3 scripts/read_marketplace_name.py --marketplace-path .agents/plugins/marketplace.json
uv run --with pyyaml python /Users/wy/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .

installed_fixture_dir=$(mktemp -d /tmp/retrace-installed-XXXXXX)
rsync -a --exclude '/node_modules' --exclude '.git' ./ "$installed_fixture_dir/"
git -C "$installed_fixture_dir" init -q
git -C "$installed_fixture_dir" config user.email codex-validation@example.invalid
git -C "$installed_fixture_dir" config user.name codex-validation
git -C "$installed_fixture_dir" add -A
git -C "$installed_fixture_dir" commit -qm 'installed host validation fixture'

codex plugin marketplace add "$installed_fixture_dir" --json
codex plugin add retrace@retrace-local --json
codex plugin list
```

The temporary Git copy is important: the Codex installer installs committed marketplace contents, not the source worktree or its ignored files. The `.gitignore` was adjusted so the built `server/dist`, `web/dist`, and a flat production-only `server/node_modules` runtime are included in a distributable commit. The runtime was prepared with `pnpm install --prod --node-linker=hoisted`; no participant-side package installation is required. This is a deployment fix, not a mechanism change.

To reproduce the real CLI hook check in an isolated project:

```sh
cli_project_dir=$(mktemp -d /tmp/retrace-cli-host-XXXXXX)
git -C "$cli_project_dir" init -q
git -C "$cli_project_dir" config user.email codex-validation@example.invalid
git -C "$cli_project_dir" config user.name codex-validation
printf '%s\\n' '# ReTrace host validation fixture' > "$cli_project_dir/README.md"
git -C "$cli_project_dir" add README.md
git -C "$cli_project_dir" commit -qm 'host validation fixture'
cli_data_dir=$(mktemp -d /tmp/retrace-cli-data-XXXXXX)
PLUGIN_DATA="$cli_data_dir" RETRACE_INVITATION_MODE=observe_only \
  codex exec --cd "$cli_project_dir" --ephemeral --skip-git-repo-check \
  --dangerously-bypass-hook-trust --color never --json \
  'Reply with exactly OK and do not modify files.'
```

The installed MCP check uses the same `server/dist/index.js` from the cache with a temporary MCP client. The final check is cold: it uses only the files delivered by `codex plugin add`, with no source-worktree dependency symlink and no package-manager command after installation. An earlier hydrated check is retained only as historical diagnostic evidence.

## Validation matrix

| Area | Method and observed result | Status |
|---|---|---|
| M2/M3-D semantics | Existing M1/M2/M3 tests, fixture replay, and five M3 mechanism episodes remain green. | PASS — logical |
| Marketplace registration | `codex plugin marketplace add` and `codex plugin add retrace@retrace-local` succeed from the Git-backed fixture. | PASS |
| Installed build artifacts | After the packaging fix, installed cache contains `server/dist/index.js`, runtime modules, `web/dist/index.html`, and web assets. | PASS |
| Cold installed MCP | A fresh Git-backed marketplace install starts with only delivered files, exposes 15 tools including `record_invitation_choice`, reports `runtimeOk: true`, and serves `text/html;profile=mcp`. | PASS |
| Hydrated MCP control | Earlier diagnostic copy with a source-worktree dependency symlink also initialized; it is no longer needed for the final cold-install result. | PASS — diagnostic |
| Real CLI hook loading | `codex exec` loaded the installed hook configuration; the sync UserPromptSubmit path was accepted and the real prompt returned `OK`. The host also logged an MCP initialize failure during shutdown, so CLI MCP lifecycle is not fully green. | PARTIAL_WITH_VALIDATED_FALLBACK |
| CLI async capture | CLI output repeatedly reports `async hooks are not supported yet` and skips the seven async capture registrations. No async capture events reached `PLUGIN_DATA`. | BLOCKED — host capability |
| Installed `observe_only` | Direct execution of the installed assessor hook returns exit 0 with empty stdout. | PASS — fail-open path |
| Installed `soft_gate` cold path | The final installed assessor uses the vendored runtime, blocks the confirmed third low-information corrective prompt, and emits the host block decision. | PASS |
| Soft-gate action branches | In the cold installed-runtime check, a confirmed candidate produced `INVITATION`; CONTINUE returned exact `还是不对，再改一下`, ENTER returned `PRE_SURVEY`, and PAUSE returned `IDLE`, all with `isError: false`. | PASS |
| Verbatim resend shape | The UI adapter still emits `{ role: "user", content: [{ type: "text", text }] }`; the installed-runtime action result returned the exact stored `pre_prompt_text`. | PASS — adapter contract |
| Host follow-up delivery | The UI adapter contract is covered, but actual Codex host delivery of `ui/message` / `window.openai.sendFollowUpMessage` was not observable in CLI 0.145.0. | NOT_REQUIRED_FOR_PILOT — COPY fallback |
| Hook correlation | Three installed emitter events drained to SQLite with the same session, `turn-1`/`turn-2`, and `tool-1` identifiers; normalized types were `SESSION_START`, `USER_PROMPT`, and `TOOL_RESULT`. | PASS — adapter/runtime |
| Session isolation | Two session IDs produced independent issue chains, Invitation runs, and verbatim pre-prompts in the same local database. | PASS — runtime |
| Telemetry/failure paths | The installed full episode recovered runtime failures, timestamps, identifiers, review actions, and completion data; LOW confidence, runtime error, and timeout remain fail-open in existing fixtures. | PASS |
| Desktop UI lifecycle/PIP | Standard standalone UI renders its offline fallback. The actual host-embedded Invitation → PRE → Review → COPY click path, refresh, close/reopen, and PIP behavior remain unobserved; browser harness URL restrictions prevented claiming the existing `file://` tab. | NOT_TESTED |
| Live A–E semantics | No participant-like live run was counted. The existing deterministic five-episode replay remains the evidence for A–E mechanism semantics. | NOT RUN — intentionally |

## Blocker taxonomy

### Resolved B1 — Cold installed MCP runtime dependencies

The plugin now carries a flat production-only runtime under `server/node_modules`. A fresh Git-backed marketplace install starts MCP without a source-worktree symlink, package-manager command, or manual hydration. This remains a platform-specific release artifact: `better-sqlite3` must be rebuilt for any different participant OS/architecture.

### B2 — Codex CLI 0.145.0 skips async hooks (deferred host capability)

The sync UserPromptSubmit assessor is recognized and is sufficient for prompt capture, issue-chain assessment, and soft-gate interception. The seven async capture hooks are skipped by CLI 0.145.0, so agent/tool lifecycle capture is unavailable in that host. The pilot configuration therefore uses sync prompt interception plus explicit/manual evidence association; async capture is deferred and is not a semantic M2/M3 change.

### B3 — Desktop Invitation/follow-up lifecycle is unobserved (remaining integration risk)

The cold MCP and state protocol are green, but the actual desktop host has not been shown to render Invitation, deliver `ui/message`, preserve the iframe across refresh/continued conversation, or return control after the original prompt is resent. COPY-first provides a validated participant fallback; this is the remaining host-surface validation risk, not a reason to rewrite the intervention boundary.

## Pilot supported configuration

The single recommended configuration for an internal pilot is:

```text
Codex host:             codex-cli 0.145.0 / installed ReTrace plugin
Invitation:             RETRACE_INVITATION_MODE=soft_gate
Prompt interception:    synchronous UserPromptSubmit
Re-entry UI:            standard MCP app surface when available
Resume:                 COPY-first; participant manually pastes into Codex
SEND:                   optional and deferred
Investigation:          COPY prompt, participant runs it, explicit/manual result association
Async hooks:            deferred; not required by this pilot protocol
Adapter:                deterministic local reconstruction/composer
Live LLM:               not used for mechanism claims in internal pilot
```

Deterministic reconstruction is sufficient for an internal mechanism pilot, but its template-like output must not be used as evidence of natural live-composer quality in the formal study. A formal study configuration should separately validate the live adapter.

## Exit criteria before participant pilot

1. Install from the intended release artifact and start MCP without a source-worktree dependency symlink. **Passed.**
2. Run one disposable real host session and verify the sync prompt path; async capture is optional/deferred for the supported pilot configuration.
3. Verify Invitation rendering and all three actions in the desktop host; for CONTINUE_DIRECT, compare the host-received user text byte-for-byte with `pre_prompt_text`.
4. Verify standard UI recovery after reload/host continuation. `ui/message` is not required when COPY-first is used.
5. Keep the result labelled `PARTIAL_WITH_VALIDATED_FALLBACK` until the desktop host click/lifecycle check passes. No participant session is required for these checks, and no result here authorizes M4.
