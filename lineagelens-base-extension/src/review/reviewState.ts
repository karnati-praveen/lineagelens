/**
 * Review-state transition logic — pure, no vscode.
 *
 * The human review lifecycle for an AI-origin capture. Kept as an explicit state
 * machine so the allowed moves are auditable and unit-testable; the store and UI
 * are thin callers of {@link applyReview}.
 *
 *   unreviewed ──▶ reviewed / needs_changes / rejected
 *   needs_changes ─▶ reviewed / rejected / unreviewed(reopen)
 *   reviewed ──────▶ accepted / needs_changes / unreviewed(reopen)
 *   accepted ──────▶ needs_changes / unreviewed(reopen)
 *   rejected ──────▶ unreviewed(reopen) / needs_changes
 *
 * `accepted` is only reachable from `reviewed` — you cannot accept code that was
 * never reviewed.
 */

import { CaptureRecord, ReviewState } from '../store';

export const REVIEW_TRANSITIONS: Record<ReviewState, ReviewState[]> = {
  unreviewed: ['reviewed', 'needs_changes', 'rejected'],
  needs_changes: ['reviewed', 'rejected', 'unreviewed'],
  reviewed: ['accepted', 'needs_changes', 'unreviewed'],
  accepted: ['needs_changes', 'unreviewed'],
  rejected: ['unreviewed', 'needs_changes'],
};

/** Whether `to` is a legal next state from `from` (same-state is not a transition). */
export function canTransition(from: ReviewState, to: ReviewState): boolean {
  return REVIEW_TRANSITIONS[from]?.includes(to) ?? false;
}

/**
 * Produce the record resulting from a review action.
 *
 *  - Legal state change → new record with `reviewState`, `reviewedAt`, and (if
 *    supplied) `reviewNote` updated.
 *  - Same state but a changed note → new record with just the note updated.
 *  - Illegal transition with no note change → the *same* record reference
 *    (callers can detect a no-op by identity).
 *
 * `now` is injectable for deterministic tests.
 */
export function applyReview(
  record: CaptureRecord,
  to: ReviewState,
  note?: string,
  now?: string,
): CaptureRecord {
  const from = record.reviewState ?? 'unreviewed';
  const stateChanges = from !== to && canTransition(from, to);
  const noteChanges = note !== undefined && note !== record.reviewNote;

  if (!stateChanges && !noteChanges) {
    return record;
  }

  return {
    ...record,
    reviewState: stateChanges ? to : record.reviewState,
    reviewedAt: stateChanges ? (now ?? new Date().toISOString()) : record.reviewedAt,
    reviewNote: note !== undefined ? note : record.reviewNote,
  };
}
