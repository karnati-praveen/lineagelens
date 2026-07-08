/**
 * Bridge between the pure relocation logic (`evidence/rangeBinding`) and the
 * VS Code editor surfaces. Given a document, returns this file's captures with
 * their *current* ranges and lineage, so CodeLens / hover / decorations all
 * agree on where each block sits now.
 */

import * as vscode from 'vscode';
import { CaptureStore, CaptureRecord, LineageState } from '../store';
import { locateCapture } from '../evidence/rangeBinding';

export interface LocatedCapture {
  record: CaptureRecord;
  range: vscode.Range;
  lineageState: LineageState;
}

function clampLine(line: number, document: vscode.TextDocument): number {
  return Math.max(0, Math.min(line, Math.max(0, document.lineCount - 1)));
}

/**
 * Locate every capture belonging to `document` at its current position.
 * Records whose block has been deleted are omitted (nothing to anchor to).
 */
export function locateCapturesForDocument(
  store: CaptureStore,
  document: vscode.TextDocument,
): LocatedCapture[] {
  const filePath = document.uri.fsPath;
  const text = document.getText();
  const out: LocatedCapture[] = [];

  for (const record of store.getAll()) {
    if (record.filePath !== filePath) { continue; }
    const loc = locateCapture(text, record);
    if (loc.lineageState === 'deleted') { continue; }

    const startLine = clampLine(loc.startLine, document);
    const endLine = clampLine(loc.endLine, document);
    const endChar = document.lineAt(endLine).text.length;
    out.push({
      record,
      range: new vscode.Range(startLine, 0, endLine, endChar),
      lineageState: loc.lineageState,
    });
  }
  return out;
}
