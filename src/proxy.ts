import * as http from 'http';
import httpProxy = require('http-proxy');
import * as net from 'net';
import { Duplex, Readable } from 'stream';
import { v4 as uuidv4 } from 'uuid';

const DEFAULT_PROXY_PORT = 8080;
const DEFAULT_RETENTION_MS = 5 * 60_000;
const CLEANUP_INTERVAL_MS = 5_000;
const REDACTED_HEADER_VALUE = '[redacted]';
const MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024;
const MAX_RESPONSE_BODY_BYTES = 4 * 1024 * 1024;
const SENSITIVE_HEADER_PATTERN =
  /^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|x-auth-token|x-csrf-token)$/i;
const DEFAULT_LLM_HOST_PATTERNS: RegExp[] = [
  /(^|\.)api\.openai\.com$/i,
  /(^|\.)api\.anthropic\.com$/i,
  /(^|\.)api\.githubcopilot\.com$/i,
  /(^|\.)githubcopilot\.com$/i,
  /(^|\.)copilot-proxy\.githubusercontent\.com$/i,
  /(^|\.)openrouter\.ai$/i
];

type NormalizedHeaders = Record<string, string | string[]>;

export type CaptureStatus = 'full' | 'metadata_only' | 'tunnel_only' | 'hook' | 'unavailable';

export type RequestMetadata = {
  method: string;
  targetUrl: string;
  targetHost: string;
  targetPort: number | null;
  path: string;
  headers: NormalizedHeaders;
  userAgent: string | null;
  captureStatus: CaptureStatus;
  captureReason: string | null;
};

export type RequestBodyCapture = {
  rawBodyUtf8: string;
  rawBodyBase64: string;
  payload: unknown;
  messages: unknown;
  model: unknown;
  temperature: unknown;
  systemPrompt: unknown;
  parameters: Record<string, unknown> | undefined;
};

export type TunnelMetadata = {
  targetHost: string;
  targetPort: number;
  clientAddress: string | null;
  serverAddress: string | null;
  startedAtIso: string;
  endedAtIso: string | null;
  durationMs: number | null;
  bytesUpstream: number;
  bytesDownstream: number;
  connectionCount: number;
};

export type ProxyCapabilityReport = {
  port: number;
  retentionMs: number;
  allowlist: string[];
  observedRequests: number;
  capturedRequests: number;
  fullCaptures: number;
  metadataOnlyCaptures: number;
  tunnelOnlyCaptures: number;
  unavailableCaptures: number;
  recentHosts: Array<{
    host: string;
    status: CaptureStatus;
    count: number;
    lastSeenIso: string;
  }>;
  notes: string[];
};

type TargetResolution = {
  origin: string;
  fullUrl: string;
  host: string;
  port: number;
  path: string;
};

type StoredPair = {
  id: string;
  createdAtMs: number;
  updatedAtMs: number;
  request: CapturedLlmRequest;
  response?: CapturedLlmResponse;
};

export type CapturedLlmRequest = {
  id: string;
  timestampIso: string;
  method: string;
  targetUrl: string;
  targetHost: string;
  targetPort: number | null;
  headers: NormalizedHeaders;
  captureStatus: CaptureStatus;
  captureReason: string | null;
  requestMetadata: RequestMetadata;
  requestBody: RequestBodyCapture | null;
  tunnelMetadata: TunnelMetadata | null;
  rawBodyUtf8: string;
  rawBodyBase64: string;
  payload: unknown;
  messages: unknown;
  model: unknown;
  temperature: unknown;
  systemPrompt: unknown;
  parameters: Record<string, unknown> | undefined;
};

export type CapturedLlmResponse = {
  timestampIso: string;
  statusCode: number;
  headers: NormalizedHeaders;
  rawBodyUtf8: string;
  rawBodyBase64: string;
};

export type RequestResponsePair = {
  id: string;
  createdAtMs: number;
  updatedAtMs: number;
  request: CapturedLlmRequest;
  response?: CapturedLlmResponse;
};

export type LocalLlmProxyOptions = {
  port?: number;
  retentionMs?: number;
  hostPatterns?: RegExp[];
  log?: (message: string) => void;
};

export type LocalLlmProxyRuntime = {
  port: number;
  getRecentPairs: () => RequestResponsePair[];
  getCapabilityReport: () => ProxyCapabilityReport;
  stop: () => Promise<void>;
};

export async function startLocalLlmProxy(
  options: LocalLlmProxyOptions = {}
): Promise<LocalLlmProxyRuntime> {
  const port = clampPort(options.port ?? DEFAULT_PROXY_PORT);
  const retentionMs = Math.max(1_000, options.retentionMs ?? DEFAULT_RETENTION_MS);
  const hostPatterns = options.hostPatterns ?? DEFAULT_LLM_HOST_PATTERNS;
  const log = options.log ?? (() => undefined);

  const recentPairs = new Map<string, StoredPair>();
  const capabilityTelemetry = new Map<
    string,
    {
      host: string;
      status: CaptureStatus;
      totalCount: number;
      fullCount: number;
      metadataOnlyCount: number;
      tunnelOnlyCount: number;
      unavailableCount: number;
      lastSeenIso: string;
    }
  >();
  const pendingByRequest = new WeakMap<http.IncomingMessage, string>();
  const tunnelByClientSocket = new WeakMap<Duplex, string>();
  const proxy = httpProxy.createProxyServer({
    secure: true,
    changeOrigin: true,
    xfwd: true,
    selfHandleResponse: true
  });

  proxy.on('proxyRes', (proxyRes, req, res) => {
    const captureChunks: Buffer[] = [];
    let captureBytes = 0;
    let captureTruncated = false;

    const statusCode = proxyRes.statusCode ?? 502;
    const outgoingHeaders = sanitizeOutgoingHeaders(proxyRes.headers);
    if (!res.headersSent) {
      res.writeHead(statusCode, outgoingHeaders);
    }

    proxyRes.on('data', (chunk: Buffer | string) => {
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      // Always forward every byte to the client — capture is observational only
      res.write(buffer);
      captureBytes += buffer.byteLength;
      if (captureBytes <= MAX_RESPONSE_BODY_BYTES) {
        captureChunks.push(buffer);
      } else if (!captureTruncated) {
        captureTruncated = true;
        log('Response capture truncated at ' + String(MAX_RESPONSE_BODY_BYTES) + ' bytes.');
      }
    });

    proxyRes.on('end', () => {
      res.end();
      const rawBuffer = Buffer.concat(captureChunks);

      const pendingId = pendingByRequest.get(req);
      if (pendingId) {
        const pair = recentPairs.get(pendingId);
        if (pair) {
          const existingCaptureStatus = pair.request.captureStatus;
          const existingCaptureReason = pair.request.captureReason;
          pair.response = {
            timestampIso: new Date().toISOString(),
            statusCode,
            headers: normalizeIncomingHeaders(proxyRes.headers),
            rawBodyUtf8: rawBuffer.toString('utf8'),
            rawBodyBase64: rawBuffer.toString('base64')
          };
          if (existingCaptureStatus === 'full') {
            pair.request.captureStatus = 'full';
            pair.request.requestMetadata.captureStatus = 'full';
            pair.request.requestBody = {
              rawBodyUtf8: pair.request.rawBodyUtf8,
              rawBodyBase64: pair.request.rawBodyBase64,
              payload: pair.request.payload,
              messages: pair.request.messages,
              model: pair.request.model,
              temperature: pair.request.temperature,
              systemPrompt: pair.request.systemPrompt,
              parameters: pair.request.parameters
            };
          }
          pair.request.captureReason = existingCaptureReason;
          pair.request.requestMetadata.captureReason = existingCaptureReason;
          pair.updatedAtMs = Date.now();
        }
      }

      pruneExpiredPairs(recentPairs, retentionMs);
    });
  });

  proxy.on('error', (error: Error, _req, res) => {
    const serverResponse = res as http.ServerResponse | undefined;

    if (serverResponse && !serverResponse.headersSent) {
      serverResponse.writeHead(502, { 'Content-Type': 'application/json' });
    }

    serverResponse?.end(
      JSON.stringify({
        error: 'Upstream proxy error',
        message: error.message
      })
    );

    log('Local LLM proxy upstream error: ' + error.message);
  });

  const server = http.createServer(async (req, res) => {
    const target = resolveTarget(req);
    if (!target) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          error: 'Unable to resolve upstream target URL from request.'
        })
      );
      return;
    }

    const allowlisted = isAllowlistedHost(target.host, hostPatterns);
    if (!allowlisted) {
      recordCapabilityObservation(capabilityTelemetry, target.host, 'unavailable');
      forwardRequest(proxy, req, res, target);
      return;
    }

    const requestMethod = (req.method ?? 'GET').toUpperCase();
    if (requestMethod !== 'POST') {
      const requestId = uuidv4();
      const createdAtMs = Date.now();
      const pair: StoredPair = {
        id: requestId,
        createdAtMs,
        updatedAtMs: createdAtMs,
        request: {
          id: requestId,
          timestampIso: new Date(createdAtMs).toISOString(),
          method: req.method ?? 'GET',
          targetUrl: target.fullUrl,
          targetHost: target.host,
          targetPort: target.port,
          headers: normalizeIncomingHeaders(req.headers),
          captureStatus: 'metadata_only',
          captureReason: 'Allowlisted request captured with metadata only because the body was not captured.',
          requestMetadata: {
            method: req.method ?? 'GET',
            targetUrl: target.fullUrl,
            targetHost: target.host,
            targetPort: target.port,
            path: target.path,
            headers: normalizeIncomingHeaders(req.headers),
            userAgent: firstHeaderValue(req.headers['user-agent']) ?? null,
            captureStatus: 'metadata_only',
            captureReason: 'Allowlisted request captured with metadata only because the body was not captured.'
          },
          requestBody: null,
          tunnelMetadata: null,
          rawBodyUtf8: '',
          rawBodyBase64: '',
          payload: undefined,
          messages: undefined,
          model: undefined,
          temperature: undefined,
          systemPrompt: undefined,
          parameters: undefined
        }
      };

      recentPairs.set(requestId, pair);
      pendingByRequest.set(req, requestId);
      recordCapabilityObservation(capabilityTelemetry, target.host, 'metadata_only');
      pruneExpiredPairs(recentPairs, retentionMs);

      log('Captured allowlisted metadata-only request ' + requestId + ' for host ' + target.host + '.');

      forwardRequest(proxy, req, res, target);
      return;
    }

    let rawBody: Buffer;
    try {
      rawBody = await readRawBody(req);
    } catch (error: unknown) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          error: 'Failed to read request body',
          message: toErrorMessage(error)
        })
      );
      return;
    }

    const oversized = rawBody.length > MAX_REQUEST_BODY_BYTES;
    const parsedPayload = oversized ? undefined : parseJson(rawBody);
    const payloadRecord = (!oversized && isRecord(parsedPayload)) ? parsedPayload : undefined;

    const captureStatus: CaptureStatus = oversized ? 'metadata_only' : 'full';
    const captureReason = oversized
      ? 'Request body exceeds capture limit; forwarded without full body capture.'
      : null;

    const requestId = uuidv4();
    const createdAtMs = Date.now();
    const pair: StoredPair = {
      id: requestId,
      createdAtMs,
      updatedAtMs: createdAtMs,
      request: {
        id: requestId,
        timestampIso: new Date(createdAtMs).toISOString(),
        method: req.method ?? 'POST',
        targetUrl: target.fullUrl,
        targetHost: target.host,
        targetPort: target.port,
        headers: normalizeIncomingHeaders(req.headers),
        captureStatus,
        captureReason,
        requestMetadata: {
          method: req.method ?? 'POST',
          targetUrl: target.fullUrl,
          targetHost: target.host,
          targetPort: target.port,
          path: target.path,
          headers: normalizeIncomingHeaders(req.headers),
          userAgent: firstHeaderValue(req.headers['user-agent']) ?? null,
          captureStatus,
          captureReason
        },
        requestBody: oversized ? null : {
          rawBodyUtf8: rawBody.toString('utf8'),
          rawBodyBase64: rawBody.toString('base64'),
          payload: parsedPayload,
          messages: payloadRecord?.messages,
          model: payloadRecord?.model,
          temperature: payloadRecord?.temperature,
          systemPrompt: extractSystemPrompt(payloadRecord),
          parameters: payloadRecord
        },
        tunnelMetadata: null,
        rawBodyUtf8: oversized ? '' : rawBody.toString('utf8'),
        rawBodyBase64: oversized ? '' : rawBody.toString('base64'),
        payload: parsedPayload,
        messages: payloadRecord?.messages,
        model: payloadRecord?.model,
        temperature: payloadRecord?.temperature,
        systemPrompt: oversized ? undefined : extractSystemPrompt(payloadRecord),
        parameters: payloadRecord
      }
    };

    recentPairs.set(requestId, pair);
    pendingByRequest.set(req, requestId);
    recordCapabilityObservation(capabilityTelemetry, target.host, captureStatus);
    pruneExpiredPairs(recentPairs, retentionMs);

    req.headers['content-length'] = String(rawBody.length);
    delete req.headers['proxy-connection'];

    log('Captured LLM POST request ' + requestId + ' for host ' + target.host + '.');

    forwardRequest(proxy, req, res, target, rawBody);
  });

  server.on('connect', (request, clientSocket, head) => {
    const target = resolveConnectTarget(request.url);
    if (!target) {
      clientSocket.write('HTTP/1.1 400 Bad Request\r\n\r\n');
      clientSocket.destroy();
      log('Invalid CONNECT target: ' + String(request.url ?? 'unknown') + '.');
      return;
    }

    const upstreamSocket = net.connect(target.port, target.host, () => {
      const tunnelRequestId = uuidv4();
      const startedAtMs = Date.now();
      const startedAtIso = new Date(startedAtMs).toISOString();
      const pair: StoredPair = {
        id: tunnelRequestId,
        createdAtMs: startedAtMs,
        updatedAtMs: startedAtMs,
        request: {
          id: tunnelRequestId,
          timestampIso: startedAtIso,
          method: 'CONNECT',
          targetUrl: target.host + ':' + String(target.port),
          targetHost: target.host,
          targetPort: target.port,
          headers: normalizeIncomingHeaders(request.headers),
          captureStatus: 'tunnel_only',
          captureReason: 'HTTPS payload is encrypted inside the tunnel.',
          requestMetadata: {
            method: 'CONNECT',
            targetUrl: target.host + ':' + String(target.port),
            targetHost: target.host,
            targetPort: target.port,
            path: '/',
            headers: normalizeIncomingHeaders(request.headers),
            userAgent: firstHeaderValue(request.headers['user-agent']) ?? null,
            captureStatus: 'tunnel_only',
            captureReason: 'HTTPS payload is encrypted inside the tunnel.'
          },
          requestBody: null,
          tunnelMetadata: {
            targetHost: target.host,
            targetPort: target.port,
            clientAddress: request.socket.remoteAddress ?? null,
            serverAddress: upstreamSocket.localAddress ?? null,
            startedAtIso,
            endedAtIso: null,
            durationMs: null,
            bytesUpstream: 0,
            bytesDownstream: 0,
            connectionCount: 1
          },
          rawBodyUtf8: '',
          rawBodyBase64: '',
          payload: undefined,
          messages: undefined,
          model: undefined,
          temperature: undefined,
          systemPrompt: undefined,
          parameters: undefined
        }
      };

      recentPairs.set(tunnelRequestId, pair);
      tunnelByClientSocket.set(clientSocket, tunnelRequestId);
      recordCapabilityObservation(capabilityTelemetry, target.host, 'tunnel_only');

      clientSocket.write('HTTP/1.1 200 Connection Established\r\n\r\n');

      if (head.length > 0) {
        upstreamSocket.write(head);
      }

      upstreamSocket.pipe(clientSocket);
      clientSocket.pipe(upstreamSocket);
      log(
        'Established CONNECT tunnel for ' +
          target.host +
          ':' +
          String(target.port) +
          '. HTTPS payload is tunneled and not captured.'
      );
    });

    upstreamSocket.on('error', (error: Error) => {
      if (!clientSocket.destroyed) {
        clientSocket.write('HTTP/1.1 502 Bad Gateway\r\n\r\n');
        clientSocket.destroy();
      }

      log(
        'CONNECT tunnel error for ' +
          target.host +
          ':' +
          String(target.port) +
          ': ' +
          error.message
      );
    });

    clientSocket.on('error', () => {
      if (!upstreamSocket.destroyed) {
        upstreamSocket.destroy();
      }
    });

    clientSocket.on('close', () => {
      const tunnelRequestId = tunnelByClientSocket.get(clientSocket);
      const pair = tunnelRequestId ? recentPairs.get(tunnelRequestId) : undefined;

      if (pair) {
        const endedAtMs = Date.now();
        pair.updatedAtMs = endedAtMs;
        if (pair.request.tunnelMetadata) {
          pair.request.tunnelMetadata.endedAtIso = new Date(endedAtMs).toISOString();
          pair.request.tunnelMetadata.durationMs = Math.max(
            0,
            endedAtMs - Date.parse(pair.request.tunnelMetadata.startedAtIso)
          );
        }
      }

      if (!upstreamSocket.destroyed) {
        upstreamSocket.destroy();
      }
    });
  });

  await listen(server, port);
  log('Local LLM proxy listening on 127.0.0.1:' + String(port) + '.');

  const cleanupTimer = setInterval(() => {
    pruneExpiredPairs(recentPairs, retentionMs);
  }, CLEANUP_INTERVAL_MS);
  cleanupTimer.unref();

  return {
    port,
    getRecentPairs: () => {
      pruneExpiredPairs(recentPairs, retentionMs);

      return [...recentPairs.values()]
        .sort((left, right) => right.updatedAtMs - left.updatedAtMs)
        .map((pair) => ({
          id: pair.id,
          createdAtMs: pair.createdAtMs,
          updatedAtMs: pair.updatedAtMs,
          request: pair.request,
          response: pair.response
        }));
    },
    getCapabilityReport: () =>
      buildProxyCapabilityReport({
        port,
        retentionMs,
        hostPatterns,
        recentPairs,
        capabilityTelemetry
      }),
    stop: async () => {
      clearInterval(cleanupTimer);
      await closeServer(server);
      closeProxy(proxy);
      recentPairs.clear();
      log('Local LLM proxy stopped.');
    }
  };
}

function clampPort(value: number): number {
  if (!Number.isFinite(value)) {
    return DEFAULT_PROXY_PORT;
  }

  return Math.max(1, Math.min(65535, Math.floor(value)));
}

function isAllowlistedHost(host: string, hostPatterns: RegExp[]): boolean {
  return hostPatterns.some((pattern) => pattern.test(host));
}

function resolveTarget(req: http.IncomingMessage): TargetResolution | undefined {
  const rawUrl = req.url;
  if (!rawUrl) {
    return undefined;
  }

  try {
    let parsed: URL;
    if (rawUrl.startsWith('http://') || rawUrl.startsWith('https://')) {
      parsed = new URL(rawUrl);
    } else {
      const hostHeader = firstHeaderValue(req.headers.host);
      if (!hostHeader) {
        return undefined;
      }

      const protocol = inferProtocol(req);
      parsed = new URL(protocol + '://' + hostHeader + rawUrl);
    }

    return {
      origin: parsed.origin,
      fullUrl: parsed.toString(),
      host: parsed.hostname.toLowerCase(),
      port: parsed.port.length > 0 ? Number(parsed.port) : parsed.protocol === 'https:' ? 443 : 80,
      path: parsed.pathname + parsed.search
    };
  } catch {
    return undefined;
  }
}

function resolveConnectTarget(rawTarget: string | undefined): { host: string; port: number } | undefined {
  if (!rawTarget || rawTarget.trim().length === 0) {
    return undefined;
  }

  try {
    const parsed = new URL('http://' + rawTarget.trim());
    const host = parsed.hostname;
    const port = parsed.port.length > 0 ? Number(parsed.port) : 443;

    if (!host || !Number.isFinite(port) || port < 1 || port > 65535) {
      return undefined;
    }

    return {
      host,
      port
    };
  } catch {
    return undefined;
  }
}

function inferProtocol(req: http.IncomingMessage): 'http' | 'https' {
  const forwarded = firstHeaderValue(req.headers['x-forwarded-proto']);
  if (forwarded === 'http' || forwarded === 'https') {
    return forwarded;
  }

  const maybeTlsSocket = req.socket as { encrypted?: boolean };
  return maybeTlsSocket.encrypted ? 'https' : 'http';
}

function firstHeaderValue(
  value: string | string[] | number | undefined
): string | undefined {
  if (typeof value === 'string') {
    return value;
  }

  if (typeof value === 'number') {
    return String(value);
  }

  if (Array.isArray(value) && value.length > 0) {
    return value[0];
  }

  return undefined;
}

function normalizeIncomingHeaders(headers: http.IncomingHttpHeaders): NormalizedHeaders {
  const normalized: NormalizedHeaders = {};

  for (const [key, value] of Object.entries(headers)) {
    if (typeof value === 'undefined') {
      continue;
    }

    if (Array.isArray(value)) {
      normalized[key] = SENSITIVE_HEADER_PATTERN.test(key)
        ? value.map(() => REDACTED_HEADER_VALUE)
        : value.map((item) => String(item));
      continue;
    }

    normalized[key] = SENSITIVE_HEADER_PATTERN.test(key)
      ? REDACTED_HEADER_VALUE
      : String(value);
  }

  return normalized;
}

function sanitizeOutgoingHeaders(headers: http.IncomingHttpHeaders): http.OutgoingHttpHeaders {
  const outgoing: http.OutgoingHttpHeaders = {};

  for (const [key, value] of Object.entries(headers)) {
    if (typeof value !== 'undefined') {
      outgoing[key] = value;
    }
  }

  return outgoing;
}

function forwardRequest(
  proxy: ReturnType<typeof httpProxy.createProxyServer>,
  req: http.IncomingMessage,
  res: http.ServerResponse,
  target: TargetResolution,
  rawBody?: Buffer
): void {
  req.url = target.path;

  proxy.web(req, res, {
    target: target.origin,
    prependPath: false,
    ignorePath: false,
    buffer: rawBody ? Readable.from(rawBody) : undefined
  });
}

function parseJson(rawBody: Buffer): unknown {
  if (rawBody.length === 0) {
    return undefined;
  }

  const utf8Body = rawBody.toString('utf8');

  try {
    return JSON.parse(utf8Body) as unknown;
  } catch {
    return undefined;
  }
}

function extractSystemPrompt(payloadRecord: Record<string, unknown> | undefined): unknown {
  if (!payloadRecord) {
    return undefined;
  }

  if (Object.prototype.hasOwnProperty.call(payloadRecord, 'system')) {
    return payloadRecord.system;
  }

  const messages = payloadRecord.messages;
  if (!Array.isArray(messages)) {
    return undefined;
  }

  for (const message of messages) {
    if (!isRecord(message)) {
      continue;
    }

    const role = typeof message.role === 'string' ? message.role.toLowerCase() : '';
    if (role === 'system' && Object.prototype.hasOwnProperty.call(message, 'content')) {
      return message.content;
    }
  }

  return undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function pruneExpiredPairs(pairs: Map<string, StoredPair>, retentionMs: number): void {
  const cutoff = Date.now() - retentionMs;

  for (const [id, pair] of pairs) {
    if (pair.updatedAtMs < cutoff) {
      pairs.delete(id);
    }
  }
}

function readRawBody(req: http.IncomingMessage): Promise<Buffer> {
  return new Promise<Buffer>((resolve, reject) => {
    const chunks: Buffer[] = [];

    req.on('data', (chunk: Buffer | string) => {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    });

    req.on('end', () => {
      resolve(Buffer.concat(chunks));
    });

    req.on('error', (error: Error) => {
      reject(error);
    });
  });
}

function listen(server: http.Server, port: number): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const onError = (error: Error): void => {
      server.off('listening', onListening);
      reject(error);
    };

    const onListening = (): void => {
      server.off('error', onError);
      resolve();
    };

    server.once('error', onError);
    server.once('listening', onListening);
    server.listen(port, '127.0.0.1');
  });
}

function closeServer(server: http.Server): Promise<void> {
  if (!server.listening) {
    return Promise.resolve();
  }

  return new Promise<void>((resolve) => {
    server.close(() => {
      resolve();
    });
  });
}

function closeProxy(proxy: ReturnType<typeof httpProxy.createProxyServer>): void {
  try {
    proxy.close();
  } catch {
    // No action needed during shutdown.
  }
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

function statusCounts(status: CaptureStatus): { full: number; metadataOnly: number; tunnelOnly: number; unavailable: number } {
  return {
    full: status === 'full' ? 1 : 0,
    metadataOnly: status === 'metadata_only' ? 1 : 0,
    tunnelOnly: status === 'tunnel_only' ? 1 : 0,
    unavailable: status === 'unavailable' ? 1 : 0
  };
}

function recordCapabilityObservation(
  telemetry: Map<
    string,
    {
      host: string;
      status: CaptureStatus;
      totalCount: number;
      fullCount: number;
      metadataOnlyCount: number;
      tunnelOnlyCount: number;
      unavailableCount: number;
      lastSeenIso: string;
    }
  >,
  host: string,
  status: CaptureStatus
): void {
  const key = host.toLowerCase();
  const current = telemetry.get(key);
  const counts = statusCounts(status);
  const next = current
    ? {
        ...current,
        status,
        totalCount: current.totalCount + 1,
        fullCount: current.fullCount + counts.full,
        metadataOnlyCount: current.metadataOnlyCount + counts.metadataOnly,
        tunnelOnlyCount: current.tunnelOnlyCount + counts.tunnelOnly,
        unavailableCount: current.unavailableCount + counts.unavailable,
        lastSeenIso: new Date().toISOString()
      }
    : {
        host,
        status,
        totalCount: 1,
        fullCount: counts.full,
        metadataOnlyCount: counts.metadataOnly,
        tunnelOnlyCount: counts.tunnelOnly,
        unavailableCount: counts.unavailable,
        lastSeenIso: new Date().toISOString()
      };

  telemetry.set(key, next);
}

function buildProxyCapabilityReport(input: {
  port: number;
  retentionMs: number;
  hostPatterns: RegExp[];
  recentPairs: Map<string, StoredPair>;
  capabilityTelemetry: Map<
    string,
    {
      host: string;
      status: CaptureStatus;
      totalCount: number;
      fullCount: number;
      metadataOnlyCount: number;
      tunnelOnlyCount: number;
      unavailableCount: number;
      lastSeenIso: string;
    }
  >;
}): ProxyCapabilityReport {
  let fullCaptures = 0;
  let metadataOnlyCaptures = 0;
  let tunnelOnlyCaptures = 0;
  let unavailableCaptures = 0;

  for (const entry of input.capabilityTelemetry.values()) {
    fullCaptures += entry.fullCount;
    metadataOnlyCaptures += entry.metadataOnlyCount;
    tunnelOnlyCaptures += entry.tunnelOnlyCount;
    unavailableCaptures += entry.unavailableCount;
  }

  return {
    port: input.port,
    retentionMs: input.retentionMs,
    allowlist: input.hostPatterns.map((pattern) => pattern.toString()),
    observedRequests: [...input.capabilityTelemetry.values()].reduce((sum, entry) => sum + entry.totalCount, 0),
    capturedRequests: fullCaptures + metadataOnlyCaptures + tunnelOnlyCaptures,
    fullCaptures,
    metadataOnlyCaptures,
    tunnelOnlyCaptures,
    unavailableCaptures,
    recentHosts: [...input.capabilityTelemetry.values()]
      .sort((left, right) => right.lastSeenIso.localeCompare(left.lastSeenIso))
      .slice(0, 12)
      .map((entry) => ({
        host: entry.host,
        status: entry.status,
        count: entry.totalCount,
        lastSeenIso: entry.lastSeenIso
      })),
    notes: [
      'Lightweight proxy captures full bodies for allowlisted POST requests and metadata only for other allowlisted methods.',
      'CONNECT tunnels are recorded as tunnel_only metadata unless a dedicated interception sidecar is enabled.',
      'Requests outside the allowlist are counted as unavailable.'
    ]
  };
}
