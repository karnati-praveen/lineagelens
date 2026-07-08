/**
 * Hover receipt — hovering a captured range shows a compact markdown summary
 * (origin, confidence, lines, review/lineage status) without opening the panel.
 */

import * as vscode from 'vscode';
import { CaptureStore } from '../store';
import { locateCapturesForDocument } from './locator';
import { hoverMarkdown } from './labels';

export class CaptureHoverProvider implements vscode.HoverProvider {
  constructor(private readonly store: CaptureStore) {}

  provideHover(
    document: vscode.TextDocument,
    position: vscode.Position,
  ): vscode.Hover | undefined {
    const hit = locateCapturesForDocument(this.store, document).find((l) =>
      l.range.contains(position),
    );
    if (!hit) { return undefined; }

    const md = new vscode.MarkdownString(hoverMarkdown(hit.record, hit.lineageState));
    // Content is plain markdown built from local data; no command links needed.
    md.isTrusted = false;
    return new vscode.Hover(md, hit.range);
  }
}
