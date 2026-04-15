import * as fs from 'fs/promises';
import * as http from 'http';
import * as https from 'https';
import * as path from 'path';
import * as vscode from 'vscode';
import simpleGit from 'simple-git';
import { normalizeAST, ProvenanceRecord } from '../provenance';
import {
  ExplanationResult,
  LineageUpdateResult,
  LoadedProvenancePayload,
  ProvenanceIngestResult,
  ProvenanceSearchFilters,
  ProvenanceSearchResultItem,
  ProvenanceStorageService
} from './StorageService';

const LOCAL_STORAGE_RELATIVE_PATH = path.join('.vscode', 'ai-provenance', 'records.json');
const GLOBAL_STATE_KEY = 'aiInsertionDetector.localStorage.records.v1';
const DEFAULT_OLLAMA_URL = 'http://127.0.0.1:11434/api/generate';
const DEFAULT_OLLAMA_MODEL = 'qwen2.5-coder:7b';
const DEFAULT_OLLAMA_TIMEOUT_MS = 15_000;
const MAX_SNIPPET_LENGTH = 700;

type LineageRelationshipType =
  | 'INITIAL'
  | 'EXTENDED'
  | 'REFACTORED'
  | 'MOVED'
  | 'DELETED'
  | 'UNKNOWN';

type LocalLineage = {
  parentUuid: string | null;
  relationshipType: LineageRelationshipType;
  similarity: number | null;
  commitHash: string | null;
  updatedAtIso: string;
};

type LocalRecordEntry = {
  uuid: string;
  record: ProvenanceRecord;
  searchText: string;
  storedAtIso: string;
  updatedAtIso: string;
  lineage: LocalLineage;
};

type LocalStoreDocument = {
  schemaVersion: 1;
  records: LocalRecordEntry[];
  updatedAtIso: string;
};

type OllamaConfig = {
  provider: 'templated' | 'ollama';
  url: string;
  model: string;
  timeoutMs: number;
};

type HttpResponse = {
  statusCode: number;
  body: string;
};

export class LocalStorageService implements ProvenanceStorageService {
  public readonly mode = 'local' as const;

  public constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly log: (message: string) => void
  ) {}

  public async initialize(resource?: vscode.Uri): Promise<void> {
    await this.ensureStore(resource);
  }

  public async shutdown(): Promise<void> {
    return;
  }

  public dispose(): void {
    void this.shutdown();
  }

  public async handleConfigurationChanged(resource?: vscode.Uri): Promise<void> {
    await this.ensureStore(resource);
  }

  public async authenticate(): Promise<void> {
    await vscode.window.showWarningMessage(
      'Backend authentication is disabled in Local Mode. Use AI Provenance: Switch to Backend Mode to enable team features.'
    );
  }

  public async ingest(record: ProvenanceRecord, resource?: vscode.Uri): Promise<ProvenanceIngestResult> {
    const store = await this.readStore(resource);
    const nowIso = new Date().toISOString();
    const uuid = record.uuid.trim().toLowerCase();

    if (!uuid) {
      throw new Error('Cannot store provenance record without a UUID.');
    }

    const normalizedRecord = cloneRecord(record);
    const existingIndex = store.records.findIndex((entry) => entry.uuid === uuid);

    if (existingIndex >= 0) {
      const existing = store.records[existingIndex];
      store.records[existingIndex] = {
        ...existing,
        record: normalizedRecord,
        searchText: buildSearchText(normalizedRecord),
        updatedAtIso: nowIso
      };
    } else {
      const inferredLineage = inferLineageFromPrevious(store.records, normalizedRecord);

      store.records.push({
        uuid,
        record: normalizedRecord,
        searchText: buildSearchText(normalizedRecord),
        storedAtIso: nowIso,
        updatedAtIso: nowIso,
        lineage: {
          parentUuid: inferredLineage.parentUuid,
          relationshipType: inferredLineage.relationshipType,
          similarity: inferredLineage.similarity,
          commitHash: null,
          updatedAtIso: nowIso
        }
      });
    }

    store.updatedAtIso = nowIso;
    await this.writeStore(resource, store);

    return {
      uuid,
      transport: 'local-json',
      mode: this.mode,
      message: 'Stored provenance locally for offline use.',
      warnings: this.getModeWarnings()
    };
  }

  public async getProvenanceByUuid(
    uuid: string,
    resource?: vscode.Uri
  ): Promise<LoadedProvenancePayload> {
    const normalizedUuid = uuid.trim().toLowerCase();
    const store = await this.readStore(resource);

    const entry = store.records.find((item) => item.uuid === normalizedUuid);
    if (!entry) {
      throw new Error('No local provenance record found for UUID ' + uuid + '.');
    }

    const record = toPlainRecord(entry.record);
    const evolutionChain = buildEvolutionChain(store.records, entry.record.file.path);

    record.evolutionChain = evolutionChain;
    record.localLineage = entry.lineage;
    record.storageMode = 'local';

    const explanationResult = await this.getExplanation(normalizedUuid, record, resource);

    return {
      uuid: normalizedUuid,
      record,
      explanation: explanationResult.explanation,
      explanationError: explanationResult.explanationError,
      sourceLabel: this.getSourceLabel(resource),
      fetchedAtIso: new Date().toISOString(),
      mode: this.mode,
      warnings: this.getModeWarnings()
    };
  }

  public async getExplanation(
    _uuid: string,
    record: Record<string, unknown>,
    resource?: vscode.Uri
  ): Promise<ExplanationResult> {
    const ollamaConfig = this.getOllamaConfig(resource);

    if (ollamaConfig.provider === 'ollama') {
      try {
        const explanation = await this.requestOllamaExplanation(ollamaConfig, record);
        if (explanation && explanation.trim().length > 0) {
          return {
            explanation,
            explanationError: null
          };
        }
      } catch (error: unknown) {
        this.log('Local Ollama explanation failed. Falling back to template: ' + toErrorMessage(error));
      }
    }

    return {
      explanation: buildTemplatedExplanation(record),
      explanationError: null
    };
  }

  public async search(
    filters: ProvenanceSearchFilters,
    resource?: vscode.Uri
  ): Promise<ProvenanceSearchResultItem[]> {
    const store = await this.readStore(resource);
    const terms = splitTerms(filters.keywords);
    const normalizedModelFilter = filters.model.trim().toLowerCase();
    const dateFromEpoch = parseDateToEpoch(filters.dateFrom);
    const dateToEpoch = parseDateToEpoch(filters.dateTo);
    const normalizedCurrentFilePath = normalizePath(filters.currentFilePath ?? '');

    const results: ProvenanceSearchResultItem[] = [];

    for (const entry of store.records) {
      const recordTimestamp = parseDateToEpoch(entry.record.timestampIso);
      if (dateFromEpoch !== null && (recordTimestamp === null || recordTimestamp < dateFromEpoch)) {
        continue;
      }

      if (dateToEpoch !== null && (recordTimestamp === null || recordTimestamp > dateToEpoch)) {
        continue;
      }

      const modelName = normalizeModelName(entry.record.prompt.modelName);
      if (normalizedModelFilter.length > 0 && !modelName.toLowerCase().includes(normalizedModelFilter)) {
        continue;
      }

      if (filters.currentFileOnly && normalizedCurrentFilePath.length > 0) {
        const recordFilePath = normalizePath(entry.record.file.path);
        if (recordFilePath !== normalizedCurrentFilePath) {
          continue;
        }
      }

      const haystack = entry.searchText;
      const score = scoreByTerms(haystack, terms);
      if (terms.length > 0 && score <= 0) {
        continue;
      }

      results.push({
        uuid: entry.uuid,
        score: terms.length > 0 ? Number(score.toFixed(4)) : null,
        model: modelName.length > 0 ? modelName : null,
        timestampIso: sanitizeNullableString(entry.record.timestampIso),
        filePath: sanitizeNullableString(entry.record.file.path),
        snippet: extractSnippet(entry.record),
        mode: this.mode
      });
    }

    results.sort((left, right) => {
      const leftScore = typeof left.score === 'number' ? left.score : Number.NEGATIVE_INFINITY;
      const rightScore = typeof right.score === 'number' ? right.score : Number.NEGATIVE_INFINITY;

      if (leftScore !== rightScore) {
        return rightScore - leftScore;
      }

      const leftTime = parseDateToEpoch(left.timestampIso ?? '');
      const rightTime = parseDateToEpoch(right.timestampIso ?? '');
      const leftEpoch = leftTime ?? Number.NEGATIVE_INFINITY;
      const rightEpoch = rightTime ?? Number.NEGATIVE_INFINITY;

      if (leftEpoch !== rightEpoch) {
        return rightEpoch - leftEpoch;
      }

      return left.uuid.localeCompare(right.uuid);
    });

    return results;
  }

  public async updateLineageFromLatestCommit(resource?: vscode.Uri): Promise<LineageUpdateResult> {
    const store = await this.readStore(resource);
    const workspaceFolder = this.resolveWorkspaceFolder(resource);

    if (!workspaceFolder) {
      let recordsUpdated = 0;
      const grouped = groupByFile(store.records);
      const nowIso = new Date().toISOString();

      for (const entries of grouped.values()) {
        sortEntriesByTime(entries);
        for (let index = 1; index < entries.length; index += 1) {
          const previous = entries[index - 1];
          const current = entries[index];
          const lineage = deriveLineage(previous.record, current.record);

          const before = JSON.stringify(current.lineage);
          current.lineage = {
            parentUuid: previous.uuid,
            relationshipType: lineage.relationshipType,
            similarity: lineage.similarity,
            commitHash: current.lineage.commitHash,
            updatedAtIso: nowIso
          };

          if (before !== JSON.stringify(current.lineage)) {
            recordsUpdated += 1;
          }
        }
      }

      if (recordsUpdated > 0) {
        store.updatedAtIso = nowIso;
        await this.writeStore(resource, store);
      }

      return {
        mode: this.mode,
        commitHash: 'n/a',
        parentCommitHash: null,
        filesChanged: grouped.size,
        recordsUpdated,
        message: 'Updated local lineage without git metadata (workspace folder not available).'
      };
    }

    const git = simpleGit(workspaceFolder.uri.fsPath);
    let commitHash = 'n/a';
    let parentCommitHash: string | null = null;

    try {
      commitHash = (await git.revparse(['HEAD'])).trim();
    } catch {
      return {
        mode: this.mode,
        commitHash: 'n/a',
        parentCommitHash: null,
        filesChanged: 0,
        recordsUpdated: 0,
        message: 'No git commits found for this workspace yet.'
      };
    }

    try {
      parentCommitHash = (await git.revparse(['HEAD~1'])).trim();
      if (parentCommitHash.length === 0) {
        parentCommitHash = null;
      }
    } catch {
      parentCommitHash = null;
    }

    const diffOutput = parentCommitHash
      ? await git.diff(['--name-only', parentCommitHash + '..' + commitHash])
      : await git.show(['--name-only', '--pretty=format:', commitHash]);

    const changedFiles = parseChangedFileList(diffOutput)
      .map((relativePath) => normalizePath(path.join(workspaceFolder.uri.fsPath, relativePath)))
      .filter((value) => value.length > 0);

    const changedSet = new Set(changedFiles);
    if (changedSet.size === 0) {
      return {
        mode: this.mode,
        commitHash,
        parentCommitHash,
        filesChanged: 0,
        recordsUpdated: 0,
        message: 'No changed files detected in the selected commit window.'
      };
    }

    const grouped = groupByFile(
      store.records.filter((entry) => changedSet.has(normalizePath(entry.record.file.path)))
    );

    const nowIso = new Date().toISOString();
    let recordsUpdated = 0;

    for (const entries of grouped.values()) {
      sortEntriesByTime(entries);
      for (let index = 1; index < entries.length; index += 1) {
        const previous = entries[index - 1];
        const current = entries[index];
        const lineage = deriveLineage(previous.record, current.record);

        const before = JSON.stringify(current.lineage);
        current.lineage = {
          parentUuid: previous.uuid,
          relationshipType: lineage.relationshipType,
          similarity: lineage.similarity,
          commitHash,
          updatedAtIso: nowIso
        };

        if (before !== JSON.stringify(current.lineage)) {
          recordsUpdated += 1;
        }
      }
    }

    if (recordsUpdated > 0) {
      store.updatedAtIso = nowIso;
      await this.writeStore(resource, store);
    }

    return {
      mode: this.mode,
      commitHash,
      parentCommitHash,
      filesChanged: changedSet.size,
      recordsUpdated,
      message:
        'Local lineage refresh complete for commit ' +
        commitHash +
        ' (' +
        String(recordsUpdated) +
        ' records updated).'
    };
  }

  public getModeWarnings(): string[] {
    return [
      'Team sharing requires backend mode.',
      'Neo4j lineage graph and vector similarity search are available only in backend mode.'
    ];
  }

  private async requestOllamaExplanation(
    config: OllamaConfig,
    record: Record<string, unknown>
  ): Promise<string | null> {
    const insertedCode = getAtPath(record, ['insertion', 'extractedInsertedCodeBlock']);
    const modelName = getAtPath(record, ['prompt', 'modelName']);
    const filePath = getAtPath(record, ['file', 'path']);
    const language = getAtPath(record, ['file', 'languageId']);

    const prompt = [
      'You are summarizing AI code provenance for a developer.',
      'Explain what the code insertion does, potential risks, and why it may have been generated.',
      'Keep it concise and practical.',
      '',
      'File: ' + stringify(filePath),
      'Language: ' + stringify(language),
      'Model: ' + stringify(modelName),
      '',
      'Inserted code:',
      String(insertedCode ?? '').slice(0, 6_000)
    ].join('\n');

    const response = await requestJson(
      'POST',
      config.url,
      {
        'Content-Type': 'application/json',
        Accept: 'application/json'
      },
      {
        model: config.model,
        prompt,
        stream: false,
        options: {
          temperature: 0.2
        }
      },
      config.timeoutMs
    );

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw new Error('Ollama returned status ' + String(response.statusCode) + '.');
    }

    const parsed = safeJsonParse(response.body);
    if (!parsed) {
      return null;
    }

    const text =
      toNonEmptyString(parsed.response) ||
      toNonEmptyString(parsed.output) ||
      toNonEmptyString(parsed.message) ||
      null;

    return text;
  }

  private getSourceLabel(resource?: vscode.Uri): string {
    const storageFilePath = this.resolveStorageFilePath(resource);
    if (storageFilePath) {
      return storageFilePath;
    }

    return 'VS Code globalState';
  }

  private getOllamaConfig(resource?: vscode.Uri): OllamaConfig {
    const config = vscode.workspace.getConfiguration('aiInsertionDetector', resource);

    const providerRaw =
      config.get<string>('local.explanation.provider', 'templated')?.trim().toLowerCase() ??
      'templated';

    return {
      provider: providerRaw === 'ollama' ? 'ollama' : 'templated',
      url: config.get<string>('local.ollama.url', DEFAULT_OLLAMA_URL) ?? DEFAULT_OLLAMA_URL,
      model:
        config.get<string>('local.ollama.model', DEFAULT_OLLAMA_MODEL) ??
        DEFAULT_OLLAMA_MODEL,
      timeoutMs: Math.max(
        2_000,
        config.get<number>('local.ollama.timeoutMs', DEFAULT_OLLAMA_TIMEOUT_MS) ??
          DEFAULT_OLLAMA_TIMEOUT_MS
      )
    };
  }

  private async ensureStore(resource?: vscode.Uri): Promise<void> {
    const storageFilePath = this.resolveStorageFilePath(resource);
    if (!storageFilePath) {
      const existing = this.context.globalState.get<LocalStoreDocument>(GLOBAL_STATE_KEY);
      if (!existing) {
        await this.context.globalState.update(GLOBAL_STATE_KEY, createEmptyStore());
      }
      return;
    }

    await fs.mkdir(path.dirname(storageFilePath), { recursive: true });

    try {
      await fs.stat(storageFilePath);
    } catch {
      await fs.writeFile(storageFilePath, JSON.stringify(createEmptyStore(), null, 2), 'utf8');
    }
  }

  private async readStore(resource?: vscode.Uri): Promise<LocalStoreDocument> {
    const storageFilePath = this.resolveStorageFilePath(resource);

    if (!storageFilePath) {
      const existing = this.context.globalState.get<LocalStoreDocument>(GLOBAL_STATE_KEY);
      return sanitizeStoreDocument(existing);
    }

    await this.ensureStore(resource);

    try {
      const raw = await fs.readFile(storageFilePath, 'utf8');
      const parsed = safeJsonParse(raw);
      return sanitizeStoreDocument(parsed);
    } catch (error: unknown) {
      this.log('Failed to read local provenance store, using empty store: ' + toErrorMessage(error));
      return createEmptyStore();
    }
  }

  private async writeStore(resource: vscode.Uri | undefined, store: LocalStoreDocument): Promise<void> {
    const storageFilePath = this.resolveStorageFilePath(resource);
    const normalizedStore = sanitizeStoreDocument(store);

    if (!storageFilePath) {
      await this.context.globalState.update(GLOBAL_STATE_KEY, normalizedStore);
      return;
    }

    await fs.mkdir(path.dirname(storageFilePath), { recursive: true });
    await fs.writeFile(storageFilePath, JSON.stringify(normalizedStore, null, 2), 'utf8');
  }

  private resolveStorageFilePath(resource?: vscode.Uri): string | undefined {
    const workspaceFolder = this.resolveWorkspaceFolder(resource);
    if (!workspaceFolder) {
      return undefined;
    }

    return path.join(workspaceFolder.uri.fsPath, LOCAL_STORAGE_RELATIVE_PATH);
  }

  private resolveWorkspaceFolder(resource?: vscode.Uri): vscode.WorkspaceFolder | undefined {
    if (resource) {
      const fromResource = vscode.workspace.getWorkspaceFolder(resource);
      if (fromResource) {
        return fromResource;
      }
    }

    return vscode.workspace.workspaceFolders?.[0];
  }
}

function cloneRecord(record: ProvenanceRecord): ProvenanceRecord {
  return JSON.parse(JSON.stringify(record)) as ProvenanceRecord;
}

function toPlainRecord(record: ProvenanceRecord): Record<string, unknown> {
  return JSON.parse(JSON.stringify(record)) as Record<string, unknown>;
}

function createEmptyStore(): LocalStoreDocument {
  return {
    schemaVersion: 1,
    records: [],
    updatedAtIso: new Date().toISOString()
  };
}

function sanitizeStoreDocument(value: unknown): LocalStoreDocument {
  if (!isRecord(value)) {
    return createEmptyStore();
  }

  const recordsValue = Array.isArray(value.records) ? value.records : [];
  const records: LocalRecordEntry[] = [];

  for (const entry of recordsValue) {
    if (!isRecord(entry)) {
      continue;
    }

    const uuid = toNonEmptyString(entry.uuid)?.toLowerCase();
    if (!uuid) {
      continue;
    }

    const recordCandidate = entry.record;
    if (!isRecord(recordCandidate)) {
      continue;
    }

    const normalizedRecord = recordCandidate as unknown as ProvenanceRecord;
    const lineage = sanitizeLineage(entry.lineage);

    records.push({
      uuid,
      record: normalizedRecord,
      searchText:
        toNonEmptyString(entry.searchText) ?? buildSearchText(normalizedRecord),
      storedAtIso: toNonEmptyString(entry.storedAtIso) ?? new Date().toISOString(),
      updatedAtIso: toNonEmptyString(entry.updatedAtIso) ?? new Date().toISOString(),
      lineage
    });
  }

  return {
    schemaVersion: 1,
    records,
    updatedAtIso: toNonEmptyString(value.updatedAtIso) ?? new Date().toISOString()
  };
}

function sanitizeLineage(value: unknown): LocalLineage {
  if (!isRecord(value)) {
    return {
      parentUuid: null,
      relationshipType: 'INITIAL',
      similarity: null,
      commitHash: null,
      updatedAtIso: new Date().toISOString()
    };
  }

  const relationshipType = toNonEmptyString(value.relationshipType);

  return {
    parentUuid: toNonEmptyString(value.parentUuid) ?? null,
    relationshipType: isValidLineageRelationship(relationshipType) ? relationshipType : 'UNKNOWN',
    similarity: toFiniteNumber(value.similarity),
    commitHash: toNonEmptyString(value.commitHash) ?? null,
    updatedAtIso: toNonEmptyString(value.updatedAtIso) ?? new Date().toISOString()
  };
}

function isValidLineageRelationship(value?: string): value is LineageRelationshipType {
  return (
    value === 'INITIAL' ||
    value === 'EXTENDED' ||
    value === 'REFACTORED' ||
    value === 'MOVED' ||
    value === 'DELETED' ||
    value === 'UNKNOWN'
  );
}

function inferLineageFromPrevious(
  records: readonly LocalRecordEntry[],
  record: ProvenanceRecord
): {
  parentUuid: string | null;
  relationshipType: LineageRelationshipType;
  similarity: number | null;
} {
  const filePath = normalizePath(record.file.path);
  const candidates = records.filter(
    (entry) => normalizePath(entry.record.file.path) === filePath && entry.uuid !== record.uuid
  );

  if (candidates.length === 0) {
    return {
      parentUuid: null,
      relationshipType: 'INITIAL',
      similarity: null
    };
  }

  candidates.sort((left, right) => {
    const leftEpoch = parseDateToEpoch(left.record.timestampIso) ?? 0;
    const rightEpoch = parseDateToEpoch(right.record.timestampIso) ?? 0;
    return rightEpoch - leftEpoch;
  });

  const previous = candidates[0];
  const lineage = deriveLineage(previous.record, record);

  return {
    parentUuid: previous.uuid,
    relationshipType: lineage.relationshipType,
    similarity: lineage.similarity
  };
}

function deriveLineage(
  previous: ProvenanceRecord,
  next: ProvenanceRecord
): {
  relationshipType: LineageRelationshipType;
  similarity: number;
} {
  const previousPath = normalizePath(previous.file.path);
  const nextPath = normalizePath(next.file.path);

  const previousTokens = extractAstTokens(previous);
  const nextTokens = extractAstTokens(next);

  const similarity = computeJaccard(previousTokens, nextTokens);

  if (previousPath !== nextPath) {
    return {
      relationshipType: 'MOVED',
      similarity
    };
  }

  const growthRatio = nextTokens.length / Math.max(1, previousTokens.length);

  if (similarity >= 0.7 && growthRatio >= 1.1) {
    return {
      relationshipType: 'EXTENDED',
      similarity
    };
  }

  if (similarity >= 0.35) {
    return {
      relationshipType: 'REFACTORED',
      similarity
    };
  }

  return {
    relationshipType: 'UNKNOWN',
    similarity
  };
}

function extractAstTokens(record: ProvenanceRecord): string[] {
  const fromSnapshot = Array.isArray(record.astSnapshot.normalizedNodeTypes)
    ? record.astSnapshot.normalizedNodeTypes.filter((value): value is string => typeof value === 'string')
    : [];

  if (fromSnapshot.length > 0) {
    return fromSnapshot;
  }

  return normalizeAST(record.insertion.extractedInsertedCodeBlock, record.file.languageId);
}

function computeJaccard(leftTokens: readonly string[], rightTokens: readonly string[]): number {
  const leftSet = new Set(leftTokens.filter((token) => token.length > 0));
  const rightSet = new Set(rightTokens.filter((token) => token.length > 0));

  if (leftSet.size === 0 || rightSet.size === 0) {
    return 0;
  }

  let overlap = 0;
  for (const token of leftSet) {
    if (rightSet.has(token)) {
      overlap += 1;
    }
  }

  const union = leftSet.size + rightSet.size - overlap;
  return union <= 0 ? 0 : overlap / union;
}

function buildEvolutionChain(
  records: readonly LocalRecordEntry[],
  filePath: string
): Array<Record<string, unknown>> {
  const normalizedFilePath = normalizePath(filePath);

  const fileEntries = records.filter(
    (entry) => normalizePath(entry.record.file.path) === normalizedFilePath
  );

  sortEntriesByTime(fileEntries);

  return fileEntries.map((entry) => ({
    versionId: entry.uuid,
    parentVersionId: entry.lineage.parentUuid,
    relationshipType: entry.lineage.relationshipType,
    commitHash: entry.lineage.commitHash,
    similarity: entry.lineage.similarity,
    timestampIso: entry.record.timestampIso,
    filePath: entry.record.file.path,
    code: entry.record.insertion.extractedInsertedCodeBlock
  }));
}

function sortEntriesByTime(entries: LocalRecordEntry[]): void {
  entries.sort((left, right) => {
    const leftEpoch = parseDateToEpoch(left.record.timestampIso) ?? 0;
    const rightEpoch = parseDateToEpoch(right.record.timestampIso) ?? 0;
    return leftEpoch - rightEpoch;
  });
}

function groupByFile(records: readonly LocalRecordEntry[]): Map<string, LocalRecordEntry[]> {
  const grouped = new Map<string, LocalRecordEntry[]>();

  for (const entry of records) {
    const filePath = normalizePath(entry.record.file.path);
    if (!grouped.has(filePath)) {
      grouped.set(filePath, []);
    }

    grouped.get(filePath)?.push(entry);
  }

  return grouped;
}

function parseChangedFileList(diffOutput: string): string[] {
  return diffOutput
    .split(/\r\n|\r|\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function buildSearchText(record: ProvenanceRecord): string {
  const parts: string[] = [];

  parts.push(record.uuid);
  parts.push(record.file.path);
  parts.push(record.file.languageId);
  parts.push(record.repository.gitBranch ?? '');
  parts.push(stringify(record.prompt.modelName));
  parts.push(stringify(record.prompt.fullMessages));
  parts.push(record.insertion.extractedInsertedCodeBlock);
  parts.push(record.insertion.surroundingContext.before);
  parts.push(record.insertion.surroundingContext.after);
  parts.push(stringify(record.contextSnapshot));

  return parts.join('\n').toLowerCase();
}

function splitTerms(value: string): string[] {
  return value
    .toLowerCase()
    .split(/\s+/)
    .map((term) => term.trim())
    .filter((term) => term.length > 0);
}

function scoreByTerms(haystack: string, terms: readonly string[]): number {
  if (terms.length === 0) {
    return 0;
  }

  let matchedTerms = 0;
  let rawMentions = 0;

  for (const term of terms) {
    const escaped = escapeRegExp(term);
    const regex = new RegExp(escaped, 'g');
    const mentions = (haystack.match(regex) ?? []).length;

    if (mentions > 0) {
      matchedTerms += 1;
      rawMentions += mentions;
    }
  }

  if (matchedTerms === 0) {
    return 0;
  }

  const coverage = matchedTerms / terms.length;
  const density = Math.min(1, rawMentions / Math.max(1, terms.length * 3));
  return coverage * 0.7 + density * 0.3;
}

function extractSnippet(record: ProvenanceRecord): string {
  const inserted = record.insertion.extractedInsertedCodeBlock.trim();
  if (!inserted) {
    return '(no snippet)';
  }

  return inserted.length > MAX_SNIPPET_LENGTH
    ? inserted.slice(0, MAX_SNIPPET_LENGTH) + ' ...'
    : inserted;
}

function buildTemplatedExplanation(record: Record<string, unknown>): string {
  const filePath = stringify(getAtPath(record, ['file', 'path']));
  const language = stringify(getAtPath(record, ['file', 'languageId']));
  const model = stringify(getAtPath(record, ['prompt', 'modelName']));
  const netAddedLines = stringify(getAtPath(record, ['insertion', 'netAddedLines']));
  const branch = stringify(getAtPath(record, ['repository', 'gitBranch']));
  const promptStatus = stringify(getAtPath(record, ['promptStatus']));
  const astNodeCount = stringify(getAtPath(record, ['astSnapshot', 'nodeCount']));
  const code = stringify(getAtPath(record, ['insertion', 'extractedInsertedCodeBlock']));

  const bulletLines = [
    'Local explanation (offline fallback):',
    '- File: ' + filePath,
    '- Language: ' + language,
    '- Model: ' + model,
    '- Prompt captured: ' + promptStatus,
    '- Net added lines: ' + netAddedLines,
    '- AST node count: ' + astNodeCount,
    '- Git branch: ' + branch,
    '',
    'Likely intent: introduce or modify behavior in this file based on the captured prompt/context.',
    '',
    'Inserted code excerpt:',
    code.slice(0, 1000)
  ];

  return bulletLines.join('\n');
}

async function requestJson(
  method: 'GET' | 'POST',
  endpointUrl: string,
  headers: Record<string, string>,
  payload: unknown,
  timeoutMs: number
): Promise<HttpResponse> {
  const target = new URL(endpointUrl);
  const body = JSON.stringify(payload);
  const transport = target.protocol === 'https:' ? https : http;

  const requestHeaders: Record<string, string> = {
    ...headers,
    'Content-Length': String(Buffer.byteLength(body))
  };

  return await new Promise<HttpResponse>((resolve, reject) => {
    const request = transport.request(
      {
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port,
        method,
        path: target.pathname + target.search,
        headers: requestHeaders,
        timeout: timeoutMs
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
      request.destroy(new Error('Request timed out after ' + String(timeoutMs) + 'ms.'));
    });

    request.on('error', (error: Error) => {
      reject(error);
    });

    request.write(body);
    request.end();
  });
}

function normalizeModelName(value: unknown): string {
  if (typeof value === 'string') {
    return value.trim();
  }

  if (value && typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }

  return '';
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function parseDateToEpoch(value: string | null | undefined): number | null {
  const input = (value ?? '').trim();
  if (input.length === 0) {
    return null;
  }

  const parsed = Date.parse(input);
  return Number.isNaN(parsed) ? null : parsed;
}

function normalizePath(value: string): string {
  return path.normalize((value ?? '').trim()).toLowerCase();
}

function sanitizeNullableString(value: string): string | null {
  const trimmed = (value ?? '').trim();
  return trimmed.length > 0 ? trimmed : null;
}

function stringify(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }

  if (value === null || typeof value === 'undefined') {
    return 'n/a';
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function getAtPath(source: unknown, pathSegments: string[]): unknown {
  let cursor: unknown = source;

  for (const segment of pathSegments) {
    if (!isRecord(cursor) || !(segment in cursor)) {
      return undefined;
    }

    cursor = cursor[segment];
  }

  return cursor;
}

function safeJsonParse(value: string): Record<string, unknown> | undefined {
  if (!value) {
    return undefined;
  }

  try {
    const parsed = JSON.parse(value) as unknown;
    return isRecord(parsed) ? parsed : undefined;
  } catch {
    return undefined;
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

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
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
