/**
 * Evidence hashing primitives — pure, dependency-free (no vscode, no fs).
 */

import { createHash } from 'crypto';

/** Lowercase hex SHA-256 of a UTF-8 string. */
export function sha256Hex(input: string): string {
  return createHash('sha256').update(input, 'utf-8').digest('hex');
}

/**
 * Deterministic JSON serialization with stably-sorted object keys.
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
 * Normalize code for content-addressable comparison.
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
