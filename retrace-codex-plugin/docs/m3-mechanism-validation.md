# M3 End-to-End Mechanism Validation

Date: 2026-08-29  
Scope: M2 stall assessment → Invitation → PRE barrier → frozen reconstruction → user review → investigation → reviewed state → next delegation → explicit completion.

## 1. Validation goal

This audit checks whether a user’s newly formed judgment, evidence requirement, uncertainty, and governance rule survive the full Re-entry path. It is a mechanism validation, not a claim that the deterministic local extractor or composer has research-grade natural-language quality.

The five episodes below are high-fidelity replay fixtures built from the existing M2/M3 event shapes. They are not participant traces or evidence of live desktop-host behavior. The replay runner is [`scripts/audit-m3-episodes.ts`](/Users/wy/Desktop/HCI/retrace-codex-plugin/scripts/audit-m3-episodes.ts), and it can be rerun with:

```sh
pnpm exec tsx scripts/audit-m3-episodes.ts
```

All five episodes use the same M2 trigger boundary: an initial goal, two low-information corrective prompts, and a third corrective prompt. In each case the third prompt has `eligible: true`, `confidence: HIGH`, `unmetReportCount: 2`, and all four information-gain flags false. The saved `pre_prompt_text` is exactly `还是不对，再修一下`.

The normal state sequence observed in A, B, C, and E was:

```text
INVITED(v1) → PRE_SURVEY(v2) → REENTRY_CONTEXT(v3) → USER_REVIEW(v4)
→ NEXT_PROMPT_READY(v5) → RESUMABLE(v6)
```

D stayed at `USER_REVIEW(v4)` while the bounded investigation result was imported and reviewed, then followed the same generation and completion path. PRE exposed the five Likert questions but no reconstruction content. The frozen snapshot used `a-u3`/`b-u3`/`c-u3`/`d-u3`/`e-u3` as the trigger event, so the later investigation result in D was not retroactively inserted into the original reconstruction.

## 2. Episodes

| Episode | Scenario | Reconstruction items | Review actions | Completion |
|---|---|---:|---:|---|
| A | Premature “fixed/working” claim without verification | 1 goal, 1 evidence, 1 claim, 1 uncertainty | 4 | `COPIED` |
| B | HTTP 500 observed, unsupported token-causality explanation | 1 goal, 1 evidence, 1 claim, 1 uncertainty | 5 | `CANCELLED` |
| C | Goal drift from EXIF-preserving import to PNG-only scope | 1 goal, 1 evidence, 1 claim, 1 uncertainty | 4 | `COPIED` |
| D | PDF blank-output uncertainty followed by bounded investigation | 1 goal, 1 evidence, 1 claim, 1 uncertainty | 5 initial + 1 candidate review | `CANCELLED` |
| E | User adds test and scope/authority governance rules | 1 goal, 1 evidence, 1 claim, 1 uncertainty | 4 | `COPIED` |

## 3. End-to-end traces

### Episode A — premature closure

**Original user goal:** `请让登录按钮在移动端显示`

**M2 issue chain and stall:** issue summary remained the original goal; `firstEventId=a-u1`, `lastEventId=a-u3`, `unmetReportCount=2`, `sameIssue=true`, `reportsTargetUnmet=true`, `informationGain={newObservation:false,newRequirement:false,newEvidence:false,investigationDirection:false}`, `confidence=HIGH`, `eligible=true`.

**Invitation and PRE:** `pre_prompt_text` was `还是不对，再修一下`. `ENTER_REENTRY` produced `PRE_SURVEY(v2)` and no reconstruction was exposed. The five-question set was `RETRACE-PRE-V1`, 1–7 scale. The submitted PRE values were `{system_understanding:3, agent_actions:3, claim_credibility:2, next_action:3, continuation_confidence:2}`.

**REENTRY_CONTEXT / reconstruction:**

- Goal: `原始用户目标：请让登录按钮在移动端显示` from `a-u1`.
- Evidence: `browser check: mobile viewport button selector returned 0 nodes` from `a-tool`, `TOOL_RESULT`, unverified.
- Explanation: `已修复，按钮现在 working` from `a-agent`, classified as `AGENT_HYPOTHESIS`.
- Uncertainty: `未验证 Agent 声明：已修复，按钮现在 working`.
- Agent Claim: `claim-1`, kind `SUCCESS`, text `已修复，按钮现在 working`, no supporting evidence, `UNVERIFIED`.

**USER_REVIEW:** confirmed the goal and uncertainty; rejected `claim-1`; added `必须运行移动端 integration test 并返回可观察结果。` as an `EVIDENCE_REQUIREMENT`.

**ReviewedReentryState:** goal remained the original goal; `acceptedEvidence=[]`; `unresolvedUncertainties` retained the unverified claim; `evidenceRequirements` contained the new integration-test requirement; `rejectedClaims` contained `claim-1`. No rejected claim entered `knownFacts`.

**Generated Next Prompt:** objective was the original goal; known facts were `none`; open question was the unverified Agent claim; evidence requirement and verification criterion both required the mobile integration test and observable result; constraints included “do not treat unverified Agent claims as facts.”

```text
Goal:
- 原始用户目标：请让登录按钮在移动端显示

What is currently known:
- none

What remains unverified:
- 未验证 Agent 声明：已修复，按钮现在 working

Evidence required:
- 必须运行移动端 integration test 并返回可观察结果。

Constraints:
- 只处理当前目标相关范围，不做无关修改。
- 不要把未验证的解释或 Agent 声明当作事实。

Requested next action:
- 先执行或检查用户要求的证据路径，再根据结果决定是否需要修改。

Verification condition:
- 必须运行移动端 integration test 并返回可观察结果。
```

**Edited Next Prompt:** `请先运行移动端 integration test，返回实际结果；不要直接声称已修复。`  The completion reason was `COPIED`; the edited text was used as the final prompt value. This edit strengthened the governance rule, but it was supplied directly by the user rather than generated by the composer.

### Episode B — unsupported causal explanation

**Original user goal:** `请修复支付接口返回 500`

**M2 issue chain and stall:** `firstEventId=b-u1`, `lastEventId=b-u3`, `unmetReportCount=2`, `sameIssue=true`, `reportsTargetUnmet=true`, all information-gain flags false, `confidence=HIGH`, `eligible=true`. `pre_prompt_text` was preserved verbatim as `还是不对，再修一下`; PRE was shown without reconstruction content.

**REENTRY_CONTEXT / reconstruction:**

- Goal: `原始用户目标：请修复支付接口返回 500` from `b-u1`.
- Evidence: `GET /checkout returned HTTP 500; no server log or token validation output was captured` from `b-tool`.
- Explanation: `问题是认证 token 过期导致的。` from `b-agent`, an `AGENT_HYPOTHESIS`.
- Uncertainty: initially `未验证 Agent 声明：问题是认证 token 过期导致的`.
- Agent Claim: `claim-1`, kind `CAUSE`, unverified.

**USER_REVIEW:** confirmed the HTTP 500 evidence; rejected the explanation; edited the uncertainty to `仍不知道 500 是否由 token 造成，需要检查服务端日志。`; added `必须检查 server logs 后再判断原因。` as an evidence requirement; confirmed the goal.

**ReviewedReentryState and propagation:** the HTTP 500 observation entered `acceptedEvidence` and then `knownFacts`. The edited uncertainty entered `openQuestions`. The rejected explanation did not become a fact; it appeared only as a negative constraint: `不要直接采用未采纳的解释：问题是认证 token 过期导致的`. The server-log requirement entered both `evidenceRequirements` and `verificationCriteria`.

**Generated Next Prompt:**

```text
Goal:
- 原始用户目标：请修复支付接口返回 500

What is currently known:
- GET /checkout returned HTTP 500; no server log or token validation output was captured

What remains unverified:
- 仍不知道 500 是否由 token 造成，需要检查服务端日志。

Evidence required:
- 必须检查 server logs 后再判断原因。

Constraints:
- 只处理当前目标相关范围，不做无关修改。
- 不要把未验证的解释或 Agent 声明当作事实。
- 不要直接采用未采纳的解释：问题是认证 token 过期导致的

Requested next action:
- 先执行或检查用户要求的证据路径，再根据结果决定是否需要修改。

Verification condition:
- 必须检查 server logs 后再判断原因。
```

No edited prompt was created. Completion was `CANCELLED`.

### Episode C — goal drift

**Original user goal:** `请实现照片导入并保留 EXIF 信息`

**M2 issue chain and stall:** `firstEventId=c-u1`, `lastEventId=c-u3`, `unmetReportCount=2`, same issue/high confidence, zero information-gain flags, `eligible=true`. `pre_prompt_text` was `还是不对，再修一下`; PRE was shown without reconstruction.

**REENTRY_CONTEXT / reconstruction:**

- Goal: `原始用户目标：请实现照片导入并保留 EXIF 信息` from `c-u1`.
- Evidence: `manual check: JPEG import path was not exercised; EXIF retention is unknown` from `c-tool`.
- Explanation and claim: `为了快速上线，现在只支持 PNG。` from `c-agent`, claim kind `OTHER`, unverified.
- Uncertainty: `未验证 Agent 声明：为了快速上线，现在只支持 PNG`.

**USER_REVIEW:** edited the goal to `用户重新界定目标：支持 JPEG 和 PNG 导入，并保留 EXIF 信息。`; rejected the PNG-only claim; added `不要把只支持 PNG 当成已批准的范围；需要验证 JPEG 和 PNG 的 EXIF 保留。` and added the evidence requirement `必须分别验证 JPEG 和 PNG 导入后的 EXIF 信息。`.

**ReviewedReentryState:** the edited goal replaced the original goal in the reviewed goal list. The rejected claim was present in `rejectedClaims`, not `knownFacts`. The evidence requirement propagated. The added scope sentence became a `GOVERNANCE_CONSTRAINT` with kind `SCOPE`.

**Generated Next Prompt:** objective correctly used the edited JPEG+PNG/EXIF goal. It required separate JPEG and PNG EXIF verification, and `不要把只支持 PNG 当成已批准的范围；需要验证 JPEG 和 PNG 的 EXIF 保留。` appeared under `Constraints`, not `What remains unverified`.

```text
Goal:
- 用户重新界定目标：支持 JPEG 和 PNG 导入，并保留 EXIF 信息。

What is currently known:
- none

What remains unverified:
- 未验证 Agent 声明：为了快速上线，现在只支持 PNG

Evidence required:
- 必须分别验证 JPEG 和 PNG 导入后的 EXIF 信息。

Constraints:
- 只处理当前目标相关范围，不做无关修改。
- 不要把未验证的解释或 Agent 声明当作事实。
- 不要把只支持 PNG 当成已批准的范围；需要验证 JPEG 和 PNG 的 EXIF 保留。

Requested next action:
- 先执行或检查用户要求的证据路径，再根据结果决定是否需要修改。

Verification condition:
- 必须分别验证 JPEG 和 PNG 导入后的 EXIF 信息。
```

No edited prompt was created. Completion was `COPIED`.

### Episode D — investigation and new evidence

**Original user goal:** `请修复导出 PDF 为空白`

**M2 issue chain and stall:** `firstEventId=d-u1`, `lastEventId=d-u3`, `unmetReportCount=2`, same issue/high confidence, zero information-gain flags, `eligible=true`. `pre_prompt_text` was `还是不对，再修一下`; PRE was shown without reconstruction.

**REENTRY_CONTEXT / reconstruction:**

- Goal: `原始用户目标：请修复导出 PDF 为空白`.
- Evidence: `export command exits 0 but the generated PDF is blank` from `d-tool`.
- Explanation and claim: `导出已经成功。`, claim kind `SUCCESS`, unverified.
- Uncertainty: `未验证 Agent 声明：导出已经成功`.

**USER_REVIEW:** confirmed the observed blank PDF evidence and goal; rejected the success claim; confirmed the uncertainty; added `必须打开生成后的 PDF 检查页面内容，而不是只看退出码。` as an evidence requirement.

**Investigation:** targeted exactly `uncertainty-claim-1`. The generated investigation was `DRAFT` and constrained to one question: verify the unverified success claim. It required an observable result, included the reviewed goal and blank-PDF observation, and used these constraints:

```text
只调查这个问题，不做无关修改。
优先运行或检查现有项目，不要假设问题已解决。
只返回可观察证据和未解决部分。
```

The expected result was command output, a test result, runtime state, or a concrete file/code inspection with source. The copied investigation prompt was not sent automatically. A later `Stop` event reported `打开生成后的 PDF 后，第一页仍为空白。`; it was imported as an evidence candidate with source `d-investigation-result`, and the investigation status became `RESULT_PENDING_REVIEW`.

**Candidate review:** the result initially remained `RESULT_PENDING_REVIEW`, and a `CONFIRM` review was then recorded for its candidate ID. After M3-D, `candidateInAcceptedEvidence=true`; `ReviewedReentryState.acceptedEvidence` contains the candidate with its candidate/investigation/result/source/review provenance, and the candidate appears in generated `knownFacts`. `EDIT` and `REJECT` follow the same membership-validated path and preserve the original and reviewed values.

**Generated Next Prompt:** retained the original goal, original blank-PDF observation, the newly confirmed `打开生成后的 PDF 后，第一页仍为空白。` candidate, uncertainty about the Agent success claim, and the requirement to open the PDF. Completion was `CANCELLED`.

### Episode E — governance rules

**Original user goal:** `请修复搜索结果排序`

**M2 issue chain and stall:** `firstEventId=e-u1`, `lastEventId=e-u3`, `unmetReportCount=2`, same issue/high confidence, zero information-gain flags, `eligible=true`. `pre_prompt_text` was `还是不对，再修一下`; PRE was shown without reconstruction.

**REENTRY_CONTEXT / reconstruction:**

- Goal: `原始用户目标：请修复搜索结果排序`.
- Evidence: `integration test: ordering assertion failed for equal-ranked results` from `e-tool`.
- Explanation and claim: `已完成排序修改。`, claim kind `COMPLETION`, unverified.
- Uncertainty: `未验证 Agent 声明：已完成排序修改`.

**USER_REVIEW:** confirmed the goal; rejected the completion claim; added `必须运行 integration test 并返回失败或通过的原始结果。`; and added `不要修改数据库 schema；修改前先解释原因。`.

**ReviewedReentryState:** the integration-test requirement became an `EVIDENCE_REQUIREMENT`. The schema/explanation rule became a `GOVERNANCE_CONSTRAINT` with kind `SCOPE`; it was not added to unresolved uncertainties. The rejected completion claim was not a known fact.

**Generated Next Prompt:** carried the test requirement into evidence requirements and verification criteria, and placed the schema prohibition directly under `Constraints`:

```text
What remains unverified:
- 未验证 Agent 声明：已完成排序修改

Evidence required:
- 必须运行 integration test 并返回失败或通过的原始结果。

Constraints:
- 只处理当前目标相关范围，不做无关修改。
- 不要把未验证的解释或 Agent 声明当作事实。
- 不要修改数据库 schema；修改前先解释原因。
```

No manual repair of the schema rule was required. The completion reason was `COPIED`.

## 4. Governance knowledge carryover

The table reports propagation into the generated prompt. Manual final-prompt edits are not counted as generated propagation.

| User review item | Type | Should affect next prompt? | Actually propagated | Representation |
|---|---|---:|---:|---|
| A: confirm original login-button goal | `CONFIRM` goal | yes | yes | objective |
| A: reject “已修复 / working” | `REJECT` Agent Claim | yes | yes | absent from known facts; retained in rejected claims and related open question |
| A: confirm unresolved success claim | `CONFIRM` uncertainty | yes | yes | open question |
| A: mobile integration test + observable result | `ADD` evidence requirement | yes | yes | evidence requirement + verification criterion |
| B: confirm HTTP 500 observation | `CONFIRM` evidence | yes | yes | known fact |
| B: reject token-expiry explanation | `REJECT` explanation | yes | yes | not a fact; negative constraint |
| B: uncertainty edited to “仍不知道是否由 token 造成” | `EDIT` uncertainty | yes | yes | open question |
| B: inspect server logs before causal claim | `ADD` evidence requirement | yes | yes | evidence requirement + verification criterion |
| C: goal changed to JPEG+PNG with EXIF | `EDIT` goal | yes | yes | objective |
| C: reject PNG-only interpretation | `REJECT` Agent Claim | yes | yes | absent from known facts; rejected claim and residual open question |
| C: do not treat PNG-only as approved scope | `ADD` governance rule | yes | yes | `governanceConstraints` with `SCOPE`; generated `Constraints` |
| C: verify EXIF separately for JPEG and PNG | `ADD` evidence requirement | yes | yes | evidence requirement + verification criterion |
| D: confirm blank-PDF observation | `CONFIRM` evidence | yes | yes | known fact |
| D: reject “export succeeded” | `REJECT` Agent Claim | yes | yes | absent from known facts; rejected claim and residual open question |
| D: confirm unresolved success claim | `CONFIRM` uncertainty | yes | yes | open question |
| D: inspect rendered PDF, not only exit code | `ADD` evidence requirement | yes | yes | evidence requirement + verification criterion |
| D: confirm investigation evidence candidate | `CONFIRM` result evidence | yes | yes | accepted evidence with candidate/investigation/result/review provenance; generated `knownFacts` |
| E: integration test with raw result | `ADD` evidence requirement | yes | yes | evidence requirement + verification criterion |
| E: do not modify DB schema; explain first | `ADD` governance rule | yes | yes | `governanceConstraints` with `SCOPE`; generated `Constraints` |

Summary: all 5 added evidence requirements reached the generated next prompt. The one edited goal reached the objective. The investigation candidate reached `acceptedEvidence` only after confirmation, with provenance preserved. C/E governance rules reached `governanceConstraints` and generated `Constraints` without entering `openQuestions`.

## 5. Information-loss analysis

| Check | Result | Evidence |
|---|---|---|
| Rejected information resurrection | No direct resurrection as a known fact | A/C/D/E rejected claims were absent from `knownFacts`; B’s rejected explanation appeared only as a negative constraint. |
| Indirect rejected-claim repetition | Present | In A/C/D/E the corresponding unverified claim remained in `openQuestions`; this is not factual resurrection, but it duplicates a claim the user rejected or did not want foregrounded. |
| Uncertainty collapse | Not observed | B preserved “仍不知道是否由 token 造成”; A/D/E kept “未验证”; no causal claim was rewritten as established fact. |
| Evidence requirement loss | Not observed for the five added requirements | Every requirement appeared in both `evidenceRequirements` and `verificationCriteria`. |
| Goal drift | Correctly resisted in C | The edited JPEG+PNG/EXIF goal replaced the original objective in the reviewed state and generated prompt. |
| Agent authority leakage | Mostly resisted | Unverified Agent claims never became `knownFacts`; however, duplicated claim text in open questions still gives the Agent wording more visual prominence than necessary. |
| Investigation result handling | Fixed | D result remains `RESULT_PENDING_REVIEW` until review; confirmed/edited candidates enter accepted evidence with provenance, while rejected candidates enter rejected evidence. |
| Governance semantic loss | Not observed after M3-D | Explicit `GOVERNANCE_CONSTRAINT` additions remain separate from uncertainty and map to generated `Constraints`. |

The frozen M2 boundary itself was not violated in these replays. Later D evidence was not inserted into the original reconstruction, which is the intended snapshot behavior. The issue is downstream representation and review propagation, not a need to change stall detection, Invitation, or the PRE barrier.

## 6. Reconstruction burden and usefulness

The replay counts were small and stable: each reconstruction had four top-level item categories with one item each. The review burden was 4 actions in A, 5 in B, 4 in C, 6 in D including the candidate, and 4 in E. D is the only episode with an additional investigation copy/result/review sequence.

| Item type | Decision value observed | Burden issue |
|---|---|---|
| Goal | High; especially useful in C for correcting scope drift | Low burden at one item, but goal and issue summary can repeat the same wording. |
| Evidence card | High in B and D because it separates HTTP/PDF observations from explanations | The evidence card is not automatically included in known facts unless explicitly confirmed, which is correct but adds a review action. |
| Agent Claim | High for detecting premature closure and causal overreach | Claim text is repeated in explanation and uncertainty, increasing reading cost. |
| Uncertainty | High when it expresses a real epistemic gap | Generated uncertainty can simply restate a rejected claim, producing duplicate or low-decision-value content. |
| User-added governance rule | High in principle | The dedicated constraint affordance preserves the operational role; the remaining question is whether the kind selector is understandable without adding too much burden. |

No reconstruction was large enough in this audit to justify a UI optimization. However, the repeated claim → explanation → uncertainty presentation is a likely “more tiring than reading the original chat” risk as the number of claims grows. Mark this as an interaction/generation-quality concern to measure in the user study, not as a reason to change the schema in this round.

## 7. Investigation boundedness

D was classified **BOUNDED**. It targeted exactly one unresolved claim (`uncertainty-claim-1`), required observable evidence, explicitly prohibited unrelated modification, instructed the Agent not to assume resolution, and specified the expected evidence form. The investigation did not expand into completing the PDF task.

No `PARTIALLY_BOUNDED` or `UNBOUNDED` investigation was observed in this replay set. This confirms prompt construction and storage, not the behavior of a live Agent after the investigation prompt is sent.

## 8. Failure cases and M3-D disposition

The two material failures below were found in the original M3 audit and were re-run after M3-D contract hardening. Both are now **FIXED** in the local mechanism replay. The original observations remain documented so the repair is auditable.

### FIXED — confirmed investigation candidate enters reviewed evidence

- **Original observation:** D imported a source-backed result candidate, recorded a user `CONFIRM`, and still generated a next prompt without the confirmed result.
- **Repair:** `getReviewedState` now supplies investigation candidates to derivation; `deriveReviewedReentryState` consumes only candidate IDs with a legal `CONFIRM`, `EDIT`, or `REJECT` review. `EDIT` uses the reviewed value, and accepted/rejected evidence retain candidate, investigation, result, source, and review provenance.
- **Post-fix result:** `打开生成后的 PDF 后，第一页仍为空白。` appears in `ReviewedReentryState.acceptedEvidence` and `NextPromptDraft.knownFacts` only after confirmation. The D regression fixture asserts this.
- **Additional hardening:** review target membership rejects unknown IDs before persistence with `REVIEW_TARGET_NOT_FOUND`.
- **Disposition:** **FIXED**. The original classification was an implementation bug at the review/derivation boundary with a representation gap; no M2 or host change was required.

### FIXED — governance rule has a first-class semantic channel

- **Original observation:** E’s `不要修改数据库 schema；修改前先解释原因。` and C’s analogous scope rule were stored as unresolved uncertainties, so the generated prompt omitted them from `Constraints`.
- **Repair:** `GOVERNANCE_CONSTRAINT` review additions derive into `ReviewedReentryState.governanceConstraints`, with explicit `SCOPE`, `PROCESS`, `EVIDENCE`, `AUTHORITY`, `DO_NOT_ASSUME`, and `OTHER` kinds. Existing governance items can be edited without changing identity.
- **Post-fix result:** C and E place the governance text under `Constraints`, not `What remains unverified`; E no longer requires a manual final-prompt edit for this rule.
- **Disposition:** **FIXED**. The original classification was a representation limitation with interaction consequences. No automatic inference from ordinary sentences was added.

## 9. Mechanism bugs and disposition

1. **FIXED — candidate review is connected to evidence derivation.** Legal `CONFIRM`, `EDIT`, and `REJECT` actions now affect the next delegation with preserved provenance.
2. **FIXED — review membership is validated.** Unknown target IDs are rejected before a review row is persisted.
3. **FIXED — governance knowledge has a first-class representation.** Scope prohibitions, process requirements, evidence rules, authority boundaries, and “do not assume” rules are separate from uncertainty.

These findings do not require changing the frozen M2 contract, the soft gate, Invitation choices, or PRE information barrier.

## 10. Interaction issues

- A/B/C/D/E all require reviewing the generated claim and its associated uncertainty separately when the user’s intended judgment is often one decision: “this claim is unverified.”
- The current flow is auditable and explicit, but the same Agent wording can appear in claim, explanation, uncertainty, rejected-claim, and open-question locations.
- Governance rules now have a dedicated add/edit affordance; the remaining interaction question is whether the extra kind selector is understandable without adding too much review burden.
- D adds investigation copy/result/review steps without changing the state version, which preserves the user-review phase but may be cognitively non-obvious. This is an interaction observation, not a state-machine failure.

## 11. Generation-quality issues

The deterministic adapter is useful for replayability and contract testing, but several outputs are not evidence of final Composer quality:

- The generated prose is repetitive because the same claim is represented in multiple structured categories.
- `none` for known facts in A/C/E is mechanically correct when no evidence was confirmed, but may feel unhelpful to users who can see a tool result in the reconstruction.
- The negative constraint in B is conservative and safe, but may be awkward in natural language.
- C/E now preserve governance semantics in the generated prompt; natural-language repetition and compactness remain open quality questions.

These should be evaluated as generation/presentation quality after the propagation contract is repaired. They do not justify adding a live LLM loop or M4 work in this round.

## 12. Integration debt

The audit exercised the source-tree Repository/runtime and the existing local soft-gate/MCP/UI smoke paths. It did not establish installed-host behavior. The following remain separate deployment/integration debt:

- Codex CLI `0.145.0` skipped asynchronous hooks with `async hooks are not supported yet` in the prior real-host smoke.
- Desktop async capture and actual pre-send blocking remain unverified.
- Installed-state MCP server startup still needs distributable runtime dependency validation.
- Host `ui/message` follow-up delivery and desktop UI/PIP lifecycle remain unverified.
- The D investigation replay imports a supplied result event; automatic host capture after copying an investigation prompt is not implemented.

These risks should not be used to redesign the logical M3 mechanism, but they must be listed in field validation before claiming an end-to-end participant deployment.

## 13. Recommended checks before user study

1. Keep the D/E/C regression fixtures as release gates for candidate provenance, promotion, and governance constraint placement. Keep M2, Invitation, and PRE contracts unchanged.
2. Measure the repeated claim/explanation/uncertainty burden with participants before optimizing the reconstruction UI.
3. Complete installed desktop-host checks independently: async capture, synchronous soft gate, MCP startup, UI rendering, and follow-up resend.

## Validation conclusion

M3 is **logical-complete / mechanism-replay-validated / integration-pending / participant-pilot-pending** for the tested path. The core boundary is sound in the replay set: high-confidence stall detection gates the third low-information correction; PRE hides reconstruction; the frozen snapshot preserves provenance; rejected claims do not become known facts; uncertainty does not collapse; edited goals and evidence requirements propagate; governance constraints remain constraints; and investigation prompts are bounded.

M3-D fixed the two material downstream issues found in the original audit: confirmed investigation evidence now has a provenance-preserving promotion path, and governance rules have a first-class constraint representation. These were M3 review/representation contract issues, not reasons to alter M2 stall detection or Invitation behavior. No M4 functionality was added.
