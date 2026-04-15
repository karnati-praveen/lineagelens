import * as http from 'http';
import httpProxy = require('http-proxy');
import { Readable } from 'stream';
import { v4 as uuidv4 } from 'uuid';

const DEFAULT_PROXY_PORT = 8080;
const DEFAULT_RETENTION_MS = 30_000;
const CLEANUP_INTERVAL_MS = 5_000;
const DEFAULT_LLM_HOST_PATTERNS: RegExp[] = [
  /(^|\.)api\.openai\.com$/i,
  /(^|\.)api\.anthropic\.com$/i,
  /(^|\.)api\.githubcopilot\.com$/i,
  /(^|\.)githubcopilot\.com$/i,
  /(^|\.)copilot-proxy\.githubusercontent\.com$/i,
  /(^|\.)openrouter\.ai$/i
];

type NormalizedHeaders = Record<string, string | string[]>;

type TargetResolution = {
  origin: string;
  fullUrl: string;
  host: string;
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
  headers: NormalizedHeaders;
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
  const pendingByRequest = new WeakMap<http.IncomingMessage, string>();
  const proxy = httpProxy.createProxyServer({
    secure: true,
    changeOrigin: true,
    xfwd: true,
    selfHandleResponse: true
  });

  proxy.on('proxyRes', (proxyRes, req, res) => {
    const chunks: Buffer[] = [];

    proxyRes.on('data', (chunk: Buffer | string) => {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    });

    proxyRes.on('end', () => {
      const rawBuffer = Buffer.concat(chunks);
      const statusCode = proxyRes.statusCode ?? 502;
      const outgoingHeaders = sanitizeOutgoingHeaders(proxyRes.headers);

      if (!res.headersSent) {
        res.writeHead(statusCode, outgoingHeaders);
      }
      res.end(rawBuffer);

      const pendingId = pendingByRequest.get(req);
      if (pendingId) {
        const pair = recentPairs.get(pendingId);
        if (pair) {
          pair.response = {
            timestampIso: new Date().toISOString(),
            statusCode,
            headers: normalizeIncomingHeaders(proxyRes.headers),
            rawBodyUtf8: rawBuffer.toString('utf8'),
            rawBodyBase64: rawBuffer.toString('base64')
          };
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

    const shouldCapture = shouldCaptureRequest(req.method ?? 'GET', target.host, hostPatterns);
    if (!shouldCapture) {
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

    const parsedPayload = parseJson(rawBody);
    const payloadRecord = isRecord(parsedPayload) ? parsedPayload : undefined;

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
        headers: normalizeIncomingHeaders(req.headers),
        rawBodyUtf8: rawBody.toString('utf8'),
        rawBodyBase64: rawBody.toString('base64'),
        payload: parsedPayload,
        messages: payloadRecord?.messages,
        model: payloadRecord?.model,
        temperature: payloadRecord?.temperature,
        systemPrompt: extractSystemPrompt(payloadRecord),
        parameters: payloadRecord
      }
    };

    recentPairs.set(requestId, pair);
    pendingByRequest.set(req, requestId);
    pruneExpiredPairs(recentPairs, retentionMs);

    req.headers['content-length'] = String(rawBody.length);
    delete req.headers['proxy-connection'];

    log('Captured LLM POST request ' + requestId + ' for host ' + target.host + '.');

    forwardRequest(proxy, req, res, target, rawBody);
  });

  server.on('connect', (request, socket) => {
    socket.write('HTTP/1.1 405 Method Not Allowed\r\n\r\n');
    socket.destroy();
    log('Rejected CONNECT tunnel request for ' + String(request.url ?? 'unknown') + '.');
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

function shouldCaptureRequest(method: string, host: string, hostPatterns: RegExp[]): boolean {
  if (method.toUpperCase() !== 'POST') {
    return false;
  }

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
      path: parsed.pathname + parsed.search
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
      normalized[key] = value.map((item) => String(item));
      continue;
    }

    normalized[key] = String(value);
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
