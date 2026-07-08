/**
 * Deterministic PR summary — pure, no vscode, no AI (blueprint §7 Feature 8).
 *
 * Produces a Markdown block a developer can paste into a pull request describing
 * the AI-assisted changes: how much was generated, how much survives, and the
 * review/drift state. Deterministic for a given record set so it is snapshot-able.
 */

import { CaptureRecord } from '../store';
import { hasHighRisk } from '../risk/rules';

function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? '' : 's'}`;
}

export function buildPrSummary(records: CaptureRecord[]): string {
  if (records.length === 0) {
    return '## AI-assisted changes\n\nNo AI captures recorded.\n';
  }

  const files = new Set(records.map((r) => r.filePath)).size;
  const generated = records.reduce((sum, r) => sum + (r.linesAdded || 0), 0);
  const surviving = records
    .filter((r) => r.lineageState !== 'deleted')
    .reduce((sum, r) => sum + (r.linesAdded || 0), 0);

  const reviewed = records.filter(
    (r) => r.reviewState === 'reviewed' || r.reviewState === 'accepted',
  ).length;
  const unreviewed = records.filter((r) => (r.reviewState ?? 'unreviewed') === 'unreviewed').length;
  const needsChanges = records.filter(
    (r) => r.reviewState === 'needs_changes' || r.reviewState === 'rejected',
  ).length;
  const drifted = records.filter(
    (r) =>
      r.lineageState === 'modified' ||
      r.lineageState === 'moved' ||
      r.lineageState === 'deleted',
  ).length;
  const highRisk = records.filter((r) => hasHighRisk(r.riskSignals)).length;

  return [
    '## AI-assisted changes',
    '',
    `- ${plural(records.length, 'AI capture')} across ${plural(files, 'file')}`,
    `- ${generated} generated lines, ${surviving} still present`,
    `- ${reviewed} reviewed · ${unreviewed} unreviewed · ${needsChanges} needs changes`,
    `- ${drifted} drifted since capture`,
    `- ${plural(highRisk, 'high-risk signal')}`,
    '',
    '_Generated locally by LineageLens Base — no code leaves your machine._',
  ].join('\n') + '\n';
}
