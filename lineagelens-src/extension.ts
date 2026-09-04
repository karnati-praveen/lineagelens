import * as http from 'node:http';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { TraceLinePanelManager } from './traceLinePanel';
import { StructuredLogger } from './logger';
import simpleGit from 'simple-git';
import { v4 as uuidv4 } from 'uuid';
import {
  correlateInsertionWithProxyRequest,
  PromptCorrelationResult,
  resetCorrelationState
} from './correlation';
import { LocalLlmProxyRuntime, startLocalLlmProxy } from './proxy';
import { ContextSnapshot, captureContextSnapshot } from './contextSnapshot';
import {
  PROVENANCE_EVENT_SCHEMA_VERSION,
  buildProviderAgnosticProvenanceEvent
} from './eventSchema';
import { assessProvenanceRisk } from './insights';
import { InsightsDashboardViewProvider } from './insightsDashboard';
import { createDefaultAgentAdapterRegistry } from './agentAdapters';
import {
  ProvenanceEmbedding,
  ProvenanceEmbeddingBundle,
  ProvenanceRecord,
  normalizeAST
} from './provenance';
import { ProvenanceSidebarViewProvider, extractUuidFromText } from './provenanceSidebar';
import { ProvenanceCodeLensProvider } from './provenanceCodeLens';
import { ProvenanceQuickActionProvider } from './provenanceCodeLens';
import { ProvenanceHoverProvider } from './provenanceHover';
import { CaptureStatusToastManager } from './captureStatusToast';
import { DiffViewPanel } from './diffView';
import { runOnboardingWizard } from './onboardingWizard';
import { runWelcomeFlow, promptAndSaveEmail, removeEmail } from './welcomeFlow';
import { FileTimelineProvider } from './fileTimeline';
import {
  ProvenanceSearchSidebarViewProvider,
  SearchSelection
} from './provenanceSearchSidebar';
import { ProvenanceReviewerService } from './reviewer';
import { BackendIngestClient } from './backend';
import { BackendAuthSession } from './backendAuth';
import { HumanReviewDepthTracker, runAttestHumanReview } from './humanReviewAttestation';
import { BackendStorageService } from './storage/BackendStorageService';
import { LocalStorageService } from './storage/LocalStorageService';
import {
  ProvenanceMode,
  ProvenanceStorageService,
  getConfiguredMode
} from './storage/StorageService';
import { getStoragePathForUri } from './storagePath';
import { DEFAULT_BACKEND_BASE_URL } from './src/constants';

const CONFIG_SECTION = 'aiInsertionDetector';
const MODE_CONFIG_SECTION = 'aiCodeProvenance';
const DEFAULT_LINE_THRESHOLD = 4;
const DEFAULT_PROXY_PORT = 8787;
const DEFAULT_ENABLED = true;
const DEFAULT_LOCAL_PROXY_ENABLED = true;
const DEFAULT_LOCAL_PROXY_PORT = 8080;
const DEFAULT_CORRELATION_SIMILARITY_THRESHOLD = 0.7;
const DEFAULT_CORRELATION_WINDOW_MS = 30_000;
const DEFAULT_LOCAL_PROXY_RETENTION_MS = 5 * 60_000;
const DEFAULT_ACTIVATION_STARTUP_MODE = 'lazy';
const CONTEXT_TOKEN_WINDOW = 200;
const LOCAL_EMBEDDING_DIMENSIONS = 128;
const LOCAL_EMBEDDING_PROVIDER = 'local-hash';
const LOCAL_EMBEDDING_MODEL = 'deterministic-hash-v1';
const agentAdapterRegistry = createDefaultAgentAdapterRegistry();

const humanReviewDepthTracker = new HumanReviewDepthTracker();

type StartupMode = 'lazy' | 'eager';

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
  localProxyRetentionMs: number;
  correlationWindowMs: number;
  correlationSimilarityThreshold: number;
  startupMode: StartupMode;
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
const documentChangeQueues = new Map<string, Promise<void>>();
let outputChannel: vscode.OutputChannel | undefined;
let lastPayload: DetectionPayload | undefined;
let lastProvenanceRecord: ProvenanceRecord | undefined;
let localLlmProxy: LocalLlmProxyRuntime | undefined;
let localProxyRuntimeConfig: { port: number; retentionMs: number } | undefined;
let extensionContextRef: vscode.ExtensionContext | undefined;
let activeStorageService: ProvenanceStorageService | undefined;
let activeStorageMode: ProvenanceMode | undefined;
let storageInitialized = false;
let runtimeInitialized = false;
let _storageInitChain: Promise<void> = Promise.resolve();
let _syncProxyChain: Promise<void> = Promise.resolve();
let runtimeInitializationPromise: Promise<void> | undefined;
let detectorStatusBarItem: vscode.StatusBarItem | undefined;
let structuredLogger: StructuredLogger | undefined;
let activeCodeLensProvider: ProvenanceCodeLensProvider | undefined;
let activeFileTimelineProvider: FileTimelineProvider | undefined;
let activeToastManager: CaptureStatusToastManager | undefined;
let activeQuickActionProvider: ProvenanceQuickActionProvider | undefined;

type TelemetryCounters = {
  insertionsDetected: number;
  insertionsIngested: number;
  ingestErrors: number;
  correlationsCaptured: number;
  correlationsMissed: number;
};

const telemetry: TelemetryCounters = {
  insertionsDetected: 0,
  insertionsIngested: 0,
  ingestErrors: 0,
  correlationsCaptured: 0,
  correlationsMissed: 0
};

export function activate(context: vscode.ExtensionContext): void {
  extensionContextRef = context;
  outputChannel = vscode.window.createOutputChannel('AI Insertion Detector');
  structuredLogger = new StructuredLogger((line) => outputChannel?.appendLine(line));
  context.subscriptions.push(outputChannel);

  detectorStatusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  detectorStatusBarItem.command = 'aiInsertionDetector.showStatus';
  context.subscriptions.push(detectorStatusBarItem);
  updateStatusBarIndicator(vscode.window.activeTextEditor?.document.uri);
  detectorStatusBarItem.show();

  const getStorageService = (): ProvenanceStorageService => {
    if (!activeStorageService) {
      const activeUri = vscode.window.activeTextEditor?.document.uri;
      const mode = getConfiguredMode(activeUri);
      activeStorageService = createStorageService(mode, context);
      activeStorageMode = mode;
      storageInitialized = false;
    }

    return activeStorageService;
  };

  const provenanceSidebarProvider = new ProvenanceSidebarViewProvider(
    context.extensionUri,
    log,
    getStorageService
  );
  const reviewerService = new ProvenanceReviewerService(context, log);
  const insightsDashboardProvider = new InsightsDashboardViewProvider(
    context.extensionUri,
    log,
    getStorageService,
    reviewerService
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

  const codeLensProvider = new ProvenanceCodeLensProvider(getStorageService, log);
  activeCodeLensProvider = codeLensProvider;
  const hoverProvider = new ProvenanceHoverProvider(getStorageService, log);
  const fileTimelineProvider = new FileTimelineProvider(getStorageService, log);
  activeFileTimelineProvider = fileTimelineProvider;

  const fileTimelineView = vscode.window.createTreeView('lineagelens.fileTimeline', {
    treeDataProvider: fileTimelineProvider,
    showCollapseAll: false
  });

  context.subscriptions.push(
    codeLensProvider,
    fileTimelineView,
    fileTimelineProvider,
    vscode.languages.registerCodeLensProvider({ scheme: 'file' }, codeLensProvider),
    vscode.languages.registerHoverProvider({ scheme: 'file' }, hoverProvider),
    vscode.window.onDidChangeActiveTextEditor((editor) => {
      fileTimelineProvider.refresh(editor?.document.uri.fsPath);
    })
  );

  // Seed the timeline for the currently active editor on activation
  fileTimelineProvider.refresh(vscode.window.activeTextEditor?.document.uri.fsPath);

  // Capture status toast manager
  activeToastManager = new CaptureStatusToastManager();
  context.subscriptions.push(activeToastManager);

  // Quick action CodeLens — always register; use a lazy proxy so it works in both startup modes
  const quickActionStorageProxy = {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    search: async (filters: any, resource: any): Promise<any[]> => {
      try { return await getStorageService().search(filters, resource); } catch { return []; }
    }
  };
  activeQuickActionProvider = new ProvenanceQuickActionProvider(quickActionStorageProxy);
  context.subscriptions.push(
    activeQuickActionProvider,
    vscode.languages.registerCodeLensProvider({ scheme: 'file' }, activeQuickActionProvider)
  );

  context.subscriptions.push(
    provenanceSidebarProvider,
    insightsDashboardProvider,
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
      InsightsDashboardViewProvider.viewType,
      insightsDashboardProvider,
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

  // Seed snapshots for documents already open when the extension activates
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
      // fire-and-forget intentional — errors logged in catch
      queueDocumentChangeProcessing(event).catch((error: unknown) => {
        log('Document change processing failed: ' + toErrorMessage(error));
      });
    }),
    vscode.commands.registerCommand('lineagelens.start', async () => {
      await initializeStorageService(context, vscode.window.activeTextEditor?.document.uri);
      vscode.window.showInformationMessage('LineageLens is active.');
    }),
    vscode.commands.registerCommand('aiInsertionDetector.toggleFeature', async () => {
      await toggleFeature();
    }),
    vscode.commands.registerCommand('aiInsertionDetector.showStatus', () => {
      showStatus();
    }),
    vscode.commands.registerCommand('aiInsertionDetector.showProvenance', async () => {
      await ensureRuntimeInitialized(vscode.window.activeTextEditor?.document.uri);
      await handleShowProvenanceCommand(provenanceSidebarProvider);
    }),
    vscode.commands.registerCommand('aiInsertionDetector.showAgentAdapterDiagnostics', async () => {
      await showAgentAdapterDiagnostics();
    }),
    vscode.commands.registerCommand('aiInsertionDetector.openProvenanceSearch', async () => {
      await ensureRuntimeInitialized(vscode.window.activeTextEditor?.document.uri);
      await provenanceSearchSidebarProvider.focus();
    }),
    vscode.commands.registerCommand('aiInsertionDetector.openInsightsDashboard', async () => {
      await ensureRuntimeInitialized(vscode.window.activeTextEditor?.document.uri);
      await insightsDashboardProvider.focus();
    }),
    vscode.commands.registerCommand('aiInsertionDetector.reviewCurrentFile', async () => {
      await ensureRuntimeInitialized(vscode.window.activeTextEditor?.document.uri);
      const review = await reviewerService.reviewCurrentFile(
        getStorageService(),
        vscode.window.activeTextEditor?.document.uri
      );

      await insightsDashboardProvider.focus();
      await insightsDashboardProvider.showReviewResult(review);
      vscode.window.showInformationMessage(
        'AI reviewer completed for ' + review.filePath + ' (' + review.source + ').'
      );
    }),
    vscode.commands.registerCommand('aiInsertionDetector.configureReviewerApiKey', async () => {
      await reviewerService.configureApiKey();
    }),
    vscode.commands.registerCommand('aiInsertionDetector.backendLogin', async () => {
      await ensureRuntimeInitialized(vscode.window.activeTextEditor?.document.uri);
      await activeStorageService?.authenticate(vscode.window.activeTextEditor?.document.uri);
    }),
    vscode.commands.registerCommand('aiInsertionDetector.switchToBackendMode', async () => {
      await switchToBackendMode();
    }),
    vscode.commands.registerCommand('aiInsertionDetector.refreshLocalLineage', async () => {
      await refreshLocalLineage();
    }),
    vscode.commands.registerCommand('lineagelens.showProvenance', async (uuid?: string) => {
      await ensureRuntimeInitialized(vscode.window.activeTextEditor?.document.uri);
      if (uuid && typeof uuid === 'string' && uuid.trim().length > 0) {
        try {
          await provenanceSidebarProvider.showProvenance(uuid.trim());
        } catch (error: unknown) {
          const message = 'Unable to open provenance sidebar: ' + toErrorMessage(error);
          log(message);
          vscode.window.showErrorMessage(message);
        }
      } else {
        await handleShowProvenanceCommand(provenanceSidebarProvider);
      }
    }),
    vscode.commands.registerCommand('lineagelens.checkConfiguration', async () => {
      await checkConfiguration();
    }),
    vscode.commands.registerCommand('lineagelens.migrateToBackend', async () => {
      await migrateLocalToBackend(context);
    }),
    vscode.commands.registerCommand('lineagelens.traceLine', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document.uri.scheme !== 'file') {
        vscode.window.showWarningMessage('LineageLens: open a file in the editor to trace a line.');
        return;
      }
      await ensureRuntimeInitialized(editor.document.uri);
      const filePath = editor.document.uri.fsPath;
      const line = editor.selection.active.line;
      await TraceLinePanelManager.show(filePath, line, getStorageService(), context.extensionUri, log);
    }),
    // lineagelens.showDiff — show code evolution for current file
    vscode.commands.registerCommand('lineagelens.showDiff', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showInformationMessage('Open a file to view its AI insertion history.');
        return;
      }
      try {
        await ensureRuntimeInitialized(editor.document.uri);
        const filePath = editor.document.uri.fsPath;
        const panel = DiffViewPanel.createOrShow(context.extensionUri);
        const diffStorage = getStorageService();
        const records = await diffStorage.search(
            { keywords: '', model: '', dateFrom: '', dateTo: '', currentFileOnly: true, currentFilePath: filePath },
            undefined
          );
        panel.showFileDiff(filePath, records.map((r: any) => {
          const record = isRecord(r.record) ? (r.record as Record<string, any>) : undefined;
          const normalizedEvent = isRecord(record?.normalizedEvent)
            ? (record.normalizedEvent as Record<string, any>)
            : undefined;
          const normalizedFile = isRecord(normalizedEvent?.file)
            ? (normalizedEvent.file as Record<string, any>)
            : undefined;
          const normalizedTimestamps = isRecord(normalizedEvent?.timestamps)
            ? (normalizedEvent.timestamps as Record<string, any>)
            : undefined;
          const normalizedDiff = isRecord(normalizedEvent?.diff)
            ? (normalizedEvent.diff as Record<string, any>)
            : undefined;
          const riskAssessment = isRecord(record?.riskAssessment)
            ? (record.riskAssessment as Record<string, any>)
            : undefined;

          return {
            uuid:
              (typeof r.uuid === 'string' && r.uuid.length > 0 ? r.uuid : undefined) ??
              (typeof record?.uuid === 'string' && record.uuid.length > 0 ? record.uuid : undefined) ??
              (typeof normalizedEvent?.eventId === 'string' ? normalizedEvent.eventId : ''),
            filePath:
              (typeof r.filePath === 'string' && r.filePath.length > 0 ? r.filePath : undefined) ??
              (typeof record?.filePath === 'string' && record.filePath.length > 0 ? record.filePath : undefined) ??
              (typeof normalizedFile?.path === 'string' ? normalizedFile.path : undefined) ??
              filePath,
            modelName:
              (typeof r.modelName === 'string' ? r.modelName : undefined) ??
              (typeof r.model === 'string' ? r.model : undefined) ??
              (typeof record?.modelName === 'string' ? record.modelName : undefined) ??
              (typeof normalizedEvent?.model?.name === 'string' ? normalizedEvent.model.name : undefined),
            riskScore:
              (typeof r.riskScore === 'number' ? r.riskScore : undefined) ??
              (typeof record?.riskScore === 'number' ? record.riskScore : undefined) ??
              (typeof riskAssessment?.score === 'number' ? riskAssessment.score : undefined),
            timestampIso:
              (typeof r.timestampIso === 'string' ? r.timestampIso : undefined) ??
              (typeof record?.timestampIso === 'string' ? record.timestampIso : undefined) ??
              (typeof normalizedTimestamps?.insertedAtIso === 'string'
                ? normalizedTimestamps.insertedAtIso
                : undefined),
            insertedCodeSnippet:
              (typeof r.snippet === 'string' ? r.snippet : undefined) ??
              (typeof record?.insertedCode === 'string' ? record.insertedCode : undefined) ??
              (typeof normalizedDiff?.insertedText === 'string' ? normalizedDiff.insertedText : undefined) ??
              '',
            isRedacted:
              typeof r.isRedacted === 'boolean'
                ? r.isRedacted
                : typeof record?.isRedacted === 'boolean'
                  ? record.isRedacted
                  : false,
          };
        }));
      } catch (err) {
        vscode.window.showErrorMessage(`Failed to load diff: ${toErrorMessage(err)}`);
      }
    }),
    // lineagelens.flagRecord — flag a record
    vscode.commands.registerCommand('lineagelens.flagRecord', async (uuid?: string) => {
      const targetUuid = uuid ?? await vscode.window.showInputBox({ prompt: 'Enter record UUID to flag' });
      if (!targetUuid) { return; }
      const reason = await vscode.window.showInputBox({
        prompt: 'Flag reason (optional)',
        placeHolder: 'e.g. needs-review, suspicious',
      });
      try {
        await ensureRuntimeInitialized(vscode.window.activeTextEditor?.document.uri);
        const storageService = getStorageService();
        if (!(storageService instanceof BackendStorageService)) {
          vscode.window.showWarningMessage('Flagging records requires backend mode.');
          return;
        }

        await storageService.flagRecord(targetUuid, reason ?? undefined, vscode.window.activeTextEditor?.document.uri);
        vscode.window.showInformationMessage(`Record ${targetUuid} flagged${reason ? ': ' + reason : ''}.`);
        activeToastManager?.notify({ status: 'success', message: 'Record flagged', uuid: targetUuid });
      } catch (error: unknown) {
        const message = 'Failed to flag record: ' + toErrorMessage(error);
        log(message);
        vscode.window.showErrorMessage(message);
      }
    }),
    // lineagelens.addToReview — add to reviewer queue
    vscode.commands.registerCommand('lineagelens.addToReview', async (uuid?: string) => {
      const targetUuid = uuid ?? await vscode.window.showInputBox({ prompt: 'Enter record UUID to add to review' });
      if (!targetUuid) { return; }
      try {
        await ensureRuntimeInitialized(vscode.window.activeTextEditor?.document.uri);
        const storageService = getStorageService();
        if (!(storageService instanceof BackendStorageService)) {
          vscode.window.showWarningMessage('Adding records to review requires backend mode.');
          return;
        }

        await storageService.addRecordToReviewQueue(targetUuid, undefined, vscode.window.activeTextEditor?.document.uri);
        vscode.window.showInformationMessage(`Record ${targetUuid} added to reviewer queue.`);
      } catch (error: unknown) {
        const message = 'Failed to add record to reviewer queue: ' + toErrorMessage(error);
        log(message);
        vscode.window.showErrorMessage(message);
      }
    }),
    // lineagelens.explainRecord — explain AI-generated code
    vscode.commands.registerCommand('lineagelens.explainRecord', async (uuid?: string) => {
      const targetUuid = uuid ?? await vscode.window.showInputBox({ prompt: 'Enter record UUID to explain' });
      if (!targetUuid) { return; }
      // Route to existing showProvenance or explain flow
      vscode.commands.executeCommand('lineagelens.showProvenance', targetUuid).then(undefined, (error: unknown) => {
        log('Explain record navigation failed: ' + toErrorMessage(error));
      });
    }),
    // lineagelens.runOnboarding — re-run the setup wizard
    vscode.commands.registerCommand('lineagelens.runOnboarding', async () => {
      await runOnboardingWizard(context);
    }),
    // lineagelens.attestHumanReview — attest that a human reviewed AI-generated lines.
    // Time-on-diff is measured from the moment the command is first invoked with
    // 'start' (or when the user opens a diff view); elapsed seconds, inline
    // comment count, and lines shown are pre-filled from the local tracker.
    // Telemetry stays local until the reviewer explicitly submits.
    vscode.commands.registerCommand('lineagelens.attestHumanReview', async () => {
      const resource = vscode.window.activeTextEditor?.document.uri;
      try {
        // Lazy-start the tracker on first invocation so time begins only when
        // the reviewer intentionally enters the attest flow.
        if (humanReviewDepthTracker.secondsOnDiff === 0) {
          humanReviewDepthTracker.startReview();
        }
        const authSession = new BackendAuthSession(context, log);
        const apiClient = new BackendIngestClient(authSession, log);
        const result = await runAttestHumanReview(apiClient, humanReviewDepthTracker, resource);
        if (result) {
          const label = result.depthSignal ?? 'unknown';
          vscode.window.showInformationMessage(
            `LineageLens: Review attested — depth: ${label}, verdict: ${result.verdict ?? '—'}.`
          );
        }
      } catch (error: unknown) {
        const msg = error instanceof Error ? error.message : String(error);
        log('Attest human review failed: ' + msg);
        vscode.window.showErrorMessage('LineageLens attestation failed: ' + msg);
      }
    }),
    vscode.commands.registerCommand('lineagelens.openWelcome', async () => {
      const seenWelcome = context.globalState.get('lineagelens.hasSeenWelcome');
      if (!seenWelcome) {
        await runWelcomeFlow(context);
      } else {
        vscode.window.showInformationMessage(
          'LineageLens tracks AI-generated code — model, prompt, and timestamp. All features work locally with no account required.',
          'Show my AI code'
        ).then(action => {
          if (action === 'Show my AI code') {
            vscode.commands.executeCommand('aiInsertionDetector.openInsightsDashboard').then(undefined, (error: unknown) => {
              log('Opening insights dashboard failed: ' + toErrorMessage(error));
            });
          }
        }, (error: unknown) => {
          log('Welcome info message failed: ' + toErrorMessage(error));
        });
      }
    }),
    vscode.commands.registerCommand('lineagelens.saveEmail', async () => {
      await promptAndSaveEmail(context, true);
    }),
    vscode.commands.registerCommand('lineagelens.removeEmail', async () => {
      await removeEmail(context);
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      const activeUri = vscode.window.activeTextEditor?.document.uri;

      if (
        event.affectsConfiguration(CONFIG_SECTION + '.localProxy.enabled') ||
        event.affectsConfiguration(CONFIG_SECTION + '.localProxy.port') ||
        event.affectsConfiguration(CONFIG_SECTION + '.localProxy.retentionMs')
      ) {
        if (runtimeInitialized) {
          syncLocalProxyLifecycle().catch((error: unknown) => {
            log('Local proxy lifecycle sync failed: ' + toErrorMessage(error));
          });
        } else {
          log('Local proxy configuration updated. Changes will apply when runtime initializes.');
        }
      }

      if (event.affectsConfiguration(MODE_CONFIG_SECTION + '.mode')) {
        if (runtimeInitialized) {
          initializeStorageService(context, activeUri, true).catch((error: unknown) => {
            log('Storage service initialization failed: ' + toErrorMessage(error));
          });
        } else {
          prepareStorageService(context, activeUri, true).catch((error: unknown) => {
            log('Storage service preparation failed: ' + toErrorMessage(error));
          });
        }

        updateStatusBarIndicator(activeUri);
        return;
      }

      if (event.affectsConfiguration(CONFIG_SECTION + '.activation.startupMode')) {
        const startupMode = getDetectorConfig(activeUri).startupMode;

        if (startupMode === 'eager' && !runtimeInitialized) {
          ensureRuntimeInitialized(activeUri).catch((error: unknown) => {
            log('Runtime initialization failed: ' + toErrorMessage(error));
          });
        }
      }

      if (isStorageConfigurationChange(event)) {
        if (runtimeInitialized) {
          activeStorageService?.handleConfigurationChanged(activeUri)?.catch((error: unknown) => {
            log('Storage configuration change handling failed: ' + toErrorMessage(error));
          });
        }
      }

      updateStatusBarIndicator(activeUri);
    })
  );

  const activeUri = vscode.window.activeTextEditor?.document.uri;
  prepareStorageService(context, activeUri, true).catch((error: unknown) => {
    log('Storage service preparation failed: ' + toErrorMessage(error));
  });

  const startupMode = getDetectorConfig(activeUri).startupMode;
  if (startupMode === 'eager') {
    ensureRuntimeInitialized(activeUri).catch((error: unknown) => {
      log('Runtime initialization failed: ' + toErrorMessage(error));
    });
  }

  // First-run welcome + optional email capture (once per install, non-blocking)
  runWelcomeFlow(context).catch((error: unknown) => {
    log('Welcome flow failed: ' + toErrorMessage(error));
  });

  log('AI Insertion Detector activated in ' + startupMode + ' startup mode.');
}

export async function deactivate(): Promise<void> {
  previousDocumentTexts.clear();
  documentChangeQueues.clear();
  await stopLocalProxy();
  await activeStorageService?.shutdown();
  activeStorageService = undefined;
  activeStorageMode = undefined;
  storageInitialized = false;
  runtimeInitialized = false;
  runtimeInitializationPromise = undefined;
  detectorStatusBarItem?.hide();
  detectorStatusBarItem = undefined;
  extensionContextRef = undefined;
  structuredLogger = undefined;
  activeCodeLensProvider = undefined;
  activeFileTimelineProvider = undefined;
  activeToastManager?.dispose();
  activeToastManager = undefined;
  activeQuickActionProvider?.dispose();
  activeQuickActionProvider = undefined;
}

function trackDocumentSnapshot(document: vscode.TextDocument): void {
  if (document.uri.scheme !== 'file') {
    return;
  }

  previousDocumentTexts.set(document.uri.toString(), document.getText());
}

async function persistProvenanceRecord(
  provenanceRecord: ProvenanceRecord,
  document: vscode.TextDocument,
  storageService: ProvenanceStorageService
): Promise<void> {
  try {
    const ingestResult = await storageService.ingest(provenanceRecord, document.uri);
    telemetry.insertionsIngested += 1;
    const statusMessage =
      'AI provenance stored: ' + ingestResult.uuid + ' (' + ingestResult.mode + ', ' + ingestResult.transport + ').';
    vscode.window.setStatusBarMessage(statusMessage, 6000);
    log(statusMessage);
    for (const warning of ingestResult.warnings ?? []) {
      log('Storage warning: ' + warning);
    }
    // Refresh inline markers so the new record shows up immediately
    activeCodeLensProvider?.refresh();
    activeFileTimelineProvider?.refresh(document.uri.fsPath);
    // Notify toast manager of successful capture
    const toastModelName = provenanceRecord.prompt?.modelName;
    activeToastManager?.notify({
      status: 'success',
      filePath: provenanceRecord.normalizedEvent?.file?.path ?? provenanceRecord.file?.path,
      model: typeof toastModelName === 'string' ? toastModelName : undefined,
      risk: (provenanceRecord.metadata as Record<string, unknown> & { riskAssessment?: { score?: number } })?.riskAssessment?.score,
      uuid: provenanceRecord.normalizedEvent?.eventId ?? provenanceRecord.uuid,
    });
  } catch (error: unknown) {
    telemetry.ingestErrors += 1;
    const message =
      'Provenance persistence failed for ' + provenanceRecord.uuid + ': ' + toErrorMessage(error);
    log(message);
    vscode.window.showWarningMessage(message);
    // Check if the error indicates backend is unreachable (offline queue case)
    const errMsg = toErrorMessage(error).toLowerCase();
    if (errMsg.includes('fetch') || errMsg.includes('network') || errMsg.includes('econnrefused') || errMsg.includes('timeout')) {
      activeToastManager?.notify({ status: 'offline', message: 'Backend unreachable — queued locally' });
    } else {
      activeToastManager?.notify({ status: 'error', message: message });
    }
  }
}

async function handleTextDocumentChange(event: vscode.TextDocumentChangeEvent): Promise<void> {
  const document = event.document;
  const key = document.uri.toString();
  const newText = document.getText();

  if (!previousDocumentTexts.has(key)) {
    previousDocumentTexts.set(key, newText);
    return;
  }

  const oldText = previousDocumentTexts.get(key) ?? newText;

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

    await ensureRuntimeInitialized(document.uri);

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
    const storedFilePath = getStoragePathForUri(document.uri);
    const insertedText = diffResult.chunks.map((chunk) => chunk.text).join('\n');
    const insertionTimestampIso = new Date().toISOString();
    const contextSnapshot = await captureContextSnapshot(document.uri.fsPath);
    const provenance = await correlateInsertionWithProxyRequest({
      insertionTimestampIso,
      filePath: storedFilePath,
      localProxy: localLlmProxy,
      insertedCode: insertedText,
      correlationWindowMs: config.correlationWindowMs,
      similarityThreshold: config.correlationSimilarityThreshold
    });

    const payload: DetectionPayload = {
      id: uuidv4(),
      timestampIso: insertionTimestampIso,
      filePath: storedFilePath,
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

    telemetry.insertionsDetected += 1;
    if (provenance.promptStatus === 'captured') {
      telemetry.correlationsCaptured += 1;
    } else {
      telemetry.correlationsMissed += 1;
    }

    log('Qualifying insertion detected: ' + payload.id, { filePath: payload.filePath, netAddedLines: payload.netAddedLines });

    const storageService = activeStorageService;
    if (storageService) {
      await persistProvenanceRecord(provenanceRecord, document, storageService);
    } else {
      log('No active storage service is available; persistence skipped for ' + provenanceRecord.uuid + '.');
    }

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
  if (editor?.document.uri.toString() === document.uri.toString()) {
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
  const startupModeRaw =
    config
      .get<string>('activation.startupMode', DEFAULT_ACTIVATION_STARTUP_MODE)
      ?.trim()
      .toLowerCase() ?? DEFAULT_ACTIVATION_STARTUP_MODE;
  const startupMode: StartupMode = startupModeRaw === 'eager' ? 'eager' : 'lazy';

  return {
    enabled: config.get<boolean>('enabled', DEFAULT_ENABLED),
    lineThreshold: Math.max(1, config.get<number>('lineThreshold', DEFAULT_LINE_THRESHOLD)),
    proxyPort: Math.max(1, config.get<number>('proxyPort', DEFAULT_PROXY_PORT)),
    localProxyEnabled: config.get<boolean>('localProxy.enabled', DEFAULT_LOCAL_PROXY_ENABLED),
    localProxyPort: Math.max(1, config.get<number>('localProxy.port', DEFAULT_LOCAL_PROXY_PORT)),
    localProxyRetentionMs: Math.max(
      1_000,
      config.get<number>('localProxy.retentionMs', DEFAULT_LOCAL_PROXY_RETENTION_MS) ??
        DEFAULT_LOCAL_PROXY_RETENTION_MS
    ),
    correlationWindowMs: Math.max(
      1_000,
      config.get<number>('correlation.windowMs', DEFAULT_CORRELATION_WINDOW_MS) ??
        DEFAULT_CORRELATION_WINDOW_MS
    ),
    correlationSimilarityThreshold: Math.min(
      1,
      Math.max(
        0,
        config.get<number>(
          'correlation.similarityThreshold',
          DEFAULT_CORRELATION_SIMILARITY_THRESHOLD
        )
      )
    ),
    startupMode
  };
}

function buildProvenanceRecord(
  payload: DetectionPayload,
  document: vscode.TextDocument,
  config: DetectorConfig
): ProvenanceRecord {
  const normalizedNodeTypes = normalizeAST(payload.insertedText, document.languageId || payload.filePath);
  const workspaceHint = vscode.workspace.getWorkspaceFolder(document.uri)?.name ?? null;
  const agentContext = agentAdapterRegistry.detect({
    timestampIso: payload.timestampIso,
    filePath: payload.filePath,
    fileLanguageId: document.languageId || 'unknown',
    workspaceHint,
    insertedText: payload.insertedText,
    correlation: payload.provenance
  });
  const correlationCaptured = payload.provenance.promptStatus === 'captured';
  const fullPromptCaptured = correlationCaptured && payload.provenance.captureStatus === 'full';
  const isMetadataOnly = correlationCaptured && payload.provenance.captureStatus === 'metadata_only';
  const normalizedEvent = buildProviderAgnosticProvenanceEvent({
    eventId: payload.id,
    timestampIso: payload.timestampIso,
    filePath: payload.filePath,
    fileUri: payload.fileUri,
    languageId: document.languageId,
    workspaceHint,
    gitBranch: payload.activeGitBranch,
    insertedText: payload.insertedText,
    insertedChunks: payload.insertedChunks,
    netAddedLines: payload.netAddedLines,
    surroundingContext: payload.surroundingContext,
    contextSnapshot: payload.contextSnapshot,
    correlation: payload.provenance,
    agentContext,
    shimName: 'vscode-extension'
  });

  const record: ProvenanceRecord = {
    schemaVersion: PROVENANCE_EVENT_SCHEMA_VERSION,
    uuid: payload.id,
    requestUuid: correlationCaptured ? payload.provenance.requestUuid : null,
    timestampIso: payload.timestampIso,
    insertionTimestampIso: payload.timestampIso,
    promptStatus: correlationCaptured
      ? (isMetadataOnly ? 'partial' : 'captured')
      : 'not-captured',
    prompt: {
      fullMessages: fullPromptCaptured
        ? payload.provenance.fullPromptMessages
        : isMetadataOnly
          ? buildPartialPromptContext(payload.provenance.modelName, agentContext?.toolName)
          : null,
      modelName: correlationCaptured ? payload.provenance.modelName : null,
      parameters: correlationCaptured ? payload.provenance.parameters ?? null : null,
      rawModelResponse: fullPromptCaptured ? payload.provenance.rawModelResponse ?? null : null,
      rawModelResponseBase64: fullPromptCaptured ? payload.provenance.rawModelResponseBase64 ?? null : null
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
    normalizedEvent,
    rawData: {
      detectionPayload: payload,
      proxyRequest: buildRawProxyRequest(payload.provenance),
      proxyResponse: buildRawProxyResponse(payload.provenance)
    },
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
      featureVersion: 'governance-dashboard-v1',
      schemaVersion: PROVENANCE_EVENT_SCHEMA_VERSION,
      localProxyPort: payload.proxyPort,
      captureStatus: payload.provenance.captureStatus,
      proxyCapture: payload.provenance.captureEvidence ?? null,
      agentContext,
      agentDiagnostics: agentContext
        ? {
            adapterName: agentContext.adapterName,
            confidence: agentContext.confidence,
            evidence: agentContext.evidence
          }
        : null,
      workspaceHint
    }
  };

  record.metadata.riskAssessment = assessProvenanceRisk(record);

  return record;
}

function buildPartialPromptContext(
  modelName: unknown,
  toolName: string | null | undefined
): Array<{ role: string; content: string }> | null {
  const parts: string[] = [];
  if (toolName) {
    parts.push('AI tool: ' + toolName);
  }
  if (modelName != null && typeof modelName === 'string' && modelName.trim().length > 0) {
    parts.push('Model: ' + modelName.trim());
  }
  if (parts.length === 0) {
    return null;
  }
  return [{ role: 'system', content: parts.join(', ') }];
}

function buildRawProxyRequest(correlation: PromptCorrelationResult): Record<string, unknown> | null {
  if (correlation.promptStatus !== 'captured') {
    return null;
  }

  return {
    requestUuid: correlation.requestUuid,
    timestampIso: correlation.proxyRequestTimestampIso,
    targetHost: correlation.targetHost,
    requestHeaders: correlation.requestHeaders,
    fullPromptMessages: correlation.fullPromptMessages,
    modelName: correlation.modelName,
    parameters: correlation.parameters,
    systemPrompt: correlation.systemPrompt,
    captureEvidence: correlation.captureEvidence
  };
}

function buildRawProxyResponse(correlation: PromptCorrelationResult): Record<string, unknown> | null {
  if (correlation.promptStatus !== 'captured') {
    return null;
  }

  return {
    timestampIso: correlation.proxyResponseTimestampIso,
    rawModelResponse: correlation.rawModelResponse,
    rawModelResponseBase64: correlation.rawModelResponseBase64
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
    hash ^= value.codePointAt(index) ?? 0;
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
  const pending = _syncProxyChain.then(async () => {
    const activeUri = vscode.window.activeTextEditor?.document.uri;
    const config = getDetectorConfig(activeUri);

    if (!config.localProxyEnabled) {
      if (localLlmProxy) {
        await stopLocalProxy();
        log('Local LLM proxy disabled by configuration.');
      }
      return;
    }

    if (
      localLlmProxy &&
      localProxyRuntimeConfig &&
      localLlmProxy.port === config.localProxyPort &&
      localProxyRuntimeConfig.retentionMs === config.localProxyRetentionMs
    ) {
      return;
    }

    await stopLocalProxy();

    try {
      resetCorrelationState();
      localLlmProxy = await startLocalLlmProxy({
        port: config.localProxyPort,
        retentionMs: config.localProxyRetentionMs,
        log
      });
      localProxyRuntimeConfig = {
        port: config.localProxyPort,
        retentionMs: config.localProxyRetentionMs
      };
      log('Local LLM proxy started on 127.0.0.1:' + String(localLlmProxy.port) + '.');
    } catch (error: unknown) {
      const message =
        'AI Insertion Detector could not start the local LLM proxy: ' + toErrorMessage(error);
      log(message);
      vscode.window.showErrorMessage(message);
    }
  });
  _syncProxyChain = pending.catch(() => {});
  return pending;
}

async function stopLocalProxy(): Promise<void> {
  const proxy = localLlmProxy;
  if (!proxy) {
    resetCorrelationState();
    return;
  }

  try {
    await proxy.stop();
  } finally {
    resetCorrelationState();
    localLlmProxy = undefined;
    localProxyRuntimeConfig = undefined;
  }
}

function queueDocumentChangeProcessing(event: vscode.TextDocumentChangeEvent): Promise<void> {
  const key = event.document.uri.toString();
  const previousWork = documentChangeQueues.get(key) ?? Promise.resolve();
  const nextWork = previousWork.then(
    () => handleTextDocumentChange(event),
    () => handleTextDocumentChange(event)
  );

  documentChangeQueues.set(key, nextWork);

  // Rejection is already handled by the .catch() the caller attaches to the
  // returned nextWork below; this derived promise just needs its own no-op
  // catch so it doesn't surface as an unhandled rejection.
  nextWork.finally(() => {
    if (documentChangeQueues.get(key) === nextWork) {
      documentChangeQueues.delete(key);
    }
  }).catch(() => {});

  return nextWork;
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
  updateStatusBarIndicator(vscode.window.activeTextEditor?.document.uri);
}

function updateStatusBarIndicator(resource?: vscode.Uri): void {
  if (!detectorStatusBarItem) {
    return;
  }

  const config = getDetectorConfig(resource);
  const mode = getConfiguredMode(resource);
  const enabledLabel = config.enabled ? 'on' : 'off';
  const runtimeLabel = runtimeInitialized ? 'ready' : 'idle';

  detectorStatusBarItem.text = 'AI Prov: ' + enabledLabel + ' (' + mode + ')';
  detectorStatusBarItem.tooltip =
    'AI provenance detection is ' +
    enabledLabel +
    '. Runtime is ' +
    runtimeLabel +
    '. Click for detailed status.';
  detectorStatusBarItem.show();
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
    ', localProxyRetentionMs=' +
    String(config.localProxyRetentionMs) +
    ', correlationWindowMs=' +
    String(config.correlationWindowMs) +
    ', correlationSimilarityThreshold=' +
    String(config.correlationSimilarityThreshold) +
    ', startupMode=' +
    config.startupMode +
    ', localProxyRunning=' +
    String(Boolean(localLlmProxy)) +
    ', mode=' +
    mode +
    ', storageServiceReady=' +
    String(Boolean(activeStorageService)) +
    ', storageInitialized=' +
    String(storageInitialized) +
    ', runtimeInitialized=' +
    String(runtimeInitialized);

  const proxyReport = localLlmProxy?.getCapabilityReport();
  const proxySummary = proxyReport
    ? ', proxyCapture=' +
      String(proxyReport.fullCaptures) +
      '/' +
      String(proxyReport.metadataOnlyCaptures) +
      '/' +
      String(proxyReport.tunnelOnlyCaptures) +
      '/' +
      String(proxyReport.unavailableCaptures)
    : '';

  const telemetrySummary =
    ', [telemetry] detected=' + String(telemetry.insertionsDetected) +
    ' ingested=' + String(telemetry.insertionsIngested) +
    ' errors=' + String(telemetry.ingestErrors) +
    ' captured=' + String(telemetry.correlationsCaptured) +
    ' missed=' + String(telemetry.correlationsMissed);

  vscode.window.showInformationMessage('AI Insertion Detector status: ' + status + proxySummary + telemetrySummary);
  log('Status requested: ' + status + telemetrySummary);
  if (proxyReport) {
    log('Proxy capability report: ' + JSON.stringify(proxyReport, null, 2));
  }

  if (lastPayload) {
    log('Last detected insertion id: ' + lastPayload.id);
  }

  if (lastProvenanceRecord) {
    log('Last provenance record uuid: ' + lastProvenanceRecord.uuid);
  }
}

async function showAgentAdapterDiagnostics(): Promise<void> {
  const activeUri = vscode.window.activeTextEditor?.document.uri;
  const provenance = lastProvenanceRecord;

  if (!provenance) {
    const enteredUuid = await vscode.window.showInputBox({
      title: 'Adapter Diagnostics',
      prompt: 'Enter a provenance UUID to inspect adapter matching.',
      ignoreFocusOut: true
    });

    if (!enteredUuid || enteredUuid.trim().length === 0) {
      return;
    }

    const storageService = activeStorageService;
    if (!storageService) {
      throw new Error('No active storage service is available.');
    }

    const payload = await storageService.getProvenanceByUuid(enteredUuid.trim(), activeUri);
    logAgentDiagnostics(payload.record, payload.uuid);
    return;
  }

  logAgentDiagnostics(provenance as unknown as Record<string, unknown>, provenance.uuid);
}

function logAgentDiagnostics(record: Record<string, unknown>, uuid: string): void {
  const metadata = isRecord(record.metadata) ? record.metadata : {};
  const agentContext = isRecord(metadata.agentContext) ? metadata.agentContext : null;
  const agentDiagnostics = isRecord(metadata.agentDiagnostics) ? metadata.agentDiagnostics : null;
  const correlationCaptureStatus =
    isRecord(record.correlation) && typeof record.correlation.captureStatus === 'string'
      ? record.correlation.captureStatus
      : 'unknown';
  const captureStatus =
    typeof metadata.captureStatus === 'string' ? metadata.captureStatus : correlationCaptureStatus;

  const payload = {
    uuid,
    captureStatus,
    agentContext,
    agentDiagnostics,
    correlation: isRecord(record.correlation) ? record.correlation : null
  };

  const text = JSON.stringify(payload, null, 2);
  log('Agent adapter diagnostics for ' + uuid + ': ' + text);
  vscode.window.showInformationMessage(
    'Adapter diagnostics logged for ' + uuid + ' (' + String(captureStatus) + ').'
  );
}

async function initializeStorageService(
  context: vscode.ExtensionContext,
  resource?: vscode.Uri,
  forceRecreate = false
): Promise<void> {
  await prepareStorageService(context, resource, forceRecreate);

  if (!activeStorageService) {
    return;
  }

  if (storageInitialized && !forceRecreate) {
    await activeStorageService.handleConfigurationChanged(resource);
    return;
  }

  await activeStorageService.initialize(resource);
  storageInitialized = true;

  const activeMode = activeStorageMode ?? activeStorageService.mode;
  const modeMessage = 'AI provenance mode active: ' + activeMode + '.';
  log(modeMessage);
  vscode.window.setStatusBarMessage(modeMessage, 5000);
  updateStatusBarIndicator(resource);
}

async function prepareStorageService(
  context: vscode.ExtensionContext,
  resource?: vscode.Uri,
  forceRecreate = false
): Promise<void> {
  const pending = _storageInitChain.then(async () => {
    const targetMode = getConfiguredMode(resource);
    if (activeStorageService && activeStorageMode === targetMode && !forceRecreate) {
      return;
    }
    if (activeStorageService) {
      await activeStorageService.shutdown();
      activeStorageService.dispose();
      activeStorageService = undefined;
    }
    activeStorageService = createStorageService(targetMode, context);
    activeStorageMode = targetMode;
    storageInitialized = false;
    updateStatusBarIndicator(resource);
  });
  _storageInitChain = pending.catch(() => {});
  return pending;
}

async function ensureRuntimeInitialized(
  resource?: vscode.Uri,
  forceStorageRecreate = false
): Promise<void> {
  if (!extensionContextRef) {
    return;
  }

  if (runtimeInitialized && !forceStorageRecreate) {
    return;
  }

  if (runtimeInitializationPromise && !forceStorageRecreate) {
    return runtimeInitializationPromise;
  }

  const ctx = extensionContextRef;
  runtimeInitializationPromise = (async () => {
    try {
      await initializeStorageService(ctx, resource, forceStorageRecreate);
      await syncLocalProxyLifecycle();
      runtimeInitialized = true;
      updateStatusBarIndicator(resource);
    } finally {
      runtimeInitializationPromise = undefined;
    }
  })();

  return runtimeInitializationPromise;
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
    CONFIG_SECTION + '.local.storage.location',
    CONFIG_SECTION + '.local.storage.customFilePath',
    CONFIG_SECTION + '.local.explanation.provider',
    CONFIG_SECTION + '.local.ollama.url',
    CONFIG_SECTION + '.local.ollama.model',
    CONFIG_SECTION + '.local.ollama.timeoutMs'
  ];

  return keys.some((key) => event.affectsConfiguration(key));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

async function switchToBackendMode(): Promise<void> {
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

  await ensureRuntimeInitialized(vscode.window.activeTextEditor?.document.uri, true);

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
  await ensureRuntimeInitialized(vscode.window.activeTextEditor?.document.uri);

  if (!activeStorageService) {
    return;
  }

  try {
    const result = await activeStorageService.updateLineageFromLatestCommit(
      vscode.window.activeTextEditor?.document.uri
    );

    log(result.message);
    vscode.window.showInformationMessage(result.message);
  } catch (error: unknown) {
    const message = 'Failed to refresh local lineage: ' + toErrorMessage(error);
    log(message);
    vscode.window.showWarningMessage(message);
  }
}

async function handleShowProvenanceCommand(
  sidebarProvider: ProvenanceSidebarViewProvider
): Promise<void> {
  await ensureRuntimeInitialized(vscode.window.activeTextEditor?.document.uri);

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
    vscode.window.showErrorMessage('No valid provenance UUID found.');
    return;
  }

  try {
    await sidebarProvider.showProvenance(uuid);
  } catch (error: unknown) {
    const message = 'Unable to open provenance sidebar: ' + toErrorMessage(error);
    log(message);
    vscode.window.showErrorMessage(message);
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

async function checkConfiguration(): Promise<void> {
  const config = vscode.workspace.getConfiguration('aiInsertionDetector');
  const issues: string[] = [];
  const ok: string[] = [];

  // Check 1: backend URL reachable
  const backendUrl = (config.get<string>('backend.baseUrl', DEFAULT_BACKEND_BASE_URL) ?? DEFAULT_BACKEND_BASE_URL).replace(/\/$/, '');
  try {
    const resp = await fetch(backendUrl + '/health', { signal: AbortSignal.timeout(3000) });
    if (resp.ok) {
      const data = await resp.json() as Record<string, unknown>;
      ok.push('Backend reachable (' + (typeof data.productMode === 'string' ? data.productMode : 'unknown') + ' mode)');
    } else {
      issues.push('Backend returned ' + String(resp.status) + ' from ' + backendUrl + '/health');
    }
  } catch {
    issues.push('Cannot reach backend at ' + backendUrl + ' — is it running?');
  }

  // Check 2: storage mode configured
  const storageMode = vscode.workspace.getConfiguration('aiCodeProvenance').get<string>('mode', 'local') ?? 'local';
  ok.push('Storage mode: ' + storageMode);

  // Check 3: reviewer API key if reviewer is enabled
  const reviewerProvider = config.get<string>('reviewer.provider');
  if (reviewerProvider && reviewerProvider !== 'heuristic' && reviewerProvider !== 'ollama') {
    const key = config.get<string>('reviewer.apiKey');
    if (!key || key.trim().length === 0) {
      issues.push('Reviewer provider "' + reviewerProvider + '" requires aiInsertionDetector.reviewer.apiKey');
    } else {
      ok.push('Reviewer API key: set');
    }
  }

  const lines = [
    ...ok.map(m => '✅ ' + m),
    ...issues.map(m => '❌ ' + m),
  ];

  log('Configuration health check: ' + lines.join(' | '));

  if (issues.length === 0) {
    vscode.window.showInformationMessage('LineageLens: All checks passed — ' + ok.join(', '));
  } else {
    vscode.window.showWarningMessage(
      'LineageLens: ' + String(issues.length) + ' configuration issue(s)',
      'Show Details'
    ).then(action => {
      if (action === 'Show Details') {
        const panel = vscode.window.createWebviewPanel(
          'lineagelens.configHealth',
          'LineageLens Configuration Health',
          vscode.ViewColumn.One,
          {}
        );
        panel.webview.html =
          '<html><body style="font-family:monospace;padding:16px;"><pre>' +
          lines.map(l => escapeHtml(l)).join('\n') +
          '</pre></body></html>';
      }
    }, (error: unknown) => {
      log('Configuration health warning message failed: ' + toErrorMessage(error));
    });
  }
}

async function migrateLocalToBackend(context: vscode.ExtensionContext): Promise<void> {
  if (!activeStorageService) {
    vscode.window.showWarningMessage('LineageLens: Storage is not yet initialized. Try again after activating the extension.');
    return;
  }

  const currentMode = activeStorageMode ?? activeStorageService.mode;
  if (currentMode !== 'backend') {
    const switchChoice = await vscode.window.showWarningMessage(
      'LineageLens: Backend mode is not active. Switch to backend mode first.',
      'Switch Now',
      'Cancel'
    );
    if (switchChoice === 'Switch Now') {
      await vscode.commands.executeCommand('aiInsertionDetector.switchToBackendMode');
    }
    return;
  }

  const proceed = await vscode.window.showInformationMessage(
    'This will upload all local provenance records to the connected backend. Continue?',
    'Upload',
    'Cancel'
  );
  if (proceed !== 'Upload') {
    return;
  }

  // Read all local records using a temporary LocalStorageService
  const localService = new LocalStorageService(context, log);
  await localService.initialize(vscode.window.activeTextEditor?.document.uri);

  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: 'LineageLens: Migrating records...',
      cancellable: false
    },
    async (progress) => {
      let successCount = 0;
      let failCount = 0;

      try {
        // Search with no filters to retrieve all records
        const results = await localService.search(
          {
            keywords: '',
            model: '',
            dateFrom: '',
            dateTo: '',
            currentFileOnly: false,
            limit: 2000
          },
          undefined
        );

        const total = results.length;
        if (total === 0) {
          vscode.window.showInformationMessage('LineageLens: No local records found to migrate.');
          return;
        }

        progress.report({ message: '0/' + String(total) + ' records' });

        for (let i = 0; i < results.length; i++) {
          const item = results[i];
          const uuid = item.uuid;

          try {
            const loaded = await localService.getProvenanceByUuid(uuid, undefined);
            // Ingest using the active backend storage service
            await activeStorageService?.ingest(
              loaded.record as unknown as ProvenanceRecord,
              vscode.window.activeTextEditor?.document.uri
            );
            successCount += 1;
          } catch {
            failCount += 1;
          }

          progress.report({
            message: String(i + 1) + '/' + String(total) + ' records',
            increment: 100 / total
          });
        }
      } catch (error: unknown) {
        vscode.window.showErrorMessage('LineageLens: Migration failed — ' + toErrorMessage(error));
        return;
      } finally {
        await localService.shutdown();
      }

      vscode.window.showInformationMessage(
        'LineageLens: Migration complete — ' +
        String(successCount) + ' succeeded, ' +
        String(failCount) + ' failed.'
      );
      log('Migration complete: ' + String(successCount) + ' succeeded, ' + String(failCount) + ' failed.');
    }
  );
}

function escapeHtml(text: string): string {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function log(message: string, fields?: Record<string, unknown>): void {
  if (structuredLogger) {
    structuredLogger.info(message, fields);
  } else {
    outputChannel?.appendLine(message);
  }
}

export {
  handleTextDocumentChange,
  previousDocumentTexts,
  countApproximateLines,
  extractInsertedChunksFromDiff
};


