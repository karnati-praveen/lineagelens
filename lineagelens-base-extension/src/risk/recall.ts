/**
 * Local AI recall drill — pure, no vscode (blueprint §8.3 / Feature 14).
 *
 * "If this AI block turned out to be unsafe, what else would I need to inspect?"
 * Given a suspect capture, find the same or similar captured blocks across the
 * store and produce a recall set + an exportable report. Operates over the
 * capture set (fast, local-first) rather than re-scanning the whole workspace.
 */

import { CaptureRecord } from '../store';
import { rangeContentHash } from '../evidence/hash';

export interface RecallMatch {
  record: CaptureRecord;
  /** 0..1 line-set (Jaccard) similarity; 1 for an exact content-hash match. */
  similarity: number;
  exact: boolean;
}

export interface RecallSet {
  target: CaptureRecord;
  matches: RecallMatch[];
  /** Distinct file paths to inspect (target first). */
  files: string[];
}

export interface RecallOptions {
  /** Minimum similarity (0..1) for a near-duplicate to be included. Default 0.5. */
  threshold?: number;
}

/** Set of normalized, non-empty lines in a code block. */
function lineSet(code: string): Set<string> {
  return new Set(
    code
      .split(/\r\n|\r|\n/)
      .map((l) => l.replace(/\s+$/, '').trim())
      .filter(Boolean),
  );
}

function jaccard(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 && b.size === 0) { return 1; }
  let intersection = 0;
  for (const x of a) {
    if (b.has(x)) { intersection++; }
  }
  const union = a.size + b.size - intersection;
  return union === 0 ? 0 : intersection / union;
}

function hashOf(record: CaptureRecord): string {
  return record.rangeContentHash ?? rangeContentHash(record.insertedCode);
}

/** Build the recall set for a suspect capture against all captures. */
export function buildRecallSet(
  target: CaptureRecord,
  allRecords: CaptureRecord[],
  opts: RecallOptions = {},
): RecallSet {
  const threshold = opts.threshold ?? 0.5;
  const targetHash = hashOf(target);
  const targetLines = lineSet(target.insertedCode);

  const matches: RecallMatch[] = [];
  for (const record of allRecords) {
    if (record.id === target.id) { continue; }
    const exact = hashOf(record) === targetHash;
    const similarity = exact ? 1 : jaccard(targetLines, lineSet(record.insertedCode));
    if (exact || similarity >= threshold) {
      matches.push({ record, similarity, exact });
    }
  }
  matches.sort((a, b) => b.similarity - a.similarity || a.record.id.localeCompare(b.record.id));

  const files = [target.filePath, ...matches.map((m) => m.record.filePath)].filter(
    (p, i, arr) => arr.indexOf(p) === i,
  );

  return { target, matches, files };
}

function pct(similarity: number): string {
  return `${Math.round(similarity * 100)}%`;
}

/** Render a recall set as a portable Markdown report. */
export function buildRecallReport(set: RecallSet): string {
  const { target, matches, files } = set;
  const lines: string[] = [
    `## AI recall report — \`${target.fileName}\``,
    '',
    `Suspect capture \`${target.id}\` (${target.linesAdded} lines).`,
    '',
  ];

  if (matches.length === 0) {
    lines.push('No other captures match this block. Nothing else to recall.');
    lines.push('');
  } else {
    lines.push(`${matches.length} similar capture(s) across ${files.length} file(s):`);
    lines.push('');
    for (const m of matches) {
      const tag = m.exact ? 'exact' : `~${pct(m.similarity)}`;
      lines.push(`- [${tag}] \`${m.record.fileName}\` — ${m.record.id}`);
    }
    lines.push('');
    lines.push('### Files to inspect');
    for (const f of files) {
      lines.push(`- ${f}`);
    }
    lines.push('');
  }

  lines.push('_Local recall over captured AI blocks — heuristic similarity, not a guarantee._');
  return lines.join('\n') + '\n';
}
