import * as http from 'http';
import * as path from 'path';
import * as vscode from 'vscode';
import simpleGit from 'simple-git';
import { v4 as uuidv4 } from 'uuid';
import { correlateInsertionWithProxyRequest, PromptCorrelationResult } from './correlation';
import { LocalLlmProxyRuntime, startLocalLlmProxy } from './proxy';
import { ContextSnapshot, captureContextSnapshot } from './contextSnapshot';
import {
  ProvenanceEmbedding,
  ProvenanceEmbeddingBundle,
  ProvenanceRecord,
  normalizeAST
} from './provenance';
import { ProvenanceSidebarViewProvider, extractUuidFromText } from './provenanceSidebar';
import {
  ProvenanceSearchSidebarViewProvider,
  SearchSelection
} from './provenanceSearchSidebar';
import { BackendStorageService } from './storage/BackendStorageService';
import { LocalStorageService } from './storage/LocalStorageService';
import {
  ProvenanceMode,
  ProvenanceStorageService,
  getConfiguredMode
} from './storage/StorageService';

const CONFIG_SECTION = 'aiInsertionDetector';
const MODE_CONFIG_SECTION = 'aiCodeProvenance';
const DEFAULT_LINE_THRESHOLD = 4;
const DEFAULT_PROXY_PORT = 8787;
const DEFAULT_ENABLED = true;
const DEFAULT_LOCAL_PROXY_ENABLED = true;
const DEFAULT_LOCAL_PROXY_PORT = 8080;
const DEFAULT_CORRELATION_SIMILARITY_THRESHOLD = 0.7;
const DEFAULT_BACKEND_BASE_URL = 'http://127.0.0.1:8787';
const CONTEXT_TOKEN_WINDOW = 200;
const LOCAL_EMBEDDING_DIMENSIONS = 128;
const LOCAL_EMBEDDING_PROVIDER = 'local-hash';
const LOCAL_EMBEDDING_MODEL = 'deterministic-hash-v1';

type LineColumn = {
  line: number;
  column: number;
};

type DetectorConfig = {
  enabled: boolean;
  lineThreshold: number;
  proxyPort: number;
  localProxyEnabled: boolean;
  localProxyPort: number;
  correlationSimilarityThreshold: number;
};

type InternalInsertedChunk = {
  text: string;
  startOffset: number;
  endOffset: number;
  addedLines: number;
  removedLines: number;
};

type InsertedChunkPayload = {
  text: string;
  start: LineColumn;
  end: LineColumn;
  addedLines: number;
  removedLines: number;
};

type SurroundingContext = {
  before: string;
  after: string;
  tokenWindow: number;
};

type DetectionPayload = {
  id: string;
  timestampIso: string;
  filePath: string;
  fileUri: string;
  cursor: LineColumn;
  netAddedLines: number;
  insertedText: string;
  insertedChunks: InsertedChunkPayload[];
  surroundingContext: SurroundingContext;
  contextSnapshot: ContextSnapshot;
  provenance: PromptCorrelationResult;
  activeGitBranch: string | null;
  proxyPort: number;
};

const previousDocumentTexts = new Map<string, string>();
let outputChannel: vscode.OutputChannel | undefined;
let lastPayload: DetectionPayload | undefined;
let lastProvenanceRecord: ProvenanceRecord | undefined;
let localLlmProxy: LocalLlmProxyRuntime | undefined;
let extensionContextRef: vscode.ExtensionContext | undefined;
let activeStorageService: ProvenanceStorageService | undefined;

export function activate(context: vscode.ExtensionContext): void {
  extensionContextRef = context;
  outputChannel = vscode.window.createOutputChannel('AI Insertion Detector');
  context.subscriptions.push(outputChannel);

  const getStorageService = (): ProvenanceStorageService => {
    if (!activeStorageService) {
      throw new Error('Storage service has not been initialized yet.');
    }

    return activeStorageService;
  };

  const provenanceSidebarProvider = new ProvenanceSidebarViewProvider(
    context.extensionUri,
    log,
    getStorageService
  );
  const provenanceSearchSidebarProvider = new ProvenanceSearchSidebarViewProvider(
    context.extensionUri,
    log,
    getStorageService,
    async (selection: SearchSelection) => {
      await openFileForSearchSelection(selection);
      await provenanceSidebarProvider.showProvenance(selection.uuid);
    }
  );

  context.subscriptions.push(
    provenanceSidebarProvider,
    provenanceSearchSidebarProvider,
    vscode.window.registerWebviewViewProvider(
      ProvenanceSidebarViewProvider.viewType,
      provenanceSidebarProvider,
      {
        webviewOptions: {
          retainContextWhenHidden: true
        }
      }
    ),
    vscode.window.registerWebviewViewProvider(
      ProvenanceSearchSidebarViewProvider.viewType,
      provenanceSearchSidebarProvider,
      {
        webviewOptions: {
          retainContextWhenHidden: true
        }
      }
    )
  );

  for (const document of vscode.workspace.textDocuments) {
    trackDocumentSnapshot(document);
  }

  context.subscriptions.push(
    vscode.workspace.onDidOpenTextDocument((document) => {
      trackDocumentSnapshot(document);
    }),
    vscode.workspace.onDidCloseTextDocument((document) => {
      previousDocumentTexts.delete(document.uri.toString());
    }),
    vscode.workspace.onDidChangeTextDocument((event) => {
      void handleTextDocumentChange(event);
    }),
    vscode.commands.registerCommand('aiInsertionDetector.toggleFeature', async () => {
      await toggleFeature();
    }),
    vscode.commands.registerCommand('aiInsertionDetector.showStatus', () => {
      showStatus();
    }),
    vscode.commands.registerCommand('aiInsertionDetector.showProvenance', async () => {
      await handleShowProvenanceCommand(provenanceSidebarProvider);
    }),
    vscode.commands.registerCommand('aiInsertionDetector.openProvenanceSearch', async () => {
      await provenanceSearchSidebarProvider.focus();
    }),
    vscode.commands.registerCommand('aiInsertionDetector.backendLogin', async () => {
      if (!activeStorageService) {
        return;
      }

      await activeStorageService.authenticate(vscode.window.activeTextEditor?.document.uri);
    }),
    vscode.commands.registerCommand('aiInsertionDetector.switchToBackendMode', async () => {
      await switchToBackendMode(context);
    }),
    vscode.commands.registerCommand('aiInsertionDetector.refreshLocalLineage', async () => {
      await refreshLocalLineage();
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (
        event.affectsConfiguration(CONFIG_SECTION + '.localProxy.enabled') ||
        event.affectsConfiguration(CONFIG_SECTION + '.localProxy.port')
      ) {
        void syncLocalProxyLifecycle();
      }

      if (event.affectsConfiguration(MODE_CONFIG_SECTION + '.mode')) {
        void initializeStorageService(context, vscode.window.activeTextEditor?.document.uri, true);
        return;
      }

      if (isStorageConfigurationChange(event)) {
        void activeStorageService?.handleConfigurationChanged(vscode.window.activeTextEditor?.document.uri);
      }
    })
  );

  void syncLocalProxyLifecycle();
  void initializeStorageService(context, vscode.window.activeTextEditor?.document.uri, true);

  log('AI Insertion Detector activated.');
}

export async function deactivate(): Promise<void> {
  previousDocumentTexts.clear();
  await stopLocalProxy();
  await activeStorageService?.shutdown();
  activeStorageService = undefined;
  extensionContextRef = undefined;
}

function trackDocumentSnapshot(document: vscode.TextDocument): void {
  if (document.uri.scheme !== 'file') {
    return;
  }

  previousDocumentTexts.set(document.uri.toString(), document.getText());
}

async function handleTextDocumentChange(event: vscode.TextDocumentChangeEvent): Promise<void> {
  const document = event.document;
  const key = document.uri.toString();
  const oldText = previousDocumentTexts.get(key) ?? '';
  const newText = document.getText();

  try {
    if (document.uri.scheme !== 'file') {
      return;
    }

    if (event.contentChanges.length === 0) {
      return;
    }

    const config = getDetectorConfig(document.uri);
    if (!config.enabled) {
      return;
    }

    const diffResult = extractInsertedChunksFromDiff(oldText, event.contentChanges);
    if (diffResult.chunks.length === 0 || diffResult.netAddedLines < config.lineThreshold) {
      return;
    }

    const span = getCombinedInsertionSpan(diffResult.chunks);
    if (!span) {
      return;
    }

    const surroundingContext = extractSurroundingContext(
      newText,
      span.startOffset,
      span.endOffset,
      CONTEXT_TOKEN_WINDOW
    );
    const insertedText = diffResult.chunks.map((chunk) => chunk.text).join('\n');
    const insertionTimestampIso = new Date().toISOString();
    const contextSnapshot = await captureContextSnapshot(document.uri.fsPath);
    const provenance = await correlateInsertionWithProxyRequest({
      insertionTimestampIso,
      filePath: document.uri.fsPath,
      localProxy: localLlmProxy,
      insertedCode: insertedText,
      similarityThreshold: config.correlationSimilarityThreshold
    });

    const payload: DetectionPayload = {
      id: uuidv4(),
      timestampIso: insertionTimestampIso,
      filePath: document.uri.fsPath,
      fileUri: document.uri.toString(),
      cursor: getCursorPosition(document, span.endOffset),
      netAddedLines: diffResult.netAddedLines,
      insertedText,
      insertedChunks: diffResult.chunks.map((chunk) => ({
        text: chunk.text,
        start: toLineColumn(document.positionAt(chunk.startOffset)),
        end: toLineColumn(document.positionAt(chunk.endOffset)),
        addedLines: chunk.addedLines,
        removedLines: chunk.removedLines
      })),
      surroundingContext,
      contextSnapshot,
      provenance,
      activeGitBranch: await getActiveGitBranch(document.uri),
      proxyPort: config.proxyPort
    };

    lastPayload = payload;
    const provenanceRecord = buildProvenanceRecord(payload, document, config);
    lastProvenanceRecord = provenanceRecord;

    log('[' + payload.timestampIso + '] Qualifying insertion detected: ' + payload.id);
    log(JSON.stringify(payload, null, 2));

    if (!activeStorageService && extensionContextRef) {
      await initializeStorageService(extensionContextRef, document.uri, false);
    }

    const storageService = activeStorageService;
    const shouldPersistRecord =
      storageService !== undefined &&
      (storageService.mode === 'local' || provenanceRecord.promptStatus === 'captured');

    if (storageService && shouldPersistRecord) {
      try {
        const ingestResult = await storageService.ingest(provenanceRecord, document.uri);

        const statusMessage =
          'AI provenance stored: ' +
          ingestResult.uuid +
          ' (' +
          ingestResult.mode +
          ', ' +
          ingestResult.transport +
          ').';
        vscode.window.setStatusBarMessage(statusMessage, 6000);
        log(statusMessage);

        for (const warning of ingestResult.warnings ?? []) {
          log('Storage warning: ' + warning);
        }
      } catch (error: unknown) {
        const message =
          'Provenance persistence failed for ' + provenanceRecord.uuid + ': ' + toErrorMessage(error);
        log(message);
        void vscode.window.showWarningMessage(message);
      }
    } else if (!storageService) {
      log('No active storage service is available; persistence skipped for ' + provenanceRecord.uuid + '.');
    } else if (provenanceRecord.promptStatus !== 'captured' && storageService.mode === 'backend') {
      log('Backend ingest skipped for ' + provenanceRecord.uuid + ' because correlation was not captured.');
    }

    await postPayloadToProxy(config.proxyPort, payload);
  } catch (error: unknown) {
    log('Error while processing change event: ' + toErrorMessage(error));
  } finally {
    if (document.uri.scheme === 'file') {
      previousDocumentTexts.set(key, newText);
    }
  }
}

function extractInsertedChunksFromDiff(
  oldText: string,
  changes: readonly vscode.TextDocumentContentChangeEvent[]
): { chunks: InternalInsertedChunk[]; netAddedLines: number } {
  const sortedChanges = [...changes].sort((left, right) => left.rangeOffset - right.rangeOffset);

  let workingText = oldText;
  let offsetDelta = 0;
  let netAddedLines = 0;
  const chunks: InternalInsertedChunk[] = [];

  for (const change of sortedChanges) {
    const startOffset = change.rangeOffset + offsetDelta;
    const endOffset = startOffset + change.rangeLength;
    const removedText = workingText.slice(startOffset, endOffset);
    const insertedText = change.text;

    const addedLines = countApproximateLines(insertedText);
    const removedLines = countApproximateLines(removedText);
    netAddedLines += addedLines - removedLines;

    if (insertedText.length > 0) {
      chunks.push({
        text: insertedText,
        startOffset,
        endOffset: startOffset + insertedText.length,
        addedLines,
        removedLines
      });
    }

    workingText =
      workingText.slice(0, startOffset) + insertedText + workingText.slice(endOffset);
    offsetDelta += insertedText.length - change.rangeLength;
  }

  return { chunks, netAddedLines };
}

function countApproximateLines(text: string): number {
  if (text.length === 0) {
    return 0;
  }

  const newlineCount = (text.match(/\r\n|\r|\n/g) ?? []).length;
  const endsInNewline = text.endsWith('\n') || text.endsWith('\r');

  return endsInNewline ? newlineCount : newlineCount + 1;
}

function getCombinedInsertionSpan(
  chunks: readonly InternalInsertedChunk[]
): { startOffset: number; endOffset: number } | undefined {
  if (chunks.length === 0) {
    return undefined;
  }

  let startOffset = chunks[0].startOffset;
  let endOffset = chunks[0].endOffset;

  for (const chunk of chunks) {
    if (chunk.startOffset < startOffset) {
      startOffset = chunk.startOffset;
    }

    if (chunk.endOffset > endOffset) {
      endOffset = chunk.endOffset;
    }
  }

  return { startOffset, endOffset };
}

function extractSurroundingContext(
  text: string,
  insertionStartOffset: number,
  insertionEndOffset: number,
  tokenWindow: number
): SurroundingContext {
  const beforeTokens = tokenize(text.slice(0, Math.max(0, insertionStartOffset)));
  const afterTokens = tokenize(text.slice(Math.max(insertionEndOffset, 0)));

  return {
    before: beforeTokens.slice(-tokenWindow).join(' '),
    after: afterTokens.slice(0, tokenWindow).join(' '),
    tokenWindow
  };
}

function tokenize(text: string): string[] {
  return text.match(/\S+/g) ?? [];
}

function getCursorPosition(document: vscode.TextDocument, fallbackOffset: number): LineColumn {
  const editor = vscode.window.activeTextEditor;
  if (editor && editor.document.uri.toString() === document.uri.toString()) {
    return toLineColumn(editor.selection.active);
  }

  const clampedOffset = Math.min(Math.max(fallbackOffset, 0), document.getText().length);
  return toLineColumn(document.positionAt(clampedOffset));
}

function toLineColumn(position: vscode.Position): LineColumn {
  return {
    line: position.line + 1,
    column: position.character + 1
  };
}

function getDetectorConfig(resource?: vscode.Uri): DetectorConfig {
  const config = vscode.workspace.getConfiguration(CONFIG_SECTION, resource);

  return {
    enabled: config.get<boolean>('enabled', DEFAULT_ENABLED),
    lineThreshold: Math.max(1, config.get<number>('lineThreshold', DEFAULT_LINE_THRESHOLD)),
    proxyPort: Math.max(1, config.get<number>('proxyPort', DEFAULT_PROXY_PORT)),
    localProxyEnabled: config.get<boolean>('localProxy.enabled', DEFAULT_LOCAL_PROXY_ENABLED),
    localProxyPort: Math.max(1, config.get<number>('localProxy.port', DEFAULT_LOCAL_PROXY_PORT)),
    correlationSimilarityThreshold: Math.min(
      1,
      Math.max(
        0,
        config.get<number>(
          'correlation.similarityThreshold',
          DEFAULT_CORRELATION_SIMILARITY_THRESHOLD
        )
      )
    )
  };
}

function buildProvenanceRecord(
  payload: DetectionPayload,
  document: vscode.TextDocument,
  config: DetectorConfig
): ProvenanceRecord {
  const normalizedNodeTypes = normalizeAST(payload.insertedText, document.languageId || payload.filePath);
  const promptCaptured = payload.provenance.promptStatus === 'captured';

  return {
    uuid: payload.id,
    requestUuid: promptCaptured ? payload.provenance.requestUuid : null,
    timestampIso: payload.timestampIso,
    insertionTimestampIso: payload.timestampIso,
    promptStatus: payload.provenance.promptStatus,
    prompt: {
      fullMessages: promptCaptured ? payload.provenance.fullPromptMessages : null,
      modelName: promptCaptured ? payload.provenance.modelName : null,
      parameters: promptCaptured ? payload.provenance.parameters ?? null : null,
      rawModelResponse: promptCaptured ? payload.provenance.rawModelResponse ?? null : null,
      rawModelResponseBase64: promptCaptured ? payload.provenance.rawModelResponseBase64 ?? null : null
    },
    insertion: {
      extractedInsertedCodeBlock: payload.insertedText,
      insertedChunks: payload.insertedChunks,
      netAddedLines: payload.netAddedLines,
      cursorPosition: payload.cursor,
      surroundingContext: payload.surroundingContext
    },
    file: {
      path: payload.filePath,
      uri: payload.fileUri,
      languageId: document.languageId
    },
    repository: {
      gitBranch: payload.activeGitBranch
    },
    contextSnapshot: payload.contextSnapshot,
    embeddings: buildDeterministicEmbeddingBundle(payload),
    astSnapshot: {
      parserEngine: 'tree-sitter',
      normalizationVersion: 'node-type-sequence-v1',
      languageDetected: document.languageId || 'unknown',
      rootNodeType: normalizedNodeTypes.length > 0 ? normalizedNodeTypes[0] : null,
      normalizedNodeTypes,
      nodeCount: normalizedNodeTypes.length,
      parseSucceeded: normalizedNodeTypes.length > 0,
      parseError: normalizedNodeTypes.length > 0 ? null : 'AST normalization produced no nodes.',
      createdAtIso: new Date().toISOString()
    },
    correlation: payload.provenance,
    metadata: {
      similarityThreshold: config.correlationSimilarityThreshold,
      correlationWindowMs: payload.provenance.correlationWindowMs,
      timingDifferenceMs: payload.provenance.timingDifferenceMs,
      correlationConfidence: payload.provenance.correlationConfidence,
      contentSimilarityApplied: payload.provenance.contentSimilarityApplied,
      ambiguityResolvedByContent: payload.provenance.ambiguityResolvedByContent,
      featureVersion: 'backend-integration-v1',
      localProxyPort: payload.proxyPort
    }
  };
}

function buildDeterministicEmbeddingBundle(payload: DetectionPayload): ProvenanceEmbeddingBundle {
  const result: ProvenanceEmbeddingBundle = {};

  const insertedCodeEmbedding = createDeterministicEmbedding(payload.insertedText, 'inserted-code');
  if (insertedCodeEmbedding) {
    result.insertedCode = insertedCodeEmbedding;
  }

  const promptText =
    payload.provenance.promptStatus === 'captured'
      ? safeSerialize(payload.provenance.fullPromptMessages)
      : undefined;
  const promptEmbedding = createDeterministicEmbedding(promptText, 'prompt');
  if (promptEmbedding) {
    result.prompt = promptEmbedding;
  }

  const responseText =
    payload.provenance.promptStatus === 'captured' ? payload.provenance.rawModelResponse : undefined;
  const responseEmbedding = createDeterministicEmbedding(responseText, 'response');
  if (responseEmbedding) {
    result.response = responseEmbedding;
  }

  const contextSnapshotEmbedding = createDeterministicEmbedding(
    safeSerialize(payload.contextSnapshot),
    'context-snapshot'
  );
  if (contextSnapshotEmbedding) {
    result.contextSnapshot = contextSnapshotEmbedding;
  }

  const surroundingContextEmbedding = createDeterministicEmbedding(
    payload.surroundingContext.before + '\n' + payload.surroundingContext.after,
    'other'
  );
  if (surroundingContextEmbedding) {
    result.additional = [surroundingContextEmbedding];
  }

  return result;
}

function createDeterministicEmbedding(
  text: string | undefined,
  source: ProvenanceEmbedding['source']
): ProvenanceEmbedding | undefined {
  if (!text || text.trim().length === 0) {
    return undefined;
  }

  const vector = deterministicUnitVector(text, LOCAL_EMBEDDING_DIMENSIONS);

  return {
    provider: LOCAL_EMBEDDING_PROVIDER,
    model: LOCAL_EMBEDDING_MODEL,
    vector,
    dimensions: vector.length,
    generatedAtIso: new Date().toISOString(),
    source
  };
}

function deterministicUnitVector(text: string, dimensions: number): number[] {
  const vector = new Array<number>(dimensions).fill(0);
  const tokens = text.toLowerCase().match(/[a-z0-9_]{2,}/g) ?? [];

  if (tokens.length === 0) {
    return vector;
  }

  for (const token of tokens) {
    const hash = fnv1aHash(token);
    const index = hash % dimensions;
    const signedWeight = (hash & 1) === 0 ? 1 : -1;
    vector[index] += signedWeight;
  }

  let normSquared = 0;
  for (const value of vector) {
    normSquared += value * value;
  }

  if (normSquared <= 0) {
    return vector;
  }

  const norm = Math.sqrt(normSquared);
  return vector.map((value) => Number((value / norm).toFixed(6)));
}

function fnv1aHash(value: string): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }

  return hash >>> 0;
}

function safeSerialize(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

async function syncLocalProxyLifecycle(): Promise<void> {
  const activeUri = vscode.window.activeTextEditor?.document.uri;
  const config = getDetectorConfig(activeUri);

  if (!config.localProxyEnabled) {
    if (localLlmProxy) {
      await stopLocalProxy();
      log('Local LLM proxy disabled by configuration.');
    }

    return;
  }

  if (localLlmProxy && localLlmProxy.port === config.localProxyPort) {
    return;
  }

  await stopLocalProxy();

  try {
    localLlmProxy = await startLocalLlmProxy({
      port: config.localProxyPort,
      log
    });
    log('Local LLM proxy started on 127.0.0.1:' + String(localLlmProxy.port) + '.');
  } catch (error: unknown) {
    const message =
      'AI Insertion Detector could not start the local LLM proxy: ' + toErrorMessage(error);
    log(message);
    void vscode.window.showErrorMessage(message);
  }
}

async function stopLocalProxy(): Promise<void> {
  if (!localLlmProxy) {
    return;
  }

  await localLlmProxy.stop();
  localLlmProxy = undefined;
}

async function getActiveGitBranch(resource: vscode.Uri): Promise<string | null> {
  const workspaceFolder = vscode.workspace.getWorkspaceFolder(resource);
  if (!workspaceFolder) {
    return null;
  }

  try {
    const git = simpleGit(workspaceFolder.uri.fsPath);
    const branchSummary = await git.branchLocal();
    return branchSummary.current || null;
  } catch (error: unknown) {
    log('Git branch lookup failed: ' + toErrorMessage(error));
    return null;
  }
}

async function postPayloadToProxy(proxyPort: number, payload: DetectionPayload): Promise<void> {
  const body = JSON.stringify(payload);

  await new Promise<void>((resolve) => {
    const request = http.request(
      {
        hostname: '127.0.0.1',
        port: proxyPort,
        path: '/ai-insertions',
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body)
        }
      },
      (response) => {
        response.resume();
        log(
          'Proxy POST completed for ' +
            payload.id +
            ' with status ' +
            String(response.statusCode ?? 'unknown') +
            '.'
        );
        resolve();
      }
    );

    request.on('error', (error: Error) => {
      log(
        'Proxy POST failed for ' +
          payload.id +
          ' on port ' +
          String(proxyPort) +
          ': ' +
          error.message
      );
      resolve();
    });

    request.write(body);
    request.end();
  });
}

async function toggleFeature(): Promise<void> {
  const config = vscode.workspace.getConfiguration(CONFIG_SECTION);
  const current = config.get<boolean>('enabled', DEFAULT_ENABLED);
  const next = !current;

  await config.update('enabled', next, vscode.ConfigurationTarget.Workspace);

  const message =
    'AI insertion detection ' + (next ? 'enabled' : 'disabled') + ' (workspace setting).';
  vscode.window.showInformationMessage(message);
  log(message);
}

function showStatus(): void {
  const activeUri = vscode.window.activeTextEditor?.document.uri;
  const config = getDetectorConfig(activeUri);
  const mode = getConfiguredMode(activeUri);
  const status =
    'enabled=' +
    String(config.enabled) +
    ', lineThreshold=' +
    String(config.lineThreshold) +
    ', proxyPort=' +
    String(config.proxyPort) +
    ', localProxyEnabled=' +
    String(config.localProxyEnabled) +
    ', localProxyPort=' +
    String(config.localProxyPort) +
    ', correlationSimilarityThreshold=' +
    String(config.correlationSimilarityThreshold) +
    ', localProxyRunning=' +
    String(Boolean(localLlmProxy)) +
    ', mode=' +
    mode +
    ', storageServiceReady=' +
    String(Boolean(activeStorageService));

  vscode.window.showInformationMessage('AI Insertion Detector status: ' + status);
  log('Status requested: ' + status);

  if (lastPayload) {
    log('Last detected insertion id: ' + lastPayload.id);
  }

  if (lastProvenanceRecord) {
    log('Last provenance record uuid: ' + lastProvenanceRecord.uuid);
  }
}

async function initializeStorageService(
  context: vscode.ExtensionContext,
  resource?: vscode.Uri,
  forceRecreate = false
): Promise<void> {
  const targetMode = getConfiguredMode(resource);

  if (activeStorageService && activeStorageService.mode === targetMode && !forceRecreate) {
    await activeStorageService.handleConfigurationChanged(resource);
    return;
  }

  if (activeStorageService) {
    await activeStorageService.shutdown();
    activeStorageService.dispose();
    activeStorageService = undefined;
  }

  activeStorageService = createStorageService(targetMode, context);
  await activeStorageService.initialize(resource);

  const modeMessage = 'AI provenance mode active: ' + targetMode + '.';
  log(modeMessage);
  vscode.window.setStatusBarMessage(modeMessage, 5000);
}

function createStorageService(
  mode: ProvenanceMode,
  context: vscode.ExtensionContext
): ProvenanceStorageService {
  if (mode === 'backend') {
    return new BackendStorageService(context, log);
  }

  return new LocalStorageService(context, log);
}

function isStorageConfigurationChange(event: vscode.ConfigurationChangeEvent): boolean {
  const keys = [
    CONFIG_SECTION + '.backend.baseUrl',
    CONFIG_SECTION + '.backend.websocketUrl',
    CONFIG_SECTION + '.backend.ingestPath',
    CONFIG_SECTION + '.backend.auth.loginPath',
    CONFIG_SECTION + '.backend.auth.registerPath',
    CONFIG_SECTION + '.backend.auth.refreshPath',
    CONFIG_SECTION + '.backend.auth.refreshSkewSeconds',
    CONFIG_SECTION + '.backend.auth.autoAcquireOnActivate',
    CONFIG_SECTION + '.backend.retry.websocketAttempts',
    CONFIG_SECTION + '.backend.retry.httpAttempts',
    CONFIG_SECTION + '.backend.vectorSearchPath',
    CONFIG_SECTION + '.local.explanation.provider',
    CONFIG_SECTION + '.local.ollama.url',
    CONFIG_SECTION + '.local.ollama.model',
    CONFIG_SECTION + '.local.ollama.timeoutMs'
  ];

  return keys.some((key) => event.affectsConfiguration(key));
}

async function switchToBackendMode(context: vscode.ExtensionContext): Promise<void> {
  const modeConfig = vscode.workspace.getConfiguration(MODE_CONFIG_SECTION);
  await modeConfig.update('mode', 'backend', vscode.ConfigurationTarget.Workspace);

  const detectorConfig = vscode.workspace.getConfiguration(CONFIG_SECTION);
  const existingBaseUrl =
    detectorConfig.get<string>('backend.baseUrl', DEFAULT_BACKEND_BASE_URL) ??
    DEFAULT_BACKEND_BASE_URL;

  const enteredBaseUrl = await vscode.window.showInputBox({
    title: 'Switch to Backend Mode',
    prompt: 'Enter backend base URL (for example: http://127.0.0.1:8787).',
    value: existingBaseUrl,
    ignoreFocusOut: true,
    validateInput: (value) => {
      const trimmed = value.trim();
      if (trimmed.length === 0) {
        return 'Provide a backend URL.';
      }

      try {
        // eslint-disable-next-line no-new
        new URL(trimmed);
        return undefined;
      } catch {
        return 'Enter a valid absolute URL with protocol, for example http://127.0.0.1:8787.';
      }
    }
  });

  const normalizedBaseUrl =
    enteredBaseUrl && enteredBaseUrl.trim().length > 0
      ? enteredBaseUrl.trim().replace(/\/$/, '')
      : existingBaseUrl;

  await detectorConfig.update(
    'backend.baseUrl',
    normalizedBaseUrl,
    vscode.ConfigurationTarget.Workspace
  );

  const websocketUrl = deriveWebSocketUrlFromBase(normalizedBaseUrl);
  if (websocketUrl) {
    await detectorConfig.update(
      'backend.websocketUrl',
      websocketUrl,
      vscode.ConfigurationTarget.Workspace
    );
  }

  await initializeStorageService(context, vscode.window.activeTextEditor?.document.uri, true);

  const loginChoice = await vscode.window.showInformationMessage(
    'Backend mode enabled. Configure your backend and sign in to start shared ingest.',
    'Login Now',
    'Later'
  );

  if (loginChoice === 'Login Now') {
    await activeStorageService?.authenticate(vscode.window.activeTextEditor?.document.uri);
  }
}

function deriveWebSocketUrlFromBase(baseUrl: string): string | undefined {
  try {
    const parsed = new URL(baseUrl);
    const protocol = parsed.protocol === 'https:' ? 'wss:' : 'ws:';
    parsed.protocol = protocol;
    parsed.pathname = '/ws/capture';
    parsed.search = '';
    parsed.hash = '';
    return parsed.toString();
  } catch {
    return undefined;
  }
}

async function refreshLocalLineage(): Promise<void> {
  if (!activeStorageService && extensionContextRef) {
    await initializeStorageService(
      extensionContextRef,
      vscode.window.activeTextEditor?.document.uri,
      false
    );
  }

  if (!activeStorageService) {
    return;
  }

  try {
    const result = await activeStorageService.updateLineageFromLatestCommit(
      vscode.window.activeTextEditor?.document.uri
    );

    log(result.message);
    void vscode.window.showInformationMessage(result.message);
  } catch (error: unknown) {
    const message = 'Failed to refresh local lineage: ' + toErrorMessage(error);
    log(message);
    void vscode.window.showWarningMessage(message);
  }
}

async function handleShowProvenanceCommand(
  sidebarProvider: ProvenanceSidebarViewProvider
): Promise<void> {
  const selectedText = getActiveSelectionText();
  let uuid = extractUuidFromText(selectedText ?? '');

  if (!uuid) {
    const input = await vscode.window.showInputBox({
      title: 'Show Provenance',
      prompt: 'Enter a provenance UUID to load in the sidebar.',
      value: lastProvenanceRecord?.uuid ?? lastPayload?.id ?? '',
      placeHolder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
      ignoreFocusOut: true,
      validateInput: (value) => {
        return extractUuidFromText(value)
          ? undefined
          : 'Provide a valid UUID (for example: 123e4567-e89b-12d3-a456-426614174000).';
      }
    });

    if (!input) {
      return;
    }

    uuid = extractUuidFromText(input);
  }

  if (!uuid) {
    void vscode.window.showErrorMessage('No valid provenance UUID found.');
    return;
  }

  try {
    await sidebarProvider.showProvenance(uuid);
  } catch (error: unknown) {
    const message = 'Unable to open provenance sidebar: ' + toErrorMessage(error);
    log(message);
    void vscode.window.showErrorMessage(message);
  }
}

async function openFileForSearchSelection(selection: SearchSelection): Promise<void> {
  if (!selection.filePath || selection.filePath.trim().length === 0) {
    return;
  }

  const fileUri = await resolveSearchFileUri(selection.filePath);
  if (!fileUri) {
    log('Search result file not found: ' + selection.filePath);
    return;
  }

  const document = await vscode.workspace.openTextDocument(fileUri);
  await vscode.window.showTextDocument(document, {
    preview: false,
    preserveFocus: false
  });
}

async function resolveSearchFileUri(filePathInput: string): Promise<vscode.Uri | undefined> {
  const trimmed = filePathInput.trim();
  if (trimmed.length === 0) {
    return undefined;
  }

  if (/^file:\/\//i.test(trimmed)) {
    const uri = vscode.Uri.parse(trimmed);
    return (await fileExists(uri)) ? uri : undefined;
  }

  if (path.isAbsolute(trimmed)) {
    const absoluteUri = vscode.Uri.file(trimmed);
    return (await fileExists(absoluteUri)) ? absoluteUri : undefined;
  }

  for (const workspaceFolder of vscode.workspace.workspaceFolders ?? []) {
    const candidateUri = vscode.Uri.file(path.join(workspaceFolder.uri.fsPath, trimmed));
    if (await fileExists(candidateUri)) {
      return candidateUri;
    }
  }

  return undefined;
}

async function fileExists(uri: vscode.Uri): Promise<boolean> {
  try {
    await vscode.workspace.fs.stat(uri);
    return true;
  } catch {
    return false;
  }
}

function getActiveSelectionText(): string | undefined {
  const activeEditor = vscode.window.activeTextEditor;
  if (!activeEditor || activeEditor.selection.isEmpty) {
    return undefined;
  }

  return activeEditor.document.getText(activeEditor.selection);
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

function log(message: string): void {
  outputChannel?.appendLine(message);
}
