/**
 * Evidence hashing primitives — pure, dependency-free (no vscode, no fs).
 *
 * These underpin the local trust layer: range content hashes let a capture be
 * relocated after edits (Phase 1), and the same primitives feed the tamper-
 * evident event chain and capsule export (Phase 3). Keeping them pure makes the
 * trust math unit-testable without a VS Code extension host.
 */

import { createHash } from 'crypto';

/** Lowercase hex SHA-256 of a UTF-8 string. */
export function sha256Hex(input: string): string {
  return createHash('sha256').update(input, 'utf-8').digest('hex');
}

/**
 * Deterministic JSON serialization with stably-sorted object keys, so two
 * logically-equal values always hash to the same digest regardless of key
 * insertion order. Arrays keep their order; primitives serialize as JSON.
 */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortDeep(value));
}

function sortDeep(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortDeep);
  }
  if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(obj).sort()) {
      out[key] = sortDeep(obj[key]);
    }
    return out;
  }
  return value;
}

/**
 * Normalize code for content-addressable comparison: strip trailing whitespace
 * from each line, then trim leading/trailing blank lines. Mirrors the
 * normalisation used by capture scoring so paste/AI/relocation comparisons agree.
 */
export function normalizeForHash(code: string): string {
  return code
    .split('\n')
    .map((line) => line.replace(/\s+$/, ''))
    .join('\n')
    .trim();
}

/** Whitespace-tolerant content hash of a code block. */
export function rangeContentHash(code: string): string {
  return sha256Hex(normalizeForHash(code));
}
