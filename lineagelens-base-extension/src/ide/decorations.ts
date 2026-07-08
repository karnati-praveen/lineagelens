/**
 * Editor decorations — a subtle whole-line tint + overview-ruler mark on AI
 * captured ranges, keyed by review/lineage state. Quiet by default; a focus
 * mode narrows to just the blocks that need attention (drift / needs-changes).
 */

import * as vscode from 'vscode';
import { CaptureStore, LineageState, ReviewState } from '../store';
import { locateCapturesForDocument } from './locator';
import { hasHighRisk } from '../risk/rules';

type Bucket = 'unreviewed' | 'reviewed' | 'drift' | 'risk';

function makeType(rgb: string): vscode.TextEditorDecorationType {
  return vscode.window.createTextEditorDecorationType({
    isWholeLine: true,
    backgroundColor: `rgba(${rgb},0.06)`,
    overviewRulerColor: `rgba(${rgb},0.85)`,
    overviewRulerLane: vscode.OverviewRulerLane.Left,
    rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
  });
}

export class CaptureDecorations implements vscode.Disposable {
  private readonly types: Record<Bucket, vscode.TextEditorDecorationType>;
  private focusMode = false;

  constructor(private readonly store: CaptureStore) {
    this.types = {
      unreviewed: makeType('124,140,255'), // indigo
      reviewed: makeType('52,208,88'),     // green
      drift: makeType('245,166,35'),       // amber
      risk: makeType('244,71,71'),         // red
    };
  }

  /** Toggle focus mode (drift + needs-changes only). Returns the new state. */
  toggleFocusMode(): boolean {
    this.focusMode = !this.focusMode;
    return this.focusMode;
  }

  /** Recompute and paint decorations for the given editor. */
  apply(editor: vscode.TextEditor | undefined): void {
    if (!editor) { return; }
    const enabled = vscode.workspace
      .getConfiguration('lineagelensBase')
      .get<boolean>('showGutter', true);

    const buckets: Record<Bucket, vscode.Range[]> = { unreviewed: [], reviewed: [], drift: [], risk: [] };
    if (enabled) {
      for (const located of locateCapturesForDocument(this.store, editor.document)) {
        const highRisk = hasHighRisk(located.record.riskSignals);
        const bucket = bucketFor(located.record.reviewState, located.lineageState, highRisk);
        if (this.focusMode && !this.isAttention(bucket, located.record.reviewState)) {
          continue;
        }
        buckets[bucket].push(located.range);
      }
    }
    editor.setDecorations(this.types.unreviewed, buckets.unreviewed);
    editor.setDecorations(this.types.reviewed, buckets.reviewed);
    editor.setDecorations(this.types.drift, buckets.drift);
    editor.setDecorations(this.types.risk, buckets.risk);
  }

  private isAttention(bucket: Bucket, reviewState: ReviewState | undefined): boolean {
    return bucket === 'risk' || bucket === 'drift' || reviewState === 'needs_changes' || reviewState === 'rejected';
  }

  dispose(): void {
    for (const type of Object.values(this.types)) { type.dispose(); }
  }
}

function bucketFor(
  reviewState: ReviewState | undefined,
  lineage: LineageState,
  highRisk: boolean,
): Bucket {
  if (highRisk) { return 'risk'; }
  if (lineage === 'moved' || lineage === 'modified') { return 'drift'; }
  if (reviewState === 'reviewed' || reviewState === 'accepted') { return 'reviewed'; }
  return 'unreviewed';
}
