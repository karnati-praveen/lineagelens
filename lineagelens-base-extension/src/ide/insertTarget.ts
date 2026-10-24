/**
 * Insert-target resolution — where captured code goes when the user clicks
 * "Insert at cursor" from a webview (detail panel or sidebar).
 *
 * Clicking inside a webview steals focus and clears activeTextEditor, so a
 * naive `activeTextEditor` check always fails from webview buttons. The
 * resolution order here survives that focus steal:
 *
 *   1. a visible editor already showing the capture's own file
 *   2. the live active editor (the normal, focused-editor case)
 *   3. the last known active editor, if still live
 *   4. none — the caller opens the capture's file and inserts at its end
 *
 * Extracted from extension.ts so the chain is unit-testable without a host.
 */

import * as path from 'path';
import * as vscode from 'vscode';

/** True when `editor`'s document is the same file as `filePath` (platform-aware). */
export function sameFile(editor: vscode.TextEditor | undefined, filePath: string | undefined): boolean {
  if (!editor || !filePath || editor.document.uri.scheme !== 'file') { return false; }
  const left = path.resolve(editor.document.uri.fsPath);
  const right = path.resolve(filePath);
  return process.platform === 'win32' ? left.toLowerCase() === right.toLowerCase() : left === right;
}

/** True when the editor is still usable (document open and pane visible). */
export function isLiveEditor(editor: vscode.TextEditor | undefined): editor is vscode.TextEditor {
  return !!editor && !editor.document.isClosed
    && vscode.window.visibleTextEditors.includes(editor);
}

export type InsertTarget =
  | { kind: 'matchedFile'; editor: vscode.TextEditor }
  | { kind: 'active'; editor: vscode.TextEditor }
  | { kind: 'lastActive'; editor: vscode.TextEditor }
  | { kind: 'none' };

/**
 * Pick the editor an insert should land in. `lastActiveTextEditor` is tracked
 * by the caller via onDidChangeActiveTextEditor; a stale/closed reference
 * never wins — resolution falls through to 'none' and the caller reopens the
 * capture's file instead.
 */
export function resolveInsertTarget(
  record: { filePath?: string },
  lastActiveTextEditor: vscode.TextEditor | undefined,
): InsertTarget {
  const matched = vscode.window.visibleTextEditors.find(e => sameFile(e, record.filePath));
  if (matched) { return { kind: 'matchedFile', editor: matched }; }

  if (isLiveEditor(vscode.window.activeTextEditor)) {
    return { kind: 'active', editor: vscode.window.activeTextEditor };
  }
  if (isLiveEditor(lastActiveTextEditor)) {
    return { kind: 'lastActive', editor: lastActiveTextEditor };
  }
  return { kind: 'none' };
}

/** Insert `text` at every selection cursor of `editor`. False when the edit is rejected. */
export async function insertIntoEditor(editor: vscode.TextEditor, text: string): Promise<boolean> {
  return editor.edit(editBuilder => {
    for (const sel of editor.selections) {
      editBuilder.insert(sel.active, text);
    }
  });
}

/** Open `filePath` and append `text` on a fresh final line. False when the edit is rejected. */
export async function insertAtDocumentEnd(filePath: string, text: string): Promise<boolean> {
  const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(filePath));
  const editor = await vscode.window.showTextDocument(doc, { preview: false, viewColumn: vscode.ViewColumn.One });
  const lastLine = doc.lineAt(Math.max(0, doc.lineCount - 1));
  // A file without a trailing newline would otherwise glue the insert onto
  // the last line and corrupt it — start on a fresh line instead.
  const prefix = lastLine.text.length > 0 ? (doc.eol === vscode.EndOfLine.CRLF ? '\r\n' : '\n') : '';
  return editor.edit(editBuilder => { editBuilder.insert(lastLine.range.end, prefix + text); });
}
