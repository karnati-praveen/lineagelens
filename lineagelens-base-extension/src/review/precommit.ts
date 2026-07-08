/**
 * Pre-commit gate selection — pure, no vscode / no git.
 *
 * Given the capture store and the set of staged file paths, pick the captures
 * that warrant a warning before commit: unreviewed AI code, code flagged
 * needs-changes/rejected, and code that has drifted since capture. The git/IDE
 * shell that supplies `stagedFilePaths` lives in the extension layer.
 */

import { CaptureRecord } from '../store';
import { hasHighRisk } from '../risk/rules';

export interface GateBuckets {
  /** Captured AI code still marked unreviewed. */
  unreviewed: CaptureRecord[];
  /** Captures explicitly flagged needs-changes or rejected. */
  needsChanges: CaptureRecord[];
  /** Captures carrying a high-severity local risk signal. */
  risky: CaptureRecord[];
  /** Captures whose block has moved/changed/been removed since capture. */
  drifted: CaptureRecord[];
}

/** Normalize a path for cross-tool comparison (git uses '/', VS Code may use '\'). */
function normPath(p: string): string {
  return p.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}

/** Select captures in the staged set that need attention before commit. */
export function selectForGate(records: CaptureRecord[], stagedFilePaths: string[]): GateBuckets {
  const staged = new Set(stagedFilePaths.map(normPath));
  const inScope = records.filter((r) => staged.has(normPath(r.filePath)));

  return {
    unreviewed: inScope.filter((r) => (r.reviewState ?? 'unreviewed') === 'unreviewed'),
    needsChanges: inScope.filter(
      (r) => r.reviewState === 'needs_changes' || r.reviewState === 'rejected',
    ),
    risky: inScope.filter((r) => hasHighRisk(r.riskSignals)),
    drifted: inScope.filter(
      (r) =>
        r.lineageState === 'modified' ||
        r.lineageState === 'moved' ||
        r.lineageState === 'deleted',
    ),
  };
}

/** True if the gate found anything worth warning about. */
export function hasBlockingFindings(buckets: GateBuckets): boolean {
  return (
    buckets.unreviewed.length > 0 ||
    buckets.needsChanges.length > 0 ||
    buckets.risky.length > 0 ||
    buckets.drifted.length > 0
  );
}

/** One-line summary, e.g. `2 unreviewed · 1 needs changes · 1 high-risk · 1 drifted`. */
export function gateSummaryLine(buckets: GateBuckets): string {
  if (!hasBlockingFindings(buckets)) {
    return 'No unreviewed, flagged, risky, or drifted AI code in staged changes.';
  }
  const parts: string[] = [];
  if (buckets.unreviewed.length) { parts.push(`${buckets.unreviewed.length} unreviewed`); }
  if (buckets.needsChanges.length) { parts.push(`${buckets.needsChanges.length} needs changes`); }
  if (buckets.risky.length) { parts.push(`${buckets.risky.length} high-risk`); }
  if (buckets.drifted.length) { parts.push(`${buckets.drifted.length} drifted`); }
  return parts.join(' · ');
}
