import { createHash } from 'crypto';
import type { AgentEvidence, AgentOperationType, AgentSessionKind, NormalizedAgentContext } from './types';

export function toText(value: unknown): string {
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

export function safeSerialize(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return toText(value);
  }
}

export function normalizeModelName(value: unknown): string {
  const text = toText(value).trim();
  return text;
}

export function findHeaderValue(
  headers: Record<string, string | string[]> | null | undefined,
  key: string
): string | null {
  if (!headers) {
    return null;
  }

  const matchingKey = Object.keys(headers).find((headerKey) => headerKey.toLowerCase() === key.toLowerCase());
  if (!matchingKey) {
    return null;
  }

  const value = headers[matchingKey];
  if (typeof value === 'string') {
    return value.trim() || null;
  }

  if (Array.isArray(value) && value.length > 0) {
    return toText(value[0]).trim() || null;
  }

  return null;
}

export function normalizeUserAgent(value: unknown): string | null {
  const text = toText(value).trim();
  return text.length > 0 ? text : null;
}

export function buildDetectionPayloadBlob(input: {
  fullPromptMessages: unknown;
  parameters: unknown;
  systemPrompt: unknown;
  insertedText: string;
}): string {
  return [
    safeSerialize(input.fullPromptMessages),
    safeSerialize(input.parameters),
    toText(input.systemPrompt),
    input.insertedText
  ]
    .join('\n')
    .toLowerCase();
}

export function inferProvider(targetHost: string | null, modelName: string, rawBlob: string): string | null {
  const normalizedModel = modelName.toLowerCase();

  if (targetHost?.includes('anthropic.com') || normalizedModel.includes('claude')) {
    return 'Anthropic';
  }

  if (
    targetHost?.includes('openai.com') ||
    /\b(gpt|o1|o3|o4(?:[-\s]?mini)?|codex)\b/.test(normalizedModel)
  ) {
    return 'OpenAI';
  }

  if (targetHost?.includes('githubcopilot')) {
    return 'GitHub';
  }

  if (
    targetHost?.includes('generativelanguage.googleapis.com') ||
    targetHost?.includes('aiplatform.googleapis.com') ||
    normalizedModel.includes('gemini')
  ) {
    return 'Google';
  }

  if (
    targetHost?.includes('codewhisperer.') ||
    targetHost?.includes('q.us-east-1.amazonaws.com') ||
    targetHost?.includes('amazonq.')
  ) {
    return 'AWS';
  }

  if (targetHost?.includes('codeium.com') || targetHost?.includes('windsurf.codeium.com')) {
    return 'Codeium';
  }

  if (targetHost?.includes('sourcegraph.com') || targetHost?.includes('cody-gateway.sourcegraph.com')) {
    return 'Sourcegraph';
  }

  if (targetHost?.includes('openrouter.ai')) {
    return 'OpenRouter';
  }

  return null;
}

export function classifyOperationType(input: {
  insertedText: string;
  promptBlob: string;
  modelName?: string | null;
}): AgentOperationType {
  const text = (input.insertedText + '\n' + input.promptBlob + '\n' + (input.modelName ?? '')).toLowerCase();

  if (
    /multi[-\s]?file|multiple files|apply_patch|across multiple files|across files|many files|batch edit|workspace run/.test(text) ||
    countDistinctFiles(text) > 1
  ) {
    return 'multi-file-run';
  }

  if (/test\s*[- ]?\s*fix|fix .*tests?|failing tests|pytest|jest|vitest|unit test/.test(text)) {
    return 'test-fix';
  }

  if (/refactor|cleanup|rewrite|simplify|moderni[sz]e/.test(text)) {
    return 'refactor';
  }

  if (/explain|why does|what does this do|summar(i[sz]e|ise)/.test(text)) {
    return 'explain';
  }

  if (/\b(chat session|in conversation|replying to|assistant reply)\b/.test(text)) {
    return 'chat';
  }

  return 'edit';
}

function countDistinctFiles(text: string): number {
  const matches =
    text.match(/(?:\.\.?[\\/]|(?:[A-Za-z]:)?[\\/]|(?:[a-z0-9_.-]+[\\/])+)[^\s'"`]+?\.[a-z0-9]+/gi) ?? [];
  return new Set(matches.map((value) => value.toLowerCase())).size;
}

export function createEvidence(
  source: AgentEvidence['source'],
  field: string,
  value: unknown,
  weight: number,
  note?: string
): AgentEvidence {
  return {
    source,
    field,
    value: toText(value).slice(0, 240),
    weight,
    note
  };
}

export function clampConfidence(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }

  return Number(Math.max(0, Math.min(1, value)).toFixed(4));
}

export function pushEvidence(
  evidence: AgentEvidence[],
  condition: boolean,
  source: AgentEvidence['source'],
  field: string,
  value: unknown,
  weight: number,
  note?: string
): void {
  if (!condition) {
    return;
  }

  evidence.push(createEvidence(source, field, value, weight, note));
}

export function sumEvidenceWeights(evidence: readonly AgentEvidence[]): number {
  return evidence.reduce((sum, entry) => sum + entry.weight, 0);
}

export function hashContext(parts: Array<string | null | undefined>): string {
  const normalized = parts.map((part) => (part ?? '').trim()).join('|');
  return createHash('sha256').update(normalized).digest('hex').slice(0, 16);
}

export function buildSessionSignature(context: {
  toolName: string | null;
  provider: string | null;
  modelName: string | null;
  sessionKind: AgentSessionKind;
  sessionId: string | null;
  conversationId: string | null;
  runId: string | null;
}): string {
  return [
    context.toolName ?? 'unknown-tool',
    context.provider ?? 'unknown-provider',
    context.modelName ?? 'unknown-model',
    context.sessionKind,
    context.sessionId ?? context.conversationId ?? context.runId ?? 'unknown-session'
  ].join('|');
}

export function normalizeAgentContext(value: Partial<NormalizedAgentContext> | null | undefined): NormalizedAgentContext | null {
  if (!value) {
    return null;
  }

  const confidence = clampConfidence(typeof value.confidence === 'number' ? value.confidence : 0);
  const evidence = Array.isArray(value.evidence)
    ? value.evidence.filter((item): item is AgentEvidence => Boolean(item && typeof item.field === 'string'))
    : [];

  return {
    toolName: value.toolName ?? null,
    provider: value.provider ?? null,
    sessionId: value.sessionId ?? null,
    conversationId: value.conversationId ?? null,
    runId: value.runId ?? null,
    modelName: value.modelName ?? null,
    userAgent: value.userAgent ?? null,
    workspaceHint: value.workspaceHint ?? null,
    operationType: value.operationType ?? 'unknown',
    confidence,
    evidence,
    adapterName: value.adapterName ?? 'unknown',
    matchSource: value.matchSource ?? 'heuristic',
    sessionKind: value.sessionKind ?? 'unknown',
    host: value.host ?? null,
    sessionSignature: value.sessionSignature ?? buildSessionSignature({
      toolName: value.toolName ?? null,
      provider: value.provider ?? null,
      modelName: value.modelName ?? null,
      sessionKind: value.sessionKind ?? 'unknown',
      sessionId: value.sessionId ?? null,
      conversationId: value.conversationId ?? null,
      runId: value.runId ?? null
    }),
    detectedAtIso: value.detectedAtIso ?? new Date().toISOString()
  };
}
