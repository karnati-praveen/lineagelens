/**
 * Tests for humanReviewAttestation.ts (F6 extension side).
 *
 * Coverage:
 * - HumanReviewDepthTracker: depth signal mirrors backend formula.
 * - runAttestHumanReview: submits the right payload shape (camelCase) to the
 *   backend and returns a ReviewStatus on success.
 * - fetchReviewStatus: maps the snake_case server response to camelCase.
 */

import { HumanReviewDepthTracker, runAttestHumanReview, fetchReviewStatus } from '../humanReviewAttestation';
import type { DepthSignal } from '../humanReviewAttestation';

// ─── Depth tracker: local formula mirrors backend ─────────────────────────────

describe('HumanReviewDepthTracker', () => {
  it('returns shallow before startReview() is called', () => {
    const t = new HumanReviewDepthTracker();
    t.setLinesShown(100);
    expect(t.secondsOnDiff).toBe(0);
    expect(t.computeLocalDepth()).toBe('shallow');
  });

  it('returns shallow for implausibly-fast approval (time_per_line < 1)', () => {
    const t = new HumanReviewDepthTracker();
    t.setLinesShown(400);
    // Inject a very recent startMs so secondsOnDiff ≈ 0
    (t as unknown as { startMs: number }).startMs = Date.now() - 100; // 0.1 s
    expect(t.computeLocalDepth()).toBe('shallow');
  });

  it('returns adequate for a normal review pace', () => {
    const t = new HumanReviewDepthTracker();
    t.setLinesShown(40);
    (t as unknown as { startMs: number }).startMs = Date.now() - 200_000; // 200 s
    expect(t.computeLocalDepth()).toBe('adequate');
  });

  it('returns deep for thorough review (≥5 s/line, ≥3 comments, ≥50 lines)', () => {
    const t = new HumanReviewDepthTracker();
    t.setLinesShown(60);
    t.incrementComments(3);
    (t as unknown as { startMs: number }).startMs = Date.now() - 360_000; // 360 s → 6 s/line
    expect(t.computeLocalDepth()).toBe('deep');
  });

  it('reset() clears state and makes secondsOnDiff 0', () => {
    const t = new HumanReviewDepthTracker();
    t.startReview();
    t.setLinesShown(50);
    t.incrementComments(2);
    t.reset();
    expect(t.secondsOnDiff).toBe(0);
    expect(t.commentCount).toBe(0);
    expect(t.linesShown).toBe(0);
  });

  it('incrementComments accumulates', () => {
    const t = new HumanReviewDepthTracker();
    t.incrementComments(2);
    t.incrementComments(1);
    expect(t.commentCount).toBe(3);
  });
});

// ─── runAttestHumanReview: payload shape + happy-path ────────────────────────

describe('runAttestHumanReview', () => {
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

    // Stub vscode.window to return deterministic values.
    const mockWindow = {
      showInputBox: jest
        .fn()
        .mockResolvedValueOnce('record-abc')   // scopeRef
        .mockResolvedValueOnce('60')           // linesReviewed
        .mockResolvedValueOnce('300')          // secondsOnDiff
        .mockResolvedValueOnce('3'),           // commentCount
      showQuickPick: jest.fn().mockResolvedValueOnce({ label: 'approved' })
    };

    const tracker = new HumanReviewDepthTracker();
    tracker.setLinesShown(60);
    (tracker as unknown as { startMs: number }).startMs = Date.now() - 300_000;
    tracker.incrementComments(3);

    // Temporarily swap the vscode module reference inside the module.
    // In practice this test runs in jest with the __mocks__/vscode shim.
    const vscode = require('vscode') as typeof import('vscode');
    (vscode.window as unknown as Record<string, unknown>).showInputBox = mockWindow.showInputBox;
    (vscode.window as unknown as Record<string, unknown>).showQuickPick = mockWindow.showQuickPick;

    const result = await runAttestHumanReview(
      mockClient as unknown as import('../backend').BackendIngestClient,
      tracker
    );

    expect(capturedPath).toBe('/review/attest');

    const body = capturedBody as Record<string, unknown>;
    // Must use camelCase keys — this is the key correctness check.
    expect(body).toHaveProperty('scopeRef', 'record-abc');
    expect(body).toHaveProperty('linesReviewed');
    expect(body).toHaveProperty('secondsOnDiff');
    expect(body).toHaveProperty('commentCount');
    expect(body).toHaveProperty('verdict', 'approved');
    // Must NOT have snake_case keys.
    expect(body).not.toHaveProperty('scope_ref');
    expect(body).not.toHaveProperty('lines_reviewed');

    expect(result).not.toBeUndefined();
    expect(result?.hasReview).toBe(true);
    expect(result?.depthSignal).toBe('adequate');
    expect(result?.verdict).toBe('approved');
    expect(result?.attestationId).toBe(42);

    // Tracker must be reset after successful submission.
    expect(tracker.secondsOnDiff).toBe(0);
    expect(tracker.commentCount).toBe(0);
  });

  it('returns undefined when the user cancels scope ref input', async () => {
    const vscode = require('vscode') as typeof import('vscode');
    (vscode.window as unknown as Record<string, unknown>).showInputBox = jest
      .fn()
      .mockResolvedValueOnce(undefined); // user cancelled

    const mockClient = { callApi: jest.fn() };
    const tracker = new HumanReviewDepthTracker();

    const result = await runAttestHumanReview(
      mockClient as unknown as import('../backend').BackendIngestClient,
      tracker
    );

    expect(result).toBeUndefined();
    expect(mockClient.callApi).not.toHaveBeenCalled();
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

    expect(status.hasReview).toBe(true);
    expect(status.depthSignal).toBe('deep');
    expect(status.verdict).toBe('approved');
    expect(status.attestationId).toBe(99);
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

    expect(status.hasReview).toBe(false);
    expect(status.depthSignal).toBeNull();
  });
});
