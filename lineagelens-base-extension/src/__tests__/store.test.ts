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
