import { buildIngestPayload } from '../sync/payload';
import { parseCapability, detectCapability, FetchLike } from '../sync/capability';
import {
  enqueueEntry,
  backoffDelay,
  dueEntries,
  OutboxEntry,
  RETRY_BASE_MS,
  RETRY_MAX_MS,
} from '../sync/outbox';
import { CaptureRecord } from '../store';

function rec(over: Partial<CaptureRecord> = {}): CaptureRecord {
  return {
    id: 'id1',
    timestamp: '2026-06-25T10:00:00.000Z',
    filePath: '/repo/a.ts',
    fileName: 'a.ts',
    language: 'typescript',
    insertedCode: 'const x = 1;',
    linesAdded: 4,
    workspaceFolder: null,
    confidence: 0.8,
    source: 'ai',
    schemaVersion: 2,
    reviewState: 'reviewed',
    lineageState: 'modified',
    rangeContentHash: 'hash123',
    eventHash: 'ev1',
    prevHash: 'prev0',
    riskSignals: [{ id: 'generated-auth-code', label: 'auth', category: 'auth', severity: 'high', message: 'm' }],
    ...over,
  };
}

// ── buildIngestPayload ────────────────────────────────────────────────────────

test('legacy payload carries only the original file-level fields', () => {
  const p = buildIngestPayload(rec(), { workspaceId: 'ws', redact: true, capability: 'legacy' });
  expect(p).toEqual({
    id: 'id1',
    timestampIso: '2026-06-25T10:00:00.000Z',
    filePath: '/repo/a.ts',
    insertedText: 'const x = 1;',
    netAddedLines: 4,
    workspaceId: 'ws',
    languageId: 'typescript',
    captureStatus: 'file_diff',
    source: { shim: 'lineagelens-base-extension', ide: 'vscode' },
  });
});

test('evidence-v2 payload attaches the local trust layer losslessly', () => {
  const p = buildIngestPayload(rec(), { workspaceId: 'ws', redact: true, capability: 'evidence-v2' });
  expect(p.eventHash).toBe('ev1');
  expect(p.prevHash).toBe('prev0');
  expect(p.rangeContentHash).toBe('hash123');
  expect(p.reviewState).toBe('reviewed');
  expect(p.lineageState).toBe('modified');
  expect(p.detectedSource).toBe('ai');
  expect((p.riskSignals as unknown[]).length).toBe(1);
  // The shim descriptor is preserved and not clobbered by detectedSource.
  expect(p.source).toEqual({ shim: 'lineagelens-base-extension', ide: 'vscode' });
});

test('payload redacts secrets in the inserted text on egress', () => {
  const p = buildIngestPayload(
    rec({ insertedCode: 'const k = "sk-ant-abcdefghijklmnop1234567890";' }),
    { workspaceId: 'ws', redact: true, capability: 'legacy' },
  );
  expect(p.insertedText).not.toContain('sk-ant-');
  expect(p.insertedText).toContain('[REDACTED]');
});

test('payload keeps raw text when redaction is off', () => {
  const p = buildIngestPayload(
    rec({ insertedCode: 'token sk-ant-abcdefghijklmnop1234567890' }),
    { workspaceId: 'ws', redact: false, capability: 'legacy' },
  );
  expect(p.insertedText).toContain('sk-ant-');
});

// ── capability ────────────────────────────────────────────────────────────────

test('parseCapability recognizes evidence-v2 via flag or capabilities array', () => {
  expect(parseCapability(200, { evidenceV2: true })).toBe('evidence-v2');
  expect(parseCapability(200, { capabilities: ['evidence-v2'] })).toBe('evidence-v2');
});

test('parseCapability defaults to legacy for unknown/error responses', () => {
  expect(parseCapability(200, {})).toBe('legacy');
  expect(parseCapability(404, null)).toBe('legacy');
  expect(parseCapability(200, 'not-json')).toBe('legacy');
});

test('detectCapability uses the response and degrades on fetch error', async () => {
  const ok: FetchLike = async () => ({ status: 200, json: async () => ({ evidenceV2: true }) });
  await expect(detectCapability('http://b', ok)).resolves.toBe('evidence-v2');

  const boom: FetchLike = async () => { throw new Error('offline'); };
  await expect(detectCapability('http://b', boom)).resolves.toBe('legacy');
});

// ── outbox helpers ────────────────────────────────────────────────────────────

function entry(id: string, over: Partial<OutboxEntry> = {}): OutboxEntry {
  return { id, payload: { id }, attempts: 0, nextRetryAt: 0, ...over };
}

test('enqueueEntry replaces by id (idempotency key)', () => {
  const start = [entry('a'), entry('b')];
  const out = enqueueEntry(start, entry('a', { attempts: 5 }));
  expect(out).toHaveLength(2);
  expect(out.find((e) => e.id === 'a')?.attempts).toBe(5);
});

test('enqueueEntry caps the queue and drops the oldest', () => {
  let entries: OutboxEntry[] = [];
  for (let i = 0; i < 205; i++) {
    entries = enqueueEntry(entries, entry(`id-${i}`));
  }
  expect(entries).toHaveLength(200);
  expect(entries.find((e) => e.id === 'id-0')).toBeUndefined();
  expect(entries.find((e) => e.id === 'id-5')).toBeDefined();
});

test('backoffDelay grows exponentially and is capped', () => {
  expect(backoffDelay(1)).toBe(RETRY_BASE_MS);
  expect(backoffDelay(2)).toBe(RETRY_BASE_MS * 2);
  expect(backoffDelay(3)).toBe(RETRY_BASE_MS * 4);
  expect(backoffDelay(100)).toBe(RETRY_MAX_MS);
});

test('dueEntries selects entries whose retry time has passed', () => {
  const now = 1000;
  const entries = [entry('a', { nextRetryAt: 500 }), entry('b', { nextRetryAt: 1500 })];
  expect(dueEntries(entries, now).map((e) => e.id)).toEqual(['a']);
});
