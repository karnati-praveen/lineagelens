/**
 * Insert-target resolution tests — the webview-focus regression guard.
 *
 * Clicking a webview button (capture detail panel / sidebar) clears
 * activeTextEditor; resolution must still find a sane editor or signal the
 * caller to reopen the capture's file. These tests drive the mocked vscode
 * window state directly.
 */
import mocked from 'vscode';

import {
  isLiveEditor,
  resolveInsertTarget,
  sameFile,
} from '../ide/insertTarget';

interface FakeDoc {
  uri: { scheme: string; fsPath: string };
  isClosed: boolean;
}

interface FakeEditor {
  document: FakeDoc;
}

const vscodeMock = mocked as unknown as {
  window: {
    activeTextEditor?: unknown;
    visibleTextEditors: unknown[];
  };
};

function editor(fsPath: string, opts: { closed?: boolean; scheme?: string } = {}): FakeEditor {
  return { document: { uri: { scheme: opts.scheme ?? 'file', fsPath }, isClosed: opts.closed ?? false } };
}

function setWindow(active: unknown, visible: unknown[]): void {
  vscodeMock.window.activeTextEditor = active;
  vscodeMock.window.visibleTextEditors = visible;
}

describe('sameFile', () => {
  it('matches a file-scheme editor for the same resolved path', () => {
    expect(sameFile(editor('C:\\proj\\a.ts') as never, 'C:\\proj\\a.ts')).toBe(true);
  });

  it('rejects non-file schemes (e.g. untitled, debug)', () => {
    expect(sameFile(editor('x', { scheme: 'untitled' }) as never, 'x')).toBe(false);
  });

  it('rejects undefined sides', () => {
    expect(sameFile(undefined, 'x')).toBe(false);
    expect(sameFile(editor('x') as never, undefined)).toBe(false);
  });
});

describe('isLiveEditor', () => {
  it('accepts an open, visible editor', () => {
    const e = editor('a.ts');
    setWindow(undefined, [e]);
    expect(isLiveEditor(e as never)).toBe(true);
  });

  it('rejects a closed document', () => {
    const e = editor('a.ts', { closed: true });
    setWindow(undefined, [e]);
    expect(isLiveEditor(e as never)).toBe(false);
  });

  it('rejects an editor that is no longer visible', () => {
    const e = editor('a.ts');
    setWindow(undefined, []);
    expect(isLiveEditor(e as never)).toBe(false);
  });
});

describe('resolveInsertTarget', () => {
  it('prefers a visible editor already showing the capture file over the active editor', () => {
    const mine = editor('C:\\proj\\mine.ts');
    const other = editor('C:\\proj\\other.ts');
    setWindow(other, [other, mine]);
    const t = resolveInsertTarget({ filePath: 'C:\\proj\\mine.ts' }, undefined);
    expect(t.kind).toBe('matchedFile');
    expect((t as { editor: unknown }).editor).toBe(mine);
  });

  it('falls back to the live active editor (normal focused case)', () => {
    const active = editor('C:\\proj\\anywhere.ts');
    setWindow(active, [active]);
    const t = resolveInsertTarget({ filePath: 'C:\\proj\\elsewhere.ts' }, undefined);
    expect(t.kind).toBe('active');
  });

  it('uses the tracked last-active editor when a webview stole focus (the regression)', () => {
    const last = editor('C:\\proj\\last.ts');
    // Webview focused: activeTextEditor undefined, but `last` is still open+visible.
    setWindow(undefined, [last]);
    const t = resolveInsertTarget({ filePath: 'C:\\proj\\other.ts' }, last as never);
    expect(t.kind).toBe('lastActive');
    expect((t as { editor: unknown }).editor).toBe(last);
  });

  it('never lets a dead last-active reference win — resolves none so the caller reopens the file', () => {
    const stale = editor('C:\\proj\\gone.ts', { closed: true });
    setWindow(undefined, []);
    const t = resolveInsertTarget({ filePath: 'C:\\proj\\gone.ts' }, stale as never);
    expect(t.kind).toBe('none');
  });

  it('resolves none when nothing matches anywhere', () => {
    setWindow(undefined, []);
    expect(resolveInsertTarget({}, undefined).kind).toBe('none');
  });
});
