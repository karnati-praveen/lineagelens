import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

import { CaptureStore } from '../store';

let tmpDir: string;

function makeContext() {
  return { globalStorageUri: { fsPath: tmpDir } } as any;
}

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'll-test-'));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
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

test('persists to disk and reloads on new instance', () => {
  const ctx = makeContext();
  const store = new CaptureStore(ctx);
  const rec = store.add({ filePath: '/p.ts', fileName: 'p.ts', language: 'typescript', insertedCode: 'persist', linesAdded: 3, workspaceFolder: null });

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

test('reorder persists to disk', () => {
  const { store, ctx, a, b, c } = makeThree();
  store.reorder([a.id], null);
  const store2 = new CaptureStore(ctx);
  expect(store2.getAll()[0].fileName).toBe('a.ts');
});
