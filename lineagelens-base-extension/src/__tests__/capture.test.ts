/**
 * Unit tests for CaptureService: outbox retry path and undo/redo guard.
 *
 * These tests use the vscode __mock__ and a fake globalState/context so no
 * VS Code extension host is required.
 */

import * as vscode from 'vscode';
import { CaptureService } from '../capture';
import { CaptureStore } from '../store';
import { rangeContentHash } from '../evidence/hash';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

// ── Helpers ───────────────────────────────────────────────────────────────────

let tmpDir: string;

/** Minimal in-memory globalState compatible with VS Code's API surface. */
function makeGlobalState() {
  const store = new Map<string, unknown>();
  return {
    get: <T>(key: string, defaultValue?: T): T | undefined =>
      store.has(key) ? (store.get(key) as T) : defaultValue,
    update: (key: string, value: unknown) => {
      store.set(key, value);
      return Promise.resolve();
    },
    keys: () => [...store.keys()],
    setKeysForSync: () => {},
  };
}

function makeContext() {
  return {
    globalStorageUri: { fsPath: tmpDir },
    globalState: makeGlobalState(),
    secrets: {
      get: (_k: string) => Promise.resolve(undefined),
      store: (_k: string, _v: string) => Promise.resolve(),
      delete: (_k: string) => Promise.resolve(),
    },
    subscriptions: [],
  } as unknown as vscode.ExtensionContext;
}

function makeStore(ctx: vscode.ExtensionContext) {
  return new CaptureStore(ctx as any);
}

function makeStatusBar() {
  return vscode.window.createStatusBarItem() as vscode.StatusBarItem;
}

function makeService(ctx: vscode.ExtensionContext): CaptureService {
  const store = makeStore(ctx);
  const bar = makeStatusBar();
  return new CaptureService(store, bar, ctx, () => {});
}

// ── Outbox tests ──────────────────────────────────────────────────────────────

describe('outbox retry path', () => {
  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'll-capture-test-'));
  });
  afterEach(() => {
    // Tolerate the Windows teardown race where a debounced background save
    // re-creates the store file in this dir as rmSync removes it.
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch { /* best-effort */ }
    jest.restoreAllMocks();
  });

  it('retryOutbox is a no-op when backendUrl is not configured', async () => {
    // Default config mock returns empty string for backendUrl.
    const ctx = makeContext();
    const svc = makeService(ctx);
    // Should resolve without error even with no backend configured.
    await expect(svc.retryOutbox()).resolves.toBeUndefined();
    svc.dispose();
  });

  it('retryOutbox sends pending entries when backendUrl is configured', async () => {
    const ctx = makeContext();

    // Seed one outbox entry directly via globalState.
    const entry = {
      id: 'test-id-1',
      payload: { id: 'test-id-1', workspaceId: 'ws-test' },
      attempts: 0,
      nextRetryAt: 0, // due immediately
    };
    await ctx.globalState.update('lineagelens.base.outbox', [entry]);

    // Configure backendUrl and ingestToken via the config mock.
    jest.spyOn(vscode.workspace, 'getConfiguration').mockReturnValue({
      get: (key: string, def?: unknown) => {
        if (key === 'backendUrl') { return 'http://localhost:8787'; }
        if (key === 'ingestToken') { return 'test-token'; }
        if (key === 'workspaceId') { return 'ws-test'; }
        if (key === 'redactSecretsOnEgress') { return false; }
        return def;
      },
    } as any);

    // Mock fetch to succeed.
    const fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock as any;

    const svc = makeService(ctx);
    await svc.retryOutbox();

    // fetch should have been called once with the idempotency key.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://localhost:8787/ingest');
    expect((options.headers as Record<string, string>)['X-Idempotency-Key']).toBe('test-id-1');

    // Outbox should be empty after successful send.
    const remaining = ctx.globalState.get<unknown[]>('lineagelens.base.outbox');
    expect(remaining).toHaveLength(0);

    svc.dispose();
  });

  it('keeps failed entry in outbox with increased attempts and backoff delay', async () => {
    const ctx = makeContext();

    const entry = {
      id: 'test-id-2',
      payload: { id: 'test-id-2' },
      attempts: 0,
      nextRetryAt: 0,
    };
    await ctx.globalState.update('lineagelens.base.outbox', [entry]);

    jest.spyOn(vscode.workspace, 'getConfiguration').mockReturnValue({
      get: (key: string, def?: unknown) => {
        if (key === 'backendUrl') { return 'http://localhost:8787'; }
        if (key === 'ingestToken') { return 'test-token'; }
        return def;
      },
    } as any);

    // Fetch always fails.
    global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 503 }) as any;

    const svc = makeService(ctx);
    await svc.retryOutbox();

    const remaining = ctx.globalState.get<{ attempts: number; nextRetryAt: number }[]>(
      'lineagelens.base.outbox',
    );
    expect(remaining).toHaveLength(1);
    expect(remaining![0].attempts).toBe(1);
    // nextRetryAt should be in the future (30s base × 2^0 = 30s).
    expect(remaining![0].nextRetryAt).toBeGreaterThan(Date.now());

    svc.dispose();
  });

  it('caps the outbox at 200 entries, dropping the oldest', async () => {
    const ctx = makeContext();
    const svc = makeService(ctx);

    jest.spyOn(vscode.workspace, 'getConfiguration').mockReturnValue({
      get: (key: string, def?: unknown) => {
        if (key === 'backendUrl') { return 'http://localhost:8787'; }
        if (key === 'ingestToken') { return 'tok'; }
        if (key === 'workspaceId') { return 'ws'; }
        if (key === 'redactSecretsOnEgress') { return false; }
        return def;
      },
    } as any);

    // Fail every POST so all payloads land in the outbox.
    global.fetch = jest.fn().mockRejectedValue(new Error('network')) as any;

    // Directly enqueue 210 items by calling the private method via type assertion.
    const enqueue = (svc as any)._enqueue.bind(svc);
    for (let i = 0; i < 210; i++) {
      enqueue({ id: `id-${i}`, payload: {}, attempts: 0, nextRetryAt: Date.now() + 60_000 });
    }

    const outbox = ctx.globalState.get<{ id: string }[]>('lineagelens.base.outbox');
    expect(outbox!.length).toBe(200);
    // The oldest 10 entries (id-0 through id-9) should have been dropped.
    expect(outbox!.find(e => e.id === 'id-0')).toBeUndefined();
    expect(outbox!.find(e => e.id === 'id-10')).toBeDefined();

    svc.dispose();
  });
});

// ── Undo / redo guard tests ───────────────────────────────────────────────────

describe('undo/redo guard', () => {
  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'll-capture-test-'));
  });
  afterEach(() => {
    // Tolerate the Windows teardown race where a debounced background save
    // re-creates the store file in this dir as rmSync removes it.
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch { /* best-effort */ }
    jest.restoreAllMocks();
  });

  /** Build a minimal TextDocumentChangeEvent. */
  function makeEvent(
    text: string,
    reason?: number,
    isDirty = true,
  ): vscode.TextDocumentChangeEvent {
    // Generate enough newlines to exceed the default minInsertionLines threshold.
    const lines = text.split('\n');
    const lineCount = Math.max(lines.length, 1);
    return {
      document: {
        uri: { scheme: 'file', fsPath: '/tmp/test.ts' },
        languageId: 'typescript',
        fileName: '/tmp/test.ts',
        isDirty,
        lineCount,
        lineAt: (n: number) => ({
          range: {
            end: { line: n, character: 0, isEqual: () => false },
          },
        }),
      },
      reason,
      contentChanges: [
        {
          text,
          range: {
            start: { line: 0, character: 0 },
            end: { line: 0, character: 0, isEqual: () => false },
          },
          rangeLength: 0,
          rangeOffset: 0,
        },
      ],
    } as unknown as vscode.TextDocumentChangeEvent;
  }

  it('skips undo events (reason === TextDocumentChangeReason.Undo)', async () => {
    const ctx = makeContext();
    const store = makeStore(ctx);
    const bar = makeStatusBar();
    let captured = 0;
    const svc = new CaptureService(store, bar, ctx, () => { captured++; });
    svc.start();

    const undoEvent = makeEvent('const x = 1;\n'.repeat(6), vscode.TextDocumentChangeReason.Undo);
    // Access the private handleChange via type assertion.
    await (svc as any).handleChange(undoEvent);

    expect(captured).toBe(0);
    svc.dispose();
  });

  it('skips redo events (reason === TextDocumentChangeReason.Redo)', async () => {
    const ctx = makeContext();
    const store = makeStore(ctx);
    const bar = makeStatusBar();
    let captured = 0;
    const svc = new CaptureService(store, bar, ctx, () => { captured++; });

    const redoEvent = makeEvent('const x = 1;\n'.repeat(6), vscode.TextDocumentChangeReason.Redo);
    await (svc as any).handleChange(redoEvent);

    expect(captured).toBe(0);
    svc.dispose();
  });

  it('processes events with no reason (normal typing / AI insertion)', async () => {
    const ctx = makeContext();
    const store = makeStore(ctx);
    const bar = makeStatusBar();
    let captured = 0;
    const svc = new CaptureService(store, bar, ctx, () => { captured++; });

    // No reason → normal change; enough lines to exceed threshold.
    const normalEvent = makeEvent('const x = 1;\n'.repeat(6), undefined);
    await (svc as any).handleChange(normalEvent);

    expect(captured).toBe(1);
    svc.dispose();
  });

  it('records the inserted range and a content hash on capture', async () => {
    const ctx = makeContext();
    const store = makeStore(ctx);
    const bar = makeStatusBar();
    const svc = new CaptureService(store, bar, ctx, () => {});

    const text = 'const x = 1;\n'.repeat(6); // 6 newlines → spans lines 0..6
    await (svc as any).handleChange(makeEvent(text, undefined));

    const rec = store.getAll()[0];
    expect(rec).toBeDefined();
    expect(rec.startLine).toBe(0);
    expect(rec.endLine).toBe(6);
    expect(rec.rangeContentHash).toBe(rangeContentHash(text));
    svc.dispose();
  });
});
