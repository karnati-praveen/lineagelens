'use strict';

// Shared terminal-rendering helpers for the reporting commands (blame, report,
// scan). Previously each command carried its own copy, which meant the same bar
// could round differently in two outputs describing the same repo.

const fs = require('fs');

/** Files above this size are not attributed line-by-line. */
const MAX_FILE_BYTES = 1024 * 1024;

/**
 * ANSI colour helpers. No dependency; every colour collapses to identity when
 * `useColor` is false (`--no-color`, a pipe, or a non-TTY).
 *
 * The full palette is returned regardless of caller, so adding a colour to one
 * command does not require touching the others.
 *
 * @param {boolean} useColor
 * @returns {Record<'bold'|'dim'|'red'|'green'|'yellow'|'blue'|'magenta'|'cyan', (s: string) => string>}
 */
function makePalette(useColor) {
  const wrap = (code) => (s) => (useColor ? `\x1b[${code}m${s}\x1b[0m` : s);
  return {
    bold: wrap('1'),
    dim: wrap('2'),
    red: wrap('31'),
    green: wrap('32'),
    yellow: wrap('33'),
    blue: wrap('34'),
    magenta: wrap('35'),
    cyan: wrap('36'),
  };
}

/**
 * Fixed-width proportion bar. Clamped so a percentage slightly outside 0–100
 * (floating-point drift, or a file that grew between read and blame) can never
 * produce a negative repeat count.
 *
 * @param {number} percent
 * @param {number} [width]
 * @returns {string}
 */
function bar(percent, width = 20) {
  const filled = Math.max(0, Math.min(width, Math.round((percent / 100) * width)));
  return '█'.repeat(filled) + '░'.repeat(width - filled);
}

/** Thousands-separated integer. */
function num(n) {
  return Number(n).toLocaleString('en-US');
}

/** One-decimal percentage, e.g. `41.8%`. */
function pct(n) {
  return `${(Math.round(Number(n) * 10) / 10).toFixed(1)}%`;
}

/**
 * Count with a correctly pluralised noun: `plural(1, 'file')` → `1 file`.
 * @param {number} n
 * @param {string} singular
 * @param {string} [pluralForm] for irregulars
 * @returns {string}
 */
function plural(n, singular, pluralForm) {
  const word = Number(n) === 1 ? singular : (pluralForm ?? `${singular}s`);
  return `${num(n)} ${word}`;
}

/** Cheap binary check: a NUL byte in the first 8 KB. */
function looksBinary(buf) {
  return buf.subarray(0, 8192).includes(0);
}

/**
 * Read a file as UTF-8 lines, or null when it should not be attributed
 * (unreadable, oversized, or binary).
 *
 * @param {string} absPath
 * @returns {string[]|null}
 */
function readTextLines(absPath) {
  let buf;
  try {
    buf = fs.readFileSync(absPath);
  } catch {
    return null;
  }
  if (buf.length > MAX_FILE_BYTES || looksBinary(buf)) return null;
  const text = buf.toString('utf-8');
  return text === '' ? [] : text.split('\n');
}

module.exports = {
  MAX_FILE_BYTES,
  makePalette,
  bar,
  num,
  pct,
  plural,
  looksBinary,
  readTextLines,
};
