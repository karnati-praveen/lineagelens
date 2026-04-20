import type { PromptCorrelationResult } from './correlation';
import type { ProvenanceRecord } from './provenance';
import type { ProvenanceMode } from './storage/StorageService';
import { pathsReferToSameFile } from './pathUtils';
import type { AgentEvidence, AgentOperationType } from './agentAdapters';
import {
  buildSessionSignature,
  clampConfidence,
  classifyOperationType,
  createEvidence,
  hashContext
} from './agentAdapters/shared';

const AGENT_SESSION_GAP_MS = 20 * 60 * 1000;
const HIGH_RISK_THRESHOLD = 65;
const CRITICAL_RISK_THRESHOLD = 85;

type RiskSignalDefinition = {
  pattern: RegExp;
  score: number;
  reason: string;
  category: RiskCategory;
};

type AgentSessionAccumulator = {
  signature: string;
  sessionId: string;
  conversationId: string | null;
  runId: string | null;
  startedAtMs: number;
  endedAtMs: number;
  records: ProvenanceRecord[];
  toolName: string | null;
  provider: string | null;
  modelName: string | null;
  adapterName: string | null;
  adapterConfidence: number | null;
  sessionKind: AgentSessionKind;
  host: string | null;
  evidence: string[];
};

const CODE_RISK_SIGNALS: RiskSignalDefinition[] = [
  {
    pattern:
      /(api[_-]?key|access[_-]?token|secret[_-]?key|private[_-]?key|authorization\s*[:=])/i,
    score: 28,
    reason: 'The inserted block appears to contain credential-like material.',
    category: 'security'
  },
  {
    pattern: /\b(eval|Function\s*\(|new Function|exec\s*\(|execSync\s*\()/,
    score: 24,
    reason: 'Dynamic code execution is present in the generated block.',
    category: 'security'
  },
  {
    pattern: /\b(subprocess\.(run|Popen)|os\.system|child_process|spawnSync|spawn)\b/,
    score: 22,
    reason: 'Shell or process execution was introduced by the generated block.',
    category: 'security'
  },
  {
    pattern: /\b(dangerouslySetInnerHTML|innerHTML\s*=|document\.write\s*\()/,
    score: 20,
    reason: 'Unsafe DOM mutation patterns were introduced.',
    category: 'security'
  },
  {
    pattern: /\b(SELECT\s+[^\n]{0,200}FROM|INSERT\s+INTO|UPDATE\s+\w{1,80}\s+SET|DELETE\s+FROM)\b/i,
    score: 16,
    reason: 'Raw SQL appears in the generated block.',
    category: 'reliability'
  },
  {
    pattern: /\b(password|token|credential|session|jwt|oauth|auth)\b/i,
    score: 12,
    reason: 'Authentication or credential handling appears in the generated block.',
    category: 'compliance'
  },
  {
    pattern: /\b(fetch|axios|requests\.|httpx\.|urllib\.request|WebSocket)\b/,
    score: 10,
    reason: 'Network-facing behavior was introduced.',
    category: 'reliability'
  }
];

const PATH_RISK_SIGNALS: RiskSignalDefinition[] = [
  {
    pattern: /(auth|security|permission|rbac|iam|oauth|token|secret|credential)/i,
    score: 14,
    reason: 'The file path suggests a security-sensitive surface.',
    category: 'compliance'
  },
  {
    pattern: /(payment|billing|invoice|checkout|ledger|finance)/i,
    score: 14,
    reason: 'The file path suggests a financially sensitive surface.',
    category: 'compliance'
  },
  {
    pattern: /(migration|schema|db|database|repository|sql)/i,
    score: 10,
    reason: 'The file path suggests data or schema impact.',
    category: 'reliability'
  }
];

export type RiskLevel = 'low' | 'medium' | 'high' | 'critical';
export type RiskCategory = 'security' | 'compliance' | 'reliability' | 'provenance';
export type ComplianceStatus = 'pass' | 'warning' | 'fail';
export type AgentSessionKind = 'agentic' | 'assistant' | 'unknown';

export type InsightsFilters = {
  dateFrom: string;
  dateTo: string;
  currentFileOnly: boolean;
  currentFilePath?: string;
};

export type RiskAssessment = {
  score: number;
  level: RiskLevel;
  reasons: string[];
  categories: RiskCategory[];
  signalCount: number;
};

export type AgentContext = {
  toolName: string | null;
  provider: string | null;
  sessionId: string | null;
  conversationId: string | null;
  runId: string | null;
  workspaceHint: string | null;
  operationType: AgentOperationType;
  confidence: number;
  evidence: AgentEvidence[];
  adapterName: string | null;
  matchSource: 'adapter' | 'heuristic';
  sessionKind: AgentSessionKind;
  host: string | null;
  userAgent: string | null;
  modelName: string | null;
  sessionSignature: string;
  detectedAtIso: string;
};

export type ComplianceControlStatus = {
  id: string;
  title: string;
  status: ComplianceStatus;
  summary: string;
  metric: string;
};

export type DashboardRecordPreview = {
  uuid: string;
  filePath: string;
  timestampIso: string;
  model: string | null;
  promptStatus: 'captured' | 'not-captured';
  riskScore: number;
  riskLevel: RiskLevel;
  summary: string;
  toolName: string | null;
  provider: string | null;
  adapterName: string | null;
  adapterConfidence: number | null;
  captureStatus: string | null;
};

export type DashboardFileHotspot = {
  filePath: string;
  recordCount: number;
  highRiskCount: number;
  avgRiskScore: number;
  latestTimestampIso: string | null;
};

export type DashboardModelMetric = {
  model: string;
  recordCount: number;
  promptCaptureRate: number;
  avgRiskScore: number;
  highRiskCount: number;
};

export type DashboardTrendPoint = {
  bucketLabel: string;
  recordCount: number;
  highRiskCount: number;
  avgRiskScore: number;
  promptCaptureRate: number;
};

export type AgentSessionSummary = {
  sessionId: string;
  conversationId: string | null;
  runId: string | null;
  toolName: string | null;
  provider: string | null;
  modelName: string | null;
  adapterName: string | null;
  adapterConfidence: number | null;
  sessionKind: AgentSessionKind;
  startedAtIso: string;
  endedAtIso: string;
  recordCount: number;
  highRiskCount: number;
  promptCaptureRate: number;
  totalNetAddedLines: number;
  files: string[];
  evidence: string[];
};

export type DashboardMemberMetric = {
  id: string;
  username: string;
  role: string;
  recordCount: number;
  joinedAtIso: string;
};

export type InsightsDashboardPayload = {
  mode: ProvenanceMode;
  generatedAtIso: string;
  summary: {
    totalRecords: number;
    promptCapturedRecords: number;
    promptCaptureRate: number;
    avgRiskScore: number;
    highRiskRecords: number;
    criticalRecords: number;
    uniqueFiles: number;
    uniqueModels: number;
    uniqueAgentSessions: number;
    agenticRecords: number;
    totalNetAddedLines: number;
  };
  complianceControls: ComplianceControlStatus[];
  highRiskRecords: DashboardRecordPreview[];
  hotspots: DashboardFileHotspot[];
  modelAnalytics: DashboardModelMetric[];
  riskTrends: DashboardTrendPoint[];
  agentSessions: AgentSessionSummary[];
  memberStats: DashboardMemberMetric[];
  warnings: string[];
};

export function buildInsightsDashboard(
  records: readonly ProvenanceRecord[],
  mode: ProvenanceMode,
  filters: InsightsFilters,
  warnings: string[] = []
): InsightsDashboardPayload {
  const filteredRecords = applyInsightsFilters(records, filters);
  const recordSummaries = filteredRecords.map((record) => {
    const risk = getStoredOrComputedRisk(record);
    const agentContext = getStoredOrComputedAgentContext(record);
    const model = normalizeModelName(record.prompt.modelName);

    return {
      record,
      risk,
      agentContext,
      model
    };
  });

  const totalRecords = recordSummaries.length;
  const promptCapturedRecords = recordSummaries.filter(
    (entry) => entry.record.promptStatus === 'captured'
  ).length;
  const totalRiskScore = recordSummaries.reduce((sum, entry) => sum + entry.risk.score, 0);
  const highRiskRecords = recordSummaries.filter((entry) => entry.risk.score >= HIGH_RISK_THRESHOLD);
  const criticalRecords = recordSummaries.filter(
    (entry) => entry.risk.score >= CRITICAL_RISK_THRESHOLD
  );
  const uniqueFiles = new Set(recordSummaries.map((entry) => entry.record.file.path).filter(Boolean));
  const uniqueModels = new Set(recordSummaries.map((entry) => entry.model).filter(Boolean));
  const agentSessions = buildAgentSessions(recordSummaries);
  const agenticRecords = recordSummaries.filter(
    (entry) => entry.agentContext?.sessionKind === 'agentic'
  ).length;
  const totalNetAddedLines = recordSummaries.reduce(
    (sum, entry) => sum + Math.max(0, entry.record.insertion.netAddedLines || 0),
    0
  );
  const promptCaptureRate =
    totalRecords > 0 ? Number((promptCapturedRecords / totalRecords).toFixed(4)) : 0;
  const avgRiskScore = totalRecords > 0 ? Number((totalRiskScore / totalRecords).toFixed(2)) : 0;

  return {
    mode,
    generatedAtIso: new Date().toISOString(),
    summary: {
      totalRecords,
      promptCapturedRecords,
      promptCaptureRate,
      avgRiskScore,
      highRiskRecords: highRiskRecords.length,
      criticalRecords: criticalRecords.length,
      uniqueFiles: uniqueFiles.size,
      uniqueModels: uniqueModels.size,
      uniqueAgentSessions: agentSessions.length,
      agenticRecords,
      totalNetAddedLines
    },
    complianceControls: buildComplianceControls({
      totalRecords,
      promptCaptureRate,
      avgRiskScore,
      highRiskRecords: highRiskRecords.length,
      criticalRecords: criticalRecords.length,
      uniqueAgentSessions: agentSessions.length,
      agenticRecords,
      averageCorrelationConfidence: averageCorrelationConfidence(recordSummaries.map((entry) => entry.record))
    }),
    highRiskRecords: highRiskRecords
      .sort((left, right) => compareRiskEntries(right, left))
      .slice(0, 12)
      .map((entry) => toDashboardRecordPreview(entry.record, entry.risk, entry.agentContext, entry.model)),
    hotspots: buildFileHotspots(recordSummaries),
    modelAnalytics: buildModelAnalytics(recordSummaries),
    riskTrends: buildRiskTrends(recordSummaries),
    agentSessions,
    memberStats: [],
    warnings
  };
}

export function assessProvenanceRisk(record: ProvenanceRecord): RiskAssessment {
  let score = 12;
  const reasons: string[] = [];
  const categories = new Set<RiskCategory>();

  if (record.promptStatus !== 'captured') {
    score += 24;
    reasons.push('Prompt capture is missing, which reduces auditability and reviewer confidence.');
    categories.add('provenance');
  }

  const correlationConfidence = toFiniteNumber(record.metadata.correlationConfidence);
  if (correlationConfidence !== null && correlationConfidence < 0.4) {
    score += 16;
    reasons.push('Prompt-to-code correlation confidence is low.');
    categories.add('provenance');
  } else if (correlationConfidence !== null && correlationConfidence < 0.65) {
    score += 8;
    reasons.push('Prompt-to-code correlation confidence is only moderate.');
    categories.add('provenance');
  }

  const netAddedLines = Math.max(0, record.insertion.netAddedLines || 0);
  if (netAddedLines >= 80) {
    score += 18;
    reasons.push('A large AI-generated block was introduced.');
    categories.add('reliability');
  } else if (netAddedLines >= 30) {
    score += 10;
    reasons.push('The generated block is large enough to warrant focused review.');
    categories.add('reliability');
  }

  const MAX_RISK_SCAN_LENGTH = 50_000;
  const insertedCode = (record.insertion.extractedInsertedCodeBlock || '').slice(0, MAX_RISK_SCAN_LENGTH);
  for (const signal of CODE_RISK_SIGNALS) {
    if (signal.pattern.test(insertedCode)) {
      score += signal.score;
      reasons.push(signal.reason);
      categories.add(signal.category);
    }
  }

  const filePath = (record.file.path || '').slice(0, 1_000);
  for (const signal of PATH_RISK_SIGNALS) {
    if (signal.pattern.test(filePath)) {
      score += signal.score;
      reasons.push(signal.reason);
      categories.add(signal.category);
    }
  }

  const agentContext = getStoredOrComputedAgentContext(record);
  if (agentContext?.sessionKind === 'agentic') {
    score += 6;
    reasons.push('The record appears to come from an autonomous or semi-autonomous coding session.');
    categories.add('provenance');
  }

  if (reasons.length === 0) {
    reasons.push('No strong governance or security risk signals were detected.');
  }

  const normalizedScore = Math.max(0, Math.min(100, score));
  return {
    score: normalizedScore,
    level: toRiskLevel(normalizedScore),
    reasons: dedupeStrings(reasons).slice(0, 5),
    categories: [...categories].sort(),
    signalCount: dedupeStrings(reasons).length
  };
}

export function deriveAgentContext(input: {
  timestampIso: string;
  correlation: PromptCorrelationResult;
  modelName: unknown;
}): AgentContext | null {
  if (input.correlation.promptStatus !== 'captured') {
    return null;
  }

  const modelName = normalizeModelName(input.modelName);
  const targetHost = input.correlation.targetHost?.trim().toLowerCase() ?? null;
  const headers = input.correlation.requestHeaders ?? null;
  const userAgent = findHeaderValue(headers, 'user-agent') || findHeaderValue(headers, 'x-client-name');
  const rawContextBlob = [
    targetHost ?? '',
    userAgent ?? '',
    modelName,
    safeSerialize(input.correlation.parameters),
    safeSerialize(input.correlation.fullPromptMessages)
  ]
    .join('\n')
    .toLowerCase();

  let toolName: string | null = null;
  let sessionKind: AgentSessionKind = 'unknown';

  if (rawContextBlob.includes('cursor')) {
    toolName = 'Cursor';
    sessionKind = 'agentic';
  } else if (rawContextBlob.includes('claude-code') || rawContextBlob.includes('claude code')) {
    toolName = 'Claude Code';
    sessionKind = 'agentic';
  } else if (rawContextBlob.includes('aider')) {
    toolName = 'Aider';
    sessionKind = 'agentic';
  } else if (rawContextBlob.includes('codex')) {
    toolName = 'Codex CLI';
    sessionKind = 'agentic';
  } else if (rawContextBlob.includes('copilot')) {
    toolName = 'GitHub Copilot';
    sessionKind = 'assistant';
  }

  const provider = inferProvider(targetHost, modelName, rawContextBlob);
  if (sessionKind === 'unknown' && provider) {
    sessionKind = 'assistant';
  }

  if (!toolName && !provider && !modelName) {
    return null;
  }

  const sessionId = findHeaderValue(headers, 'x-request-id') || hashContext([targetHost, userAgent, modelName, input.timestampIso]);
  const conversationId = findHeaderValue(headers, 'x-conversation-id') || sessionId;
  const runId = findHeaderValue(headers, 'x-run-id') || null;
  const operationType = classifyOperationType({
    insertedText: input.correlation.rawModelResponse ?? '',
    promptBlob: rawContextBlob,
    modelName
  });
  const evidence = [
    createEvidence('heuristic', 'rawContextBlob', rawContextBlob.slice(0, 240), 0.2, 'Legacy heuristic detection.'),
    createEvidence('heuristic', 'targetHost', targetHost ?? 'unknown', 0.1, 'Heuristic host match.'),
    createEvidence('heuristic', 'userAgent', userAgent ?? 'unknown', 0.1, 'Heuristic user-agent match.')
  ];

  return {
    toolName,
    provider,
    sessionId,
    conversationId,
    runId,
    workspaceHint: null,
    operationType,
    confidence: clampConfidence(toolName ? 0.55 : provider ? 0.42 : 0.3),
    evidence,
    adapterName: 'legacy-heuristic',
    matchSource: 'heuristic',
    sessionKind,
    host: targetHost,
    userAgent: userAgent ?? null,
    modelName: modelName || null,
    sessionSignature: buildSessionSignature({
      toolName,
      provider,
      modelName: modelName || null,
      sessionKind,
      sessionId,
      conversationId,
      runId
    }),
    detectedAtIso: input.timestampIso
  };
}

function applyInsightsFilters(
  records: readonly ProvenanceRecord[],
  filters: InsightsFilters
): ProvenanceRecord[] {
  const dateFromEpoch = parseEpoch(filters.dateFrom);
  const dateToEpoch = parseEpoch(filters.dateTo);
  const currentFilePath = filters.currentFilePath ?? '';

  return records.filter((record) => {
    const timestamp = parseEpoch(record.timestampIso);
    if (dateFromEpoch !== null && (timestamp === null || timestamp < dateFromEpoch)) {
      return false;
    }

    if (dateToEpoch !== null && (timestamp === null || timestamp > dateToEpoch)) {
      return false;
    }

    if (filters.currentFileOnly && currentFilePath.trim().length > 0) {
      return pathsReferToSameFile(record.file.path, currentFilePath);
    }

    return true;
  });
}

function buildComplianceControls(input: {
  totalRecords: number;
  promptCaptureRate: number;
  avgRiskScore: number;
  highRiskRecords: number;
  criticalRecords: number;
  uniqueAgentSessions: number;
  agenticRecords: number;
  averageCorrelationConfidence: number;
}): ComplianceControlStatus[] {
  const highRiskRatio =
    input.totalRecords > 0 ? input.highRiskRecords / input.totalRecords : 0;

  return [
    buildControl(
      'prompt-capture',
      'Prompt Capture Coverage',
      input.promptCaptureRate >= 0.8 ? 'pass' : input.promptCaptureRate >= 0.5 ? 'warning' : 'fail',
      percentage(input.promptCaptureRate),
      'How consistently the system retained auditable prompt evidence.'
    ),
    buildControl(
      'risk-density',
      'High-Risk Density',
      highRiskRatio <= 0.1 ? 'pass' : highRiskRatio <= 0.25 ? 'warning' : 'fail',
      percentage(highRiskRatio),
      'Share of AI-generated records currently assessed as high risk.'
    ),
    buildControl(
      'critical-records',
      'Critical Findings',
      input.criticalRecords === 0 ? 'pass' : input.criticalRecords <= 3 ? 'warning' : 'fail',
      String(input.criticalRecords),
      'Count of AI-generated records that should be escalated immediately.'
    ),
    buildControl(
      'correlation-quality',
      'Correlation Confidence',
      input.averageCorrelationConfidence >= 0.7
        ? 'pass'
        : input.averageCorrelationConfidence >= 0.5
          ? 'warning'
          : 'fail',
      percentage(input.averageCorrelationConfidence),
      'Average confidence that prompt/response evidence matches generated code.'
    ),
    buildControl(
      'agent-session-attribution',
      'Agent Session Attribution',
      input.agenticRecords === 0
        ? 'warning'
        : input.uniqueAgentSessions > 0
          ? 'pass'
          : 'warning',
      String(input.uniqueAgentSessions),
      'Whether autonomous coding activity can be grouped into reviewable sessions.'
    ),
    buildControl(
      'overall-risk',
      'Average Governance Risk',
      input.avgRiskScore < 35 ? 'pass' : input.avgRiskScore < 60 ? 'warning' : 'fail',
      String(input.avgRiskScore),
      'Heuristic governance risk score across all filtered provenance records.'
    )
  ];
}

function buildFileHotspots(
  recordSummaries: Array<{
    record: ProvenanceRecord;
    risk: RiskAssessment;
  }>
): DashboardFileHotspot[] {
  const byFile = new Map<
    string,
    {
      count: number;
      highRiskCount: number;
      riskScoreTotal: number;
      latestTimestampIso: string | null;
    }
  >();

  for (const entry of recordSummaries) {
    const filePath = entry.record.file.path || '(unknown file)';
    const existing =
      byFile.get(filePath) ?? {
        count: 0,
        highRiskCount: 0,
        riskScoreTotal: 0,
        latestTimestampIso: null
      };

    existing.count += 1;
    existing.riskScoreTotal += entry.risk.score;
    if (entry.risk.score >= HIGH_RISK_THRESHOLD) {
      existing.highRiskCount += 1;
    }

    if (!existing.latestTimestampIso || entry.record.timestampIso > existing.latestTimestampIso) {
      existing.latestTimestampIso = entry.record.timestampIso;
    }

    byFile.set(filePath, existing);
  }

  return [...byFile.entries()]
    .map(([filePath, value]) => ({
      filePath,
      recordCount: value.count,
      highRiskCount: value.highRiskCount,
      avgRiskScore: Number((value.riskScoreTotal / Math.max(1, value.count)).toFixed(2)),
      latestTimestampIso: value.latestTimestampIso
    }))
    .sort((left, right) => {
      if (left.highRiskCount !== right.highRiskCount) {
        return right.highRiskCount - left.highRiskCount;
      }

      if (left.avgRiskScore !== right.avgRiskScore) {
        return right.avgRiskScore - left.avgRiskScore;
      }

      return right.recordCount - left.recordCount;
    })
    .slice(0, 12);
}

function buildModelAnalytics(
  recordSummaries: Array<{
    record: ProvenanceRecord;
    risk: RiskAssessment;
    model: string;
  }>
): DashboardModelMetric[] {
  const byModel = new Map<
    string,
    {
      count: number;
      promptCaptured: number;
      riskScoreTotal: number;
      highRiskCount: number;
    }
  >();

  for (const entry of recordSummaries) {
    const model = entry.model || 'unknown';
    const existing =
      byModel.get(model) ?? {
        count: 0,
        promptCaptured: 0,
        riskScoreTotal: 0,
        highRiskCount: 0
      };

    existing.count += 1;
    existing.riskScoreTotal += entry.risk.score;
    if (entry.record.promptStatus === 'captured') {
      existing.promptCaptured += 1;
    }
    if (entry.risk.score >= HIGH_RISK_THRESHOLD) {
      existing.highRiskCount += 1;
    }

    byModel.set(model, existing);
  }

  return [...byModel.entries()]
    .map(([model, value]) => ({
      model,
      recordCount: value.count,
      promptCaptureRate: Number((value.promptCaptured / Math.max(1, value.count)).toFixed(4)),
      avgRiskScore: Number((value.riskScoreTotal / Math.max(1, value.count)).toFixed(2)),
      highRiskCount: value.highRiskCount
    }))
    .sort((left, right) => {
      if (left.recordCount !== right.recordCount) {
        return right.recordCount - left.recordCount;
      }

      return right.avgRiskScore - left.avgRiskScore;
    })
    .slice(0, 12);
}

function buildRiskTrends(
  recordSummaries: Array<{
    record: ProvenanceRecord;
    risk: RiskAssessment;
  }>
): DashboardTrendPoint[] {
  const buckets = new Map<
    string,
    {
      count: number;
      highRiskCount: number;
      promptCaptured: number;
      riskScoreTotal: number;
    }
  >();

  for (const entry of recordSummaries) {
    const bucketLabel = (entry.record.timestampIso || '').slice(0, 10) || 'unknown';
    const existing =
      buckets.get(bucketLabel) ?? {
        count: 0,
        highRiskCount: 0,
        promptCaptured: 0,
        riskScoreTotal: 0
      };

    existing.count += 1;
    existing.riskScoreTotal += entry.risk.score;
    if (entry.risk.score >= HIGH_RISK_THRESHOLD) {
      existing.highRiskCount += 1;
    }
    if (entry.record.promptStatus === 'captured') {
      existing.promptCaptured += 1;
    }

    buckets.set(bucketLabel, existing);
  }

  return [...buckets.entries()]
    .sort((left, right) => left[0].localeCompare(right[0]))
    .slice(-10)
    .map(([bucketLabel, value]) => ({
      bucketLabel,
      recordCount: value.count,
      highRiskCount: value.highRiskCount,
      avgRiskScore: Number((value.riskScoreTotal / Math.max(1, value.count)).toFixed(2)),
      promptCaptureRate: Number((value.promptCaptured / Math.max(1, value.count)).toFixed(4))
    }));
}

function buildAgentSessions(
  recordSummaries: Array<{
    record: ProvenanceRecord;
    risk: RiskAssessment;
    agentContext: AgentContext | null;
    model: string;
  }>
): AgentSessionSummary[] {
  const sortedEntries = [...recordSummaries]
    .filter((entry) => entry.agentContext)
    .sort((left, right) => {
      const leftEpoch = parseEpoch(left.record.timestampIso) ?? 0;
      const rightEpoch = parseEpoch(right.record.timestampIso) ?? 0;
      return leftEpoch - rightEpoch;
    });

  const sessions: AgentSessionAccumulator[] = [];
  const latestSessionBySignature = new Map<string, AgentSessionAccumulator>();
  const latestSessionByNativeId = new Map<string, AgentSessionAccumulator>();

  for (const entry of sortedEntries) {
    const agentContext = entry.agentContext;
    if (!agentContext) {
      continue;
    }

    const timestampMs = parseEpoch(entry.record.timestampIso) ?? 0;
    const nativeSessionKey = getNativeSessionKey(agentContext);
    const signature = nativeSessionKey ?? agentContext.sessionSignature;
    const existing =
      (nativeSessionKey ? latestSessionByNativeId.get(nativeSessionKey) : undefined) ??
      latestSessionBySignature.get(signature);

    if (!existing || timestampMs - existing.endedAtMs > AGENT_SESSION_GAP_MS) {
      const sessionId =
        nativeSessionKey ??
        agentContext.sessionId ??
        agentContext.runId ??
        agentContext.conversationId ??
        'session-' + entry.record.uuid;
      const created: AgentSessionAccumulator = {
        signature,
        sessionId,
        conversationId: agentContext.conversationId,
        runId: agentContext.runId,
        startedAtMs: timestampMs,
        endedAtMs: timestampMs,
        records: [entry.record],
        toolName: agentContext.toolName,
        provider: agentContext.provider,
        modelName: entry.model || agentContext.modelName,
        adapterName: agentContext.adapterName,
        adapterConfidence: agentContext.confidence,
        sessionKind: agentContext.sessionKind,
        host: agentContext.host,
        evidence: agentContext.evidence.map((item) => `${item.field}: ${item.value}`)
      };
      sessions.push(created);
      if (nativeSessionKey) {
        latestSessionByNativeId.set(nativeSessionKey, created);
      }
      latestSessionBySignature.set(signature, created);
      continue;
    }

    existing.records.push(entry.record);
    existing.endedAtMs = timestampMs;
    if (nativeSessionKey) {
      latestSessionByNativeId.set(nativeSessionKey, existing);
    }
  }

  return sessions
    .map((session) => {
      const files = new Set<string>();
      let highRiskCount = 0;
      let promptCapturedCount = 0;
      let totalNetAddedLines = 0;

      for (const record of session.records) {
        files.add(record.file.path);
        const risk = getStoredOrComputedRisk(record);
        if (risk.score >= HIGH_RISK_THRESHOLD) {
          highRiskCount += 1;
        }
        if (record.promptStatus === 'captured') {
          promptCapturedCount += 1;
        }
        totalNetAddedLines += Math.max(0, record.insertion.netAddedLines || 0);
      }

      return {
        sessionId: session.sessionId,
        conversationId: session.conversationId,
        runId: session.runId,
        toolName: session.toolName,
        provider: session.provider,
        modelName: session.modelName,
        adapterName: session.adapterName,
        adapterConfidence: session.adapterConfidence,
        sessionKind: session.sessionKind,
        startedAtIso: new Date(session.startedAtMs).toISOString(),
        endedAtIso: new Date(session.endedAtMs).toISOString(),
        recordCount: session.records.length,
        highRiskCount,
        promptCaptureRate: Number(
          (promptCapturedCount / Math.max(1, session.records.length)).toFixed(4)
        ),
        totalNetAddedLines,
        files: [...files].sort(),
        evidence: dedupeStrings(session.evidence).slice(0, 6)
      } satisfies AgentSessionSummary;
    })
    .sort((left, right) => right.endedAtIso.localeCompare(left.endedAtIso))
    .slice(0, 12);
}

function toDashboardRecordPreview(
  record: ProvenanceRecord,
  risk: RiskAssessment,
  agentContext: AgentContext | null,
  model: string
): DashboardRecordPreview {
  return {
    uuid: record.uuid,
    filePath: record.file.path,
    timestampIso: record.timestampIso,
    model: model || null,
    promptStatus: record.promptStatus,
    riskScore: risk.score,
    riskLevel: risk.level,
    summary: risk.reasons[0] ?? 'No strong risk signals detected.',
    toolName: agentContext?.toolName ?? null,
    provider: agentContext?.provider ?? null,
    adapterName: agentContext?.adapterName ?? null,
    adapterConfidence: agentContext?.confidence ?? null,
    captureStatus: record.correlation.captureStatus ?? record.metadata.captureStatus ?? null
  };
}

function getStoredOrComputedRisk(record: ProvenanceRecord): RiskAssessment {
  const metadata = record.metadata ?? {};
  const stored = metadata.riskAssessment;
  if (isStoredRiskAssessment(stored)) {
    return {
      score: stored.score,
      level: stored.level,
      reasons: [...stored.reasons],
      categories: [...stored.categories],
      signalCount: stored.signalCount
    };
  }

  return assessProvenanceRisk(record);
}

function getStoredOrComputedAgentContext(record: ProvenanceRecord): AgentContext | null {
  const metadata = record.metadata ?? {};
  const stored = metadata.agentContext;
  if (isStoredAgentContext(stored)) {
    return normalizeAgentContextRecord(stored, record);
  }

  const derived = deriveAgentContext({
    timestampIso: record.timestampIso,
    correlation: record.correlation,
    modelName: record.prompt.modelName
  });

  return normalizeAgentContextRecord(derived, record);
}

function isStoredRiskAssessment(value: unknown): value is RiskAssessment {
  if (!isRecord(value)) {
    return false;
  }

  return (
    typeof value.score === 'number' &&
    isRiskLevel(value.level) &&
    Array.isArray(value.reasons) &&
    Array.isArray(value.categories) &&
    typeof value.signalCount === 'number'
  );
}

function isStoredAgentContext(value: unknown): value is AgentContext {
  if (!isRecord(value)) {
    return false;
  }

  return (
    (typeof value.toolName === 'string' || value.toolName === null) &&
    (typeof value.provider === 'string' || value.provider === null) &&
    isAgentSessionKind(value.sessionKind) &&
    (typeof value.host === 'string' || value.host === null) &&
    (typeof value.userAgent === 'string' || value.userAgent === null) &&
    (typeof value.modelName === 'string' || value.modelName === null) &&
    typeof value.sessionSignature === 'string' &&
    typeof value.detectedAtIso === 'string'
  );
}

function normalizeAgentContextRecord(
  value: Partial<AgentContext> | null,
  record: ProvenanceRecord
): AgentContext | null {
  if (!value) {
    return null;
  }

  const modelName = normalizeModelName(value.modelName);
  const sessionKind = isAgentSessionKind(value.sessionKind) ? value.sessionKind : 'unknown';
  const toolName = value.toolName ?? null;
  const provider = value.provider ?? null;
  const sessionId = value.sessionId ?? null;
  const conversationId = value.conversationId ?? null;
  const runId = value.runId ?? null;
  const workspaceHint = value.workspaceHint ?? null;
  const operationType = isOperationType(value.operationType) ? value.operationType : 'unknown';
  const confidence = clampConfidence(typeof value.confidence === 'number' ? value.confidence : 0);
  const evidence = Array.isArray(value.evidence) ? value.evidence : [];
  const adapterName = value.adapterName ?? null;
  const matchSource = value.matchSource === 'adapter' ? 'adapter' : 'heuristic';
  const host = value.host ?? null;
  const userAgent = value.userAgent ?? null;
  const sessionSignature = buildSessionSignature({
    toolName,
    provider,
    modelName,
    sessionKind,
    sessionId,
    conversationId,
    runId
  });

  return {
    toolName,
    provider,
    sessionId,
    conversationId,
    runId,
    workspaceHint,
    operationType,
    confidence,
    evidence: evidence.filter((item): item is AgentEvidence => isRecord(item) && typeof item.field === 'string') as AgentEvidence[],
    adapterName,
    matchSource,
    sessionKind,
    host,
    userAgent,
    modelName: modelName || null,
    sessionSignature,
    detectedAtIso: value.detectedAtIso ?? record.timestampIso
  };
}

function getNativeSessionKey(agentContext: AgentContext): string | null {
  return agentContext.sessionId || agentContext.runId || agentContext.conversationId || null;
}

function isOperationType(value: unknown): value is AgentOperationType {
  return (
    value === 'edit' ||
    value === 'refactor' ||
    value === 'test-fix' ||
    value === 'explain' ||
    value === 'multi-file-run' ||
    value === 'chat' ||
    value === 'unknown'
  );
}

function compareRiskEntries(
  left: { record: ProvenanceRecord; risk: RiskAssessment },
  right: { record: ProvenanceRecord; risk: RiskAssessment }
): number {
  if (left.risk.score !== right.risk.score) {
    return left.risk.score - right.risk.score;
  }

  return left.record.timestampIso.localeCompare(right.record.timestampIso);
}

function averageCorrelationConfidence(records: readonly ProvenanceRecord[]): number {
  const values = records
    .map((record) => toFiniteNumber(record.metadata.correlationConfidence))
    .filter((value): value is number => value !== null);

  if (values.length === 0) {
    return 0;
  }

  const total = values.reduce((sum, value) => sum + value, 0);
  return Number((total / values.length).toFixed(4));
}

function buildControl(
  id: string,
  title: string,
  status: ComplianceStatus,
  metric: string,
  summary: string
): ComplianceControlStatus {
  return {
    id,
    title,
    status,
    summary,
    metric
  };
}

function inferProvider(targetHost: string | null, modelName: string, rawContextBlob: string): string | null {
  if (targetHost?.includes('openai.com') || modelName.includes('gpt')) {
    return 'OpenAI';
  }

  if (targetHost?.includes('anthropic.com') || modelName.includes('claude')) {
    return 'Anthropic';
  }

  if (targetHost?.includes('githubcopilot') || rawContextBlob.includes('copilot')) {
    return 'GitHub';
  }

  if (targetHost?.includes('openrouter.ai')) {
    return 'OpenRouter';
  }

  return null;
}

function normalizeModelName(value: unknown): string {
  if (typeof value === 'string') {
    return value.trim();
  }

  if (value === null || typeof value === 'undefined') {
    return '';
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function safeSerialize(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function findHeaderValue(
  headers: Record<string, string | string[]> | null,
  key: string
): string | null {
  if (!headers) {
    return null;
  }

  const matchingKey = Object.keys(headers).find(
    (headerKey) => headerKey.toLowerCase() === key.toLowerCase()
  );
  if (!matchingKey) {
    return null;
  }

  const value = headers[matchingKey];
  if (typeof value === 'string') {
    return value.trim() || null;
  }

  if (Array.isArray(value) && value.length > 0) {
    return String(value[0]).trim() || null;
  }

  return null;
}

function parseEpoch(value: string | null | undefined): number | null {
  const trimmed = (value ?? '').trim();
  if (!trimmed) {
    return null;
  }

  const parsed = Date.parse(trimmed);
  return Number.isNaN(parsed) ? null : parsed;
}

function percentage(value: number): string {
  return (value * 100).toFixed(1) + '%';
}

function toRiskLevel(score: number): RiskLevel {
  if (score >= CRITICAL_RISK_THRESHOLD) {
    return 'critical';
  }

  if (score >= HIGH_RISK_THRESHOLD) {
    return 'high';
  }

  if (score >= 35) {
    return 'medium';
  }

  return 'low';
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
}

function dedupeStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter((value) => value.trim().length > 0))];
}

function isRecord(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null;
}

function isRiskLevel(value: unknown): value is RiskLevel {
  return value === 'low' || value === 'medium' || value === 'high' || value === 'critical';
}

function isAgentSessionKind(value: unknown): value is AgentSessionKind {
  return value === 'agentic' || value === 'assistant' || value === 'unknown';
}
