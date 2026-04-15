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
};

export type ProvenanceSearchResultItem = {
  uuid: string;
  score: number | null;
  model: string | null;
  timestampIso: string | null;
  filePath: string | null;
  snippet: string;
  mode: ProvenanceMode;
};

export type LineageUpdateResult = {
  mode: ProvenanceMode;
  commitHash: string;
  parentCommitHash: string | null;
  filesChanged: number;
  recordsUpdated: number;
  message: string;
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

  updateLineageFromLatestCommit(resource?: vscode.Uri): Promise<LineageUpdateResult>;
  getModeWarnings(): string[];
}

export function getConfiguredMode(resource?: vscode.Uri): ProvenanceMode {
  const config = vscode.workspace.getConfiguration('aiCodeProvenance', resource);
  const value = (config.get<string>('mode', 'local') ?? '').trim().toLowerCase();

  return value === 'backend' ? 'backend' : 'local';
}
