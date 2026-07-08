/**
 * Range relocation — pure, dependency-free (no vscode).
 *
 * Given a capture's recorded inserted code (and where it originally landed) and
 * the *current* text of the file, work out where that block sits now and how it
 * has changed. This powers CodeLens / gutter / hover so they follow the code as
 * it moves and gets edited, instead of pointing at stale line numbers.
 *
 * Deliberately line-based and simple (blueprint §16.1 "start simple"): exact,
 * whitespace-tolerant window matching first; AST-aware matching is a later step.
 */

import { LineageState } from '../store';

export interface LocateInput {
  /** The code that was captured at insertion time. */
  insertedCode: string;
  /** Zero-based line where the insertion began, if known. */
  startLine?: number;
  /** Zero-based line where the insertion ended, if known. */
  endLine?: number;
}

export interface LocateResult {
  /** Zero-based first line of the block in the current document. */
  startLine: number;
  /** Zero-based last line of the block in the current document. */
  endLine: number;
  lineageState: LineageState;
}

/** Strip trailing whitespace from a line (leading indentation is preserved). */
function normalizeLine(line: string): string {
  return line.replace(/\s+$/, '');
}

function splitLines(text: string): string[] {
  return text.split(/\r\n|\r|\n/).map(normalizeLine);
}

/**
 * Normalize captured code into its non-blank line window: trailing whitespace
 * stripped per line and leading/trailing blank lines dropped. Returns the lines
 * plus how many leading blank lines were removed (so we can map back to the
 * recorded start line, which points at the raw insertion including any leading
 * newline).
 */
function blockLines(code: string): { lines: string[]; leadingBlanks: number } {
  const raw = splitLines(code);
  let start = 0;
  let end = raw.length;
  while (start < end && raw[start].trim() === '') { start++; }
  while (end > start && raw[end - 1].trim() === '') { end--; }
  return { lines: raw.slice(start, end), leadingBlanks: start };
}

/** All zero-based indices where `block` appears as a contiguous window in `docLines`. */
function findWindows(docLines: string[], block: string[]): number[] {
  const n = block.length;
  const out: number[] = [];
  if (n === 0 || docLines.length < n) { return out; }
  for (let i = 0; i + n <= docLines.length; i++) {
    if (docLines[i] !== block[0]) { continue; }
    let ok = true;
    for (let j = 1; j < n; j++) {
      if (docLines[i + j] !== block[j]) { ok = false; break; }
    }
    if (ok) { out.push(i); }
  }
  return out;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

/**
 * Locate the captured block inside the current document text.
 *
 *  - `original` — found intact at (or adjacent to) the recorded start line
 *  - `moved`    — found intact, but somewhere other than the recorded start line
 *  - `modified` — not found intact, but some of its lines still exist
 *  - `deleted`  — none of its non-blank lines remain
 *  - `unknown`  — block is empty / nothing to locate
 */
export function locateCapture(documentText: string, record: LocateInput): LocateResult {
  const { lines: block, leadingBlanks } = blockLines(record.insertedCode);
  const docLines = splitLines(documentText);

  if (block.length === 0) {
    return {
      startLine: record.startLine ?? 0,
      endLine: record.endLine ?? record.startLine ?? 0,
      lineageState: 'unknown',
    };
  }

  const matches = findWindows(docLines, block);

  if (matches.length > 0) {
    // The recorded start points at the raw insertion; the non-blank block began
    // `leadingBlanks` lines later.
    const expectedStart =
      record.startLine != null ? record.startLine + leadingBlanks : null;

    // Choose the match closest to where we expect it.
    const best =
      expectedStart == null
        ? matches[0]
        : matches.reduce((a, b) =>
            Math.abs(b - expectedStart) < Math.abs(a - expectedStart) ? b : a,
          );

    let lineageState: LineageState;
    if (expectedStart == null) {
      // Legacy capture with no recorded position: the block is intact but we
      // cannot prove it relocated, so report it as original rather than guess.
      lineageState = 'original';
    } else {
      lineageState = best === expectedStart ? 'original' : 'moved';
    }

    return { startLine: best, endLine: best + block.length - 1, lineageState };
  }

  // No intact window — decide modified vs deleted by surviving content.
  const present = new Set(docLines.filter((l) => l.trim() !== ''));
  const survives = block.some((l) => l.trim() !== '' && present.has(l));

  const lastDoc = Math.max(0, docLines.length - 1);
  const recordedStart = clamp(record.startLine ?? 0, 0, lastDoc);
  const recordedEnd = clamp(record.endLine ?? recordedStart, recordedStart, lastDoc);

  return {
    startLine: recordedStart,
    endLine: recordedEnd,
    lineageState: survives ? 'modified' : 'deleted',
  };
}
