import * as http from 'node:http';
import * as https from 'node:https';
import * as vscode from 'vscode';
import { BackendIngestClient } from '../backend';
import { BackendAuthSession } from '../backendAuth';
import type { ProvenanceRecord } from '../provenance';
import {
  ExplanationResult,
  InsightsDashboardPayload,
  InsightsFilters,
  LineageUpdateResult,
  LoadedProvenancePayload,
  ProvenanceIngestResult,
  ProvenanceSearchFilters,
  ProvenanceSearchResultItem,
  ProvenanceStorageService
} from './StorageService';

const CONFIG_SECTION = 'aiInsertionDetector';
const DEFAULT_BACKEND_BASE_URL = 'http://127.0.0.1:8787';
const DEFAULT_FALLBACK_PROXY_PORT = 8787;
const DEFAULT_VECTOR_SEARCH_PATH = '/search';
const DEFAULT_INSIGHTS_DASHBOARD_PATH = '/insights/dashboard';
const REQUEST_TIMEOUT_MS = 12_000;
const DEFAULT_LIMIT = 50;
const API_VERSION = 'v1';

type BackendResponse = {
  statusCode: number;
  body: string;
};

export class BackendStorageService implements ProvenanceStorageService {
  public readonly mode = 'backend' as const;

  private readonly authSession: BackendAuthSession;
  private readonly ingestClient: BackendIngestClient;

  public constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly log: (message: string) => void
  ) {
    this.authSession = new BackendAuthSession(context, log);
    this.ingestClient = new BackendIngestClient(this.authSession, log);
  }

  public async initialize(resource?: vscode.Uri): Promise<void> {
    await this.ingestClient.initializeAuthentication(resource);
  }

  public async shutdown(): Promise<void> {
    await this.ingestClient.shutdown();
  }

  public dispose(): void {
    void this.shutdown();
  }

  public async handleConfigurationChanged(): Promise<void> {
    await this.ingestClient.handleConfigurationChanged();
  }

  public async authenticate(resource?: vscode.Uri): Promise<void> {
    await this.ingestClient.loginToBackend(resource);
  }

  public async ingest(record: ProvenanceRecord, resource?: vscode.Uri): Promise<ProvenanceIngestResult> {
    const result = await this.ingestClient.ingestProvenanceRecord(record, resource);

    return {
      uuid: result.uuid,
      transport: result.transport,
      mode: this.mode,
      message:
        'Ingested with ' +
        (result.transport === 'websocket' ? 'WebSocket' : 'HTTP fallback') +
        ' transport.'
    };
  }

  public async getProvenanceByUuid(
    uuid: string,
    resource?: vscode.Uri
  ): Promise<LoadedProvenancePayload> {
    const baseUrl = this.getBackendBaseUrl(resource);
    const record = await this.fetchRecordByUuid(baseUrl, uuid, resource);
    const explanationResult = await this.fetchExplanation(baseUrl, uuid, record, resource);

    return {
      uuid,
      record,
      explanation: explanationResult.explanation,
      explanationError: explanationResult.explanationError,
      sourceLabel: baseUrl,
      fetchedAtIso: new Date().toISOString(),
      mode: this.mode,
      warnings: []
    };
  }

  public async getExplanation(
    uuid: string,
    record: Record<string, unknown>,
    resource?: vscode.Uri
  ): Promise<ExplanationResult> {
    const baseUrl = this.getBackendBaseUrl(resource);
    return this.fetchExplanation(baseUrl, uuid, record, resource);
  }

  public async search(
    filters: ProvenanceSearchFilters,
    resource?: vscode.Uri
  ): Promise<ProvenanceSearchResultItem[]> {
    const backendBaseUrl = this.getBackendBaseUrl(resource);
    const vectorSearchPath = this.getVectorSearchPath(resource);
    const currentFilePath = filters.currentFileOnly ? filters.currentFilePath : undefined;

    const clampedLimit = Math.min(200, Math.max(1, filters.limit ?? DEFAULT_LIMIT));
    const bodyPayload = {
      query: filters.keywords,
      keywords: filters.keywords,
      model: filters.model || undefined,
      dateFrom: filters.dateFrom || undefined,
      dateTo: filters.dateTo || undefined,
      filePath: currentFilePath,
      currentFile: currentFilePath,
      filters: {
        model: filters.model || undefined,
        dateFrom: filters.dateFrom || undefined,
        dateTo: filters.dateTo || undefined,
        filePath: currentFilePath
      },
      limit: clampedLimit,
      topK: clampedLimit
    };

    const searchUrl = joinUrl(backendBaseUrl, vectorSearchPath);
    const response = await this.requestJson('POST', searchUrl, resource, bodyPayload);

    if (response.statusCode >= 200 && response.statusCode < 300) {
      return parseSearchResults(response.body).map((item) => ({
        ...item,
        mode: this.mode
      }));
    }

    this.log(
      'Provenance search endpoint ' + searchUrl + ' returned status ' + String(response.statusCode) + '.'
    );
    throw new Error(
      'Vector similarity search failed with status ' + String(response.statusCode) + ' at ' + searchUrl + '.'
    );
  }

  public async getInsightsDashboard(
    filters: InsightsFilters,
    resource?: vscode.Uri
  ): Promise<InsightsDashboardPayload> {
    const backendBaseUrl = this.getBackendBaseUrl(resource);
    const dashboardUrl = joinUrl(backendBaseUrl, DEFAULT_INSIGHTS_DASHBOARD_PATH);
    const response = await this.requestJson('POST', dashboardUrl, resource, {
      dateFrom: filters.dateFrom || undefined,
      dateTo: filters.dateTo || undefined,
      currentFile: filters.currentFileOnly ? filters.currentFilePath || undefined : undefined,
      filePath: filters.currentFileOnly ? filters.currentFilePath || undefined : undefined
    });

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw new Error(
        'Insights dashboard request failed with status ' + String(response.statusCode) + '.'
      );
    }

    const parsed = parseJson(response.body);
    if (!isRecord(parsed)) {
      throw new Error('Insights dashboard response was not a JSON object.');
    }

    return parsed as InsightsDashboardPayload;
  }

  public async updateLineageFromLatestCommit(): Promise<LineageUpdateResult> {
    return {
      mode: this.mode,
      commitHash: 'n/a',
      parentCommitHash: null,
      filesChanged: 0,
      recordsUpdated: 0,
      message:
        'Backend mode uses server-side lineage graph processing. Local lineage refresh is not required.'
    };
  }

  public getModeWarnings(): string[] {
    return [];
  }

  public async exportAuditCsv(
    filters: { dateFrom?: string; dateTo?: string; developer?: string; filePath?: string },
    resource?: vscode.Uri
  ): Promise<string | null> {
    const baseUrl = this.getBackendBaseUrl(resource);
    const params = new URLSearchParams();
    if (filters.dateFrom) params.set('dateFrom', filters.dateFrom);
    if (filters.dateTo) params.set('dateTo', filters.dateTo);
    if (filters.developer) params.set('developer', filters.developer);
    if (filters.filePath) params.set('filePath', filters.filePath);

    const qs = params.toString();
    const url = joinUrl(baseUrl, '/export/audit') + (qs ? '?' + qs : '');
    const response = await this.requestJson('GET', url, resource);

    if (response.statusCode >= 200 && response.statusCode < 300) {
      return response.body;
    }

    throw new Error('Export failed: backend returned ' + String(response.statusCode));
  }

  private async fetchRecordByUuid(
    baseUrl: string,
    uuid: string,
    resource?: vscode.Uri
  ): Promise<Record<string, unknown>> {
    const pathStyleUrl = joinUrl(baseUrl, '/provenance/' + encodeURIComponent(uuid));

    const pathStyleResponse = await this.requestJson('GET', pathStyleUrl, resource);
    if (pathStyleResponse.statusCode >= 200 && pathStyleResponse.statusCode < 300) {
      return extractRecordObject(pathStyleResponse.body);
    }

    if (pathStyleResponse.statusCode === 404) {
      throw new Error('Provenance record not found for UUID ' + uuid + '.');
    }

    throw new Error(
      'Failed to fetch provenance record for UUID ' +
        uuid +
        '. Backend response status: ' +
        String(pathStyleResponse.statusCode) +
        '.'
    );
  }

  public async flagRecord(uuid: string, reason?: string, resource?: vscode.Uri): Promise<void> {
    const targetUuid = uuid.trim();
    if (!targetUuid) {
      throw new Error('Record UUID is required.');
    }

    const workspaceId = await this.authSession.getWorkspaceId(resource, true);
    if (!workspaceId) {
      throw new Error('Backend authentication is required. Run AI Insertion Detector: Backend Login.');
    }

    const baseUrl = this.getBackendBaseUrl(resource);
    const endpointUrl = joinUrl(baseUrl, '/provenance/' + encodeURIComponent(targetUuid) + '/tags');
    const response = await this.requestJson(
      'POST',
      endpointUrl,
      resource,
      {
        tags: this.buildFlagTags(reason),
        workspaceId
      }
    );

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw new Error('Flag request failed with status ' + String(response.statusCode) + ' at ' + endpointUrl + '.');
    }
  }

  public async addRecordToReviewQueue(
    uuid: string,
    notes?: string,
    resource?: vscode.Uri
  ): Promise<void> {
    const targetUuid = uuid.trim();
    if (!targetUuid) {
      throw new Error('Record UUID is required.');
    }

    const workspaceId = await this.authSession.getWorkspaceId(resource, true);
    if (!workspaceId) {
      throw new Error('Backend authentication is required. Run AI Insertion Detector: Backend Login.');
    }

    const baseUrl = this.getBackendBaseUrl(resource);
    const endpointUrl = joinUrl(baseUrl, '/reviews');
    const trimmedNotes = notes?.trim() ?? '';
    const response = await this.requestJson(
      'POST',
      endpointUrl,
      resource,
      {
        workspaceId,
        recordUuid: targetUuid,
        notes: trimmedNotes.length > 0 ? trimmedNotes : undefined
      }
    );

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw new Error('Review queue request failed with status ' + String(response.statusCode) + ' at ' + endpointUrl + '.');
    }
  }

  private async fetchExplanation(
    baseUrl: string,
    uuid: string,
    record: Record<string, unknown>,
    resource?: vscode.Uri
  ): Promise<ExplanationResult> {
    const explainUrl = joinUrl(baseUrl, '/explain');

    const postResponse = await this.requestJson(
      'POST',
      explainUrl,
      resource,
      {
        uuid,
        record
      }
    );

    if (postResponse.statusCode >= 200 && postResponse.statusCode < 300) {
      return {
        explanation: extractExplanationText(postResponse.body),
        explanationError: null
      };
    }

    const errorMessage =
      'Explanation endpoint returned status ' + String(postResponse.statusCode) + ' at ' + explainUrl + '.';

    this.log('Provenance explanation error: ' + errorMessage);

    return {
      explanation: null,
      explanationError: errorMessage
    };
  }

  private async requestJson(
    method: 'GET' | 'POST',
    endpointUrl: string,
    resource?: vscode.Uri,
    payload?: unknown
  ): Promise<BackendResponse> {
    const targetUrl = new URL(endpointUrl);
    const body = payload === undefined ? undefined : JSON.stringify(payload);

    const headers: Record<string, string> = {
      Accept: 'application/json, text/plain;q=0.9,*/*;q=0.8',
      'X-API-Version': API_VERSION
    };

    const authorizationHeader = await this.authSession.getAuthorizationHeader(resource, true);
    if (!authorizationHeader) {
      throw new Error(
        'Backend authentication is required. Run AI Insertion Detector: Backend Login.'
      );
    }

    headers.Authorization = authorizationHeader;

    if (typeof body === 'string') {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = String(Buffer.byteLength(body));
    }

    const requestOptions: http.RequestOptions = {
      protocol: targetUrl.protocol,
      hostname: targetUrl.hostname,
      port: targetUrl.port.length > 0 ? Number(targetUrl.port) : undefined,
      path: targetUrl.pathname + targetUrl.search,
      method,
      headers,
      timeout: REQUEST_TIMEOUT_MS
    };

    const transport = targetUrl.protocol === 'https:' ? https : http;

    return await new Promise<BackendResponse>((resolve, reject) => {
      const request = transport.request(requestOptions, (response) => {
        const chunks: Buffer[] = [];

        response.on('data', (chunk: Buffer | string) => {
          chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
        });

        response.on('end', () => {
          resolve({
            statusCode: response.statusCode ?? 0,
            body: Buffer.concat(chunks).toString('utf8')
          });
        });
      });

      request.on('timeout', () => {
        request.destroy(new Error('Request timeout after ' + String(REQUEST_TIMEOUT_MS) + 'ms.'));
      });

      request.on('error', (error: Error) => {
        reject(error);
      });

      if (typeof body === 'string') {
        request.write(body);
      }

      request.end();
    });
  }

  private getBackendBaseUrl(resource?: vscode.Uri): string {
    const config = vscode.workspace.getConfiguration(CONFIG_SECTION, resource);
    const configuredBaseUrl = config.get<string>('backend.baseUrl', DEFAULT_BACKEND_BASE_URL) ?? '';

    const trimmedConfiguredBaseUrl = configuredBaseUrl.trim();
    if (trimmedConfiguredBaseUrl.length > 0) {
      return trimmedConfiguredBaseUrl.replace(/\/$/, '');
    }

    const fallbackPort = Math.max(
      1,
      config.get<number>('proxyPort', DEFAULT_FALLBACK_PROXY_PORT) ?? DEFAULT_FALLBACK_PROXY_PORT
    );

    return 'http://127.0.0.1:' + String(fallbackPort);
  }

  private getVectorSearchPath(resource?: vscode.Uri): string {
    const config = vscode.workspace.getConfiguration(CONFIG_SECTION, resource);
    const configuredPath =
      config.get<string>('backend.vectorSearchPath', DEFAULT_VECTOR_SEARCH_PATH) ??
      DEFAULT_VECTOR_SEARCH_PATH;

    const trimmedPath = configuredPath.trim();
    if (trimmedPath.length === 0) {
      return DEFAULT_VECTOR_SEARCH_PATH;
    }

    return trimmedPath.startsWith('/') ? trimmedPath : '/' + trimmedPath;
  }

  private buildFlagTags(reason?: string): string[] {
    const tags = ['flagged'];
    const normalizedReason = (reason ?? '').trim().toLowerCase().replace(/\s+/g, '-').slice(0, 64);

    if (normalizedReason.length > 0 && normalizedReason !== 'flagged') {
      tags.push(normalizedReason);
    }

    return tags;
  }
}

function parseSearchResults(rawBody: string): Array<Omit<ProvenanceSearchResultItem, 'mode'>> {
  const parsed = parseJson(rawBody);

  const candidates = extractResultsArray(parsed);
  const results: Array<Omit<ProvenanceSearchResultItem, 'mode'>> = [];

  for (const candidate of candidates) {
    if (!isRecord(candidate)) {
      continue;
    }

    const uuid =
      extractUuidFromText(toStringValue(candidate.uuid)) ??
      extractUuidFromText(toStringValue(candidate.requestUuid)) ??
      extractUuidFromText(toStringValue(candidate.id)) ??
      extractUuidFromText(toStringValue(getAtPath(candidate, ['record', 'uuid']))) ??
      extractUuidFromText(toStringValue(getAtPath(candidate, ['record', 'id'])));

    if (!uuid) {
      continue;
    }

    const filePath =
      sanitizeFilePath(toStringValue(candidate.filePath)) ??
      sanitizeFilePath(toStringValue(candidate.path)) ??
      sanitizeFilePath(toStringValue(getAtPath(candidate, ['file', 'path']))) ??
      sanitizeFilePath(toStringValue(getAtPath(candidate, ['record', 'file', 'path'])));

    const score =
      toFiniteNumber(candidate.score) ??
      toFiniteNumber(candidate.similarity) ??
      toFiniteNumber(candidate.distance);

    const model =
      sanitizeNullableString(toStringValue(candidate.model)) ??
      sanitizeNullableString(toStringValue(getAtPath(candidate, ['record', 'prompt', 'modelName']))) ??
      sanitizeNullableString(toStringValue(getAtPath(candidate, ['record', 'provenance', 'modelName'])));

    const timestampIso =
      sanitizeNullableString(toStringValue(candidate.timestampIso)) ??
      sanitizeNullableString(toStringValue(candidate.timestamp)) ??
      sanitizeNullableString(toStringValue(getAtPath(candidate, ['record', 'timestampIso']))) ??
      sanitizeNullableString(toStringValue(getAtPath(candidate, ['record', 'insertionTimestampIso'])));

    const snippet =
      sanitizeSnippet(toStringValue(candidate.snippet)) ||
      sanitizeSnippet(toStringValue(candidate.summary)) ||
      sanitizeSnippet(toStringValue(candidate.text)) ||
      sanitizeSnippet(
        toStringValue(getAtPath(candidate, ['record', 'insertion', 'extractedInsertedCodeBlock']))
      ) ||
      sanitizeSnippet(toStringValue(getAtPath(candidate, ['record', 'insertedText']))) ||
      '(no snippet)';

    const fullRecord = isRecord(candidate.record) ? candidate.record : undefined;

    results.push({
      uuid,
      score,
      model,
      timestampIso,
      filePath,
      snippet,
      record: fullRecord
    });
  }

  results.sort((left, right) => {
    const leftScore = typeof left.score === 'number' ? left.score : Number.NEGATIVE_INFINITY;
    const rightScore = typeof right.score === 'number' ? right.score : Number.NEGATIVE_INFINITY;

    if (leftScore !== rightScore) {
      return rightScore - leftScore;
    }

    const leftTime = Date.parse(left.timestampIso ?? '');
    const rightTime = Date.parse(right.timestampIso ?? '');

    if (!Number.isNaN(leftTime) && !Number.isNaN(rightTime) && leftTime !== rightTime) {
      return rightTime - leftTime;
    }

    return left.uuid.localeCompare(right.uuid);
  });

  return results;
}

function extractRecordObject(rawBody: string): Record<string, unknown> {
  if (rawBody.trim().length === 0) {
    throw new Error('Empty provenance record response body.');
  }

  const parsed = parseJson(rawBody);

  if (isRecord(parsed)) {
    if (isRecord(parsed.record)) {
      return parsed.record;
    }

    if (isRecord(parsed.data)) {
      return parsed.data;
    }

    return parsed;
  }

  throw new Error('Provenance response did not contain a JSON object record.');
}

function extractExplanationText(rawBody: string): string {
  if (rawBody.trim().length === 0) {
    return '';
  }

  const parsed = parseJson(rawBody);

  if (typeof parsed === 'string') {
    return parsed;
  }

  if (isRecord(parsed)) {
    const explanationCandidate =
      parsed.explanation ?? parsed.explain ?? parsed.summary ?? parsed.result ?? parsed.text;

    if (typeof explanationCandidate === 'string') {
      return explanationCandidate;
    }

    return JSON.stringify(parsed, null, 2);
  }

  return rawBody;
}

function extractResultsArray(parsed: unknown): unknown[] {
  if (Array.isArray(parsed)) {
    return parsed;
  }

  if (!isRecord(parsed)) {
    return [];
  }

  const arrayCandidates = [parsed.results, parsed.matches, parsed.hits, parsed.data, parsed.items];

  for (const candidate of arrayCandidates) {
    if (Array.isArray(candidate)) {
      return candidate;
    }
  }

  if (isRecord(parsed.data)) {
    const nestedCandidates = [
      parsed.data.results,
      parsed.data.matches,
      parsed.data.hits,
      parsed.data.items
    ];

    for (const nestedCandidate of nestedCandidates) {
      if (Array.isArray(nestedCandidate)) {
        return nestedCandidate;
      }
    }
  }

  return [];
}

function sanitizeFilePath(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function sanitizeSnippet(value: string): string {
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    return '';
  }

  return trimmed.length > 700 ? trimmed.slice(0, 700) + ' ...' : trimmed;
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

function sanitizeNullableString(value: string): string | null {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function toStringValue(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }

  return '';
}

function parseJson(rawBody: string): unknown {
  try {
    return JSON.parse(rawBody) as unknown;
  } catch {
    return rawBody;
  }
}

function joinUrl(baseUrl: string, relativePath: string): string {
  const trimmedBase = baseUrl.replace(/\/$/, '');
  const normalizedRelative = relativePath.startsWith('/') ? relativePath : '/' + relativePath;
  return trimmedBase + normalizedRelative;
}

function getAtPath(source: unknown, pathSegments: string[]): unknown {
  let cursor: unknown = source;

  for (const segment of pathSegments) {
    if (!isRecord(cursor) || !(segment in cursor)) {
      return undefined;
    }

    cursor = cursor[segment];
  }

  return cursor;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

const UUID_PATTERN = /\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/i;

function extractUuidFromText(text: string): string | undefined {
  const match = UUID_PATTERN.exec(text);
  return match ? match[0].toLowerCase() : undefined;
}
