import * as vscode from 'vscode';
import type { ProvenanceRecord } from '../provenance';

export type ProvenanceMode = 'local' | 'backend';

export type ProvenanceIngestResult = {
  uuid: string;
  transport: 'local-json' | 'websocket' | 'http';
  mode: ProvenanceMode;
  message?: string;
  warnings?: string[];
};

export type LoadedProvenancePayload = {
  uuid: string;
  record: Record<string, unknown>;
  explanation: string | null;
  explanationError: string | null;
  sourceLabel: string;
  fetchedAtIso: string;
  mode: ProvenanceMode;
  warnings: string[];
};

export type ExplanationResult = {
  explanation: string | null;
  explanationError: string | null;
};

export type ProvenanceSearchFilters = {
  keywords: string;
  model: string;
  dateFrom: string;
  dateTo: string;
  currentFileOnly: boolean;
  currentFilePath?: string;
  limit?: number;
};

export type ProvenanceSearchResultItem = {
  uuid: string;
  score: number | null;
  model: string | null;
  timestampIso: string | null;
  filePath: string | null;
  snippet: string;
  mode: ProvenanceMode;
  record?: Record<string, unknown>;
};

export type LineageUpdateResult = {
  mode: ProvenanceMode;
  commitHash: string;
  parentCommitHash: string | null;
  filesChanged: number;
  recordsUpdated: number;
  message: string;
};

export type InsightsFilters = {
  dateFrom: string;
  dateTo: string;
  currentFileOnly: boolean;
  currentFilePath?: string;
};

export type ComplianceControlStatus = {
  id: string;
  title: string;
  status: 'pass' | 'warning' | 'fail';
  summary: string;
  metric: string;
};

export type DashboardRecordPreview = {
  uuid: string;
  filePath: string;
  timestampIso: string;
  model: string | null;
  promptStatus: 'captured' | 'not-captured' | 'partial';
  riskScore: number;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
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
  sessionKind: 'agentic' | 'assistant' | 'cli' | 'unknown';
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
  netAddedLines: number;
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

export interface ProvenanceStorageService extends vscode.Disposable {
  readonly mode: ProvenanceMode;

  initialize(resource?: vscode.Uri): Promise<void>;
  shutdown(): Promise<void>;
  handleConfigurationChanged(resource?: vscode.Uri): Promise<void>;
  authenticate(resource?: vscode.Uri): Promise<void>;

  ingest(record: ProvenanceRecord, resource?: vscode.Uri): Promise<ProvenanceIngestResult>;
  getProvenanceByUuid(uuid: string, resource?: vscode.Uri): Promise<LoadedProvenancePayload>;
  getExplanation(
    uuid: string,
    record: Record<string, unknown>,
    resource?: vscode.Uri
  ): Promise<ExplanationResult>;
  search(
    filters: ProvenanceSearchFilters,
    resource?: vscode.Uri
  ): Promise<ProvenanceSearchResultItem[]>;
  getInsightsDashboard(
    filters: InsightsFilters,
    resource?: vscode.Uri
  ): Promise<InsightsDashboardPayload>;

  updateLineageFromLatestCommit(resource?: vscode.Uri): Promise<LineageUpdateResult>;
  getModeWarnings(): string[];

  exportAuditCsv(
    filters: { dateFrom?: string; dateTo?: string; developer?: string; filePath?: string },
    resource?: vscode.Uri
  ): Promise<string | null>;
}

export function getConfiguredMode(resource?: vscode.Uri): ProvenanceMode {
  const config = vscode.workspace.getConfiguration('aiCodeProvenance', resource);
  const value = (config.get<string>('mode', 'local') ?? '').trim().toLowerCase();

  return value === 'backend' ? 'backend' : 'local';
}
