import * as os from 'node:os';
import * as vscode from 'vscode';
import { SECRET_BACKEND_API_KEY, SECRET_BACKEND_WORKSPACE_ID } from './backendAuth';

export type FetchResponseLike = {
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
  text?: () => Promise<string>;
};

export type FetchLike = (
  url: string,
  init?: {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
    signal?: AbortSignal;
  }
) => Promise<FetchResponseLike>;

export interface ServerHealth {
  ok: boolean;
  productMode?: string;
  tierLabel?: string;
  version?: string;
  app?: string;
}

export interface ConnectResult {
  success: boolean;
  backendUrl?: string;
  workspaceId?: string;
  workspaceName?: string;
  tierLabel?: string;
  error?: string;
}

const DEFAULT_LOCAL_URL = 'http://localhost:8787';
const CODE_PREFIX = 'LL-';
const CODE_BODY_LEN = 10;
const CROCKFORD_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';

/** Normalize server URL: strip trailing slash, ensure http/https prefix. */
export function normalizeServerUrl(raw: string): string {
  let url = (raw || '').trim();
  if (!url) {
    return DEFAULT_LOCAL_URL;
  }
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    url = 'http://' + url;
  }
  return url.replace(/\/+$/, '');
}

/** Normalize pairing code: Crockford base32 (no I, L, O, U), prefix with LL-. */
export function normalizeConnectCode(raw: string): string {
  let cleaned = (raw || '').trim().toUpperCase().replace(/-/g, '').replace(/\s+/g, '');
  if (cleaned.startsWith('LL')) {
    cleaned = cleaned.slice(2);
  }
  cleaned = cleaned.replace(/I/g, '1').replace(/L/g, '1').replace(/O/g, '0').replace(/U/g, 'V');
  if (cleaned.length !== CODE_BODY_LEN || [...cleaned].some((c) => !CROCKFORD_ALPHABET.includes(c))) {
    throw new Error('Invalid pairing code format. Expected LL-XXXXX-XXXXX (10 characters).');
  }
  return `${CODE_PREFIX}${cleaned.slice(0, 5)}-${cleaned.slice(5)}`;
}

/** Probe server /health endpoint to check server existence and extract tier. */
export async function probeServerHealth(
  serverUrl: string,
  fetchImpl?: FetchLike,
  timeoutMs = 2500
): Promise<ServerHealth> {
  const doFetch = fetchImpl ?? (globalThis.fetch as unknown as FetchLike);
  const normalized = normalizeServerUrl(serverUrl);

  const controller = typeof AbortController !== 'undefined' ? new AbortController() : undefined;
  const timeoutId = controller ? setTimeout(() => controller.abort(), timeoutMs) : undefined;

  try {
    const resp = await doFetch(`${normalized}/health`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: controller?.signal,
    });
    if (!resp.ok && resp.status !== 200) {
      return { ok: false };
    }
    const data = (await resp.json()) as Record<string, unknown>;
    if (data && typeof data === 'object' && data.status === 'ok') {
      return {
        ok: true,
        productMode: typeof data.productMode === 'string' ? data.productMode : 'lite',
        tierLabel: typeof data.tierLabel === 'string' ? data.tierLabel : 'LineageLens',
        version: typeof data.version === 'string' ? data.version : undefined,
        app: typeof data.app === 'string' ? data.app : undefined,
      };
    }
    return { ok: false };
  } catch {
    return { ok: false };
  } finally {
    if (timeoutId) {
      clearTimeout(timeoutId);
    }
  }
}

/** Redeem pairing code against /connect/redeem for a scoped API key. */
export async function redeemConnectCode(
  backendUrl: string,
  code: string,
  client: string,
  label?: string,
  fetchImpl?: FetchLike
): Promise<{
  apiKey: string;
  keyId?: string;
  workspaceId: string;
  workspaceName: string;
  scopes?: string[];
}> {
  const doFetch = fetchImpl ?? (globalThis.fetch as unknown as FetchLike);
  const normalizedUrl = normalizeServerUrl(backendUrl);
  const normalizedCode = normalizeConnectCode(code);

  const resp = await doFetch(`${normalizedUrl}/connect/redeem`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      code: normalizedCode,
      client,
      label: label || os.hostname(),
    }),
  });

  if (!resp.ok) {
    let detail = `Server returned HTTP ${resp.status}`;
    try {
      const errBody = (await resp.json()) as { detail?: string; message?: string };
      if (errBody?.detail) {
        detail = errBody.detail;
      } else if (errBody?.message) {
        detail = errBody.message;
      }
    } catch {
      // ignore json parse error on non-200
    }

    if (resp.status === 404) {
      throw new Error(`Pairing code not found. Please check that you entered the code correctly.`);
    } else if (resp.status === 410) {
      throw new Error(`This pairing code is no longer valid (${detail}). Please generate a fresh code from /connect.`);
    } else if (resp.status === 429) {
      throw new Error(`Too many redemption attempts. Please wait a moment and try again.`);
    } else {
      throw new Error(`Failed to redeem pairing code: ${detail}`);
    }
  }

  const result = (await resp.json()) as {
    apiKey: string;
    keyId?: string;
    workspaceId: string;
    workspaceName?: string;
    scopes?: string[];
  };

  if (!result || !result.apiKey || !result.workspaceId) {
    throw new Error('Server returned an invalid pairing response (missing apiKey or workspaceId).');
  }

  return {
    apiKey: result.apiKey,
    keyId: result.keyId,
    workspaceId: result.workspaceId,
    workspaceName: result.workspaceName || result.workspaceId,
    scopes: result.scopes,
  };
}

export interface ConnectWizardOptions {
  prefilledServer?: string;
  prefilledCode?: string;
}

/**
 * Interactive Connect Wizard for LineageLens Power Extension.
 *
 * 1. Probes for a local server on port 8787 or prompts for a custom server URL.
 * 2. Prompts user for pairing code (or credentials login).
 * 3. Redeems pairing code and stores API key in SecretStorage.
 * 4. Configures mode=backend and baseUrl in settings.
 * 5. Shows success notification and triggers status refresh.
 */
export async function runConnectWizard(
  context: vscode.ExtensionContext,
  fetchImpl?: FetchLike,
  options?: ConnectWizardOptions
): Promise<ConnectResult> {
  let targetUrl: string | undefined;

  if (options?.prefilledServer) {
    targetUrl = normalizeServerUrl(options.prefilledServer);
  } else {
    // Step 1: Detect or select backend URL
    const localHealth = await probeServerHealth(DEFAULT_LOCAL_URL, fetchImpl, 1500);

    if (localHealth.ok) {
      const localChoice = await vscode.window.showQuickPick(
        [
          {
            label: `$(server) Use local server (${DEFAULT_LOCAL_URL})`,
            description: `${localHealth.tierLabel || 'LineageLens'}${localHealth.version ? ` v${localHealth.version}` : ''}`,
            url: DEFAULT_LOCAL_URL,
          },
          {
            label: '$(edit) Enter a different server URL...',
            description: 'Connect to an internal team or cloud LineageLens server',
            url: '',
          },
        ],
        {
          title: 'LineageLens: Connect to Server (1/3)',
          placeHolder: 'Select server to connect to',
        }
      );

      if (!localChoice) {
        return { success: false, error: 'Cancelled by user' };
      }
      if (localChoice.url) {
        targetUrl = localChoice.url;
      }
    }
  }

  if (!targetUrl) {
    const currentConfigUrl =
      vscode.workspace.getConfiguration('aiInsertionDetector.backend').get<string>('baseUrl') || DEFAULT_LOCAL_URL;

    const inputUrl = await vscode.window.showInputBox({
      title: 'LineageLens: Connect to Server (1/3)',
      prompt: 'Enter your LineageLens server URL (e.g. http://localhost:8787 or https://lineagelens.internal)',
      value: currentConfigUrl,
      validateInput: (val) => {
        const trimmed = (val || '').trim();
        if (!trimmed) {
          return 'Server URL is required.';
        }
        if (!trimmed.startsWith('http://') && !trimmed.startsWith('https://')) {
          return 'URL must start with http:// or https://';
        }
        return null;
      },
    });

    if (!inputUrl) {
      return { success: false, error: 'Cancelled by user' };
    }

    targetUrl = normalizeServerUrl(inputUrl);

    // Validate connection to the specified URL
    const health = await probeServerHealth(targetUrl, fetchImpl, 3000);
    if (!health.ok) {
      const proceed = await vscode.window.showWarningMessage(
        `Could not reach LineageLens server at ${targetUrl}. Do you want to continue anyway?`,
        'Continue',
        'Cancel'
      );
      if (proceed !== 'Continue') {
        return { success: false, error: `Could not reach server at ${targetUrl}` };
      }
    }
  }

  let codeInput: string | undefined;

  if (options?.prefilledCode) {
    try {
      codeInput = normalizeConnectCode(options.prefilledCode);
    } catch {
      // invalid prefilled code, will prompt below with value
    }
  }

  if (!codeInput) {
    // Step 2: Auth method choice
    const authChoice = await vscode.window.showQuickPick(
      [
        {
          label: '$(key) I have a connect code (Recommended)',
          description: 'Instant setup with short-lived pairing code generated from the /connect dashboard',
          value: 'code',
        },
        {
          label: '$(sign-in) Sign in with username & password',
          description: 'Authenticate with standard user credentials',
          value: 'login',
        },
      ],
      {
        title: 'LineageLens: Connect to Server (2/3)',
        placeHolder: 'Choose how to authenticate with the server',
      }
    );

    if (!authChoice) {
      return { success: false, error: 'Cancelled by user' };
    }

    if (authChoice.value === 'login') {
      // Write settings first, then invoke existing login flow
      await vscode.workspace.getConfiguration('aiCodeProvenance').update('mode', 'backend', vscode.ConfigurationTarget.Global);
      await vscode.workspace.getConfiguration('aiInsertionDetector.backend').update('baseUrl', targetUrl, vscode.ConfigurationTarget.Global);
      await vscode.commands.executeCommand('aiInsertionDetector.login');
      return { success: true, backendUrl: targetUrl };
    }

    // Step 3: Enter Connect Code
    const inputVal = await vscode.window.showInputBox({
      title: 'LineageLens: Connect to Server (3/3)',
      prompt: `Enter the Connect Code from ${targetUrl}/connect (format: LL-XXXXX-XXXXX)`,
      placeHolder: 'LL-ABCDE-FGH1K',
      value: options?.prefilledCode,
      validateInput: (val) => {
        try {
          normalizeConnectCode(val);
          return null;
        } catch (err: unknown) {
          return err instanceof Error ? err.message : 'Invalid connect code format.';
        }
      },
    });

    if (!inputVal) {
      return { success: false, error: 'Cancelled by user' };
    }
    codeInput = inputVal;
  }

  // Step 4: Redeem Code
  let redeemed: {
    apiKey: string;
    keyId?: string;
    workspaceId: string;
    workspaceName: string;
    scopes?: string[];
  };

  try {
    redeemed = await redeemConnectCode(targetUrl, codeInput, 'vscode-power', os.hostname(), fetchImpl);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    void vscode.window.showErrorMessage(`LineageLens Connect Error: ${msg}`);
    return { success: false, error: msg };
  }

  // Step 5: Save Secrets & Configuration
  await context.secrets.store(SECRET_BACKEND_API_KEY, redeemed.apiKey);
  await context.secrets.store(SECRET_BACKEND_WORKSPACE_ID, redeemed.workspaceId);

  await vscode.workspace.getConfiguration('aiCodeProvenance').update('mode', 'backend', vscode.ConfigurationTarget.Global);
  await vscode.workspace.getConfiguration('aiInsertionDetector.backend').update('baseUrl', targetUrl, vscode.ConfigurationTarget.Global);

  // Surface success toast
  const healthInfo = await probeServerHealth(targetUrl, fetchImpl, 1500);
  const tierName = healthInfo.tierLabel || 'LineageLens';

  void vscode.window.showInformationMessage(
    `LineageLens: Connected to workspace "${redeemed.workspaceName}" (${tierName}) at ${targetUrl}.`
  );

  // Trigger status bar refresh
  void vscode.commands.executeCommand('lineagelens.refreshConnectionStatus');

  return {
    success: true,
    backendUrl: targetUrl,
    workspaceId: redeemed.workspaceId,
    workspaceName: redeemed.workspaceName,
    tierLabel: tierName,
  };
}

/** Disconnect from backend: clear stored API keys and reset mode to local. */
export async function disconnectFromServer(context: vscode.ExtensionContext): Promise<void> {
  await context.secrets.delete(SECRET_BACKEND_API_KEY);
  await context.secrets.delete(SECRET_BACKEND_WORKSPACE_ID);
  await vscode.workspace.getConfiguration('aiCodeProvenance').update('mode', 'local', vscode.ConfigurationTarget.Global);

  void vscode.commands.executeCommand('lineagelens.refreshConnectionStatus');
  void vscode.window.showInformationMessage('LineageLens: Disconnected from server. Switched to local mode.');
}
