/**
 * Unit tests for Base extension Easy Connect wizard, Crockford-32 normalization,
 * and SecretStorage migration.
 */

import {
  normalizeServerUrl,
  normalizeConnectCode,
  probeServerHealth,
  redeemBaseConnectCode,
  disconnectBaseServer,
  SECRET_INGEST_TOKEN,
  DEFAULT_LOCAL_BACKEND_URL,
} from '../sync/connectWizard';
import { FetchLike } from '../sync/capability';
import * as vscode from 'vscode';

describe('normalizeServerUrl', () => {
  test('defaults empty or whitespace to localhost:8787', () => {
    expect(normalizeServerUrl('')).toBe(DEFAULT_LOCAL_BACKEND_URL);
    expect(normalizeServerUrl('   ')).toBe(DEFAULT_LOCAL_BACKEND_URL);
  });

  test('prepends http:// if missing', () => {
    expect(normalizeServerUrl('localhost:8787')).toBe('http://localhost:8787');
    expect(normalizeServerUrl('lineage.mycompany.internal')).toBe('http://lineage.mycompany.internal');
  });

  test('strips trailing slashes cleanly', () => {
    expect(normalizeServerUrl('http://localhost:8787/')).toBe('http://localhost:8787');
    expect(normalizeServerUrl('https://api.lineagelens.dev///')).toBe('https://api.lineagelens.dev');
  });

  test('preserves valid https:// protocol', () => {
    expect(normalizeServerUrl('https://corp.internal:9000')).toBe('https://corp.internal:9000');
  });
});

describe('normalizeConnectCode', () => {
  test('formats standard 10-character code to LL-XXXXX-XXXXX', () => {
    expect(normalizeConnectCode('ABCDE12345')).toBe('LL-ABCDE-12345');
    expect(normalizeConnectCode('ll-abcde-12345')).toBe('LL-ABCDE-12345');
    expect(normalizeConnectCode('LLABCDE12345')).toBe('LL-ABCDE-12345');
  });

  test('corrects visually ambiguous characters (Crockford base32)', () => {
    // I, L -> 1; O -> 0; U -> V
    expect(normalizeConnectCode('ilou123456')).toBe('LL-110V1-23456');
    expect(normalizeConnectCode('LL-ILOU-123456')).toBe('LL-110V1-23456');
  });

  test('corrects exactly 10 characters with substitutions', () => {
    // I (1), L (1), O (0), U (V), A, B, C, D, E, F = 10 chars
    expect(normalizeConnectCode('ILOUABCDEF')).toBe('LL-110VA-BCDEF');
  });

  test('rejects invalid length codes', () => {
    expect(() => normalizeConnectCode('SHORT')).toThrow('Invalid pairing code format');
    expect(() => normalizeConnectCode('WAYTOOLONGCODEHERE12345')).toThrow('Invalid pairing code format');
  });

  test('rejects invalid Crockford characters', () => {
    // Crockford excludes I, L, O, U (which we map), but what about symbols?
    expect(() => normalizeConnectCode('LL-ABCDE-1234!')).toThrow('Invalid pairing code format');
  });
});

describe('probeServerHealth', () => {
  test('returns ok: true and metadata when health endpoint is 200 ok', async () => {
    const mockFetch: FetchLike = async () => ({
      status: 200,
      json: async () => ({
        status: 'ok',
        productMode: 'plus',
        tierLabel: 'LineageLens Plus',
        version: '1.2.4',
      }),
    });

    const result = await probeServerHealth('http://localhost:8787', mockFetch);
    expect(result.ok).toBe(true);
    expect(result.productMode).toBe('plus');
    expect(result.tierLabel).toBe('LineageLens Plus');
    expect(result.version).toBe('1.2.4');
  });

  test('returns ok: false on non-200 status', async () => {
    const mockFetch: FetchLike = async () => ({
      status: 503,
      json: async () => ({ error: 'Service unavailable' }),
    });

    const result = await probeServerHealth('http://localhost:8787', mockFetch);
    expect(result.ok).toBe(false);
  });

  test('returns ok: false on network error', async () => {
    const mockFetch: FetchLike = async () => {
      throw new Error('ECONNREFUSED');
    };

    const result = await probeServerHealth('http://localhost:8787', mockFetch);
    expect(result.ok).toBe(false);
  });
});

describe('redeemBaseConnectCode', () => {
  test('redeems valid code and returns apiKey and workspace details', async () => {
    const mockFetch: FetchLike = async (_url, init) => {
      const parsedBody = JSON.parse(init?.body || '{}');
      expect(parsedBody.code).toBe('LL-ABCDE-12345');
      expect(parsedBody.client).toBe('vscode-base');

      return {
        status: 200,
        json: async () => ({
          apiKey: 'llk_test_api_key_123',
          workspaceId: 'ws-789',
          workspaceName: 'Engineering Core',
          scopes: ['provenance:write'],
        }),
      };
    };

    const res = await redeemBaseConnectCode(
      'http://localhost:8787',
      'abcde12345',
      'vscode-base',
      'test-host',
      mockFetch
    );

    expect(res.apiKey).toBe('llk_test_api_key_123');
    expect(res.workspaceId).toBe('ws-789');
    expect(res.workspaceName).toBe('Engineering Core');
  });

  test('maps 404 to code not found error', async () => {
    const mockFetch: FetchLike = async () => ({
      status: 404,
      json: async () => ({ detail: 'Pairing code not found' }),
    });

    await expect(
      redeemBaseConnectCode('http://localhost:8787', 'abcde12345', 'vscode-base', 'test-host', mockFetch)
    ).rejects.toThrow('Pairing code not found');
  });

  test('maps 410 to expired/used code error', async () => {
    const mockFetch: FetchLike = async () => ({
      status: 410,
      json: async () => ({ detail: 'Pairing code has expired' }),
    });

    await expect(
      redeemBaseConnectCode('http://localhost:8787', 'abcde12345', 'vscode-base', 'test-host', mockFetch)
    ).rejects.toThrow('no longer valid');
  });

  test('maps 429 to rate limit error', async () => {
    const mockFetch: FetchLike = async () => ({
      status: 429,
      json: async () => ({ detail: 'Too many requests' }),
    });

    await expect(
      redeemBaseConnectCode('http://localhost:8787', 'abcde12345', 'vscode-base', 'test-host', mockFetch)
    ).rejects.toThrow('Too many redemption attempts');
  });
});

describe('disconnectBaseServer', () => {
  test('deletes secret token and clears settings', async () => {
    let deletedKey = '';
    const updatedConfigs: Record<string, unknown> = {};

    const mockContext = {
      secrets: {
        delete: async (key: string) => {
          deletedKey = key;
        },
      },
    } as unknown as vscode.ExtensionContext;

    // Spy on workspace getConfiguration
    const origGetConfig = vscode.workspace.getConfiguration;
    (vscode.workspace as any).getConfiguration = () => ({
      get: (k: string) => (k === 'ingestToken' ? 'legacy-plaintext-token' : ''),
      update: async (k: string, v: unknown) => {
        updatedConfigs[k] = v;
      },
    });

    try {
      await disconnectBaseServer(mockContext);
      expect(deletedKey).toBe(SECRET_INGEST_TOKEN);
      expect(updatedConfigs['backendUrl']).toBe('');
      expect(updatedConfigs['workspaceId']).toBe('');
      expect(updatedConfigs['ingestToken']).toBeUndefined();
    } finally {
      (vscode.workspace as any).getConfiguration = origGetConfig;
    }
  });
});
