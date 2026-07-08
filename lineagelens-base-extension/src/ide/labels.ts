/**
 * Presentation strings for the IDE-native surfaces (CodeLens title + hover
 * receipt) — pure, no vscode. Kept separate so the wording is unit-testable
 * without an extension host; `codeLens.ts` / `hover.ts` are thin renderers.
 */

import { CaptureRecord, CaptureSource, LineageState, ReviewState, RiskSignal } from '../store';

/** Human label for a capture's best-guess origin. */
export function sourceLabel(source: CaptureSource): string {
  return source === 'ai' ? '🤖 AI' : source === 'paste' ? '📋 Paste' : '❓ Unknown';
}

function originPrefix(source: CaptureSource): string {
  return source === 'ai' ? 'AI capture' : source === 'paste' ? 'Pasted capture' : 'Capture';
}

/**
 * One-line status segment combining review and lineage. Review state is the
 * headline; if the block has drifted while still unreviewed we surface the
 * lineage instead so the developer sees the more actionable fact.
 */
function statusSegment(reviewState: ReviewState | undefined, lineage: LineageState): string {
  switch (reviewState) {
    case 'reviewed': return 'reviewed';
    case 'needs_changes': return 'needs changes';
    case 'rejected': return 'rejected';
    case 'accepted': return 'accepted';
    default:
      // unreviewed (or undefined) — let lineage drift take the spotlight.
      if (lineage === 'moved') { return 'moved since capture'; }
      if (lineage === 'modified') { return 'modified since capture'; }
      if (lineage === 'deleted') { return 'no longer present'; }
      return 'unreviewed';
  }
}

/** Trailing `· risk: <top>` segment, empty when there are no signals. */
function riskSegment(signals: RiskSignal[] | undefined): string {
  if (!signals || signals.length === 0) { return ''; }
  const top = signals[0]; // evaluateRisk returns signals sorted high→low severity
  const extra = signals.length > 1 ? ` +${signals.length - 1}` : '';
  return ` · risk: ${top.label}${extra}`;
}

/**
 * CodeLens title, e.g. `AI capture · 12 lines · unreviewed · risk: auth`.
 * `lineage` is the freshly-resolved position so the label never lies about drift.
 */
export function codeLensTitle(
  record: Pick<CaptureRecord, 'source' | 'linesAdded' | 'reviewState' | 'riskSignals'>,
  lineage: LineageState,
): string {
  const lines = `${record.linesAdded} line${record.linesAdded === 1 ? '' : 's'}`;
  return `$(sparkle) ${originPrefix(record.source)} · ${lines} · ${statusSegment(record.reviewState, lineage)}${riskSegment(record.riskSignals)}`;
}

/** Markdown receipt body shown on hover over a captured range. */
export function hoverMarkdown(record: CaptureRecord, lineage: LineageState): string {
  const pct = Math.round((record.confidence ?? 0.5) * 100);
  const status = statusSegment(record.reviewState, lineage);
  const when = (() => {
    try { return new Date(record.timestamp).toISOString(); } catch { return record.timestamp; }
  })();
  const lines = [
    `**${originPrefix(record.source)}** — \`${record.fileName}\``,
    '',
    `- **When:** ${when}`,
    `- **Source:** ${sourceLabel(record.source)} (${pct}% confidence — inferred)`,
    `- **Lines:** +${record.linesAdded}`,
    `- **Status:** ${status}`,
  ];
  if (record.riskSignals && record.riskSignals.length) {
    lines.push(
      `- **Risk signals:** ${record.riskSignals.map((s) => `${s.label} (${s.severity})`).join(', ')}`,
    );
  }
  lines.push('- **Detection:** file diff (observed); AI origin inferred');
  lines.push('');
  lines.push('_Open the LineageLens receipt for full detail._');
  return lines.join('\n');
}
