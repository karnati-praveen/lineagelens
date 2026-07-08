import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { randomBytes } from 'crypto';

import { CaptureStore, CAPTURE_SCHEMA_VERSION } from '../store';
import { rangeContentHash } from '../evidence/hash';
import { verifyChain } from '../evidence/hashChain';

let tmpDir: string;

function makeContext() {
  return { globalStorageUri: { fsPath: tmpDir } } as any;
}

// Context whose secrets stub is backed by an in-memory map, mimicking the
// VS Code SecretStorage API used by CaptureStore.create.
function makeContextWithSecrets() {
  const secretMap = new Map<string, string>();
  return {
    globalStorageUri: { fsPath: tmpDir },
    secrets: {
      get: (k: string) => Promise.resolve(secretMap.get(k)),
      store: (k: string, v: string) => { secretMap.set(k, v); return Promise.resolve(); },
      delete: (k: string) => { secretMap.delete(k); return Promise.resolve(); },
    },
  } as any;
}

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'll-test-'));
});

afterEach(() => {
  // A debounced background save can briefly re-create the store file in this
  // soon-to-be-removed dir (it never targets the next test's fresh dir), which
  // makes the final rmdir race with ENOTEMPTY on Windows. Best-effort cleanup.
  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch { /* tolerate teardown race */ }
});

test('starts empty', () => {
  const store = new CaptureStore(makeContext());
  expect(store.count).toBe(0);
  expect(store.getAll()).toHaveLength(0);
});

test('add returns a record with id and timestamp', () => {
  const store = new CaptureStore(makeContext());
  const rec = store.add({
    filePath: '/foo/bar.ts',
    fileName: 'bar.ts',
    language: 'typescript',
    insertedCode: 'const x = 1;\n'.repeat(5),
    linesAdded: 5,
    workspaceFolder: null,
  });
  expect(rec.id).toMatch(/^[0-9a-f-]{36}$/);
  expect(rec.timestamp).toBeTruthy();
  expect(rec.fileName).toBe('bar.ts');
  expect(store.count).toBe(1);
});

test('getById returns the correct record', () => {
  const store = new CaptureStore(makeContext());
  const r1 = store.add({ filePath: '/a.py', fileName: 'a.py', language: 'python', insertedCode: 'x', linesAdded: 1, workspaceFolder: null });
  const r2 = store.add({ filePath: '/b.py', fileName: 'b.py', language: 'python', insertedCode: 'y', linesAdded: 1, workspaceFolder: null });
  expect(store.getById(r1.id)?.fileName).toBe('a.py');
  expect(store.getById(r2.id)?.fileName).toBe('b.py');
  expect(store.getById('nonexistent')).toBeUndefined();
});

test('getAll returns records newest first', () => {
  const store = new CaptureStore(makeContext());
  store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'a', linesAdded: 1, workspaceFolder: null });
  store.add({ filePath: '/b.ts', fileName: 'b.ts', language: 'typescript', insertedCode: 'b', linesAdded: 1, workspaceFolder: null });
  const all = store.getAll();
  expect(all[0].fileName).toBe('b.ts');
  expect(all[1].fileName).toBe('a.ts');
});

test('clear empties the store', () => {
  const store = new CaptureStore(makeContext());
  store.add({ filePath: '/x.ts', fileName: 'x.ts', language: 'typescript', insertedCode: 'hi', linesAdded: 1, workspaceFolder: null });
  store.clear();
  expect(store.count).toBe(0);
  expect(store.getAll()).toHaveLength(0);
});

test('exportJson returns valid JSON with all records', () => {
  const store = new CaptureStore(makeContext());
  store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'code', linesAdded: 2, workspaceFolder: 'proj' });
  const parsed = JSON.parse(store.exportJson());
  expect(Array.isArray(parsed)).toBe(true);
  expect(parsed).toHaveLength(1);
  expect(parsed[0].fileName).toBe('a.ts');
  expect(parsed[0].workspaceFolder).toBe('proj');
});

test('persists to disk and reloads on new instance', async () => {
  const ctx = makeContext();
  const store = new CaptureStore(ctx);
  const rec = store.add({ filePath: '/p.ts', fileName: 'p.ts', language: 'typescript', insertedCode: 'persist', linesAdded: 3, workspaceFolder: null });
  await store.flush();

  const store2 = new CaptureStore(ctx);
  expect(store2.count).toBe(1);
  expect(store2.getById(rec.id)?.fileName).toBe('p.ts');
});

test('enforces maxCaptures (default 1000)', () => {
  const store = new CaptureStore(makeContext());
  for (let i = 0; i < 1005; i++) {
    store.add({ filePath: `/f${i}.ts`, fileName: `f${i}.ts`, language: 'typescript', insertedCode: 'x', linesAdded: 1, workspaceFolder: null });
  }
  expect(store.count).toBe(1000);
});

// ── confidence / source fields ────────────────────────────────────────────────

test('add defaults confidence to 0.5 and source to unknown when omitted', () => {
  const store = new CaptureStore(makeContext());
  const rec = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'x', linesAdded: 1, workspaceFolder: null });
  expect(rec.confidence).toBe(0.5);
  expect(rec.source).toBe('unknown');
});

test('add persists explicit confidence and source', () => {
  const store = new CaptureStore(makeContext());
  const rec = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'x', linesAdded: 4, workspaceFolder: null, confidence: 0.8, source: 'ai' });
  expect(rec.confidence).toBe(0.8);
  expect(rec.source).toBe('ai');
});

test('load backfills confidence and source for legacy records lacking them', () => {
  const ctx = makeContext();
  // Write a legacy-format captures.json without confidence/source.
  const legacyRecords = [{ id: 'aaa', timestamp: new Date().toISOString(), filePath: '/x.ts', fileName: 'x.ts', language: 'typescript', insertedCode: 'hi', linesAdded: 2, workspaceFolder: null }];
  fs.writeFileSync(require('path').join(tmpDir, 'captures.json'), JSON.stringify(legacyRecords), 'utf-8');
  const store = new CaptureStore(ctx);
  const rec = store.getById('aaa');
  expect(rec?.confidence).toBe(0.5);
  expect(rec?.source).toBe('unknown');
});

// ── remove() ──────────────────────────────────────────────────────────────────

test('remove deletes a single record and returns true', () => {
  const store = new CaptureStore(makeContext());
  const a = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'a', linesAdded: 1, workspaceFolder: null });
  const b = store.add({ filePath: '/b.ts', fileName: 'b.ts', language: 'typescript', insertedCode: 'b', linesAdded: 1, workspaceFolder: null });
  expect(store.remove(a.id)).toBe(true);
  expect(store.count).toBe(1);
  expect(store.getById(a.id)).toBeUndefined();
  expect(store.getById(b.id)?.fileName).toBe('b.ts');
});

test('remove returns false for an unknown id and leaves the store intact', () => {
  const store = new CaptureStore(makeContext());
  store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'a', linesAdded: 1, workspaceFolder: null });
  expect(store.remove('nope')).toBe(false);
  expect(store.count).toBe(1);
});

test('remove persists to disk', async () => {
  const ctx = makeContext();
  const store = new CaptureStore(ctx);
  const a = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'a', linesAdded: 1, workspaceFolder: null });
  store.remove(a.id);
  await store.flush();
  const store2 = new CaptureStore(ctx);
  expect(store2.count).toBe(0);
});

// ── setClassification() ───────────────────────────────────────────────────────

test('setClassification updates source and applies default confidence', () => {
  const store = new CaptureStore(makeContext());
  const a = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'a', linesAdded: 1, workspaceFolder: null });
  const ai = store.setClassification(a.id, 'ai');
  expect(ai?.source).toBe('ai');
  expect(ai?.confidence).toBe(0.99);
  const paste = store.setClassification(a.id, 'paste');
  expect(paste?.confidence).toBe(0.95);
  const unknown = store.setClassification(a.id, 'unknown');
  expect(unknown?.confidence).toBe(0.5);
});

test('setClassification honours an explicit confidence override', () => {
  const store = new CaptureStore(makeContext());
  const a = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'a', linesAdded: 1, workspaceFolder: null });
  const rec = store.setClassification(a.id, 'ai', 0.7);
  expect(rec?.confidence).toBe(0.7);
});

test('setClassification returns undefined for an unknown id', () => {
  const store = new CaptureStore(makeContext());
  expect(store.setClassification('nope', 'ai')).toBeUndefined();
});

// ── at-rest encryption ──────────────────────────────────────────────────────────

const CAPTURES_PATH = () => path.join(tmpDir, 'captures.json');

test('create() writes an encrypted store, not plaintext', async () => {
  const ctx = makeContextWithSecrets();
  const store = await CaptureStore.create(ctx);
  store.add({ filePath: '/s.ts', fileName: 's.ts', language: 'typescript', insertedCode: 'SECRET_TOKEN_abc123', linesAdded: 1, workspaceFolder: null });
  await store.flush();

  const onDisk = fs.readFileSync(CAPTURES_PATH(), 'utf-8');
  expect(onDisk.startsWith('LLENC1:')).toBe(true);
  // The sensitive content must not be readable in the file.
  expect(onDisk).not.toContain('SECRET_TOKEN_abc123');
  expect(onDisk).not.toContain('s.ts');
});

test('create() reloads and decrypts persisted records', async () => {
  const ctx = makeContextWithSecrets();
  const store = await CaptureStore.create(ctx);
  const rec = store.add({ filePath: '/r.ts', fileName: 'r.ts', language: 'typescript', insertedCode: 'roundtrip', linesAdded: 2, workspaceFolder: null });
  await store.flush();

  const store2 = await CaptureStore.create(ctx);
  expect(store2.count).toBe(1);
  expect(store2.getById(rec.id)?.insertedCode).toBe('roundtrip');
});

test('create() migrates a legacy plaintext store to encrypted on load', async () => {
  // Seed a legacy plaintext captures.json.
  const legacy = [{ id: 'lll', timestamp: new Date().toISOString(), filePath: '/x.ts', fileName: 'x.ts', language: 'typescript', insertedCode: 'legacy', linesAdded: 1, workspaceFolder: null }];
  fs.writeFileSync(CAPTURES_PATH(), JSON.stringify(legacy), 'utf-8');

  const ctx = makeContextWithSecrets();
  const store = await CaptureStore.create(ctx);
  expect(store.getById('lll')?.insertedCode).toBe('legacy');
  await store.flush();

  // After migration the file is encrypted.
  const onDisk = fs.readFileSync(CAPTURES_PATH(), 'utf-8');
  expect(onDisk.startsWith('LLENC1:')).toBe(true);
  expect(onDisk).not.toContain('legacy');
});

test('encrypted store is unreadable with the wrong key', async () => {
  const ctx = makeContext();
  const goodKey = randomBytes(32);
  const store = new CaptureStore(ctx, goodKey);
  store.add({ filePath: '/k.ts', fileName: 'k.ts', language: 'typescript', insertedCode: 'guarded', linesAdded: 1, workspaceFolder: null });

  // A store opened with a different key cannot decrypt → empty (load swallows error).
  const wrongKey = randomBytes(32);
  const store2 = new CaptureStore(ctx, wrongKey);
  expect(store2.count).toBe(0);
});

// ── reorder() ─────────────────────────────────────────────────────────────────

function makeThree() {
  const ctx = { globalStorageUri: { fsPath: fs.mkdtempSync(require('path').join(require('os').tmpdir(), 'll-ro-')) } } as any;
  const store = new CaptureStore(ctx);
  // add returns newest-first, so after three adds the order is c, b, a
  const a = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'a', linesAdded: 1, workspaceFolder: null });
  const b = store.add({ filePath: '/b.ts', fileName: 'b.ts', language: 'typescript', insertedCode: 'b', linesAdded: 1, workspaceFolder: null });
  const c = store.add({ filePath: '/c.ts', fileName: 'c.ts', language: 'typescript', insertedCode: 'c', linesAdded: 1, workspaceFolder: null });
  return { store, ctx, a, b, c };
}

test('reorder null targetId moves item to top', () => {
  const { store, a, b, c } = makeThree();
  // initial order: c, b, a
  store.reorder([a.id], null);
  const names = store.getAll().map(r => r.fileName);
  expect(names[0]).toBe('a.ts');
  expect(names).toHaveLength(3);
});

test('reorder undefined targetId moves item to bottom', () => {
  const { store, a, b, c } = makeThree();
  store.reorder([c.id], undefined);
  const names = store.getAll().map(r => r.fileName);
  expect(names[names.length - 1]).toBe('c.ts');
  expect(names).toHaveLength(3);
});

test('reorder string targetId inserts before target', () => {
  const { store, a, b, c } = makeThree();
  // order: c, b, a — move a before b
  store.reorder([a.id], b.id);
  const names = store.getAll().map(r => r.fileName);
  const aIdx = names.indexOf('a.ts');
  const bIdx = names.indexOf('b.ts');
  expect(aIdx).toBeLessThan(bIdx);
  expect(names).toHaveLength(3);
});

test('reorder targetId in dragged set uses original position', () => {
  const { store, a, b, c } = makeThree();
  // drag b, drop onto b — no corruption, b stays somewhere in the list
  store.reorder([b.id], b.id);
  const names = store.getAll().map(r => r.fileName);
  expect(names).toHaveLength(3);
  expect(names).toContain('a.ts');
  expect(names).toContain('b.ts');
  expect(names).toContain('c.ts');
});

test('reorder empty draggedIds is a no-op', () => {
  const { store, a, b, c } = makeThree();
  const before = store.getAll().map(r => r.id);
  store.reorder([], null);
  expect(store.getAll().map(r => r.id)).toEqual(before);
});

test('reorder unknown draggedId is a no-op', () => {
  const { store } = makeThree();
  const before = store.getAll().map(r => r.id);
  store.reorder(['does-not-exist'], null);
  expect(store.getAll().map(r => r.id)).toEqual(before);
});

test('reorder multi-select with null puts all dragged items at top in drag order', () => {
  const { store, a, b, c } = makeThree();
  // order: c, b, a — drag a and c to top
  store.reorder([a.id, c.id], null);
  const names = store.getAll().map(r => r.fileName);
  expect(names[0]).toBe('a.ts');
  expect(names[1]).toBe('c.ts');
  expect(names[2]).toBe('b.ts');
});

test('reorder persists to disk', async () => {
  const { store, ctx, a, b, c } = makeThree();
  store.reorder([a.id], null);
  await store.flush();
  const store2 = new CaptureStore(ctx);
  expect(store2.getAll()[0].fileName).toBe('a.ts');
});

// ── v2 evidence layer (schemaVersion / reviewState / lineageState / hash) ───────

test('add() stamps a new record with the current schema version and defaults', () => {
  const store = new CaptureStore(makeContext());
  const rec = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'const x = 1;\n'.repeat(4), linesAdded: 4, workspaceFolder: null });
  expect(rec.schemaVersion).toBe(CAPTURE_SCHEMA_VERSION);
  expect(rec.reviewState).toBe('unreviewed');
  // A freshly inserted block is at its original location.
  expect(rec.lineageState).toBe('original');
  // Content hash is computed from the inserted code.
  expect(rec.rangeContentHash).toBe(rangeContentHash(rec.insertedCode));
});

test('add() lets a caller override evidence fields', () => {
  const store = new CaptureStore(makeContext());
  const rec = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'x', linesAdded: 1, workspaceFolder: null, startLine: 10, endLine: 14, lineageState: 'unknown' });
  expect(rec.startLine).toBe(10);
  expect(rec.endLine).toBe(14);
  expect(rec.lineageState).toBe('unknown');
});

// ── setReviewState() ──────────────────────────────────────────────────────────

test('setReviewState applies a legal transition and stamps reviewedAt', () => {
  const store = new CaptureStore(makeContext());
  const a = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'x', linesAdded: 4, workspaceFolder: null });
  expect(a.reviewState).toBe('unreviewed');
  const out = store.setReviewState(a.id, 'reviewed', 'LGTM');
  expect(out?.reviewState).toBe('reviewed');
  expect(out?.reviewNote).toBe('LGTM');
  expect(out?.reviewedAt).toBeTruthy();
  expect(store.getById(a.id)?.reviewState).toBe('reviewed');
});

test('setReviewState leaves the record unchanged on an illegal transition', () => {
  const store = new CaptureStore(makeContext());
  const a = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'x', linesAdded: 4, workspaceFolder: null });
  const out = store.setReviewState(a.id, 'accepted'); // unreviewed → accepted is illegal
  expect(out?.reviewState).toBe('unreviewed');
});

test('setReviewState returns undefined for an unknown id', () => {
  const store = new CaptureStore(makeContext());
  expect(store.setReviewState('nope', 'reviewed')).toBeUndefined();
});

test('setReviewState persists across reload', async () => {
  const ctx = makeContext();
  const store = new CaptureStore(ctx);
  const a = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'x', linesAdded: 4, workspaceFolder: null });
  store.setReviewState(a.id, 'reviewed');
  await store.flush();
  const store2 = new CaptureStore(ctx);
  expect(store2.getById(a.id)?.reviewState).toBe('reviewed');
});

test('load() migrates a legacy v1 record to schema v2 with backfilled fields', () => {
  const ctx = makeContext();
  // Legacy record: no schemaVersion / reviewState / lineageState / rangeContentHash.
  const legacy = [{ id: 'v1', timestamp: new Date().toISOString(), filePath: '/x.ts', fileName: 'x.ts', language: 'typescript', insertedCode: 'legacy code\nline two', linesAdded: 2, workspaceFolder: null, confidence: 0.6, source: 'ai' }];
  fs.writeFileSync(path.join(tmpDir, 'captures.json'), JSON.stringify(legacy), 'utf-8');

  const store = new CaptureStore(ctx);
  const rec = store.getById('v1');
  expect(rec?.schemaVersion).toBe(CAPTURE_SCHEMA_VERSION);
  expect(rec?.reviewState).toBe('unreviewed');
  // We cannot know where a legacy block sits → unknown, not original.
  expect(rec?.lineageState).toBe('unknown');
  expect(rec?.rangeContentHash).toBe(rangeContentHash('legacy code\nline two'));
  // Existing fields are preserved.
  expect(rec?.confidence).toBe(0.6);
  expect(rec?.source).toBe('ai');
});

// ── evidence chain ────────────────────────────────────────────────────────────

test('add() seals records into a verifiable chain', () => {
  const store = new CaptureStore(makeContext());
  for (let i = 0; i < 3; i++) {
    store.add({ filePath: `/f${i}.ts`, fileName: `f${i}.ts`, language: 'typescript', insertedCode: `code ${i}`, linesAdded: 4, workspaceFolder: null });
  }
  const v = verifyChain(store.getAll());
  expect(v.ok).toBe(true);
  expect(v.verified).toBe(3);
  expect(v.breaks).toBe(0);
});

test('load() seals legacy records that predate the chain', () => {
  const ctx = makeContext();
  const legacy = [
    { id: 'a', timestamp: '2026-06-25T10:00:00.000Z', filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'aaa', linesAdded: 1, workspaceFolder: null, confidence: 0.5, source: 'ai' },
    { id: 'b', timestamp: '2026-06-25T10:01:00.000Z', filePath: '/b.ts', fileName: 'b.ts', language: 'typescript', insertedCode: 'bbb', linesAdded: 1, workspaceFolder: null, confidence: 0.5, source: 'ai' },
  ];
  fs.writeFileSync(path.join(tmpDir, 'captures.json'), JSON.stringify(legacy), 'utf-8');

  const store = new CaptureStore(ctx);
  const v = verifyChain(store.getAll());
  expect(v.unsealed).toEqual([]);
  expect(v.ok).toBe(true);
});

test('add() attaches local risk signals (and none for clean code)', () => {
  const store = new CaptureStore(makeContext());
  const risky = store.add({ filePath: '/repo/src/auth/login.ts', fileName: 'login.ts', language: 'typescript', insertedCode: 'function authenticate(u){ return jwt.sign(u); }', linesAdded: 4, workspaceFolder: null });
  expect(risky.riskSignals?.some(s => s.id === 'generated-auth-code')).toBe(true);

  const clean = store.add({ filePath: '/repo/src/util.test.ts', fileName: 'util.test.ts', language: 'typescript', insertedCode: 'expect(1).toBe(1);', linesAdded: 1, workspaceFolder: null });
  expect(clean.riskSignals).toEqual([]);
});

test('reclassifying or reviewing a record does not break its chain', () => {
  const store = new CaptureStore(makeContext());
  const a = store.add({ filePath: '/a.ts', fileName: 'a.ts', language: 'typescript', insertedCode: 'x', linesAdded: 4, workspaceFolder: null });
  store.add({ filePath: '/b.ts', fileName: 'b.ts', language: 'typescript', insertedCode: 'y', linesAdded: 4, workspaceFolder: null });
  store.setClassification(a.id, 'unknown', 0.2);
  store.setReviewState(a.id, 'reviewed');
  expect(verifyChain(store.getAll()).ok).toBe(true);
});
