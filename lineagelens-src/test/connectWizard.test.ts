import assert from 'node:assert/strict';
import test from 'node:test';
import {
  normalizeServerUrl,
  normalizeConnectCode,
  probeServerHealth,
  redeemConnectCode,
  FetchLike,
} from '../connectWizard';

test('normalizeServerUrl handles empty, trailing slashes, and missing scheme', () => {
  assert.equal(normalizeServerUrl(''), 'http://localhost:8787');
  assert.equal(normalizeServerUrl('   '), 'http://localhost:8787');
  assert.equal(normalizeServerUrl('http://localhost:8787/'), 'http://localhost:8787');
  assert.equal(normalizeServerUrl('http://localhost:8787///'), 'http://localhost:8787');
  assert.equal(normalizeServerUrl('localhost:8787'), 'http://localhost:8787');
  assert.equal(normalizeServerUrl('https://lineage.mycorp.internal/'), 'https://lineage.mycorp.internal');
});

test('normalizeConnectCode formats valid codes and strips prefixes', () => {
  assert.equal(normalizeConnectCode('LL-01234-56789'), 'LL-01234-56789');
  assert.equal(normalizeConnectCode('ll-01234-56789'), 'LL-01234-56789');
  assert.equal(normalizeConnectCode('01234-56789'), 'LL-01234-56789');
  assert.equal(normalizeConnectCode('0123456789'), 'LL-01234-56789');
  assert.equal(normalizeConnectCode('  ll - 01234 - 56789  '), 'LL-01234-56789');
});

test('normalizeConnectCode maps Crockford confusables I, L, O, U', () => {
  // Alphabet: no I (maps to 1), no L (maps to 1), no O (maps to 0), no U (maps to V)
  // Input body: I L O U A B C D E F -> 1 1 0 V A B C D E F
  assert.equal(normalizeConnectCode('ILOUA-BCDEF'), 'LL-110VA-BCDEF');
  assert.equal(normalizeConnectCode('ll-iloua-bcdef'), 'LL-110VA-BCDEF');
});

test('normalizeConnectCode rejects invalid lengths and invalid characters', () => {
  assert.throws(() => normalizeConnectCode('too-short'), /Invalid pairing code format/);
  assert.throws(() => normalizeConnectCode('0123456789ABC'), /Invalid pairing code format/);
  assert.throws(() => normalizeConnectCode('LL-#####-ZZZZZ'), /Invalid pairing code format/);
});

test('probeServerHealth detects healthy server and tier', async () => {
  const mockFetch: FetchLike = async (url) => {
    assert.equal(url, 'http://localhost:8787/health');
    return {
      ok: true,
      status: 200,
      json: async () => ({
        status: 'ok',
        app: 'LineageLens Server',
        productMode: 'plus',
        tierLabel: 'LineageLens Plus',
        version: '1.2.4',
      }),
    };
  };

  const res = await probeServerHealth('http://localhost:8787', mockFetch);
  assert.equal(res.ok, true);
  assert.equal(res.productMode, 'plus');
  assert.equal(res.tierLabel, 'LineageLens Plus');
  assert.equal(res.version, '1.2.4');
});

test('probeServerHealth returns ok:false on network or 500 error', async () => {
  const failingFetch: FetchLike = async () => {
    return {
      ok: false,
      status: 503,
      json: async () => ({ status: 'unhealthy' }),
    };
  };

  const res = await probeServerHealth('http://localhost:8787', failingFetch);
  assert.equal(res.ok, false);

  const errorFetch: FetchLike = async () => {
    throw new Error('ECONNREFUSED');
  };
  const res2 = await probeServerHealth('http://localhost:8787', errorFetch);
  assert.equal(res2.ok, false);
});

test('redeemConnectCode succeeds and returns credentials on valid 200 response', async () => {
  let capturedBody: any;
  const mockFetch: FetchLike = async (url, init) => {
    assert.equal(url, 'http://localhost:8787/connect/redeem');
    assert.equal(init?.method, 'POST');
    capturedBody = JSON.parse(init?.body || '{}');

    return {
      ok: true,
      status: 200,
      json: async () => ({
        apiKey: 'llk_test_secret_token_12345',
        keyId: 'k123',
        workspaceId: 'ws-alpha',
        workspaceName: 'Alpha Workspace',
        scopes: ['read', 'write', 'provenance:read', 'provenance:write'],
      }),
    };
  };

  const res = await redeemConnectCode(
    'http://localhost:8787',
    'LL-ABCDE-FGH1K',
    'vscode-power',
    'dev-machine',
    mockFetch
  );

  assert.equal(capturedBody.code, 'LL-ABCDE-FGH1K');
  assert.equal(capturedBody.client, 'vscode-power');
  assert.equal(capturedBody.label, 'dev-machine');

  assert.equal(res.apiKey, 'llk_test_secret_token_12345');
  assert.equal(res.workspaceId, 'ws-alpha');
  assert.equal(res.workspaceName, 'Alpha Workspace');
});

test('redeemConnectCode maps 404, 410, 429 to informative errors', async () => {
  const notFoundFetch: FetchLike = async () => ({
    ok: false,
    status: 404,
    json: async () => ({ detail: 'Not found' }),
  });
  await assert.rejects(
    () => redeemConnectCode('http://localhost:8787', 'LL-ABCDE-FGH1K', 'vscode-power', undefined, notFoundFetch),
    /Pairing code not found/
  );

  const expiredFetch: FetchLike = async () => ({
    ok: false,
    status: 410,
    json: async () => ({ detail: 'This pairing code has expired.' }),
  });
  await assert.rejects(
    () => redeemConnectCode('http://localhost:8787', 'LL-ABCDE-FGH1K', 'vscode-power', undefined, expiredFetch),
    /This pairing code is no longer valid/
  );

  const rateLimitFetch: FetchLike = async () => ({
    ok: false,
    status: 429,
    json: async () => ({ detail: 'Rate limit exceeded' }),
  });
  await assert.rejects(
    () => redeemConnectCode('http://localhost:8787', 'LL-ABCDE-FGH1K', 'vscode-power', undefined, rateLimitFetch),
    /Too many redemption attempts/
  );
});
