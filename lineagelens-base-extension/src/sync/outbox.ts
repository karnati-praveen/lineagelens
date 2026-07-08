/**
 * Persistent-outbox queue mechanics — pure, no vscode.
 *
 * The retry queue for backend ingest: dedupe by id (idempotency key), cap the
 * queue (drop oldest), compute exponential backoff, and select due entries.
 * CaptureService persists the array via VS Code globalState and orchestrates the
 * actual sends; these functions hold the deterministic, testable logic.
 */

export interface OutboxEntry {
  id: string;
  payload: object;
  attempts: number;
  nextRetryAt: number;
}

export const MAX_OUTBOX = 200;
export const RETRY_BASE_MS = 30_000; // 30 s
export const RETRY_MAX_MS = 600_000; // 10 min

/**
 * Add or replace an entry by id, returning a new array capped at `max`
 * (oldest entries dropped). Replacing reuses the same idempotency key.
 */
export function enqueueEntry(
  entries: OutboxEntry[],
  entry: OutboxEntry,
  max = MAX_OUTBOX,
): OutboxEntry[] {
  const idx = entries.findIndex((e) => e.id === entry.id);
  let next: OutboxEntry[];
  if (idx >= 0) {
    next = entries.slice();
    next[idx] = entry;
  } else {
    next = [...entries, entry];
  }
  if (next.length > max) {
    next = next.slice(next.length - max);
  }
  return next;
}

/** Exponential backoff with a ceiling. `attempts` is 1 after the first failure. */
export function backoffDelay(attempts: number, base = RETRY_BASE_MS, max = RETRY_MAX_MS): number {
  return Math.min(base * Math.pow(2, Math.max(0, attempts - 1)), max);
}

/** Entries whose nextRetryAt has passed. */
export function dueEntries(entries: OutboxEntry[], now: number): OutboxEntry[] {
  return entries.filter((e) => e.nextRetryAt <= now);
}
