/**
 * Tests for humanReviewAttestation.ts (F6 extension side).
 *
 * Coverage:
 * - HumanReviewDepthTracker: depth signal mirrors backend formula.
 * - runAttestHumanReview: submits the right payload shape (camelCase) to the
 *   backend and returns a ReviewStatus on success.
 * - fetchReviewStatus: maps the snake_case server response to camelCase.
 */

import assert from 'node:assert/strict';
import { describe, it, beforeEach, afterEach } from 'node:test';

import { HumanReviewDepthTracker, runAttestHumanReview, fetchReviewStatus } from '../humanReviewAttestation';
import * as vscodeMock from 'vscode';

// Helper: returns an async function that yields successive values from the queue.
function asyncQueue<T>(...values: Array<T | undefined>): () => Promise<T | undefined> {
  const q = [...values];
  return async () => q.shift();
}

// ─── Depth tracker: local formula mirrors backend ─────────────────────────────

describe('HumanReviewDepthTracker', () => {
  it('returns shallow before startReview() is called', () => {
    const t = new HumanReviewDepthTracker();
    t.setLinesShown(100);
    assert.strictEqual(t.secondsOnDiff, 0);
    assert.strictEqual(t.computeLocalDepth(), 'shallow');
  });

  it('returns shallow for implausibly-fast approval (time_per_line < 1)', () => {
    const t = new HumanReviewDepthTracker();
    t.setLinesShown(400);
    (t as unknown as { startMs: number }).startMs = Date.now() - 100; // 0.1 s
    assert.strictEqual(t.computeLocalDepth(), 'shallow');
  });

  it('returns adequate for a normal review pace', () => {
    const t = new HumanReviewDepthTracker();
    t.setLinesShown(40);
    (t as unknown as { startMs: number }).startMs = Date.now() - 200_000; // 200 s
    assert.strictEqual(t.computeLocalDepth(), 'adequate');
  });

  it('returns deep for thorough review (≥5 s/line, ≥3 comments, ≥50 lines)', () => {
    const t = new HumanReviewDepthTracker();
    t.setLinesShown(60);
    t.incrementComments(3);
    (t as unknown as { startMs: number }).startMs = Date.now() - 360_000; // 360 s → 6 s/line
    assert.strictEqual(t.computeLocalDepth(), 'deep');
  });

  it('reset() clears state and makes secondsOnDiff 0', () => {
    const t = new HumanReviewDepthTracker();
    t.startReview();
    t.setLinesShown(50);
    t.incrementComments(2);
    t.reset();
    assert.strictEqual(t.secondsOnDiff, 0);
    assert.strictEqual(t.commentCount, 0);
    assert.strictEqual(t.linesShown, 0);
  });

  it('incrementComments accumulates', () => {
    const t = new HumanReviewDepthTracker();
    t.incrementComments(2);
    t.incrementComments(1);
    assert.strictEqual(t.commentCount, 3);
  });
});

// ─── runAttestHumanReview: payload shape + happy-path ────────────────────────

describe('runAttestHumanReview', () => {
  let savedShowInputBox: typeof vscodeMock.window.showInputBox;
  let savedShowQuickPick: typeof vscodeMock.window.showQuickPick;

  beforeEach(() => {
    savedShowInputBox = vscodeMock.window.showInputBox;
    savedShowQuickPick = vscodeMock.window.showQuickPick;
  });

  afterEach(() => {
    vscodeMock.window.showInputBox = savedShowInputBox;
    vscodeMock.window.showQuickPick = savedShowQuickPick;
  });

  it('sends camelCase payload to /review/attest and returns ReviewStatus', async () => {
    let capturedPath: string | undefined;
    let capturedBody: unknown;

    const mockClient = {
      callApi: async (
        _method: string,
        path: string,
        body: unknown
      ): Promise<{ statusCode: number; body: string }> => {
        capturedPath = path;
        capturedBody = body;
        return {
          statusCode: 201,
          body: JSON.stringify({
            id: 7,
            scopeRef: 'record-abc',
            depthSignal: 'adequate',
            verdict: 'approved',
            attestationId: 42,
            createdAt: '2026-06-13T00:00:00Z'
          })
        };
      }
    };

    // Queue: scopeRef → linesReviewed → secondsOnDiff → commentCount
    vscodeMock.window.showInputBox = asyncQueue('record-abc', '60', '300', '3') as typeof vscodeMock.window.showInputBox;
    vscodeMock.window.showQuickPick = asyncQueue({ label: 'approved' }) as typeof vscodeMock.window.showQuickPick;

    const tracker = new HumanReviewDepthTracker();
    tracker.setLinesShown(60);
    (tracker as unknown as { startMs: number }).startMs = Date.now() - 300_000;
    tracker.incrementComments(3);

    const result = await runAttestHumanReview(
      mockClient as unknown as import('../backend').BackendIngestClient,
      tracker
    );

    assert.strictEqual(capturedPath, '/review/attest');

    const body = capturedBody as Record<string, unknown>;
    // Must use camelCase keys — this is the key correctness check.
    assert.strictEqual(body['scopeRef'], 'record-abc');
    assert.ok('linesReviewed' in body);
    assert.ok('secondsOnDiff' in body);
    assert.ok('commentCount' in body);
    assert.strictEqual(body['verdict'], 'approved');
    // Must NOT have snake_case keys.
    assert.ok(!('scope_ref' in body));
    assert.ok(!('lines_reviewed' in body));

    assert.ok(result !== undefined);
    assert.strictEqual(result?.hasReview, true);
    assert.strictEqual(result?.depthSignal, 'adequate');
    assert.strictEqual(result?.verdict, 'approved');
    assert.strictEqual(result?.attestationId, 42);

    // Tracker must be reset after successful submission.
    assert.strictEqual(tracker.secondsOnDiff, 0);
    assert.strictEqual(tracker.commentCount, 0);
  });

  it('returns undefined when the user cancels scope ref input', async () => {
    vscodeMock.window.showInputBox = asyncQueue(undefined) as typeof vscodeMock.window.showInputBox;

    let callApiCalled = false;
    const mockClient = {
      callApi: async () => {
        callApiCalled = true;
        return { statusCode: 200, body: '{}' };
      }
    };
    const tracker = new HumanReviewDepthTracker();

    const result = await runAttestHumanReview(
      mockClient as unknown as import('../backend').BackendIngestClient,
      tracker
    );

    assert.strictEqual(result, undefined);
    assert.strictEqual(callApiCalled, false);
  });
});

// ─── fetchReviewStatus: snake_case → camelCase mapping ───────────────────────

describe('fetchReviewStatus', () => {
  it('maps has_review / depth_signal / attestation_id from server response', async () => {
    const mockClient = {
      callApi: async (): Promise<{ statusCode: number; body: string }> => ({
        statusCode: 200,
        body: JSON.stringify({
          has_review: true,
          depth_signal: 'deep',
          verdict: 'approved',
          attestation_id: 99,
          created_at: '2026-06-13T00:00:00Z'
        })
      })
    };

    const status = await fetchReviewStatus(
      mockClient as unknown as import('../backend').BackendIngestClient,
      'record-xyz'
    );

    assert.strictEqual(status.hasReview, true);
    assert.strictEqual(status.depthSignal, 'deep');
    assert.strictEqual(status.verdict, 'approved');
    assert.strictEqual(status.attestationId, 99);
  });

  it('returns hasReview=false on non-2xx response', async () => {
    const mockClient = {
      callApi: async (): Promise<{ statusCode: number; body: string }> => ({
        statusCode: 404,
        body: JSON.stringify({ detail: 'not found' })
      })
    };

    const status = await fetchReviewStatus(
      mockClient as unknown as import('../backend').BackendIngestClient,
      'missing-ref'
    );

    assert.strictEqual(status.hasReview, false);
    assert.strictEqual(status.depthSignal, null);
  });
});
