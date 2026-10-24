import type { OutputLine } from '../components/Terminal';
import scanResult from '../data/scan-result.json';

// Turns the captured scan snapshot into terminal output lines.
//
// This is the honesty seam of the whole demo: the video renders whatever the
// real scan produced. If the snapshot changes, the video changes. Nothing here
// hardcodes a figure.

export type ScanResult = typeof scanResult;

export const scan: ScanResult = scanResult;

const n = (v: number) => v.toLocaleString('en-US');
const pct = (v: number) => `${v.toFixed(1)}%`;

/** Fixed-width bar matching the CLI's own rendering. */
function bar(percent: number, width = 24): string {
  const filled = Math.max(0, Math.min(width, Math.round((percent / 100) * width)));
  return '█'.repeat(filled) + '░'.repeat(width - filled);
}

/**
 * Wrap at word boundaries so long prose stays inside the terminal frame.
 * The terminal renders `white-space: pre`, so wrapping has to happen here.
 */
function wrap(text: string, width: number, indent = ''): string[] {
  const words = text.split(' ');
  const out: string[] = [];
  let line = '';
  for (const word of words) {
    const candidate = line === '' ? word : `${line} ${word}`;
    if (candidate.length > width && line !== '') {
      out.push(line);
      line = indent + word;
    } else {
      line = candidate;
    }
  }
  if (line !== '') out.push(line);
  return out;
}

const isFloor = scan.assurance.measurementKind === 'lower_bound';

/** The headline block: the number, and immediately what it does not mean. */
export const headlineLines: OutputLine[] = [
  { text: '' },
  {
    text: `── lineagelens scan ─ ${scan.capturedFrom} @ ${scan.scan.branch}`,
    tone: 'ink',
    bold: true,
  },
  { text: '' },
  {
    text: isFloor
      ? '  AI-written code live in this repo right now — at least'
      : '  AI-written code live in this repo right now',
    tone: 'ink',
    bold: true,
  },
  { text: '' },
  {
    text: `    ${bar(scan.totals.aiAttributedPercent)}  ${isFloor ? '≥' : ''}${pct(scan.totals.aiAttributedPercent)}` +
      `  ${n(scan.totals.aiAttributedLines)} of ${n(scan.totals.trackedLines)} surviving lines`,
    tone: 'ai',
    bold: true,
  },
  {
    text: `    across ${n(scan.totals.aiAttributedFiles)} of ${n(scan.totals.trackedFiles)} tracked files` +
      ` · ${n(scan.scan.aiCommits)} AI commits`,
    tone: 'faint',
  },
];

/** The floor warning — the part a competitor's tool would quietly omit. */
export const coverageLines: OutputLine[] = isFloor
  ? [
    { text: '' },
    { text: '  ⚠ This number is a floor, not a measurement.', tone: 'risk', bold: true },
    ...scan.coverage.reasons.flatMap((reason): OutputLine[] =>
      wrap(`· ${reason}`, 74, '  ').map((text) => ({ text: `    ${text}`, tone: 'risk' })),
    ),
  ]
  : [];

export const toolLines: OutputLine[] = [
  { text: '' },
  { text: '  Who wrote it', tone: 'ink', bold: true },
  ...Object.entries(scan.byTool).map(([tool, stats]): OutputLine => ({
    text: `    ${tool.padEnd(16)}${n(stats.lines).padStart(8)} lines   ${n(stats.commits)} commits · ${n(stats.files)} files`,
    tone: 'ai',
  })),
];

export const categoryLines: OutputLine[] = [
  { text: '' },
  { text: '  Risk surfaces this AI code sits on', tone: 'ink', bold: true },
  ...Object.entries(scan.byCategory)
    .sort((a, b) => b[1].lines - a[1].lines)
    .map(([slug, stats]): OutputLine => ({
      text: `    ${slug.padEnd(14)}${n(stats.lines).padStart(8)} lines   ${n(stats.files)} ${stats.files === 1 ? 'file' : 'files'}`,
      tone: ['auth', 'secrets', 'payments'].includes(slug) ? 'risk' : 'faint',
    })),
];

const topFiles = scan.files.slice(0, 5);
// Pad to the widest path actually shown so no bar ever collides with a path.
const pathColumn = Math.max(...topFiles.map((f) => f.path.length)) + 2;

export const fileLines: OutputLine[] = [
  { text: '' },
  { text: '  Most AI-written files', tone: 'ink', bold: true },
  ...topFiles.map((f): OutputLine => {
    const filePct = f.totalLines > 0 ? (f.attributedLines / f.totalLines) * 100 : 0;
    const tags = f.categories.length > 0 ? `  [${f.categories.join(' ')}]` : '';
    return {
      text: `    ${f.path.padEnd(pathColumn)}${bar(filePct, 12)} ${pct(filePct).padStart(6)}${tags}`,
      tone: f.categories.some((c) => ['auth', 'secrets', 'payments'].includes(c)) ? 'risk' : 'muted',
    };
  }),
];

export const assuranceLines: OutputLine[] = [
  { text: '' },
  { text: '  What this scan does and does not prove', tone: 'ink', bold: true },
  {
    text: `    proven       ${n(scan.totals.aiAttributedLines)} lines came from commits that declared an AI tool`,
    tone: 'ok',
  },
  {
    text: `    unattributed ${n(scan.totals.unattributedLines)} lines carry no AI signal —`,
    tone: 'faint',
  },
  { text: '                 this is not evidence of human authorship', tone: 'faint' },
  { text: `    unavailable  ${scan.assurance.unavailable.join(', ')}`, tone: 'faint' },
];
