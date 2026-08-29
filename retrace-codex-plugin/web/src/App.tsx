import { useCallback, useEffect, useState } from "react";
import { callTool, initializeMcpBridge, isMcpBridgeAvailable, sendFollowUpMessage, type RuntimeStatus } from "./api/mcp.js";

type ReTraceState = {
  stateVersion: number;
  sessionId: string;
  reentryRunId?: string;
  uiState: "IDLE" | "INVITATION" | "PRE_SURVEY" | "REENTRY_CONTEXT" | "USER_REVIEW" | "NEXT_PROMPT_READY" | "RESUMABLE";
  invitation?: { title: string; body: string; failureCount: number };
  context?: { snapshotVersion: number; issueSummary: string; unmetReportCount: number; triggerEventId: string };
  reconstruction?: {
    reconstructionId: string;
    snapshotVersion: number;
    generatedAt: string;
    goal: Array<{ id: string; text: string; sourceEventIds: string[]; sourceType: string; timestamp: string }>;
    evidenceItems: Array<{ id: string; kind: string; claim: string; sourceEventIds: string[]; sourceType: string; timestamp: string; verificationStatus: string }>;
    explanations: Array<{ id: string; text: string; kind: string; sourceEventIds: string[]; supportingEvidenceIds: string[]; timestamp: string }>;
    uncertainties: Array<{ id: string; text: string; sourceEventIds: string[]; relatedClaimIds: string[]; timestamp: string }>;
    agentClaims: Array<{ claimId: string; kind: string; text: string; sourceEventId: string; supportingEvidenceIds: string[]; verificationStatus: string; timestamp: string }>;
  };
  reviewActions?: Array<{ itemType: string; itemId: string; action: string }>;
  reconstructionError?: { code: string; message: string };
  preSurvey?: { questionSetVersion: string; questions: Array<{ id: string; text: string; scaleMin: number; scaleMax: number }> };
  reviewedState?: {
    reviewVersion: number;
    goal: Array<{ id: string; text: string; sourceEventIds: string[] }>;
    acceptedEvidence: Array<{ id: string; claim: string; sourceEventIds: string[] }>;
    evidenceRequirements: Array<{ id: string; text: string; sourceReviewId: string }>;
    governanceConstraints: Array<{ id: string; text: string; kind: string; sourceReviewId: string; source: string; createdAt: string }>;
    addedObservations: Array<{ id: string; text: string; sourceReviewId: string }>;
    unresolvedUncertainties: Array<{ id: string; text: string; sourceEventIds: string[] }>;
    rejectedClaims: Array<{ claimId: string; text: string; sourceEventId: string }>;
  };
  investigations?: Array<{
    investigationId: string;
    targetReviewItemId?: string;
    targetItemType: "EVIDENCE" | "UNCERTAINTY";
    questionToVerify: string;
    evidenceRequirement: string;
    relevantContext: string[];
    constraints: string[];
    expectedObservableResult: string;
    generatedPrompt: string;
    editedPrompt?: string;
    status: string;
    result?: { resultEventIds: string[]; evidenceCandidateIds: string[]; evidenceCandidates: Array<{ id: string; claim: string; sourceEventIds: string[] }> };
  }>;
  nextPrompt?: { promptId: string; reviewVersion: number; objective: string; knownFacts: string[]; openQuestions: string[]; evidenceRequirements: string[]; constraints: string[]; requestedAction: string; verificationCriteria: string[]; promptText: string; editedPrompt?: string; generatedAt: string; editedAt?: string };
  completionReason?: "COPIED" | "SENT" | "CANCELLED" | "FAILED_OPEN";
};

type ReTraceStateResponse = { changed: boolean; stateVersion: number; state?: ReTraceState };

type InvitationChoiceResult = {
  state: ReTraceState;
  originalPrompt?: string;
  shouldSendDirect: boolean;
};

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "暂无";
}

function ReviewCard({
  itemType,
  itemId,
  text,
  sourceEventIds,
  reviewed,
  onInvestigate,
  onReview,
}: {
  itemType: string;
  itemId: string;
  text: string;
  sourceEventIds?: string[];
  reviewed?: string;
  onInvestigate?: () => void;
  onReview: (action: "CONFIRM" | "EDIT" | "REJECT", before: string, after?: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(text);
  return (
    <article className="context-item">
      {editing ? <textarea value={value} onChange={(event) => setValue(event.target.value)} rows={3} /> : <p>{text}</p>}
      {sourceEventIds?.length ? <details className="provenance"><summary>查看 provenance</summary><code>{sourceEventIds.join(", ")}</code></details> : null}
      <div className="item-meta"><span>{itemType} · {itemId}</span>{reviewed ? <span>已{reviewed}</span> : null}</div>
      <div className="item-actions">
        {editing ? <button onClick={() => { onReview("EDIT", text, value); setEditing(false); }}>保存修改</button> : <button onClick={() => setEditing(true)}>编辑</button>}
        <button className="secondary" onClick={() => onReview("CONFIRM", text)}>确认</button>
        <button className="quiet" onClick={() => onReview("REJECT", text)}>不采纳</button>
        {onInvestigate ? <button className="investigate" onClick={onInvestigate}>调查</button> : null}
      </div>
    </article>
  );
}

function InvestigationCard({
  investigation,
  onEdit,
  onCopy,
  reviewActions,
  onReviewCandidate,
}: {
  investigation: NonNullable<ReTraceState["investigations"]>[number];
  onEdit: (prompt: string) => void;
  onCopy: () => void;
  reviewActions?: Array<{ itemType: string; itemId: string; action: string }>;
  onReviewCandidate: (candidateId: string, claim: string, action: "CONFIRM" | "EDIT" | "REJECT", before: string, after?: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(investigation.editedPrompt ?? investigation.generatedPrompt);
  return (
    <article className="context-item investigation-item">
      <div className="item-meta"><span>{investigation.targetItemType} · bounded investigation</span><span>{investigation.status}</span></div>
      <p><strong>要验证：</strong>{investigation.questionToVerify}</p>
      <p><strong>所需证据：</strong>{investigation.evidenceRequirement}</p>
      {editing ? <textarea value={value} onChange={(event) => setValue(event.target.value)} rows={5} /> : <details><summary>查看 investigation prompt</summary><pre>{value}</pre></details>}
      {investigation.result?.evidenceCandidates.length ? <>
        <h4>New evidence candidate · 待 review</h4>
        {investigation.result.evidenceCandidates.map((candidate) => <ReviewCard
          key={candidate.id}
          itemType="EVIDENCE CANDIDATE"
          itemId={candidate.id}
          text={candidate.claim}
          sourceEventIds={candidate.sourceEventIds}
          reviewed={reviewActions?.find((review) => review.itemId === candidate.id)?.action}
          onReview={(action, before, after) => onReviewCandidate(candidate.id, candidate.claim, action, before, after)}
        />)}
      </> : null}
      <div className="item-actions">
        {editing ? <button onClick={() => { onEdit(value); setEditing(false); }}>保存修改</button> : <button onClick={() => setEditing(true)}>编辑</button>}
        <button className="secondary" onClick={onCopy}>复制（不发送）</button>
      </div>
    </article>
  );
}

function NextPromptPanel({
  draft,
  onEdit,
  onCopy,
  onSend,
  onCancel,
}: {
  draft: NonNullable<ReTraceState["nextPrompt"]>;
  onEdit: (prompt: string) => void;
  onCopy: () => void;
  onSend: () => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(draft.editedPrompt ?? draft.promptText);
  const [editing, setEditing] = useState(Boolean(draft.editedPrompt));
  return (
    <section className="prompt-panel" aria-label="Next prompt composer">
      <p className="eyebrow">NEXT DELEGATION</p>
      <h2>为什么这样继续</h2>
      <p className="muted">这个候选 prompt 只使用你 review 后保留的目标、证据和要求。</p>
      <div className="prompt-rationale">
        <strong>目标</strong><span>{draft.objective}</span>
        <strong>仍待验证</strong><span>{draft.openQuestions.join("；") || "无"}</span>
        <strong>证据要求</strong><span>{draft.evidenceRequirements.join("；") || "返回可观察验证结果"}</span>
      </div>
      <h3>Candidate prompt</h3>
      {editing ? <textarea value={value} onChange={(event) => setValue(event.target.value)} rows={12} /> : <pre className="prompt-text">{value}</pre>}
      <div className="actions">
        {editing ? <button onClick={() => { onEdit(value); setEditing(false); }}>保存修改</button> : <button onClick={() => setEditing(true)}>编辑</button>}
        <button className="secondary" onClick={onCopy}>复制并释放</button>
        <button onClick={onSend}>发送并释放</button>
        <button className="quiet" onClick={onCancel}>取消 Re-entry</button>
      </div>
    </section>
  );
}

export function App() {
  const [status, setStatus] = useState<RuntimeStatus | null>(null);
  const [retraceState, setRetraceState] = useState<ReTraceState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [newEvidence, setNewEvidence] = useState("");
  const [newAdditionType, setNewAdditionType] = useState<"OBSERVATION" | "EVIDENCE_REQUIREMENT" | "UNCERTAINTY" | "GOVERNANCE_CONSTRAINT">("OBSERVATION");
  const [newConstraintKind, setNewConstraintKind] = useState<"SCOPE" | "PROCESS" | "EVIDENCE" | "AUTHORITY" | "DO_NOT_ASSUME" | "OTHER">("SCOPE");
  const [preResponses, setPreResponses] = useState<Record<string, number>>({});

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const nextStatus = await callTool<RuntimeStatus>("get_runtime_status", {});
      setStatus(nextStatus);
      const nextState = await callTool<ReTraceStateResponse>("get_retrace_state", { sessionId: nextStatus.activeSessionId });
      if (nextState.state) setRetraceState(nextState.state);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  const chooseInvitation = useCallback(async (choice: "ENTER_REENTRY" | "CONTINUE_DIRECT" | "PAUSE") => {
    if (!retraceState?.reentryRunId || !status) return;
    setError(null);
    try {
      const result = await callTool<InvitationChoiceResult>("record_invitation_choice", {
        participantId: status.participantId,
        sessionId: retraceState.sessionId,
        reentryRunId: retraceState.reentryRunId,
        stateVersion: retraceState.stateVersion,
        interactionId: crypto.randomUUID(),
        choice,
      });
      setRetraceState(result.state);
      if (result.shouldSendDirect) await sendFollowUpMessage(result.originalPrompt ?? "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [retraceState, status]);

  const reconstructContext = useCallback(async () => {
    if (!retraceState?.reentryRunId || !status) return;
    try {
      const result = await callTool<ReTraceStateResponse>("reconstruct_reentry_context", {
        participantId: status.participantId,
        sessionId: retraceState.sessionId,
        reentryRunId: retraceState.reentryRunId,
        stateVersion: retraceState.stateVersion,
        interactionId: crypto.randomUUID(),
      });
      if (result.state) setRetraceState(result.state);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [retraceState, status]);

  const reviewContext = useCallback(async (itemType: string, itemId: string, action: "CONFIRM" | "EDIT" | "REJECT" | "ADD", before?: unknown, after?: unknown) => {
    if (!retraceState?.reentryRunId || !status) return;
    try {
      const result = await callTool<ReTraceStateResponse>("record_context_review", {
        participantId: status.participantId,
        sessionId: retraceState.sessionId,
        reentryRunId: retraceState.reentryRunId,
        stateVersion: retraceState.stateVersion,
        interactionId: crypto.randomUUID(),
        itemType,
        itemId,
        action,
        before,
        after,
      });
      if (result.state) setRetraceState(result.state);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [retraceState, status]);

  const submitPreSurvey = useCallback(async () => {
    if (!retraceState?.reentryRunId || !status || !retraceState.preSurvey) return;
    try {
      const result = await callTool<ReTraceStateResponse>("submit_pre_survey", {
        participantId: status.participantId,
        sessionId: retraceState.sessionId,
        reentryRunId: retraceState.reentryRunId,
        stateVersion: retraceState.stateVersion,
        interactionId: crypto.randomUUID(),
        response: { questionSetVersion: retraceState.preSurvey.questionSetVersion, responses: preResponses },
      });
      if (result.state) setRetraceState(result.state);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [preResponses, retraceState, status]);

  const createInvestigation = useCallback(async (targetItemType: "EVIDENCE" | "UNCERTAINTY", targetReviewItemId: string) => {
    if (!retraceState?.reentryRunId || !status) return;
    try {
      const result = await callTool<ReTraceStateResponse>("create_investigation", {
        participantId: status.participantId, sessionId: retraceState.sessionId, reentryRunId: retraceState.reentryRunId,
        stateVersion: retraceState.stateVersion, interactionId: crypto.randomUUID(), targetItemType, targetReviewItemId,
      });
      if (result.state) setRetraceState(result.state);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [retraceState, status]);

  const editInvestigation = useCallback(async (investigationId: string, editedPrompt: string) => {
    if (!retraceState?.reentryRunId || !status) return;
    const result = await callTool<ReTraceStateResponse>("edit_investigation", {
      participantId: status.participantId, sessionId: retraceState.sessionId, reentryRunId: retraceState.reentryRunId,
      stateVersion: retraceState.stateVersion, interactionId: crypto.randomUUID(), investigationId, editedPrompt,
    });
    if (result.state) setRetraceState(result.state);
  }, [retraceState, status]);

  const copyInvestigation = useCallback(async (investigationId: string) => {
    if (!retraceState?.reentryRunId || !status) return;
    try {
      const result = await callTool<{ state: ReTraceState; prompt: string }>("copy_investigation", {
        participantId: status.participantId, sessionId: retraceState.sessionId, reentryRunId: retraceState.reentryRunId,
        stateVersion: retraceState.stateVersion, interactionId: crypto.randomUUID(), investigationId,
      });
      await navigator.clipboard.writeText(result.prompt);
      setRetraceState(result.state);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [retraceState, status]);

  const generateNextPrompt = useCallback(async () => {
    if (!retraceState?.reentryRunId || !status) return;
    try {
      const result = await callTool<ReTraceStateResponse>("generate_next_prompt", {
        participantId: status.participantId, sessionId: retraceState.sessionId, reentryRunId: retraceState.reentryRunId,
        stateVersion: retraceState.stateVersion, interactionId: crypto.randomUUID(),
      });
      if (result.state) setRetraceState(result.state);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [retraceState, status]);

  const editNextPrompt = useCallback(async (editedPrompt: string) => {
    if (!retraceState?.reentryRunId || !status) return;
    const result = await callTool<ReTraceStateResponse>("edit_next_prompt", {
      participantId: status.participantId, sessionId: retraceState.sessionId, reentryRunId: retraceState.reentryRunId,
      stateVersion: retraceState.stateVersion, interactionId: crypto.randomUUID(), editedPrompt,
    });
    if (result.state) setRetraceState(result.state);
  }, [retraceState, status]);

  const completeReentry = useCallback(async (action: "COPY" | "SENT" | "CANCEL" | "FAILED_OPEN", finalPrompt?: string) => {
    if (!retraceState?.reentryRunId || !status) return;
    const result = await callTool<{ state: ReTraceState }>("complete_reentry", {
      participantId: status.participantId, sessionId: retraceState.sessionId, reentryRunId: retraceState.reentryRunId,
      stateVersion: retraceState.stateVersion, interactionId: crypto.randomUUID(), action, finalPrompt,
    });
    setRetraceState(result.state);
  }, [retraceState, status]);

  const copyNextPrompt = useCallback(async () => {
    const draft = retraceState?.nextPrompt;
    if (!draft) return;
    try {
      const finalPrompt = draft.editedPrompt ?? draft.promptText;
      await navigator.clipboard.writeText(finalPrompt);
      await completeReentry("COPY", finalPrompt);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [completeReentry, retraceState]);

  const sendNextPrompt = useCallback(async () => {
    const draft = retraceState?.nextPrompt;
    if (!draft) return;
    const finalPrompt = draft.editedPrompt ?? draft.promptText;
    try {
      await sendFollowUpMessage(finalPrompt);
      await completeReentry("SENT", finalPrompt);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      try { await completeReentry("FAILED_OPEN"); } catch (releaseCause) { setError(releaseCause instanceof Error ? releaseCause.message : String(releaseCause)); }
    }
  }, [completeReentry, retraceState]);

  useEffect(() => {
    void initializeMcpBridge().then(refresh).catch((cause: unknown) => {
      setError(cause instanceof Error ? cause.message : String(cause));
      setLoading(false);
    });
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return (
    <main className="panel">
      <header className="header">
        <div>
          <p className="eyebrow">RETRACE · M3-C</p>
          <h1>ReTrace</h1>
        </div>
        <span className={status?.ok ? "status-dot online" : "status-dot"} aria-label={status?.ok ? "在线" : "离线"} />
      </header>
      <p className="intro">把 review 后的判断转化为一轮可检查、可编辑、需显式授权的 delegation。</p>

      {loading && !status ? <p className="muted">正在读取本地运行状态…</p> : null}
      {error ? (
        <section className="notice error" role="alert">
          <strong>暂时无法读取状态</strong>
          <span>{error}</span>
          <button onClick={() => void refresh()}>重新读取</button>
        </section>
      ) : null}
      {status ? (
        <section className="card" aria-label="运行状态">
          <div className="row"><span>数据库</span><span className="value">{status.ok ? "正常" : "异常"}</span></div>
          <div className="row"><span>已记录事件</span><span className="value">{status.rawEventCount}</span></div>
          <div className="row"><span>会话数</span><span className="value">{status.sessionCount}</span></div>
          <div className="row"><span>待处理事件</span><span className="value">{status.inboxCount}</span></div>
          <div className="row"><span>最近事件</span><span className="value">{formatTime(status.lastEventAt)}</span></div>
          <div className="row"><span>LLM 模式</span><span className="value">{status.llmMode}</span></div>
        </section>
      ) : null}

      {retraceState?.uiState === "INVITATION" && retraceState.invitation ? (
        <section className="invitation" aria-label="ReTrace invitation">
          <h2>{retraceState.invitation.title}</h2>
          <p>{retraceState.invitation.body}</p>
          <div className="actions">
            <button onClick={() => void chooseInvitation("ENTER_REENTRY")}>先看看</button>
            <button className="secondary" onClick={() => void chooseInvitation("CONTINUE_DIRECT")}>继续让 Agent 试</button>
            <button className="quiet" onClick={() => void chooseInvitation("PAUSE")}>暂时不处理</button>
          </div>
        </section>
      ) : null}
      {retraceState?.uiState === "PRE_SURVEY" ? (
        <section className="survey" aria-label="PRE survey">
          <p className="eyebrow">PRE-SURVEY · 1–7</p>
          <h2>进入前，请先评价你当前的状态</h2>
          <p className="muted">这些问题只测 intervention 前状态；提交前不会显示 Re-entry reconstruction。</p>
          {retraceState.preSurvey?.questions.map((question) => <label className="survey-question" key={question.id}><span>{question.text}</span><select value={preResponses[question.id] ?? ""} onChange={(event) => setPreResponses((current) => ({ ...current, [question.id]: Number(event.target.value) }))}><option value="" disabled>选择 1–7</option>{Array.from({ length: question.scaleMax - question.scaleMin + 1 }, (_, index) => question.scaleMin + index).map((value) => <option key={value} value={value}>{value}</option>)}</select></label>)}
          <button disabled={!retraceState.preSurvey || Object.keys(preResponses).length !== retraceState.preSurvey.questions.length} onClick={() => void submitPreSurvey()}>提交前测</button>
        </section>
      ) : null}
      {retraceState?.uiState === "REENTRY_CONTEXT" ? (
        <section className="notice" aria-label="Re-entry context">
          <strong>REENTRY_CONTEXT</strong>
          <span>{retraceState.context ? `已冻结问题上下文：${retraceState.context.issueSummary}` : "冻结上下文不可用。"}</span>
          {retraceState.reconstructionError ? <span className="error-text">{retraceState.reconstructionError.code}: {retraceState.reconstructionError.message}</span> : null}
          <button onClick={() => void reconstructContext()}>重建当前上下文</button>
        </section>
      ) : null}
      {retraceState?.uiState === "USER_REVIEW" && retraceState.reconstruction ? (
        <section className="context-review" aria-label="Re-entry context review">
          <div className="context-heading">
            <div><p className="eyebrow">RE-ENTRY CONTEXT</p><h2>请确认你现在看到的上下文</h2></div>
            <span className="muted">快照 v{retraceState.reconstruction.snapshotVersion}</span>
          </div>
          <p className="muted">证据、解释和 Agent 声明分开显示。事件 ID 可用于回溯 provenance。</p>
          <h3>目标</h3>
          {retraceState.reconstruction.goal.map((item) => <ReviewCard key={item.id} itemType="GOAL" itemId={item.id} text={item.text} sourceEventIds={item.sourceEventIds} reviewed={retraceState.reviewActions?.find((review) => review.itemId === item.id)?.action} onReview={(action, before, after) => void reviewContext("GOAL", item.id, action, before, after)} />)}
          <h3>可观察证据</h3>
          {retraceState.reconstruction.evidenceItems.length === 0 ? <p className="muted">暂无冻结历史证据。</p> : retraceState.reconstruction.evidenceItems.map((item) => <ReviewCard key={item.id} itemType={`EVIDENCE · ${item.kind}`} itemId={item.id} text={item.claim} sourceEventIds={item.sourceEventIds} reviewed={retraceState.reviewActions?.find((review) => review.itemId === item.id)?.action} onInvestigate={() => void createInvestigation("EVIDENCE", item.id)} onReview={(action, before, after) => void reviewContext("EVIDENCE", item.id, action, before, after)} />)}
          <div className="add-evidence"><select value={newAdditionType} onChange={(event) => setNewAdditionType(event.target.value as "OBSERVATION" | "EVIDENCE_REQUIREMENT" | "UNCERTAINTY" | "GOVERNANCE_CONSTRAINT")}><option value="OBSERVATION">新增观察</option><option value="EVIDENCE_REQUIREMENT">新增证据要求</option><option value="UNCERTAINTY">新增不确定性</option><option value="GOVERNANCE_CONSTRAINT">新增治理规则</option></select>{newAdditionType === "GOVERNANCE_CONSTRAINT" ? <select value={newConstraintKind} onChange={(event) => setNewConstraintKind(event.target.value as typeof newConstraintKind)}><option value="SCOPE">范围限制</option><option value="PROCESS">操作要求</option><option value="EVIDENCE">证据要求</option><option value="AUTHORITY">授权边界</option><option value="DO_NOT_ASSUME">不要假设</option><option value="OTHER">其他</option></select> : null}<textarea value={newEvidence} onChange={(event) => setNewEvidence(event.target.value)} placeholder={newAdditionType === "GOVERNANCE_CONSTRAINT" ? "添加下一步 Agent 必须遵守的规则或边界…" : "补充一个观察、证据要求或仍未知的部分…"} rows={2} /><button disabled={!newEvidence.trim()} onClick={() => { const text = newEvidence.trim(); const item = newAdditionType === "UNCERTAINTY" ? { text, sourceEventIds: [], relatedClaimIds: [], sourceType: "USER_REVIEW", timestamp: new Date().toISOString() } : newAdditionType === "GOVERNANCE_CONSTRAINT" ? { kind: newConstraintKind, text, source: "USER_ADD", sourceReviewId: "pending", createdAt: new Date().toISOString() } : { kind: newAdditionType === "EVIDENCE_REQUIREMENT" ? "EVIDENCE_REQUIREMENT" : "USER_OBSERVATION", claim: text, sourceEventIds: [], sourceType: "USER_REVIEW", timestamp: new Date().toISOString(), verificationStatus: "USER_CONFIRMED" }; void reviewContext(newAdditionType === "UNCERTAINTY" ? "UNCERTAINTY" : newAdditionType === "GOVERNANCE_CONSTRAINT" ? "GOVERNANCE_CONSTRAINT" : "EVIDENCE", "", "ADD", undefined, item); setNewEvidence(""); }}>添加</button></div>
          {retraceState.reviewedState?.evidenceRequirements.map((item) => <article className="context-item" key={item.id}><p><strong>用户新增证据要求：</strong>{item.text}</p><div className="item-actions"><button className="investigate" onClick={() => void createInvestigation("EVIDENCE", item.id)}>调查此要求</button></div></article>)}
          {retraceState.reviewedState?.governanceConstraints.map((item) => <ReviewCard key={item.id} itemType={`GOVERNANCE_CONSTRAINT · ${item.kind}`} itemId={item.id} text={item.text} reviewed={retraceState.reviewActions?.find((review) => review.itemId === item.id)?.action} onReview={(action, before, after) => void reviewContext("GOVERNANCE_CONSTRAINT", item.id, action, before, after)} />)}
          {retraceState.reviewedState?.addedObservations.map((item) => <article className="context-item" key={item.id}><p><strong>用户观察：</strong>{item.text}</p></article>)}
          <h3>解释（不是证据）</h3>
          {retraceState.reconstruction.explanations.map((item) => <ReviewCard key={item.id} itemType={`EXPLANATION · ${item.kind}`} itemId={item.id} text={item.text} sourceEventIds={item.sourceEventIds} reviewed={retraceState.reviewActions?.find((review) => review.itemId === item.id)?.action} onReview={(action, before, after) => void reviewContext("EXPLANATION", item.id, action, before, after)} />)}
          <h3>不确定性</h3>
          {retraceState.reconstruction.uncertainties.map((item) => <ReviewCard key={item.id} itemType="UNCERTAINTY" itemId={item.id} text={item.text} sourceEventIds={item.sourceEventIds} reviewed={retraceState.reviewActions?.find((review) => review.itemId === item.id)?.action} onInvestigate={() => void createInvestigation("UNCERTAINTY", item.id)} onReview={(action, before, after) => void reviewContext("UNCERTAINTY", item.id, action, before, after)} />)}
          <h3>Agent Claims</h3>
          {retraceState.reconstruction.agentClaims.length === 0 ? <p className="muted">冻结历史中没有可分离的 Agent 声明。</p> : retraceState.reconstruction.agentClaims.map((item) => <ReviewCard key={item.claimId} itemType={`AGENT_CLAIM · ${item.kind}`} itemId={item.claimId} text={`${item.text} · ${item.verificationStatus}`} sourceEventIds={[item.sourceEventId]} reviewed={retraceState.reviewActions?.find((review) => review.itemId === item.claimId)?.action} onReview={(action, before, after) => void reviewContext("AGENT_CLAIM", item.claimId, action, before, after)} />)}
          {retraceState.investigations?.map((investigation) => <InvestigationCard key={investigation.investigationId} investigation={investigation} reviewActions={retraceState.reviewActions} onEdit={(editedPrompt) => void editInvestigation(investigation.investigationId, editedPrompt)} onCopy={() => void copyInvestigation(investigation.investigationId)} onReviewCandidate={(candidateId, claim, action, before, after) => void reviewContext("EVIDENCE", candidateId, action, before || claim, after)} />)}
          <div className="actions"><button onClick={() => void generateNextPrompt()}>基于 review 生成下一轮 delegation</button><button className="quiet" onClick={() => void completeReentry("CANCEL")}>取消 Re-entry</button></div>
        </section>
      ) : null}
      {retraceState?.uiState === "NEXT_PROMPT_READY" && retraceState.nextPrompt ? <NextPromptPanel draft={retraceState.nextPrompt} onEdit={(prompt) => void editNextPrompt(prompt)} onCopy={() => void copyNextPrompt()} onSend={() => void sendNextPrompt()} onCancel={() => void completeReentry("CANCEL")} /> : null}
      {retraceState?.uiState === "RESUMABLE" ? <section className="notice"><strong>RESUMABLE</strong><span>Re-entry 已释放控制权。完成原因：{retraceState.completionReason ?? "未知"}。</span></section> : null}

      {!isMcpBridgeAvailable() ? (
        <p className="notice">当前以浏览器预览方式打开；连接到 MCP host 后会显示实时状态。</p>
      ) : null}
      <footer>UI 失败不会阻断 Codex 主流程。</footer>
    </main>
  );
}
