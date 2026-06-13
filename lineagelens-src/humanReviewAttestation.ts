import * as vscode from 'vscode';
import type { BackendIngestClient } from './backend';

export type DepthSignal = 'shallow' | 'adequate' | 'deep';

export type ReviewStatus = {
  hasReview: boolean;
  depthSignal: DepthSignal | null;
  verdict: string | null;
  attestationId: number | null;
  createdAt: string | null;
};

// ─── Depth tracker ────────────────────────────────────────────────────────────
// Measures review depth locally — time on diff, inline comments, lines shown.
// All telemetry stays local until the reviewer explicitly calls submit().
//
// Formula mirrors the backend (transparent thresholds):
//   time_per_line  = secondsOnDiff / max(linesShown, 1)
//   time_score     = min(time_per_line / 5, 1) * 40
//   comment_score  = min(commentCount / 3, 1) * 30
//   coverage_score = min(linesShown / 50, 1) * 30
//   raw            = sum of above
//   shallow < 35, adequate [35, 70), deep ≥ 70
//   Override: time_per_line < 1 → always shallow.
// ─────────────────────────────────────────────────────────────────────────────

export class HumanReviewDepthTracker {
  private startMs: number | undefined;
  private _commentCount = 0;
  private _linesShown = 0;

  startReview(): void {
    this.startMs = Date.now();
  }

  incrementComments(n = 1): void {
    this._commentCount = Math.max(0, this._commentCount + n);
  }

  setLinesShown(n: number): void {
    this._linesShown = Math.max(0, n);
  }

  get secondsOnDiff(): number {
    if (this.startMs === undefined) {
      return 0;
    }
    return Math.round((Date.now() - this.startMs) / 1000);
  }

  get commentCount(): number {
    return this._commentCount;
  }

  get linesShown(): number {
    return this._linesShown;
  }

  computeLocalDepth(): DepthSignal {
    const secs = this.secondsOnDiff;
    const lines = Math.max(this._linesShown, 1);
    const timePerLine = secs / lines;
    if (timePerLine < 1.0) {
      return 'shallow';
    }
    const timeScore = Math.min(timePerLine / 5.0, 1.0) * 40;
    const commentScore = Math.min(this._commentCount / 3.0, 1.0) * 30;
    const coverageScore = Math.min(this._linesShown / 50.0, 1.0) * 30;
    const raw = timeScore + commentScore + coverageScore;
    if (raw >= 70) {
      return 'deep';
    }
    if (raw >= 35) {
      return 'adequate';
    }
    return 'shallow';
  }

  reset(): void {
    this.startMs = undefined;
    this._commentCount = 0;
    this._linesShown = 0;
  }
}

// ─── Attest command ───────────────────────────────────────────────────────────

export async function runAttestHumanReview(
  client: BackendIngestClient,
  tracker: HumanReviewDepthTracker,
  resource?: vscode.Uri
): Promise<ReviewStatus | undefined> {
  const scopeRef = await vscode.window.showInputBox({
    title: 'LineageLens: Attest Human Review of AI Code',
    prompt: 'Scope ref: record UUID (e.g. abc-123) or PR ref (e.g. pr/42).',
    ignoreFocusOut: true,
    validateInput: (v) => (v.trim().length >= 3 ? undefined : 'Provide a valid scope ref.')
  });
  if (!scopeRef) {
    return undefined;
  }

  const verdictPick = await vscode.window.showQuickPick(
    [
      { label: 'approved', description: 'AI-generated lines reviewed and approved.' },
      { label: 'changes_requested', description: 'Review completed; changes needed before merge.' }
    ],
    {
      title: 'Verdict',
      placeHolder: 'Choose your review verdict.'
    }
  );
  if (!verdictPick) {
    return undefined;
  }

  const linesShown = tracker.linesShown;
  const secondsOnDiff = tracker.secondsOnDiff;
  const commentCount = tracker.commentCount;
  const localDepth = tracker.computeLocalDepth();

  const linesInput = await vscode.window.showInputBox({
    title: 'AI Lines Reviewed',
    prompt: 'Number of AI-generated lines you reviewed.',
    value: String(linesShown > 0 ? linesShown : ''),
    ignoreFocusOut: true,
    validateInput: (v) => (Number.isInteger(Number(v)) && Number(v) >= 0 ? undefined : 'Enter a whole number ≥ 0.')
  });
  if (linesInput === undefined) {
    return undefined;
  }

  const secondsInput = await vscode.window.showInputBox({
    title: 'Seconds on Diff',
    prompt: 'Time spent on the diff (seconds). Auto-measured: ' + String(secondsOnDiff) + ' s.',
    value: String(secondsOnDiff > 0 ? secondsOnDiff : ''),
    ignoreFocusOut: true,
    validateInput: (v) => (Number.isInteger(Number(v)) && Number(v) >= 0 ? undefined : 'Enter a whole number ≥ 0.')
  });
  if (secondsInput === undefined) {
    return undefined;
  }

  const commentsInput = await vscode.window.showInputBox({
    title: 'Inline Comment Count',
    prompt: 'Number of inline or PR comments left during review.',
    value: String(commentCount),
    ignoreFocusOut: true,
    validateInput: (v) => (Number.isInteger(Number(v)) && Number(v) >= 0 ? undefined : 'Enter a whole number ≥ 0.')
  });
  if (commentsInput === undefined) {
    return undefined;
  }

  const lines = Math.max(0, parseInt(linesInput, 10) || 0);
  const seconds = Math.max(0, parseInt(secondsInput, 10) || 0);
  const comments = Math.max(0, parseInt(commentsInput, 10) || 0);

  const result = await client.callApi(
    'POST',
    '/review/attest',
    {
      scopeRef: scopeRef.trim(),
      linesReviewed: lines,
      secondsOnDiff: seconds,
      commentCount: comments,
      verdict: verdictPick.label
    },
    resource
  );

  if (result.statusCode < 200 || result.statusCode >= 300) {
    const detail = extractDetail(result.body);
    throw new Error('Attestation failed (' + String(result.statusCode) + '): ' + detail);
  }

  const body = safeJsonParse(result.body);
  const depthSignal = toStringVal(body?.['depthSignal']) as DepthSignal | null;

  tracker.reset();

  return {
    hasReview: true,
    depthSignal,
    verdict: verdictPick.label,
    attestationId: typeof body?.['attestationId'] === 'number' ? (body['attestationId'] as number) : null,
    createdAt: toStringVal(body?.['createdAt']) ?? null
  };
}

export async function fetchReviewStatus(
  client: BackendIngestClient,
  scopeRef: string,
  resource?: vscode.Uri
): Promise<ReviewStatus> {
  const result = await client.callApi('GET', '/review/status/' + encodeURIComponent(scopeRef), undefined, resource);

  if (result.statusCode < 200 || result.statusCode >= 300) {
    return { hasReview: false, depthSignal: null, verdict: null, attestationId: null, createdAt: null };
  }

  const body = safeJsonParse(result.body);
  const has = body?.['has_review'] === true;
  return {
    hasReview: has,
    depthSignal: has ? (toStringVal(body?.['depth_signal']) as DepthSignal) : null,
    verdict: has ? toStringVal(body?.['verdict']) ?? null : null,
    attestationId: has && typeof body?.['attestation_id'] === 'number' ? (body['attestation_id'] as number) : null,
    createdAt: has ? toStringVal(body?.['created_at']) ?? null : null
  };
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function safeJsonParse(raw: string): Record<string, unknown> | undefined {
  try {
    const parsed = JSON.parse(raw) as unknown;
    return parsed !== null && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : undefined;
  } catch {
    return undefined;
  }
}

function toStringVal(value: unknown): string | undefined {
  if (typeof value === 'string' && value.trim().length > 0) {
    return value.trim();
  }
  return undefined;
}

function extractDetail(raw: string): string {
  const parsed = safeJsonParse(raw);
  if (parsed && typeof parsed['detail'] === 'string') {
    return parsed['detail'];
  }
  return raw.slice(0, 200).trim() || 'unknown error';
}
