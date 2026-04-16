import * as path from 'path';
import { LocalLlmProxyRuntime, RequestResponsePair } from './proxy';

const DEFAULT_CORRELATION_WINDOW_MS = 15_000;
const DEFAULT_SIMILARITY_THRESHOLD = 0.7;
const AMBIGUOUS_TIMING_DELTA_MS = 250;
const MAX_SIMILARITY_INPUT_LENGTH = 4_000;
const REQUEST_CLAIM_RETENTION_MS = 5 * 60_000;

const claimedPromptRequestIds = new Map<string, number>();

export type PromptCorrelationResult =
  | {
      promptStatus: 'captured';
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
      rawModelResponse: string;
      rawModelResponseBase64: string;
    }
  | {
      promptStatus: 'not-captured';
      reason:
        | 'local-proxy-unavailable'
        | 'invalid-insertion-timestamp'
        | 'no-proxy-response-within-window'
        | 'prompt-already-associated';
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
      rawModelResponse: null;
      rawModelResponseBase64: null;
    };

type NotCapturedReason =
  | 'local-proxy-unavailable'
  | 'invalid-insertion-timestamp'
  | 'no-proxy-response-within-window'
  | 'prompt-already-associated';

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
    (candidate) => !claimedPromptRequestIds.has(candidate.pair.request.id)
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
  let ambiguityResolvedByContent = false;

  if (contentSimilarityApplied) {
    for (const candidate of availableCandidates) {
      const responseBody = candidate.pair.response?.rawBodyUtf8 ?? '';
      const similarityScore = calculateLevenshteinSimilarity(responseBody, insertedCode);
      candidate.contentSimilarityScore = similarityScore;
      candidate.contentSimilarityQualified = similarityScore >= similarityThreshold;
    }

    const hasQualifiedCandidate = availableCandidates.some(
      (candidate) => candidate.contentSimilarityQualified
    );

    if (hasQualifiedCandidate) {
      ambiguityResolvedByContent = true;
      availableCandidates.sort((left, right) => {
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
    }
  }

  const bestCandidate = availableCandidates[0];
  const correlationConfidence = computeCorrelationConfidence({
    timingDifferenceMs: bestCandidate.timingDifferenceMs,
    correlationWindowMs,
    fileContextMatched: bestCandidate.fileContextMatched,
    contentSimilarityApplied,
    ambiguityResolvedByContent,
    contentSimilarityScore: bestCandidate.contentSimilarityScore,
    similarityThreshold
  });

  claimedPromptRequestIds.set(bestCandidate.pair.request.id, Date.now());

  return {
    promptStatus: 'captured',
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
    proxyResponseTimestampIso: bestCandidate.pair.response?.timestampIso ?? new Date(0).toISOString(),
    proxyRequestTimestampIso: bestCandidate.pair.request.timestampIso,
    fullPromptMessages: bestCandidate.pair.request.messages,
    modelName: bestCandidate.pair.request.model,
    parameters: bestCandidate.pair.request.parameters,
    rawModelResponse: bestCandidate.pair.response?.rawBodyUtf8 ?? '',
    rawModelResponseBase64: bestCandidate.pair.response?.rawBodyBase64 ?? ''
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
    if (!pair.response) {
      continue;
    }

    const responseTimestampMs = Date.parse(pair.response.timestampIso);
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

  if (left.timingDifferenceMs !== right.timingDifferenceMs) {
    return left.timingDifferenceMs - right.timingDifferenceMs;
  }

  return right.responseTimestampMs - left.responseTimestampMs;
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

  return Number(confidence.toFixed(4));
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
  return value.replace(/\r\n/g, '\n').replace(/\s+/g, ' ').trim().toLowerCase();
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
    if (normalizedBlob.includes(token)) {
      matchedFileContextTokens.push(token);
    }
  }

  return {
    fileContextMatched: matchedFileContextTokens.length > 0,
    matchedFileContextTokens
  };
}

function buildFileContextTokens(filePath: string): string[] {
  const normalizedPath = filePath.replace(/\\/g, '/').toLowerCase();
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
}

function stringifyUnknown(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }

  if (value === null || typeof value === 'undefined') {
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
  similarityThreshold: number
): PromptCorrelationResult {
  return {
    promptStatus: 'not-captured',
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
    rawModelResponse: null,
    rawModelResponseBase64: null
  };
}
