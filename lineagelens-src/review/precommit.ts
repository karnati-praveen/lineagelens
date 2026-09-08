/**
 * Pre-commit gate selection — pure, no vscode / no git.
 */

import type { CaptureRecord } from '../evidence/types';

function hasHighRisk(signals?: Array<{ severity?: string }>): boolean {
  return Array.isArray(signals) && signals.some((s) => s.severity === 'high');
}

export interface GateBuckets {
  unreviewed: CaptureRecord[];
  needsChanges: CaptureRecord[];
  risky: CaptureRecord[];
  drifted: CaptureRecord[];
}

function normPath(p: string): string {
  return p.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}

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

export function hasBlockingFindings(buckets: GateBuckets): boolean {
  return (
    buckets.unreviewed.length > 0 ||
    buckets.needsChanges.length > 0 ||
    buckets.risky.length > 0 ||
    buckets.drifted.length > 0
  );
}

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
