import * as path from 'node:path';
import type { CaptureStatus, LocalLlmProxyRuntime, RequestResponsePair } from './proxy';
import type { StoredHookEvent } from './hookListener';
import { extractHookEventContent, extractHookEventFilePath } from './hookListener';

const DEFAULT_CORRELATION_WINDOW_MS = 30_000;
const DEFAULT_SIMILARITY_THRESHOLD = 0.7;
const AMBIGUOUS_TIMING_DELTA_MS = 500;
const MAX_SIMILARITY_INPUT_LENGTH = 4_000;
const REQUEST_CLAIM_RETENTION_MS = 5 * 60_000;
const MAX_CLAIMED_PROMPT_REQUEST_IDS = 1_024;
const SAFE_CORRELATION_HEADER_KEYS = new Set([
  'accept',
  'content-type',
  'user-agent',
  'x-client-name',
  'x-request-id'
]);

const claimedPromptRequestIds = new Map<string, number>();
type CapturedCorrelationStatus = Extract<
  CaptureStatus,
  'full' | 'metadata_only' | 'tunnel_only' | 'hook'
>;

export function resetCorrelationState(): void {
  claimedPromptRequestIds.clear();
}

export type PromptCorrelationResult =
  | {
      promptStatus: 'captured';
      captureStatus: CapturedCorrelationStatus;
      requestUuid: string;
      timingDifferenceMs: number;
      correlationWindowMs: number;
      similarityThreshold: number;
      correlationConfidence: number;
      fileContextMatched: boolean;
      matchedFileContextTokens: string[];
      contentSimilarityApplied: boolean;
      ambiguityResolvedByContent: boolean;
      contentSimilarityScore: number | null;
      proxyResponseTimestampIso: string;
      proxyRequestTimestampIso: string;
      fullPromptMessages: unknown;
      modelName: unknown;
      parameters: Record<string, unknown> | undefined;
      targetHost: string | null;
      requestHeaders: Record<string, string | string[]> | null;
      systemPrompt: unknown;
      rawModelResponse: string;
      rawModelResponseBase64: string;
      captureEvidence: CaptureEvidence | null;
    }
  | {
      promptStatus: 'not-captured';
      captureStatus: CaptureStatus;
      reason:
        | 'local-proxy-unavailable'
        | 'invalid-insertion-timestamp'
        | 'no-proxy-response-within-window'
        | 'prompt-already-associated'
        | 'metadata-only-capture'
        | 'tunnel-only-capture';
      requestUuid: null;
      timingDifferenceMs: null;
      correlationWindowMs: number;
      similarityThreshold: number;
      correlationConfidence: 0;
      fileContextMatched: false;
      matchedFileContextTokens: [];
      contentSimilarityApplied: false;
      ambiguityResolvedByContent: false;
      contentSimilarityScore: null;
      proxyResponseTimestampIso: null;
      proxyRequestTimestampIso: null;
      fullPromptMessages: null;
      modelName: null;
      parameters: null;
      targetHost: null;
      requestHeaders: null;
      systemPrompt: null;
      rawModelResponse: null;
      rawModelResponseBase64: null;
      captureEvidence: CaptureEvidence | null;
    };

export type CaptureEvidence = {
  requestId: string | null;
  targetHost: string | null;
  targetPort: number | null;
  userAgent: string | null;
  captureStatus: CaptureStatus;
  tunnelDurationMs: number | null;
  captureReason: string | null;
};

type NotCapturedReason =
  | 'local-proxy-unavailable'
  | 'invalid-insertion-timestamp'
  | 'no-proxy-response-within-window'
  | 'prompt-already-associated'
  | 'metadata-only-capture'
  | 'tunnel-only-capture';

type CorrelationInput = {
  insertionTimestampIso: string;
  filePath: string;
  insertedCode: string;
  localProxy: LocalLlmProxyRuntime | undefined;
  correlationWindowMs?: number;
  similarityThreshold?: number;
};

type CorrelationCandidate = {
  pair: RequestResponsePair;
  timingDifferenceMs: number;
  fileContextMatched: boolean;
  matchedFileContextTokens: string[];
  responseTimestampMs: number;
  captureStatus: CapturedCorrelationStatus;
  contentSimilarityScore: number | null;
  contentSimilarityQualified: boolean;
};

export async function correlateInsertionWithProxyRequest(
  input: CorrelationInput
): Promise<PromptCorrelationResult> {
  const correlationWindowMs =
    input.correlationWindowMs && input.correlationWindowMs > 0
      ? input.correlationWindowMs
      : DEFAULT_CORRELATION_WINDOW_MS;
  const similarityThreshold = clampSimilarityThreshold(
    input.similarityThreshold ?? DEFAULT_SIMILARITY_THRESHOLD
  );

  if (!input.localProxy) {
    return buildNotCapturedResult('local-proxy-unavailable', correlationWindowMs, similarityThreshold);
  }

  const insertionTimestampMs = Date.parse(input.insertionTimestampIso);
  if (Number.isNaN(insertionTimestampMs)) {
    return buildNotCapturedResult('invalid-insertion-timestamp', correlationWindowMs, similarityThreshold);
  }

  pruneClaimedPromptRequestIds();

  const allPairs = input.localProxy.getRecentPairs();
  const allWindowCandidates = collectCandidates({
    pairs: allPairs,
    insertionTimestampMs,
    correlationWindowMs,
    filePath: input.filePath
  });

  const availableCandidates = allWindowCandidates.filter(
    (candidate) => !isClaimedPromptRequestId(candidate.pair.request.id)
  );

  if (availableCandidates.length === 0) {
    if (allWindowCandidates.length > 0) {
      return buildNotCapturedResult('prompt-already-associated', correlationWindowMs, similarityThreshold);
    }

    return buildNotCapturedResult(
      'no-proxy-response-within-window',
      correlationWindowMs,
      similarityThreshold
    );
  }

  availableCandidates.sort(compareCandidatesBaseline);

  const timingAmbiguous = isTimingWindowAmbiguous(availableCandidates);
  const insertedCode = input.insertedCode;
  const contentSimilarityApplied = timingAmbiguous && insertedCode.trim().length > 0;
  const ambiguityResolvedByContent = contentSimilarityApplied
    ? resolveByContentSimilarity(availableCandidates, insertedCode, similarityThreshold)
    : false;

  const bestCandidate = availableCandidates[0];
  const correlationConfidence = computeCorrelationConfidence({
    timingDifferenceMs: bestCandidate.timingDifferenceMs,
    correlationWindowMs,
    fileContextMatched: bestCandidate.fileContextMatched,
    contentSimilarityApplied,
    ambiguityResolvedByContent,
    contentSimilarityScore: bestCandidate.contentSimilarityScore,
    similarityThreshold,
    captureStatus: bestCandidate.captureStatus
  });

  rememberClaimedPromptRequestId(bestCandidate.pair.request.id);

  return {
    promptStatus: 'captured',
    captureStatus: bestCandidate.captureStatus,
    requestUuid: bestCandidate.pair.request.id,
    timingDifferenceMs: bestCandidate.timingDifferenceMs,
    correlationWindowMs,
    similarityThreshold,
    correlationConfidence,
    fileContextMatched: bestCandidate.fileContextMatched,
    matchedFileContextTokens: bestCandidate.matchedFileContextTokens,
    contentSimilarityApplied,
    ambiguityResolvedByContent,
    contentSimilarityScore: bestCandidate.contentSimilarityScore,
    proxyResponseTimestampIso:
      bestCandidate.pair.response?.timestampIso ??
      bestCandidate.pair.request.tunnelMetadata?.endedAtIso ??
      bestCandidate.pair.request.timestampIso,
    proxyRequestTimestampIso: bestCandidate.pair.request.timestampIso,
    fullPromptMessages: bestCandidate.pair.request.messages,
    modelName: bestCandidate.pair.request.model,
    parameters: bestCandidate.pair.request.parameters,
    targetHost: bestCandidate.pair.request.targetHost,
    requestHeaders: sanitizeCorrelationRequestHeaders(bestCandidate.pair.request.headers),
    systemPrompt: bestCandidate.pair.request.systemPrompt,
    rawModelResponse: bestCandidate.pair.response?.rawBodyUtf8 ?? '',
    rawModelResponseBase64: bestCandidate.pair.response?.rawBodyBase64 ?? '',
    captureEvidence: buildCaptureEvidence(bestCandidate)
  };
}

function collectCandidates(input: {
  pairs: RequestResponsePair[];
  insertionTimestampMs: number;
  correlationWindowMs: number;
  filePath: string;
}): CorrelationCandidate[] {
  const candidates: CorrelationCandidate[] = [];

  for (const pair of input.pairs) {
    if (pair.request.captureStatus === 'unavailable') {
      continue;
    }

    const responseTimestampSource =
      pair.response?.timestampIso ??
      pair.request.tunnelMetadata?.endedAtIso ??
      pair.request.timestampIso;
    const responseTimestampMs = Date.parse(responseTimestampSource);
    if (Number.isNaN(responseTimestampMs)) {
      continue;
    }

    const timingDifferenceMs = Math.abs(input.insertionTimestampMs - responseTimestampMs);
    if (timingDifferenceMs > input.correlationWindowMs) {
      continue;
    }

    const fileContext = matchFileContextTokens(pair, input.filePath);

    candidates.push({
      pair,
      timingDifferenceMs,
      fileContextMatched: fileContext.fileContextMatched,
      matchedFileContextTokens: fileContext.matchedFileContextTokens,
      responseTimestampMs,
      captureStatus: pair.request.captureStatus,
      contentSimilarityScore: null,
      contentSimilarityQualified: false
    });
  }

  return candidates;
}

function isTimingWindowAmbiguous(candidates: readonly CorrelationCandidate[]): boolean {
  if (candidates.length < 2) {
    return false;
  }

  const orderedByTiming = [...candidates].sort(
    (left, right) => left.timingDifferenceMs - right.timingDifferenceMs
  );

  const bestTimingDifference = orderedByTiming[0].timingDifferenceMs;
  const nearBestCount = orderedByTiming.filter(
    (candidate) => candidate.timingDifferenceMs <= bestTimingDifference + AMBIGUOUS_TIMING_DELTA_MS
  ).length;

  return nearBestCount > 1;
}

function compareCandidatesBaseline(
  left: CorrelationCandidate,
  right: CorrelationCandidate
): number {
  if (left.fileContextMatched !== right.fileContextMatched) {
    return left.fileContextMatched ? -1 : 1;
  }

  const leftPriority = captureStatusPriority(left.captureStatus);
  const rightPriority = captureStatusPriority(right.captureStatus);
  if (leftPriority !== rightPriority) {
    return rightPriority - leftPriority;
  }

  if (left.timingDifferenceMs !== right.timingDifferenceMs) {
    return left.timingDifferenceMs - right.timingDifferenceMs;
  }

  return right.responseTimestampMs - left.responseTimestampMs;
}

function captureStatusPriority(status: CaptureStatus): number {
  if (status === 'full') {
    return 3;
  }

  if (status === 'hook') {
    return 2.5;
  }

  if (status === 'metadata_only') {
    return 2;
  }

  if (status === 'tunnel_only') {
    return 1;
  }

  return 0;
}

function resolveByContentSimilarity(
  candidates: CorrelationCandidate[],
  insertedCode: string,
  similarityThreshold: number
): boolean {
  for (const candidate of candidates) {
    const responseBody = candidate.pair.response?.rawBodyUtf8 ?? '';
    const similarityScore = calculateLevenshteinSimilarity(responseBody, insertedCode);
    candidate.contentSimilarityScore = similarityScore;
    candidate.contentSimilarityQualified = similarityScore >= similarityThreshold;
    if (candidate.contentSimilarityQualified && similarityScore >= 0.95) {
      break;
    }
  }

  const hasQualified = candidates.some((c) => c.contentSimilarityQualified);
  if (!hasQualified) {
    return false;
  }

  candidates.sort((left, right) => {
    if (left.contentSimilarityQualified !== right.contentSimilarityQualified) {
      return left.contentSimilarityQualified ? -1 : 1;
    }
    const leftScore = left.contentSimilarityScore ?? 0;
    const rightScore = right.contentSimilarityScore ?? 0;
    if (leftScore !== rightScore) {
      return rightScore - leftScore;
    }
    return compareCandidatesBaseline(left, right);
  });

  return true;
}

function clampSimilarityThreshold(value: number): number {
  if (!Number.isFinite(value)) {
    return DEFAULT_SIMILARITY_THRESHOLD;
  }

  return Math.max(0, Math.min(1, value));
}

function computeCorrelationConfidence(input: {
  timingDifferenceMs: number;
  correlationWindowMs: number;
  fileContextMatched: boolean;
  contentSimilarityApplied: boolean;
  ambiguityResolvedByContent: boolean;
  contentSimilarityScore: number | null;
  similarityThreshold: number;
  captureStatus: CaptureStatus;
}): number {
  const timingRatio =
    input.correlationWindowMs > 0
      ? Math.min(1, input.timingDifferenceMs / input.correlationWindowMs)
      : 1;
  const timingConfidence = 1 - timingRatio;
  const fileContextBoost = input.fileContextMatched ? 0.15 : 0;

  let confidence = Math.min(1, timingConfidence * 0.85 + fileContextBoost);

  if (
    input.contentSimilarityApplied &&
    input.ambiguityResolvedByContent &&
    typeof input.contentSimilarityScore === 'number' &&
    input.contentSimilarityScore >= input.similarityThreshold
  ) {
    const denominator = Math.max(0.001, 1 - input.similarityThreshold);
    const normalizedSimilarityBoost =
      (input.contentSimilarityScore - input.similarityThreshold) / denominator;
    confidence = Math.min(1, Math.max(confidence, confidence + 0.1 + normalizedSimilarityBoost * 0.2));
  }

  const captureMultiplierByStatus =
    input.captureStatus === 'hook'
      ? 0.9
      : input.captureStatus === 'metadata_only'
        ? 0.78
        : input.captureStatus === 'tunnel_only'
          ? 0.62
          : 0.35;
  const captureMultiplier = input.captureStatus === 'full' ? 1 : captureMultiplierByStatus;

  return Number((confidence * captureMultiplier).toFixed(4));
}

export type HookCorrelationInput = {
  insertionTimestampIso: string;
  filePath: string;
  hookEvents: StoredHookEvent[];
  correlationWindowMs?: number;
};

export function correlateInsertionWithHookEvents(
  input: HookCorrelationInput
): PromptCorrelationResult {
  const correlationWindowMs =
    input.correlationWindowMs && input.correlationWindowMs > 0
      ? input.correlationWindowMs
      : DEFAULT_CORRELATION_WINDOW_MS;

  const insertionMs = new Date(input.insertionTimestampIso).getTime();
  if (Number.isNaN(insertionMs)) {
    return buildNotCapturedResult('invalid-insertion-timestamp', correlationWindowMs, DEFAULT_SIMILARITY_THRESHOLD);
  }

  const normalizedInsertionPath = input.filePath.replaceAll('\\', '/').toLowerCase();

  const candidates = input.hookEvents.filter((event) => {
    const eventPath = extractHookEventFilePath(event).replaceAll('\\', '/').toLowerCase();
    if (!eventPath || !normalizedInsertionPath.endsWith(eventPath) && !eventPath.endsWith(normalizedInsertionPath)) {
      return false;
    }
    const timingDiff = insertionMs - event.capturedAtMs;
    return timingDiff >= 0 && timingDiff <= correlationWindowMs;
  });

  if (candidates.length === 0) {
    return buildNotCapturedResult('no-proxy-response-within-window', correlationWindowMs, DEFAULT_SIMILARITY_THRESHOLD);
  }

  // Pick the most recent matching event within the window
  const best = candidates.reduce((prev, curr) =>
    curr.capturedAtMs > prev.capturedAtMs ? curr : prev,
    candidates[0]
  );

  const timingDifferenceMs = insertionMs - best.capturedAtMs;
  const content = extractHookEventContent(best);
  const requestUuid = (best.session_id ?? '') || ('hook-' + best.capturedAtMs.toString(16));

  const captureEvidence: CaptureEvidence = {
    requestId: requestUuid,
    targetHost: 'cli',
    targetPort: null,
    userAgent: 'claude-code',
    captureStatus: 'hook',
    tunnelDurationMs: null,
    captureReason: 'claude-code-hook'
  };

  const hookEvent = best as StoredHookEvent & Record<string, unknown>;
  const fullPromptMessages =
    (hookEvent.fullPromptMessages as unknown) ??
    (hookEvent.messages as unknown) ??
    (typeof hookEvent.prompt === 'string'
      ? [{ role: 'user', content: hookEvent.prompt as string }]
      : null);

  const systemPrompt =
    (hookEvent.systemPrompt as unknown) ??
    (hookEvent.system_prompt as unknown) ??
    null;

  const modelName =
    (hookEvent.model as unknown) ??
    (hookEvent.modelName as unknown) ??
    null;

  return {
    promptStatus: 'captured',
    captureStatus: 'hook',
    requestUuid,
    timingDifferenceMs,
    correlationWindowMs,
    similarityThreshold: DEFAULT_SIMILARITY_THRESHOLD,
    correlationConfidence: timingDifferenceMs < 5_000 ? 0.85 : 0.7,
    fileContextMatched: true,
    matchedFileContextTokens: [normalizedInsertionPath],
    contentSimilarityApplied: false,
    ambiguityResolvedByContent: false,
    contentSimilarityScore: null,
    proxyResponseTimestampIso: best.capturedAtIso,
    proxyRequestTimestampIso: best.capturedAtIso,
    fullPromptMessages,
    modelName,
    parameters: undefined,
    targetHost: 'cli',
    requestHeaders: null,
    systemPrompt,
    rawModelResponse: content,
    rawModelResponseBase64: '',
    captureEvidence
  };
}

export function calculateLevenshteinSimilarity(
  rawModelResponse: string,
  insertedCode: string
): number {
  const left = normalizeForSimilarity(rawModelResponse);
  const right = normalizeForSimilarity(insertedCode);

  if (left.length === 0 || right.length === 0) {
    return 0;
  }

  if (left === right) {
    return 1;
  }

  const leftTrimmed = left.slice(0, MAX_SIMILARITY_INPUT_LENGTH);
  const rightTrimmed = right.slice(0, MAX_SIMILARITY_INPUT_LENGTH);

  const distance = computeLevenshteinDistance(leftTrimmed, rightTrimmed);
  const maxLength = Math.max(leftTrimmed.length, rightTrimmed.length);

  if (maxLength === 0) {
    return 0;
  }

  const similarity = 1 - distance / maxLength;
  return Number(Math.max(0, Math.min(1, similarity)).toFixed(4));
}

function normalizeForSimilarity(value: string): string {
  return value.replaceAll('\r\n', '\n').replaceAll(/\s+/g, ' ').trim().toLowerCase();
}

function computeLevenshteinDistance(left: string, right: string): number {
  if (left === right) {
    return 0;
  }

  if (left.length === 0) {
    return right.length;
  }

  if (right.length === 0) {
    return left.length;
  }

  let source = left;
  let target = right;

  if (source.length > target.length) {
    source = right;
    target = left;
  }

  const sourceLength = source.length;
  const targetLength = target.length;

  const previous: number[] = new Array(sourceLength + 1);
  const current: number[] = new Array(sourceLength + 1);

  for (let index = 0; index <= sourceLength; index += 1) {
    previous[index] = index;
  }

  for (let targetIndex = 1; targetIndex <= targetLength; targetIndex += 1) {
    current[0] = targetIndex;

    for (let sourceIndex = 1; sourceIndex <= sourceLength; sourceIndex += 1) {
      const substitutionCost =
        source[sourceIndex - 1] === target[targetIndex - 1] ? 0 : 1;

      const deletion = previous[sourceIndex] + 1;
      const insertion = current[sourceIndex - 1] + 1;
      const substitution = previous[sourceIndex - 1] + substitutionCost;

      current[sourceIndex] = Math.min(deletion, insertion, substitution);
    }

    for (let sourceIndex = 0; sourceIndex <= sourceLength; sourceIndex += 1) {
      previous[sourceIndex] = current[sourceIndex];
    }
  }

  return previous[sourceLength];
}

function matchFileContextTokens(
  pair: RequestResponsePair,
  filePath: string
): { fileContextMatched: boolean; matchedFileContextTokens: string[] } {
  const fileContextTokens = buildFileContextTokens(filePath);
  if (fileContextTokens.length === 0) {
    return {
      fileContextMatched: false,
      matchedFileContextTokens: []
    };
  }

  const searchBlob =
    stringifyUnknown(pair.request.messages) + '\n' + stringifyUnknown(pair.request.parameters);

  const normalizedBlob = searchBlob.toLowerCase();
  const matchedFileContextTokens: string[] = [];

  for (const token of fileContextTokens) {
    if (tokenAppearsInText(normalizedBlob, token)) {
      matchedFileContextTokens.push(token);
    }
  }

  return {
    fileContextMatched: matchedFileContextTokens.length > 0,
    matchedFileContextTokens
  };
}

function buildFileContextTokens(filePath: string): string[] {
  const normalizedPath = filePath.replaceAll('\\', '/').toLowerCase();
  const baseName = path.basename(normalizedPath).toLowerCase();
  const extension = path.extname(baseName).toLowerCase();
  const directorySegments = normalizedPath.split('/').filter((segment) => segment.length > 0);

  const tokens = new Set<string>();

  if (baseName.length >= 3) {
    tokens.add(baseName);
  }

  const fileStem = extension.length > 0 ? baseName.slice(0, -extension.length) : baseName;
  if (fileStem.length >= 3) {
    tokens.add(fileStem);
  }

  if (extension.length >= 2) {
    tokens.add(extension);
  }

  const tailSegments = directorySegments.slice(-3);
  for (const segment of tailSegments) {
    if (segment.length >= 3) {
      tokens.add(segment);
    }
  }

  return [...tokens];
}

function pruneClaimedPromptRequestIds(): void {
  const cutoff = Date.now() - REQUEST_CLAIM_RETENTION_MS;

  for (const [requestId, claimedAtMs] of claimedPromptRequestIds.entries()) {
    if (claimedAtMs < cutoff) {
      claimedPromptRequestIds.delete(requestId);
    }
  }

  while (claimedPromptRequestIds.size > MAX_CLAIMED_PROMPT_REQUEST_IDS) {
    const oldestRequestId = claimedPromptRequestIds.keys().next().value;
    if (typeof oldestRequestId !== 'string') {
      break;
    }

    claimedPromptRequestIds.delete(oldestRequestId);
  }
}

function stringifyUnknown(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }

  if (value === null || value === undefined) {
    return '';
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function buildNotCapturedResult(
  reason: NotCapturedReason,
  correlationWindowMs: number,
  similarityThreshold: number,
  captureStatus: CaptureStatus = 'unavailable',
  captureEvidence: CaptureEvidence | null = null
): PromptCorrelationResult {
  return {
    promptStatus: 'not-captured',
    captureStatus,
    reason,
    requestUuid: null,
    timingDifferenceMs: null,
    correlationWindowMs,
    similarityThreshold,
    correlationConfidence: 0,
    fileContextMatched: false,
    matchedFileContextTokens: [],
    contentSimilarityApplied: false,
    ambiguityResolvedByContent: false,
    contentSimilarityScore: null,
    proxyResponseTimestampIso: null,
    proxyRequestTimestampIso: null,
    fullPromptMessages: null,
    modelName: null,
    parameters: null,
    targetHost: null,
    requestHeaders: null,
    systemPrompt: null,
    rawModelResponse: null,
    rawModelResponseBase64: null,
    captureEvidence
  };
}

function isClaimedPromptRequestId(requestId: string): boolean {
  const claimedAtMs = claimedPromptRequestIds.get(requestId);
  if (typeof claimedAtMs !== 'number') {
    return false;
  }

  claimedPromptRequestIds.delete(requestId);
  claimedPromptRequestIds.set(requestId, claimedAtMs);
  return true;
}

function sanitizeCorrelationRequestHeaders(
  headers: Record<string, string | string[]> | null
): Record<string, string | string[]> | null {
  if (!headers) {
    return null;
  }

  const sanitized: Record<string, string | string[]> = {};

  for (const [key, value] of Object.entries(headers)) {
    if (!SAFE_CORRELATION_HEADER_KEYS.has(key.toLowerCase())) {
      continue;
    }

    sanitized[key] = Array.isArray(value) ? value.map(String) : String(value);
  }

  return Object.keys(sanitized).length > 0 ? sanitized : null;
}

function rememberClaimedPromptRequestId(requestId: string): void {
  claimedPromptRequestIds.delete(requestId);
  claimedPromptRequestIds.set(requestId, Date.now());
  pruneClaimedPromptRequestIds();
}

function tokenAppearsInText(text: string, token: string): boolean {
  if (!token || token.trim().length === 0) {
    return false;
  }

  const escapedToken = escapeRegExp(token);
  const pattern = new RegExp('(^|[^A-Za-z0-9_])' + escapedToken + '(?=$|[^A-Za-z0-9_])');
  return pattern.test(text);
}

function escapeRegExp(value: string): string {
  return value.replaceAll(/[.*+?^${}()|[\]\\]/g, String.raw`\$&`);
}

function buildCaptureEvidence(candidate: CorrelationCandidate): CaptureEvidence {
  return {
    requestId: candidate.pair.request.id,
    targetHost: candidate.pair.request.targetHost ?? null,
    targetPort: candidate.pair.request.targetPort ?? null,
    userAgent: candidate.pair.request.requestMetadata?.userAgent ?? null,
    captureStatus: candidate.captureStatus,
    tunnelDurationMs: candidate.pair.request.tunnelMetadata?.durationMs ?? null,
    captureReason: candidate.pair.request.captureReason ?? null
  };
}
