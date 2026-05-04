import * as http from 'node:http';
import * as https from 'node:https';
import * as vscode from 'vscode';

const CONFIG_SECTION = 'aiInsertionDetector';
const DEFAULT_BACKEND_BASE_URL = 'http://127.0.0.1:8787';
const DEFAULT_AUTH_LOGIN_PATH = '/auth/login';
const DEFAULT_AUTH_REGISTER_PATH = '/auth/register';
const DEFAULT_AUTH_REFRESH_PATH = '/auth/refresh';
const DEFAULT_AUTH_REFRESH_SKEW_SECONDS = 45;
const DEFAULT_AUTO_AUTH_ON_ACTIVATE = true;
const REQUEST_TIMEOUT_MS = 12_000;

const SECRET_BACKEND_ACCESS_TOKEN = 'aiInsertionDetector.backend.accessToken';
const SECRET_BACKEND_REFRESH_TOKEN = 'aiInsertionDetector.backend.refreshToken';
const LEGACY_SECRET_BACKEND_JWT = 'aiInsertionDetector.backend.jwt';

export type BackendAuthConfig = {
  baseUrl: string;
  loginPath: string;
  registerPath: string;
  refreshPath: string;
  refreshSkewSeconds: number;
  autoAuthOnActivate: boolean;
};

type JsonRequestResult = {
  statusCode: number;
  body: string;
};

type AuthTokens = {
  accessToken: string;
  refreshToken?: string;
};

export class BackendAuthSession {
  public constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly log: (message: string) => void
  ) {}

  public async initializeAuthentication(resource?: vscode.Uri): Promise<void> {
    const config = getBackendAuthConfig(resource);
    if (!config.autoAuthOnActivate) {
      return;
    }

    await this.migrateLegacyTokenIfNeeded();

    try {
      await this.ensureAccessToken({ resource, interactive: false, forceRefresh: false });
    } catch (error: unknown) {
      this.log('Backend auth initialization failed: ' + toErrorMessage(error));
    }
  }

  public async login(resource?: vscode.Uri): Promise<void> {
    const token = await this.promptForAuthentication(resource);
    if (!token) {
      return;
    }

    vscode.window.setStatusBarMessage('AI Insertion Detector: backend authentication updated.', 5000);
  }

  public async getAuthorizationHeader(
    resource?: vscode.Uri,
    interactive = true,
    forceRefresh = false
  ): Promise<string | undefined> {
    const accessToken = await this.ensureAccessToken({ resource, interactive, forceRefresh });
    if (!accessToken) {
      return undefined;
    }

    return 'Bearer ' + accessToken;
  }

  public async getWorkspaceId(resource?: vscode.Uri, interactive = true): Promise<string | undefined> {
    const accessToken = await this.ensureAccessToken({ resource, interactive, forceRefresh: false });
    if (!accessToken) {
      return undefined;
    }

    const payload = decodeJwtPayload(accessToken);
    const workspaceId =
      toStringValue(payload?.workspace_id) || toStringValue(payload?.workspace) || undefined;

    return workspaceId;
  }

  public async clearStoredTokens(): Promise<void> {
    await this.context.secrets.delete(SECRET_BACKEND_ACCESS_TOKEN);
    await this.context.secrets.delete(SECRET_BACKEND_REFRESH_TOKEN);
  }

  private async ensureAccessToken(input: {
    resource?: vscode.Uri;
    interactive: boolean;
    forceRefresh: boolean;
  }): Promise<string | undefined> {
    await this.migrateLegacyTokenIfNeeded();

    const config = getBackendAuthConfig(input.resource);
    const storedAccess = await this.context.secrets.get(SECRET_BACKEND_ACCESS_TOKEN);

    if (
      storedAccess &&
      storedAccess.trim().length > 0 &&
      !input.forceRefresh &&
      !isTokenExpiringSoon(storedAccess.trim(), config.refreshSkewSeconds)
    ) {
      return storedAccess.trim();
    }

    const refreshed = await this.tryRefreshAccessToken(config);
    if (refreshed) {
      return refreshed;
    }

    if (!input.interactive) {
      return storedAccess?.trim() || undefined;
    }

    return await this.promptForAuthentication(input.resource);
  }

  private async promptForAuthentication(resource?: vscode.Uri): Promise<string | undefined> {
    const selection = await vscode.window.showQuickPick(
      [
        {
          label: 'Login with username/password',
          description: 'Use backend /auth/login to obtain access and refresh tokens.',
          value: 'login'
        },
        {
          label: 'Register new user',
          description: 'Create a user via /auth/register and store issued tokens.',
          value: 'register'
        },
        {
          label: 'Paste existing tokens',
          description: 'Paste an existing access token and optional refresh token.',
          value: 'paste'
        }
      ],
      {
        title: 'AI Insertion Detector: Backend Authentication',
        placeHolder: 'Choose authentication method.'
      }
    );

    if (!selection) {
      return undefined;
    }

    if (selection.value === 'login') {
      return await this.loginWithCredentials(resource);
    }

    if (selection.value === 'register') {
      return await this.registerWithCredentials(resource);
    }

    return await this.pasteTokensManually();
  }

  private async loginWithCredentials(resource?: vscode.Uri): Promise<string | undefined> {
    const credentials = await promptForUserCredentials('Login to Backend');
    if (!credentials) {
      return undefined;
    }

    const config = getBackendAuthConfig(resource);
    const loginUrl = joinUrl(config.baseUrl, config.loginPath);

    const response = await requestJson(
      'POST',
      loginUrl,
      {
        'Content-Type': 'application/json',
        Accept: 'application/json'
      },
      {
        username: credentials.username,
        password: credentials.password,
        workspaceId: credentials.workspaceId
      }
    );

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw new Error(
        'Backend login failed with status ' + String(response.statusCode) + '. ' + response.body
      );
    }

    const tokens = extractTokensFromAuthResponse(response.body);
    if (!tokens) {
      throw new Error('Login response does not contain access token payload.');
    }

    await this.storeTokens(tokens);
    return tokens.accessToken;
  }

  private async registerWithCredentials(resource?: vscode.Uri): Promise<string | undefined> {
    const credentials = await promptForUserCredentials('Register Backend User');
    if (!credentials) {
      return undefined;
    }

    const config = getBackendAuthConfig(resource);
    const registerUrl = joinUrl(config.baseUrl, config.registerPath);

    const response = await requestJson(
      'POST',
      registerUrl,
      {
        'Content-Type': 'application/json',
        Accept: 'application/json'
      },
      {
        username: credentials.username,
        password: credentials.password,
        workspaceId: credentials.workspaceId
      }
    );

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw new Error(
        'Backend registration failed with status ' +
          String(response.statusCode) +
          '. ' +
          response.body
      );
    }

    const tokens = extractTokensFromAuthResponse(response.body);
    if (!tokens) {
      throw new Error('Registration response does not contain access token payload.');
    }

    await this.storeTokens(tokens);
    return tokens.accessToken;
  }

  private async pasteTokensManually(): Promise<string | undefined> {
    const accessToken = await vscode.window.showInputBox({
      title: 'Paste Access Token',
      prompt: 'Paste backend access token (JWT).',
      ignoreFocusOut: true,
      password: true,
      validateInput: (value: string) => {
        return value.trim().length >= 16 ? undefined : 'Provide a valid JWT access token.';
      }
    });

    if (!accessToken) {
      return undefined;
    }

    const refreshToken = await vscode.window.showInputBox({
      title: 'Paste Refresh Token (Optional)',
      prompt: 'Paste backend refresh token if available (recommended).',
      ignoreFocusOut: true,
      password: true
    });

    await this.storeTokens({
      accessToken: accessToken.trim(),
      refreshToken: refreshToken?.trim() || undefined
    });

    return accessToken.trim();
  }

  private async tryRefreshAccessToken(config: BackendAuthConfig): Promise<string | undefined> {
    const refreshToken = await this.context.secrets.get(SECRET_BACKEND_REFRESH_TOKEN);
    if (!refreshToken || refreshToken.trim().length === 0) {
      return undefined;
    }

    const refreshUrl = joinUrl(config.baseUrl, config.refreshPath);

    const response = await requestJson(
      'POST',
      refreshUrl,
      {
        'Content-Type': 'application/json',
        Accept: 'application/json'
      },
      {
        refreshToken: refreshToken.trim()
      }
    );

    if (response.statusCode < 200 || response.statusCode >= 300) {
      this.log(
        'Token refresh failed with status ' + String(response.statusCode) + '. Clearing stored tokens.'
      );
      await this.clearStoredTokens();
      return undefined;
    }

    const tokens = extractTokensFromAuthResponse(response.body);
    if (!tokens) {
      this.log('Refresh response missing token payload. Clearing stored tokens.');
      await this.clearStoredTokens();
      return undefined;
    }

    await this.storeTokens(tokens);
    return tokens.accessToken;
  }

  private async migrateLegacyTokenIfNeeded(): Promise<void> {
    const accessToken = await this.context.secrets.get(SECRET_BACKEND_ACCESS_TOKEN);
    if (accessToken && accessToken.trim().length > 0) {
      return;
    }

    const legacyToken = await this.context.secrets.get(LEGACY_SECRET_BACKEND_JWT);
    if (!legacyToken || legacyToken.trim().length === 0) {
      return;
    }

    await this.context.secrets.store(SECRET_BACKEND_ACCESS_TOKEN, legacyToken.trim());
    await this.context.secrets.delete(LEGACY_SECRET_BACKEND_JWT);
    this.log('Migrated legacy backend JWT secret to access token storage.');
  }

  private async storeTokens(tokens: AuthTokens): Promise<void> {
    await this.context.secrets.store(SECRET_BACKEND_ACCESS_TOKEN, tokens.accessToken);

    if (tokens.refreshToken && tokens.refreshToken.trim().length > 0) {
      await this.context.secrets.store(SECRET_BACKEND_REFRESH_TOKEN, tokens.refreshToken.trim());
      return;
    }

    await this.context.secrets.delete(SECRET_BACKEND_REFRESH_TOKEN);
  }
}

function getBackendAuthConfig(resource?: vscode.Uri): BackendAuthConfig {
  const config = vscode.workspace.getConfiguration(CONFIG_SECTION, resource);

  return {
    baseUrl: normalizeUrl(config.get<string>('backend.baseUrl', DEFAULT_BACKEND_BASE_URL), DEFAULT_BACKEND_BASE_URL),
    loginPath: normalizeRelativePath(
      config.get<string>('backend.auth.loginPath', DEFAULT_AUTH_LOGIN_PATH),
      DEFAULT_AUTH_LOGIN_PATH
    ),
    registerPath: normalizeRelativePath(
      config.get<string>('backend.auth.registerPath', DEFAULT_AUTH_REGISTER_PATH),
      DEFAULT_AUTH_REGISTER_PATH
    ),
    refreshPath: normalizeRelativePath(
      config.get<string>('backend.auth.refreshPath', DEFAULT_AUTH_REFRESH_PATH),
      DEFAULT_AUTH_REFRESH_PATH
    ),
    refreshSkewSeconds: Math.max(
      5,
      config.get<number>('backend.auth.refreshSkewSeconds', DEFAULT_AUTH_REFRESH_SKEW_SECONDS)
    ),
    autoAuthOnActivate: config.get<boolean>(
      'backend.auth.autoAcquireOnActivate',
      DEFAULT_AUTO_AUTH_ON_ACTIVATE
    )
  };
}

type PromptedCredentials = {
  username: string;
  password: string;
  workspaceId?: string;
};

async function promptForUserCredentials(titlePrefix: string): Promise<PromptedCredentials | undefined> {
  const username = await vscode.window.showInputBox({
    title: titlePrefix + ': Username',
    prompt: 'Enter backend username.',
    ignoreFocusOut: true
  });

  if (!username || username.trim().length === 0) {
    return undefined;
  }

  const password = await vscode.window.showInputBox({
    title: titlePrefix + ': Password',
    prompt: 'Enter backend password.',
    ignoreFocusOut: true,
    password: true
  });

  if (!password) {
    return undefined;
  }

  const workspaceId = await vscode.window.showInputBox({
    title: titlePrefix + ': Workspace Scope (Optional)',
    prompt: 'Optional workspace ID. Leave empty to use backend default.',
    ignoreFocusOut: true
  });

  return {
    username: username.trim(),
    password,
    workspaceId: workspaceId?.trim() || undefined
  };
}

async function requestJson(
  method: 'GET' | 'POST',
  endpointUrl: string,
  headers: Record<string, string>,
  payload?: unknown
): Promise<JsonRequestResult> {
  const target = new URL(endpointUrl);
  const body = typeof payload === 'undefined' ? undefined : JSON.stringify(payload);

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

function extractTokensFromAuthResponse(rawBody: string): AuthTokens | undefined {
  const payload = safeJsonParse(rawBody);
  if (!payload) {
    return undefined;
  }

  const accessToken =
    toStringValue(payload.accessToken) ||
    toStringValue(payload.access_token) ||
    toStringValue(readPath(payload, ['data', 'accessToken'])) ||
    toStringValue(readPath(payload, ['data', 'access_token']));

  if (!accessToken) {
    return undefined;
  }

  const refreshToken =
    toStringValue(payload.refreshToken) ||
    toStringValue(payload.refresh_token) ||
    toStringValue(readPath(payload, ['data', 'refreshToken'])) ||
    toStringValue(readPath(payload, ['data', 'refresh_token']));

  return {
    accessToken,
    refreshToken
  };
}

function isTokenExpiringSoon(token: string, skewSeconds: number): boolean {
  const payload = decodeJwtPayload(token);
  if (!payload) {
    return true;
  }

  const expValue = payload.exp;
  let expEpochSeconds: number;
  if (typeof expValue === 'number') {
    expEpochSeconds = expValue;
  } else if (typeof expValue === 'string') {
    expEpochSeconds = Number(expValue);
  } else {
    expEpochSeconds = Number.NaN;
  }

  if (!Number.isFinite(expEpochSeconds)) {
    return true;
  }

  const nowEpochSeconds = Date.now() / 1000;
  return expEpochSeconds - nowEpochSeconds <= skewSeconds;
}

function decodeJwtPayload(token: string): Record<string, unknown> | undefined {
  const segments = token.split('.');
  if (segments.length < 2) {
    return undefined;
  }

  try {
    const payloadJson = Buffer.from(base64UrlToBase64(segments[1]), 'base64').toString('utf-8');
    const payload = JSON.parse(payloadJson) as unknown;
    return payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : undefined;
  } catch {
    return undefined;
  }
}

function base64UrlToBase64(value: string): string {
  let base64 = value.replaceAll('-', '+').replaceAll('_', '/');
  const paddingNeeded = base64.length % 4;
  if (paddingNeeded > 0) {
    base64 += '='.repeat(4 - paddingNeeded);
  }

  return base64;
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

  if (value === null || typeof value === 'undefined') {
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
