import { AgentClaimSchema, ReconstructionSchema } from "../schemas/context.js";
function splitStatements(text) {
    return text
        .split(/[\n。！？!?；;]+/u)
        .map((part) => part.trim().replace(/^[-*•\d.)]+\s*/u, ""))
        .filter((part) => part.length >= 4);
}
function claimKind(text) {
    if (/(原因|因为|导致|caused by|due to)/iu.test(text))
        return "CAUSE";
    if (/(下一步|接下来|next step|建议)/iu.test(text))
        return "NEXT_STEP";
    if (/(成功|正常工作|works|working|通过|pass(?:ed)?)/iu.test(text))
        return "SUCCESS";
    if (/(完成|修复|解决|fixed|completed|done|已改)/iu.test(text))
        return "COMPLETION";
    if (/(现在|当前|状态|目前|is now|currently)/iu.test(text))
        return "STATE";
    return "OTHER";
}
function evidenceKind(text) {
    if (/(test|测试|spec|vitest|jest|通过|失败)/iu.test(text))
        return "TEST_RESULT";
    if (/(build|构建|编译|typecheck|lint)/iu.test(text))
        return "BUILD_RESULT";
    if (/(error|错误|异常|失败|exception|stack)/iu.test(text))
        return "ERROR";
    if (/(file|文件|代码|source|源码|inspection|检查)/iu.test(text))
        return "FILE_INSPECTION";
    return "TOOL_RESULT";
}
export function buildDeterministicReconstruction(input) {
    const userPrompts = input.events.filter((event) => event.eventType === "USER_PROMPT" && event.contentText);
    const firstPrompt = userPrompts[0];
    const goal = [{
            id: "goal-original",
            text: `原始用户目标：${firstPrompt?.contentText ?? input.issueChain.issueSummary}`,
            sourceEventIds: [firstPrompt?.eventId ?? input.issueChain.firstEventId],
            sourceType: "CODEX_HOOK",
            timestamp: firstPrompt?.observedAt ?? input.generatedAt,
        }];
    if (input.issueChain.issueSummary && input.issueChain.issueSummary !== firstPrompt?.contentText) {
        goal.push({
            id: "goal-current-interpretation",
            text: `后续问题解释：${input.issueChain.issueSummary}`,
            sourceEventIds: [input.issueChain.lastEventId],
            sourceType: "CODEX_HOOK",
            timestamp: input.events.find((event) => event.eventId === input.issueChain.lastEventId)?.observedAt ?? input.generatedAt,
        });
    }
    const evidenceItems = input.events
        .filter((event) => event.eventType === "TOOL_RESULT" && event.contentText)
        .map((event, index) => ({
        id: `evidence-${index + 1}`,
        kind: evidenceKind(event.contentText ?? ""),
        claim: event.contentText ?? "",
        sourceEventIds: [event.eventId],
        sourceType: "CODEX_HOOK",
        timestamp: event.observedAt,
        verificationStatus: "UNVERIFIED",
    }));
    const agentClaims = [];
    for (const event of input.events.filter((candidate) => candidate.eventType === "AGENT_FINAL" && candidate.contentText)) {
        for (const statement of splitStatements(event.contentText ?? "")) {
            if (!/(完成|修复|解决|成功|原因|因为|导致|下一步|建议|现在|当前|状态|fixed|completed|done|success|caused by|due to|next step|currently|works?)/iu.test(statement))
                continue;
            const claim = AgentClaimSchema.parse({
                claimId: `claim-${agentClaims.length + 1}`,
                kind: claimKind(statement),
                text: statement,
                sourceEventId: event.eventId,
                supportingEvidenceIds: [],
                verificationStatus: "UNVERIFIED",
                timestamp: event.observedAt,
            });
            agentClaims.push(claim);
        }
    }
    const explanations = agentClaims.map((claim) => ({
        id: `explanation-${claim.claimId}`,
        text: claim.text,
        kind: "AGENT_HYPOTHESIS",
        sourceEventIds: [claim.sourceEventId],
        supportingEvidenceIds: [...claim.supportingEvidenceIds],
        timestamp: claim.timestamp,
    }));
    const uncertainties = agentClaims
        .filter((claim) => claim.supportingEvidenceIds.length === 0)
        .map((claim) => ({
        id: `uncertainty-${claim.claimId}`,
        text: `未验证 Agent 声明：${claim.text}`,
        sourceEventIds: [claim.sourceEventId],
        relatedClaimIds: [claim.claimId],
        timestamp: claim.timestamp,
    }));
    if (uncertainties.length === 0) {
        const lastEvent = input.events[input.events.length - 1];
        uncertainties.push({
            id: "uncertainty-missing-evidence",
            text: evidenceItems.length === 0 ? "当前冻结历史中没有可核验的工具或运行时证据。" : "当前重建没有确认剩余解释与证据之间的因果关系。",
            sourceEventIds: lastEvent ? [lastEvent.eventId] : [input.issueChain.lastEventId],
            relatedClaimIds: agentClaims.map((claim) => claim.claimId),
            timestamp: lastEvent?.observedAt ?? input.generatedAt,
        });
    }
    return ReconstructionSchema.parse({
        reconstructionId: input.reconstructionId,
        snapshotVersion: input.snapshotVersion,
        generatedAt: input.generatedAt,
        goal,
        evidenceItems,
        explanations,
        uncertainties,
        agentClaims,
    });
}
