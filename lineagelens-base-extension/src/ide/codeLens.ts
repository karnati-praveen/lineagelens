/**
 * AI-capture CodeLens — a lightweight label above each captured range that
 * follows the code as it moves and opens the full receipt on click.
 */

import * as vscode from 'vscode';
import { CaptureStore } from '../store';
import { locateCapturesForDocument } from './locator';
import { codeLensTitle } from './labels';

export class CaptureCodeLensProvider implements vscode.CodeLensProvider {
  private readonly _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this._onDidChange.event;

  constructor(private readonly store: CaptureStore) {}

  /** Ask VS Code to re-query lenses (call after captures change). */
  refresh(): void {
    this._onDidChange.fire();
  }

  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    const enabled = vscode.workspace
      .getConfiguration('lineagelensBase')
      .get<boolean>('showCodeLens', true);
    if (!enabled) { return []; }

    return locateCapturesForDocument(this.store, document).map((located) => {
      // Render the lens on the line above the block's first line.
      const anchor = new vscode.Range(located.range.start.line, 0, located.range.start.line, 0);
      return new vscode.CodeLens(anchor, {
        title: codeLensTitle(located.record, located.lineageState),
        command: 'lineagelens.openCapture',
        arguments: [located.record.id],
      });
    });
  }
}
