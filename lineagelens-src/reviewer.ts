import * as http from 'http';
import * as https from 'https';
import * as vscode from 'vscode';
import { assessProvenanceRisk } from './insights';
import { getStoragePathForUri } from './storagePath';
import type { ProvenanceRecord } from './provenance';
import type { ProvenanceStorageService } from './storage/StorageService';

const CONFIG_SECTION = 'aiInsertionDetector';
const REVIEWER_API_KEY_SECRET = 'aiInsertionDetector.reviewer.apiKey';
const DEFAULT_REVIEWER_PROVIDER = 'heuristic';
const DEFAULT_REVIEWER_API_URL = 'https://api.openai.com/v1/chat/completions';
const DEFAULT_REVIEWER_MODEL = 'gpt-4o-mini';
const DEFAULT_REVIEWER_TIMEOUT_MS = 30_000;
const MAX_REVIEW_RECORDS = 12;

type ReviewerProvider = 'heuristic' | 'openai-compatible';

type ReviewerConfig = {
  provider: ReviewerProvider;
  apiUrl: string;
  model: string;
  timeoutMs: number;
};

type JsonResponse = {
  statusCode: number;
  body: string;
};

export type CodeReviewFinding = {
  severity: 'low' | 'medium' | 'high' | 'critical';
  title: string;
  detail: string;
  suggestion: string;
  provenanceUuids: string[];
};

export type CodeReviewResult = {
  filePath: string;
  generatedAtIso: string;
  summary: string;
  findings: CodeReviewFinding[];
  reviewedRecordCount: number;
  source: 'heuristic' | 'llm';
  model: string;
  note: string | null;
};

export class ProvenanceReviewerService {
  public constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly log: (message: string) => void
  ) {}

  public async configureApiKey(): Promise<boolean> {
    const apiKey = await vscode.window.showInputBox({
      title: 'AI Provenance Reviewer API Key',
      prompt: 'Paste the API key for the reviewer agent (OpenAI-compatible bearer token).',
      ignoreFocusOut: true,
      password: true,
      validateInput: (value) => {
        return value.trim().length >= 16 ? undefined : 'Provide a valid API key.';
      }
    });

    if (!apiKey) {
      return false;
    }

    await this.context.secrets.store(REVIEWER_API_KEY_SECRET, apiKey.trim());
    vscode.window.setStatusBarMessage('Reviewer API key stored.', 4000);
    return true;
  }

  public async reviewCurrentFile(
    storageService: ProvenanceStorageService,
    resource?: vscode.Uri
  ): Promise<CodeReviewResult> {
    const targetResource = resource ?? vscode.window.activeTextEditor?.document.uri;
    if (!targetResource || targetResource.scheme !== 'file') {
      throw new Error('Open a file-backed editor before running the AI code reviewer.');
    }

    const filePath = getStoragePathForUri(targetResource);
    const searchResults = await storageService.search(
      {
        keywords: '',
        model: '',
        dateFrom: '',
        dateTo: '',
        currentFileOnly: true,
        currentFilePath: filePath,
        limit: MAX_REVIEW_RECORDS
      },
      targetResource
    );

    if (searchResults.length === 0) {
      return {
        filePath,
        generatedAtIso: new Date().toISOString(),
        summary: 'No provenance records were found for the current file.',
        findings: [],
        reviewedRecordCount: 0,
        source: 'heuristic',
        model: 'heuristic-reviewer-v1',
        note: 'Review skipped because there are no stored AI provenance records for this file.'
      };
    }

    const loadedPayloads = await Promise.all(
      searchResults.slice(0, MAX_REVIEW_RECORDS).map((result) =>
        storageService.getProvenanceByUuid(result.uuid, targetResource)
      )
    );

    const records = loadedPayloads
      .map((payload) => payload.record as unknown as ProvenanceRecord)
      .filter((record) => Boolean(record?.uuid));

    const config = this.getReviewerConfig(targetResource);
    if (config.provider === 'openai-compatible') {
      const apiKey = await this.ensureReviewerApiKey();
      if (apiKey) {
        try {
          return await this.requestLlmReview(records, filePath, config, apiKey);
        } catch (error: unknown) {
          this.log('Reviewer LLM path failed, falling back to heuristics: ' + toErrorMessage(error));
        }
      }
    }

    return this.buildHeuristicReview(records, filePath, config.provider === 'heuristic' ? null : 'LLM reviewer unavailable, using heuristic review.');
  }

  private async ensureReviewerApiKey(): Promise<string | undefined> {
    const existing = await this.context.secrets.get(REVIEWER_API_KEY_SECRET);
    if (existing && existing.trim().length > 0) {
      return existing.trim();
    }

    const configured = await this.configureApiKey();
    if (!configured) {
      return undefined;
    }

    return await this.context.secrets.get(REVIEWER_API_KEY_SECRET);
  }

  private getReviewerConfig(resource?: vscode.Uri): ReviewerConfig {
    const config = vscode.workspace.getConfiguration(CONFIG_SECTION, resource);
    const providerRaw =
      config.get<string>('reviewer.provider', DEFAULT_REVIEWER_PROVIDER)?.trim().toLowerCase() ??
      DEFAULT_REVIEWER_PROVIDER;

    return {
      provider: providerRaw === 'openai-compatible' ? 'openai-compatible' : 'heuristic',
      apiUrl:
        config.get<string>('reviewer.apiUrl', DEFAULT_REVIEWER_API_URL) ??
        DEFAULT_REVIEWER_API_URL,
      model:
        config.get<string>('reviewer.model', DEFAULT_REVIEWER_MODEL) ?? DEFAULT_REVIEWER_MODEL,
      timeoutMs: Math.max(
        5_000,
        config.get<number>('reviewer.timeoutMs', DEFAULT_REVIEWER_TIMEOUT_MS) ??
          DEFAULT_REVIEWER_TIMEOUT_MS
      )
    };
  }

  private async requestLlmReview(
    records: ProvenanceRecord[],
    filePath: string,
    config: ReviewerConfig,
    apiKey: string
  ): Promise<CodeReviewResult> {
    const compactContext = records.map((record) => {
      const risk = assessProvenanceRisk(record);
      return {
        uuid: record.uuid,
        filePath: record.file.path,
        timestampIso: record.timestampIso,
        promptStatus: record.promptStatus,
        model: normalizeModelName(record.prompt.modelName),
        correlationConfidence: record.metadata.correlationConfidence ?? null,
        risk,
        insertedCode: record.insertion.extractedInsertedCodeBlock.slice(0, 4000),
        surroundingContext: record.insertion.surroundingContext,
        promptMessages: truncateString(safeSerialize(record.prompt.fullMessages), 2500)
      };
    });

    const response = await requestJson(
      'POST',
      config.apiUrl,
      {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        Authorization: 'Bearer ' + apiKey
      },
      {
        model: config.model,
        temperature: 0.1,
        messages: [
          {
            role: 'system',
            content:
              'You are an AI code reviewer focused only on AI-generated code provenance. Return strict JSON with keys: summary, findings. findings must be an array of objects with severity, title, detail, suggestion, provenanceUuids.'
          },
          {
            role: 'user',
            content:
              'Review the AI-generated code for the current file. Only flag material risks and avoid generic advice.\n\n' +
              JSON.stringify(
                {
                  filePath,
                  records: compactContext
                },
                null,
                2
              )
          }
        ]
      },
      config.timeoutMs
    );

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw new Error('Reviewer API returned status ' + String(response.statusCode) + '.');
    }

    const content = extractChatCompletionText(response.body);
    if (!content) {
      throw new Error('Reviewer API returned no content.');
    }

    const parsed = tryParseJsonObject(content);
    if (!parsed || !Array.isArray(parsed.findings)) {
      throw new Error('Reviewer API response was not valid structured JSON.');
    }

    const findings = parsed.findings
      .filter((entry): entry is Record<string, unknown> => isRecord(entry))
      .map((entry) => ({
        severity: normalizeSeverity(entry.severity),
        title: toNonEmptyString(entry.title) ?? 'Untitled finding',
        detail: toNonEmptyString(entry.detail) ?? 'No detail provided.',
        suggestion: toNonEmptyString(entry.suggestion) ?? 'Review this generated block manually.',
        provenanceUuids: Array.isArray(entry.provenanceUuids)
          ? entry.provenanceUuids.map((value) => String(value))
          : []
      }))
      .slice(0, 12);

    return {
      filePath,
      generatedAtIso: new Date().toISOString(),
      summary:
        toNonEmptyString(parsed.summary) ??
        'The reviewer completed, but did not provide a summary.',
      findings,
      reviewedRecordCount: records.length,
      source: 'llm',
      model: config.model,
      note: null
    };
  }

  private buildHeuristicReview(
    records: ProvenanceRecord[],
    filePath: string,
    note: string | null
  ): CodeReviewResult {
    const findings: CodeReviewFinding[] = [];
    const sortedByRisk = [...records]
      .map((record) => ({
        record,
        risk: assessProvenanceRisk(record)
      }))
      .sort((left, right) => right.risk.score - left.risk.score);

    for (const entry of sortedByRisk.slice(0, 6)) {
      if (entry.risk.score < 35) {
        continue;
      }

      findings.push({
        severity: normalizeSeverity(entry.risk.level),
        title:
          entry.risk.level === 'critical'
            ? 'Critical AI-generated block requires manual review'
            : 'High-risk AI-generated block requires manual review',
        detail: entry.risk.reasons.join(' '),
        suggestion:
          entry.record.promptStatus === 'captured'
            ? 'Review the stored prompt, inserted code, and nearby context together before merging.'
            : 'Require a manual review because prompt evidence is missing for this generated block.',
        provenanceUuids: [entry.record.uuid]
      });
    }

    const missingPromptCount = records.filter((record) => record.promptStatus !== 'captured').length;
    if (missingPromptCount > 0) {
      findings.unshift({
        severity: missingPromptCount >= 3 ? 'high' : 'medium',
        title: 'Prompt evidence is incomplete for this file',
        detail:
          String(missingPromptCount) +
          ' provenance record(s) in the current file do not have captured prompt evidence.',
        suggestion: 'Treat these blocks as low-trust until prompt capture reliability improves.',
        provenanceUuids: records
          .filter((record) => record.promptStatus !== 'captured')
          .map((record) => record.uuid)
          .slice(0, 8)
      });
    }

    if (findings.length === 0) {
      findings.push({
        severity: 'low',
        title: 'No material risks detected by heuristic review',
        detail:
          'The stored provenance for the current file does not contain strong governance or security signals.',
        suggestion: 'A normal human code review should still check correctness and tests.',
        provenanceUuids: records.map((record) => record.uuid).slice(0, 8)
      });
    }

    const summary =
      findings[0]?.severity === 'critical' || findings[0]?.severity === 'high'
        ? 'The reviewer found high-priority issues in AI-generated code for the current file.'
        : 'The reviewer did not find high-priority issues in AI-generated code for the current file.';

    return {
      filePath,
      generatedAtIso: new Date().toISOString(),
      summary,
      findings: findings.slice(0, 12),
      reviewedRecordCount: records.length,
      source: 'heuristic',
      model: 'heuristic-reviewer-v1',
      note
    };
  }
}

async function requestJson(
  method: 'GET' | 'POST',
  endpointUrl: string,
  headers: Record<string, string>,
  payload: unknown,
  timeoutMs: number
): Promise<JsonResponse> {
  const target = new URL(endpointUrl);
  const body = JSON.stringify(payload);
  const transport = target.protocol === 'https:' ? https : http;

  return await new Promise<JsonResponse>((resolve, reject) => {
    const request = transport.request(
      {
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port,
        method,
        path: target.pathname + target.search,
        timeout: timeoutMs,
        headers: {
          ...headers,
          'Content-Length': String(Buffer.byteLength(body))
        }
      },
      (response) => {
        const chunks: Buffer[] = [];
        response.on('data', (chunk: Buffer | string) => {
          chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
        });
        response.on('end', () => {
          resolve({
            statusCode: response.statusCode ?? 0,
            body: Buffer.concat(chunks).toString('utf8')
          });
        });
      }
    );

    request.on('timeout', () => {
      request.destroy(new Error('Reviewer request timed out after ' + String(timeoutMs) + 'ms.'));
    });
    request.on('error', (error: Error) => {
      reject(error);
    });
    request.write(body);
    request.end();
  });
}

function extractChatCompletionText(rawBody: string): string | null {
  const parsed = tryParseJsonObject(rawBody);
  if (!parsed) {
    return null;
  }

  const choices = parsed.choices;
  if (!Array.isArray(choices) || choices.length === 0 || !isRecord(choices[0])) {
    return null;
  }

  const message = choices[0].message;
  if (!isRecord(message)) {
    return null;
  }

  const content = message.content;
  if (typeof content === 'string') {
    return content.trim() || null;
  }

  if (Array.isArray(content)) {
    const chunks = content
      .map((entry) => {
        if (typeof entry === 'string') {
          return entry;
        }

        if (isRecord(entry) && typeof entry.text === 'string') {
          return entry.text;
        }

        return '';
      })
      .filter((value) => value.trim().length > 0);

    return chunks.length > 0 ? chunks.join('\n') : null;
  }

  return null;
}

function tryParseJsonObject(value: string): Record<string, any> | null {
  try {
    const parsed = JSON.parse(value) as unknown;
    return isRecord(parsed) ? parsed : null;
  } catch {
    const start = value.indexOf('{');
    const end = value.lastIndexOf('}');
    if (start >= 0 && end > start) {
      try {
        const parsed = JSON.parse(value.slice(start, end + 1)) as unknown;
        return isRecord(parsed) ? parsed : null;
      } catch {
        return null;
      }
    }

    return null;
  }
}

function normalizeSeverity(value: unknown): 'low' | 'medium' | 'high' | 'critical' {
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === 'critical') {
    return 'critical';
  }

  if (normalized === 'high') {
    return 'high';
  }

  if (normalized === 'medium') {
    return 'medium';
  }

  return 'low';
}

function normalizeModelName(value: unknown): string {
  if (typeof value === 'string') {
    return value.trim();
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

function truncateString(value: string, maxChars: number): string {
  if (value.length <= maxChars) {
    return value;
  }

  return value.slice(0, maxChars) + '\n...<truncated>';
}

function safeSerialize(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function toNonEmptyString(value: unknown): string | undefined {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : undefined;
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }

  return undefined;
}

function isRecord(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null;
}

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }

  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
}
