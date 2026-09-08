import * as os from 'node:os';
import * as vscode from 'vscode';
import { detectCapability, FetchLike } from './capability';

export const SECRET_INGEST_TOKEN = 'lineagelensBase.ingestToken';
export const DEFAULT_LOCAL_BACKEND_URL = 'http://localhost:8787';

const CODE_PREFIX = 'LL-';
const CODE_BODY_LEN = 10;
const CROCKFORD_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';

export interface BaseServerHealth {
  ok: boolean;
  productMode?: string;
  tierLabel?: string;
  version?: string;
}

export interface BaseConnectResult {
  success: boolean;
  backendUrl?: string;
  workspaceId?: string;
  workspaceName?: string;
  error?: string;
}

/** Normalize server URL: strip trailing slashes, prepend http:// if missing. */
export function normalizeServerUrl(raw: string): string {
  let url = (raw || '').trim();
  if (!url) {
    return DEFAULT_LOCAL_BACKEND_URL;
  }
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    url = 'http://' + url;
  }
  return url.replace(/\/+$/, '');
}

/** Normalize pairing code: Crockford base32 (no I, L, O, U), returns LL-XXXXX-XXXXX. */
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

/** Probe server /health endpoint to verify connectivity and product details. */
export async function probeServerHealth(
  serverUrl: string,
  fetchImpl?: FetchLike,
  timeoutMs = 2500
): Promise<BaseServerHealth> {
  const doFetch = fetchImpl ?? (globalThis.fetch as unknown as FetchLike);
  const normalized = normalizeServerUrl(serverUrl);

  try {
    const resp = await doFetch(`${normalized}/health`, { method: 'GET' });
    if (resp.status !== 200) {
      return { ok: false };
    }
    const data = (await resp.json()) as Record<string, unknown>;
    if (data && typeof data === 'object' && data.status === 'ok') {
      return {
        ok: true,
        productMode: typeof data.productMode === 'string' ? data.productMode : undefined,
        tierLabel: typeof data.tierLabel === 'string' ? data.tierLabel : 'LineageLens',
        version: typeof data.version === 'string' ? data.version : undefined,
      };
    }
    return { ok: false };
  } catch {
    return { ok: false };
  }
}

/** Redeem pairing code via POST /connect/redeem. */
export async function redeemBaseConnectCode(
  backendUrl: string,
  code: string,
  client = 'vscode-base',
  label?: string,
  fetchImpl?: FetchLike
): Promise<{
  apiKey: string;
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

  if (resp.status !== 200) {
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
    workspaceId: string;
    workspaceName?: string;
    scopes?: string[];
  };

  if (!result || !result.apiKey || !result.workspaceId) {
    throw new Error('Server returned an invalid pairing response (missing apiKey or workspaceId).');
  }

  return {
    apiKey: result.apiKey,
    workspaceId: result.workspaceId,
    workspaceName: result.workspaceName || result.workspaceId,
    scopes: result.scopes,
  };
}

export interface BaseConnectWizardOptions {
  prefilledServer?: string;
  prefilledCode?: string;
}

/**
 * Interactive Connect Wizard for LineageLens Base Extension.
 *
 * 1. Probes for a local server or prompts for a custom server URL.
 * 2. Prompts user for pairing code (LL-XXXXX-XXXXX).
 * 3. Redeems pairing code and stores API key in SecretStorage (replacing plaintext settings).
 * 4. Configures backendUrl and workspaceId settings.
 * 5. Shows success notification.
 */
export async function runBaseConnectWizard(
  context: vscode.ExtensionContext,
  fetchImpl?: FetchLike,
  options?: BaseConnectWizardOptions
): Promise<BaseConnectResult> {
  let targetUrl: string | undefined;

  if (options?.prefilledServer) {
    targetUrl = normalizeServerUrl(options.prefilledServer);
  } else {
    // Step 1: Detect or select backend URL
    const localHealth = await probeServerHealth(DEFAULT_LOCAL_BACKEND_URL, fetchImpl, 1500);

    if (localHealth.ok) {
      const localChoice = await vscode.window.showQuickPick(
        [
          {
            label: `$(server) Use local server (${DEFAULT_LOCAL_BACKEND_URL})`,
            description: `${localHealth.tierLabel || 'LineageLens'}${localHealth.version ? ` v${localHealth.version}` : ''}`,
            url: DEFAULT_LOCAL_BACKEND_URL,
          },
          {
            label: '$(edit) Enter a different server URL...',
            description: 'Connect to an internal team or remote LineageLens server',
            url: '',
          },
        ],
        {
          title: 'LineageLens Base: Connect to Server (1/2)',
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
    const currentUrl = vscode.workspace.getConfiguration('lineagelensBase').get<string>('backendUrl') || DEFAULT_LOCAL_BACKEND_URL;
    const inputUrl = await vscode.window.showInputBox({
      title: 'LineageLens Base: Connect to Server (1/2)',
      prompt: 'Enter your LineageLens server URL (e.g. http://localhost:8787)',
      value: currentUrl,
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

    // Validate server reachability
    const health = await probeServerHealth(targetUrl, fetchImpl, 2500);
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
    // Step 2: Prompt Connect Code
    const inputVal = await vscode.window.showInputBox({
      title: 'LineageLens Base: Connect to Server (2/2)',
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

  // Step 3: Redeem Code
  let redeemed: {
    apiKey: string;
    workspaceId: string;
    workspaceName: string;
    scopes?: string[];
  };

  try {
    redeemed = await redeemBaseConnectCode(targetUrl, codeInput, 'vscode-base', os.hostname(), fetchImpl);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    void vscode.window.showErrorMessage(`LineageLens Base Connect Error: ${msg}`);
    return { success: false, error: msg };
  }

  // Step 4: Store token in SecretStorage (encrypted) and configure settings
  await context.secrets.store(SECRET_INGEST_TOKEN, redeemed.apiKey);

  const baseConfig = vscode.workspace.getConfiguration('lineagelensBase');
  await baseConfig.update('backendUrl', targetUrl, vscode.ConfigurationTarget.Global);
  await baseConfig.update('workspaceId', redeemed.workspaceId, vscode.ConfigurationTarget.Global);

  // If a legacy plaintext ingestToken exists in settings.json, remove it for security
  if (baseConfig.get<string>('ingestToken')) {
    await baseConfig.update('ingestToken', undefined, vscode.ConfigurationTarget.Global);
  }

  // Step 5: Verification probe
  await detectCapability(targetUrl, fetchImpl);

  void vscode.window.showInformationMessage(
    `LineageLens Base: Connected to workspace "${redeemed.workspaceName}" at ${targetUrl}. Captures will sync automatically.`
  );

  return {
    success: true,
    backendUrl: targetUrl,
    workspaceId: redeemed.workspaceId,
    workspaceName: redeemed.workspaceName,
  };
}

/** Disconnect Base extension from server: remove secret token and reset server settings. */
export async function disconnectBaseServer(context: vscode.ExtensionContext): Promise<void> {
  await context.secrets.delete(SECRET_INGEST_TOKEN);

  const baseConfig = vscode.workspace.getConfiguration('lineagelensBase');
  await baseConfig.update('backendUrl', '', vscode.ConfigurationTarget.Global);
  await baseConfig.update('workspaceId', '', vscode.ConfigurationTarget.Global);
  if (baseConfig.get<string>('ingestToken')) {
    await baseConfig.update('ingestToken', undefined, vscode.ConfigurationTarget.Global);
  }

  void vscode.window.showInformationMessage('LineageLens Base: Disconnected from server. Captures will remain local only.');
}
