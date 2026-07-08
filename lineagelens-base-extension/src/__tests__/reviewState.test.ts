import { canTransition, applyReview, REVIEW_TRANSITIONS } from '../review/reviewState';
import { CaptureRecord, ReviewState } from '../store';

function rec(over: Partial<CaptureRecord> = {}): CaptureRecord {
  return {
    id: 'id1',
    timestamp: '2026-06-25T10:00:00.000Z',
    filePath: '/proj/bar.ts',
    fileName: 'bar.ts',
    language: 'typescript',
    insertedCode: 'const x = 1;',
    linesAdded: 4,
    workspaceFolder: 'proj',
    confidence: 0.8,
    source: 'ai',
    schemaVersion: 2,
    reviewState: 'unreviewed',
    lineageState: 'original',
    ...over,
  };
}

// ── canTransition ─────────────────────────────────────────────────────────────

test('every declared transition is legal', () => {
  for (const from of Object.keys(REVIEW_TRANSITIONS) as ReviewState[]) {
    for (const to of REVIEW_TRANSITIONS[from]) {
      expect(canTransition(from, to)).toBe(true);
    }
  }
});

test('accepted is only reachable from reviewed', () => {
  expect(canTransition('unreviewed', 'accepted')).toBe(false);
  expect(canTransition('rejected', 'accepted')).toBe(false);
  expect(canTransition('needs_changes', 'accepted')).toBe(false);
  expect(canTransition('reviewed', 'accepted')).toBe(true);
});

test('same-state is not a transition', () => {
  expect(canTransition('reviewed', 'reviewed')).toBe(false);
});

// ── applyReview ───────────────────────────────────────────────────────────────

test('legal transition sets state, timestamp, and note', () => {
  const out = applyReview(rec(), 'reviewed', 'looks good', '2026-06-25T12:00:00.000Z');
  expect(out).not.toBe(rec()); // new object
  expect(out.reviewState).toBe('reviewed');
  expect(out.reviewedAt).toBe('2026-06-25T12:00:00.000Z');
  expect(out.reviewNote).toBe('looks good');
});

test('illegal transition with no note change is a no-op (same reference)', () => {
  const r = rec({ reviewState: 'unreviewed' });
  const out = applyReview(r, 'accepted'); // unreviewed → accepted is illegal
  expect(out).toBe(r);
  expect(out.reviewState).toBe('unreviewed');
});

test('note can be updated without a state change', () => {
  const r = rec({ reviewState: 'reviewed', reviewNote: 'old' });
  const out = applyReview(r, 'reviewed', 'new note');
  expect(out).not.toBe(r);
  expect(out.reviewState).toBe('reviewed');
  expect(out.reviewNote).toBe('new note');
});

test('an unchanged note on the same state is a no-op', () => {
  const r = rec({ reviewState: 'reviewed', reviewNote: 'same' });
  expect(applyReview(r, 'reviewed', 'same')).toBe(r);
});

test('reopening to unreviewed is always allowed from a decided state', () => {
  expect(applyReview(rec({ reviewState: 'accepted' }), 'unreviewed').reviewState).toBe('unreviewed');
  expect(applyReview(rec({ reviewState: 'rejected' }), 'unreviewed').reviewState).toBe('unreviewed');
});

test('a record with no reviewState defaults to unreviewed for transition checks', () => {
  const r = rec({ reviewState: undefined });
  expect(applyReview(r, 'reviewed').reviewState).toBe('reviewed');
});
