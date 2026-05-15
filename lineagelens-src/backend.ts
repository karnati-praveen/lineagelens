import * as http from 'node:http';
import * as https from 'node:https';
import * as vscode from 'vscode';
import WebSocket, { RawData } from 'ws';
import { BackendAuthSession } from './backendAuth';
import type { ProvenanceRecord } from './provenance';

const CONFIG_SECTION = 'aiInsertionDetector';

const DEFAULT_BACKEND_BASE_URL = 'http://127.0.0.1:8787';
const DEFAULT_BACKEND_WEBSOCKET_URL = 'ws://127.0.0.1:8787/ws/capture';
const DEFAULT_BACKEND_INGEST_PATH = '/ingest';

const DEFAULT_WEBSOCKET_RETRY_ATTEMPTS = 2;
const DEFAULT_HTTP_RETRY_ATTEMPTS = 2;

const REQUEST_TIMEOUT_MS = 12_000;
const WEBSOCKET_CONFIRMATION_TIMEOUT_MS = 8_000;
const RETRY_DELAY_BASE_MS = 300;

const API_VERSION = 'v1';
const MAX_PENDING_CONFIRMATIONS = 50;

export type BackendIngestResult = {
  uuid: string;
  transport: 'websocket' | 'http';
};

type BackendIngestConfig = {
  baseUrl: string;
  websocketUrl: string;
  ingestPath: string;
  websocketRetryAttempts: number;
  httpRetryAttempts: number;
};

type JsonRequestResult = {
  statusCode: number;
  body: string;
};

type PendingConfirmation = {
  resolve: () => void;
  reject: (error: Error) => void;
  timeoutHandle: NodeJS.Timeout;
};

export class BackendIngestClient implements vscode.Disposable {
  private websocket: WebSocket | undefined;
  private websocketEndpoint: string | undefined;
  private websocketToken: string | undefined;
  private connectingPromise: Promise<WebSocket> | undefined;
  private readonly pendingConfirmations = new Map<string, PendingConfirmation>();
  private readonly sessionTraceId: string = generateTraceId();
  private _healthChecked = false;
  private _backendHealthy = false;
  private _offlineQueue: Array<{ payload: unknown; timestamp: number }> = [];
  private readonly OFFLINE_QUEUE_MAX = 100;

  public constructor(
    private readonly authSession: BackendAuthSession,
    private readonly log: (message: string) => void
  ) {}

  public async initializeAuthentication(resource?: vscode.Uri): Promise<void> {
    await this.authSession.initializeAuthentication(resource);
    // Fire-and-forget health check on startup so the first ingest doesn't block.
    void this.checkBackendHealth(resource).then((healthy) => {
      if (!healthy) {
        vscode.window.showWarningMessage(
          'LineageLens: backend is unreachable at startup. Events will be queued until it recovers.'
        );
      }
    });
  }

  public async loginToBackend(resource?: vscode.Uri): Promise<void> {
    await this.authSession.login(resource);
  }

  public async ingestProvenanceRecord(
    record: ProvenanceRecord,
    resource?: vscode.Uri
  ): Promise<BackendIngestResult> {
    if (!this._healthChecked) {
      const healthy = await this.checkBackendHealth(resource);
      if (!healthy) {
        const config = getBackendIngestConfig(resource);
        const workspaceId = await this.authSession.getWorkspaceId(resource, false);
        const ingestPayload = toBackendIngestPayload(record, workspaceId);
        this.enqueueOffline(ingestPayload);
        vscode.window.showWarningMessage(
          'LineageLens: backend is unreachable. Event queued for later delivery.'
        );
        throw new Error('Backend health check failed; event queued offline.');
      }
    }

    const config = getBackendIngestConfig(resource);
    const workspaceId = await this.authSession.getWorkspaceId(resource, true);

    const ingestPayload = toBackendIngestPayload(record, workspaceId);

    // Attempt to flush any previously queued events before sending the new one.
    if (this._offlineQueue.length > 0) {
      await this.flushOfflineQueue(resource);
    }

    try {
      const websocketResult = await this.tryWebSocketIngestWithRetry(
        ingestPayload,
        resource,
        config
      );
      if (websocketResult) {
        return websocketResult;
      }

      return await this.tryHttpIngestWithRetry(ingestPayload, resource, config);
    } catch (error: unknown) {
      const message = toErrorMessage(error);
      const isNetworkError =
        message.includes('ECONNREFUSED') ||
        message.includes('ENOTFOUND') ||
        message.includes('ETIMEDOUT') ||
        message.includes('timed out') ||
        message.includes('network');

      if (isNetworkError) {
        this._backendHealthy = false;
        this.enqueueOffline(ingestPayload);
        throw new Error('Network error during ingest; event queued offline. ' + message);
      }

      throw error;
    }
  }

  public async handleConfigurationChanged(): Promise<void> {
    await this.closeWebSocket();
  }

  public dispose(): void {
    void this.closeWebSocket();
  }

  public async shutdown(): Promise<void> {
    await this.closeWebSocket();
  }

  public async checkBackendHealth(resource?: vscode.Uri): Promise<boolean> {
    const config = getBackendIngestConfig(resource);
    const healthUrl = joinUrl(config.baseUrl, '/health');
    try {
      const result = await requestJson('GET', healthUrl, {});
      this._backendHealthy = result.statusCode >= 200 && result.statusCode < 300;
      this._healthChecked = true;
      if (!this._backendHealthy) {
        this.log('Backend health check failed with status ' + String(result.statusCode) + '.');
      }
      return this._backendHealthy;
    } catch (error: unknown) {
      this._backendHealthy = false;
      this._healthChecked = true;
      this.log('Backend health check error: ' + toErrorMessage(error));
      return false;
    }
  }

  private enqueueOffline(payload: unknown): void {
    if (this._offlineQueue.length >= this.OFFLINE_QUEUE_MAX) {
      this._offlineQueue.shift();
    }
    this._offlineQueue.push({ payload, timestamp: Date.now() });
    vscode.window.setStatusBarMessage(
      'LineageLens: ' + String(this._offlineQueue.length) + ' event(s) queued (backend offline)',
      8000
    );
    this.log(
      'Offline queue: ' + String(this._offlineQueue.length) + ' event(s) pending (backend unreachable).'
    );
  }

  public async flushOfflineQueue(resource?: vscode.Uri): Promise<void> {
    if (this._offlineQueue.length === 0) {
      return;
    }
    const queue = [...this._offlineQueue];
    this._offlineQueue = [];
    for (const item of queue) {
      try {
        const config = getBackendIngestConfig(resource);
        await this.tryHttpIngestWithRetry(
          item.payload as Record<string, unknown>,
          resource,
          config
        );
      } catch {
        this._offlineQueue.unshift(item);
        break;
      }
    }
    if (this._offlineQueue.length === 0) {
      this.log('Offline queue flushed successfully.');
    } else {
      this.log(
        'Offline queue partially flushed; ' + String(this._offlineQueue.length) + ' event(s) remain.'
      );
    }
  }

  private async tryWebSocketIngestWithRetry(
    payload: Record<string, unknown>,
    resource: vscode.Uri | undefined,
    config: BackendIngestConfig
  ): Promise<BackendIngestResult | undefined> {
    const payloadUuid = String(payload.id ?? '').trim();
    if (!payloadUuid) {
      throw new Error('Ingest payload is missing id/uuid.');
    }

    for (let attempt = 1; attempt <= config.websocketRetryAttempts; attempt += 1) {
      try {
        const authorizationHeader = await this.authSession.getAuthorizationHeader(
          resource,
          false,
          false
        );

        if (!authorizationHeader) {
          throw new Error('No valid backend access token available for websocket ingest.');
        }

        const bearerToken = extractBearerToken(authorizationHeader);
        const socket = await this.ensureWebSocketConnection(
          config.websocketUrl,
          bearerToken,
          authorizationHeader
        );

        const confirmationPromise = this.waitForConfirmation(payloadUuid, WEBSOCKET_CONFIRMATION_TIMEOUT_MS);

        await new Promise<void>((resolve, reject) => {
          socket.send(JSON.stringify({ type: 'ingest', payload }), (error?: Error) => {
            if (error) {
              reject(error);
              return;
            }

            resolve();
          });
        });

        await confirmationPromise;

        return {
          uuid: payloadUuid,
          transport: 'websocket'
        };
      } catch (error: unknown) {
        this.log(
          'WebSocket ingest attempt ' +
            String(attempt) +
            '/' +
            String(config.websocketRetryAttempts) +
            ' failed: ' +
            toErrorMessage(error)
        );

        await this.closeWebSocket();

        if (attempt < config.websocketRetryAttempts) {
          await sleep(RETRY_DELAY_BASE_MS * attempt);
        }
      }
    }

    return undefined;
  }

  private async tryHttpIngestWithRetry(
    payload: Record<string, unknown>,
    resource: vscode.Uri | undefined,
    config: BackendIngestConfig
  ): Promise<BackendIngestResult> {
    const ingestUrl = joinUrl(config.baseUrl, config.ingestPath);
    const idempotencyKey = String(payload.id ?? '').trim();
    let lastErrorMessage = 'unknown error';

    for (let attempt = 1; attempt <= config.httpRetryAttempts; attempt += 1) {
      try {
        const authorizationHeader = await this.authSession.getAuthorizationHeader(
          resource, false, false
        );

        if (!authorizationHeader) {
          throw new Error('No valid backend access token available for HTTP ingest.');
        }

        const headers: Record<string, string> = {
          Authorization: authorizationHeader,
          'Content-Type': 'application/json',
          Accept: 'application/json',
          'X-API-Version': API_VERSION,
          'X-Trace-ID': this.sessionTraceId,
        };

        if (idempotencyKey) {
          headers['X-Idempotency-Key'] = idempotencyKey;
        }

        const response = await requestJson('POST', ingestUrl, headers, payload);

        const result = extractIngestResult(response, payload);
        if (result.success) {
          return result.value;
        }
        lastErrorMessage = result.errorMessage;
      } catch (error: unknown) {
        lastErrorMessage = toErrorMessage(error);
      }

      if (attempt < config.httpRetryAttempts) {
        await sleep(RETRY_DELAY_BASE_MS * attempt);
      }
    }

    throw new Error('HTTP fallback ingest failed: ' + lastErrorMessage);
  }

  private async ensureWebSocketConnection(
    websocketUrl: string,
    token: string,
    authorizationHeader: string
  ): Promise<WebSocket> {
    if (
      this.websocket &&
      this.websocket.readyState === WebSocket.OPEN &&
      this.websocketEndpoint === websocketUrl &&
      this.websocketToken === token
    ) {
      return this.websocket;
    }

    if (this.connectingPromise) {
      return this.connectingPromise;
    }

    this.connectingPromise = this.openWebSocketConnection(
      websocketUrl,
      token,
      authorizationHeader
    ).finally(() => {
      this.connectingPromise = undefined;
    });

    return this.connectingPromise;
  }

  private async openWebSocketConnection(
    websocketUrl: string,
    token: string,
    authorizationHeader: string
  ): Promise<WebSocket> {
    await this.closeWebSocket();

    const socket = await new Promise<WebSocket>((resolve, reject) => {
      const instance = new WebSocket(
        websocketUrl,
        [],
        {
          headers: {
            Authorization: authorizationHeader
          }
        }
      );

      let settled = false;

      instance.once('open', () => {
        settled = true;
        resolve(instance);
      });

      instance.once('error', (error: Error) => {
        if (!settled) {
          settled = true;
          reject(error);
        }
      });

      instance.once('close', (code: number, reason: Buffer) => {
        if (!settled) {
          settled = true;
          reject(
            new Error(
              'WebSocket closed before opening (code=' +
                String(code) +
                ', reason=' +
                reason.toString('utf-8') +
                ').'
            )
          );
        }
      });
    });

    this.websocket = socket;
    this.websocketEndpoint = websocketUrl;
    this.websocketToken = token;

    socket.on('message', (data: RawData) => {
      this.handleWebSocketMessage(data);
    });

    socket.on('close', (code: number, reason: Buffer) => {
      if (this.websocket === socket) {
        this.websocket = undefined;
        this.websocketEndpoint = undefined;
        this.websocketToken = undefined;
      }

      this.rejectAllPendingConfirmations(
        new Error(
          'WebSocket closed (code=' +
            String(code) +
            ', reason=' +
            reason.toString('utf-8') +
            ').'
        )
      );
    });

    socket.on('error', (error: Error) => {
      this.log('WebSocket runtime error: ' + error.message);
    });

    return socket;
  }

  private async closeWebSocket(): Promise<void> {
    this.rejectAllPendingConfirmations(new Error('WebSocket connection reset.'));

    if (!this.websocket) {
      this.websocketEndpoint = undefined;
      this.websocketToken = undefined;
      return;
    }

    const socket = this.websocket;
    this.websocket = undefined;
    this.websocketEndpoint = undefined;
    this.websocketToken = undefined;

    if (socket.readyState === WebSocket.CLOSED) {
      return;
    }

    await new Promise<void>((resolve) => {
      socket.once('close', () => {
        resolve();
      });

      socket.close();

      setTimeout(() => {
        if (socket.readyState !== WebSocket.CLOSED) {
          try {
            socket.terminate();
          } catch {
            // Ignore termination errors during shutdown.
          }
        }

        resolve();
      }, 750);
    });
  }

  private handleWebSocketMessage(data: RawData): void {
    const text = rawDataToString(data);
    if (!text) {
      return;
    }

    const payload = safeJsonParse(text);
    if (!payload || typeof payload !== 'object') {
      return;
    }

    const messageType = toStringValue(readPath(payload, ['type']))?.toLowerCase();
    if (!messageType) {
      return;
    }

    if (messageType === 'capture.connected') {
      this.log('WebSocket capture channel connected.');
      return;
    }

    if (messageType === 'capture.error') {
      const message =
        toStringValue(readPath(payload, ['error'])) ??
        'Backend returned an unspecified capture error.';
      const error = new Error(message);

      const uuid = toStringValue(readPath(payload, ['uuid']));
      if (uuid) {
        this.rejectConfirmation(uuid, error);
      } else {
        this.rejectAllPendingConfirmations(error);
      }
      return;
    }

    if (messageType === 'capture.confirmed') {
      const uuid = toStringValue(readPath(payload, ['uuid']));
      if (!uuid) {
        return;
      }

      this.resolveConfirmation(uuid);
    }
  }

  private waitForConfirmation(uuid: string, timeoutMs: number): Promise<void> {
    const normalizedUuid = uuid.toLowerCase();

    if (this.pendingConfirmations.size >= MAX_PENDING_CONFIRMATIONS) {
      return Promise.reject(
        new Error('Too many pending WebSocket confirmations (' + String(MAX_PENDING_CONFIRMATIONS) + '); dropping ingest for ' + uuid + '.')
      );
    }

    return new Promise<void>((resolve, reject) => {
      const existing = this.pendingConfirmations.get(normalizedUuid);
      if (existing) {
        clearTimeout(existing.timeoutHandle);
        existing.reject(new Error('Superseded by a newer confirmation request for ' + uuid + '.'));
      }

      const timeoutHandle = setTimeout(() => {
        this.pendingConfirmations.delete(normalizedUuid);
        reject(new Error('Timed out waiting for websocket confirmation for ' + uuid + '.'));
      }, timeoutMs);

      this.pendingConfirmations.set(normalizedUuid, {
        resolve: () => {
          this.pendingConfirmations.delete(normalizedUuid);
          clearTimeout(timeoutHandle);
          resolve();
        },
        reject: (error: Error) => {
          this.pendingConfirmations.delete(normalizedUuid);
          clearTimeout(timeoutHandle);
          reject(error);
        },
        timeoutHandle
      });
    });
  }

  private resolveConfirmation(uuid: string): void {
    const pending = this.pendingConfirmations.get(uuid.toLowerCase());
    if (pending) {
      pending.resolve();
    }
  }

  private rejectConfirmation(uuid: string, error: Error): void {
    const pending = this.pendingConfirmations.get(uuid.toLowerCase());
    if (pending) {
      pending.reject(error);
    }
  }

  private rejectAllPendingConfirmations(error: Error): void {
    for (const [uuid, pending] of this.pendingConfirmations.entries()) {
      this.pendingConfirmations.delete(uuid);
      clearTimeout(pending.timeoutHandle);
      pending.reject(error);
    }
  }
}

export function toBackendIngestPayload(
  record: ProvenanceRecord,
  workspaceId?: string
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    id: record.uuid,
    timestampIso: record.timestampIso,
    filePath: record.file.path,
    fileUri: record.file.uri,
    cursor: record.insertion.cursorPosition,
    insertedText: record.insertion.extractedInsertedCodeBlock,
    netAddedLines: record.insertion.netAddedLines,
    surroundingContext: record.insertion.surroundingContext,
    contextSnapshot: record.contextSnapshot,
    provenance: record.correlation,
    prompt: record.prompt,
    astSnapshot: record.astSnapshot,
    embeddings: record.embeddings,
    schemaVersion: record.schemaVersion,
    normalizedEvent: record.normalizedEvent,
    rawData: record.rawData,
    requestUuid: record.requestUuid,
    workspaceId: workspaceId,
    metadata: record.metadata
  };

  return payload;
}

function getBackendIngestConfig(resource?: vscode.Uri): BackendIngestConfig {
  const config = vscode.workspace.getConfiguration(CONFIG_SECTION, resource);

  return {
    baseUrl: normalizeUrl(
      config.get<string>('backend.baseUrl', DEFAULT_BACKEND_BASE_URL),
      DEFAULT_BACKEND_BASE_URL
    ),
    websocketUrl: normalizeUrl(
      config.get<string>('backend.websocketUrl', DEFAULT_BACKEND_WEBSOCKET_URL),
      DEFAULT_BACKEND_WEBSOCKET_URL
    ),
    ingestPath: normalizeRelativePath(
      config.get<string>('backend.ingestPath', DEFAULT_BACKEND_INGEST_PATH),
      DEFAULT_BACKEND_INGEST_PATH
    ),
    websocketRetryAttempts: Math.max(
      1,
      config.get<number>('backend.retry.websocketAttempts', DEFAULT_WEBSOCKET_RETRY_ATTEMPTS)
    ),
    httpRetryAttempts: Math.max(
      1,
      config.get<number>('backend.retry.httpAttempts', DEFAULT_HTTP_RETRY_ATTEMPTS)
    )
  };
}

async function requestJson(
  method: 'GET' | 'POST',
  endpointUrl: string,
  headers: Record<string, string>,
  payload?: unknown
): Promise<JsonRequestResult> {
  const target = new URL(endpointUrl);
  const body = payload === undefined ? undefined : JSON.stringify(payload);

  const requestHeaders: Record<string, string> = {
    ...headers
  };

  if (typeof body === 'string') {
    requestHeaders['Content-Length'] = String(Buffer.byteLength(body));
  }

  const transport = target.protocol === 'https:' ? https : http;

  return await new Promise<JsonRequestResult>((resolve, reject) => {
    const request = transport.request(
      {
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port,
        method,
        path: target.pathname + target.search,
        headers: requestHeaders,
        timeout: REQUEST_TIMEOUT_MS
      },
      (response) => {
        const chunks: Buffer[] = [];

        response.on('data', (chunk: Buffer | string) => {
          chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
        });

        response.on('end', () => {
          resolve({
            statusCode: response.statusCode ?? 0,
            body: Buffer.concat(chunks).toString('utf-8')
          });
        });
      }
    );

    request.on('timeout', () => {
      request.destroy(new Error('Request timed out after ' + String(REQUEST_TIMEOUT_MS) + 'ms.'));
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

function rawDataToString(data: RawData): string {
  if (typeof data === 'string') {
    return data;
  }

  if (Array.isArray(data)) {
    return Buffer.concat(data).toString('utf-8');
  }

  if (data instanceof ArrayBuffer) {
    return Buffer.from(data).toString('utf-8');
  }

  return data.toString('utf-8');
}

function extractBearerToken(authorizationHeader: string): string {
  const normalized = authorizationHeader.trim();
  if (normalized.toLowerCase().startsWith('bearer ')) {
    return normalized.slice(7).trim();
  }

  return normalized;
}

function joinUrl(baseUrl: string, relativePath: string): string {
  const normalizedBase = normalizeUrl(baseUrl, DEFAULT_BACKEND_BASE_URL);
  const normalizedPath = normalizeRelativePath(relativePath, '/');

  return new URL(
    normalizedPath,
    normalizedBase.endsWith('/') ? normalizedBase : normalizedBase + '/'
  ).toString();
}

function normalizeUrl(value: string | undefined, fallback: string): string {
  const candidate = (value ?? '').trim();
  if (!candidate) {
    return fallback;
  }

  try {
    return new URL(candidate).toString();
  } catch {
    return fallback;
  }
}

function normalizeRelativePath(value: string | undefined, fallback: string): string {
  const candidate = (value ?? '').trim();
  if (!candidate) {
    return fallback;
  }

  return candidate.startsWith('/') ? candidate : '/' + candidate;
}

function sleep(durationMs: number): Promise<void> {
  return new Promise<void>((resolve) => {
    setTimeout(resolve, durationMs);
  });
}

function safeJsonParse(value: string): Record<string, unknown> | undefined {
  if (!value) {
    return undefined;
  }

  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : undefined;
  } catch {
    return undefined;
  }
}

function readPath(source: unknown, pathSegments: string[]): unknown {
  let cursor: unknown = source;
  for (const segment of pathSegments) {
    if (!cursor || typeof cursor !== 'object' || !(segment in cursor)) {
      return undefined;
    }

    cursor = (cursor as Record<string, unknown>)[segment];
  }

  return cursor;
}

function toStringValue(value: unknown): string | undefined {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : undefined;
  }

  if (value === null || value === undefined) {
    return undefined;
  }

  const text = String(value).trim();
  return text.length > 0 ? text : undefined;
}

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }

  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
}

type IngestResultOutcome =
  | { success: true; value: BackendIngestResult }
  | { success: false; errorMessage: string };

function extractIngestResult(
  response: { statusCode: number; body: string },
  payload: Record<string, unknown>
): IngestResultOutcome {
  if (response.statusCode >= 200 && response.statusCode < 300) {
    const responsePayload = safeJsonParse(response.body);
    const responseUuid =
      toStringValue(readPath(responsePayload, ['uuid'])) ?? String(payload.id ?? '').trim();
    if (!responseUuid) {
      throw new Error('HTTP ingest succeeded but returned no UUID.');
    }
    return { success: true, value: { uuid: responseUuid, transport: 'http' } };
  }

  // Extract FastAPI's "detail" field so the log shows the actual reason.
  const errorBody = safeJsonParse(response.body);
  const backendDetail =
    errorBody !== undefined && 'detail' in errorBody
      ? String(errorBody.detail)
      : response.body.slice(0, 300).trim() || 'no detail from backend';

  return {
    success: false,
    errorMessage: 'HTTP ' + String(response.statusCode) + ' from ingest endpoint: ' + backendDetail
  };
}

function generateTraceId(): string {
  const hex = Array.from({ length: 16 }, () =>
    Math.floor(Math.random() * 256).toString(16).padStart(2, '0')
  ).join('');
  return hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16) + '-' + hex.slice(16, 20) + '-' + hex.slice(20);
}
