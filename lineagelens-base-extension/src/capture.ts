import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { minimatch } from 'minimatch';
import { CaptureStore, CaptureSource } from './store';
import { captureGitContext, GitContext, GIT_UNAVAILABLE } from './evidence/git';
import { buildIngestPayload, IngestCapability } from './sync/payload';
import { detectCapability } from './sync/capability';
import { OutboxEntry, enqueueEntry, dueEntries, backoffDelay, RETRY_BASE_MS } from './sync/outbox';

// Extension IDs (or fragments) of known AI coding assistants.
// Used to boost confidence when one of these is installed and active.
const AI_TOOL_HINTS = [
  /github\.copilot/i,
  /cursor/i,
  /codeium/i,
  /tabnine\.tabnine-vscode/i,
  /sourcegraph\.cody-ai/i,
  /continue\.continue/i,
  /amazonwebservices\.codewhisperer/i,
  /supermaven\.supermaven/i,
  /blackboxapp\.blackbox/i,
  /amazonwebservices\.amazon-q-vscode/i,
  /antigravity/i,
  /gemini/i,
  /google.*(ai|assistant)/i,
];

export function matchesAiToolHint(value: string): boolean {
  return AI_TOOL_HINTS.some(pattern => pattern.test(value));
}

// Scanning all extensions on every text-change event is O(extensions).
// Cache the result and invalidate only when the extension set actually changes.
let _cachedAiToolActive: boolean | null = null;

function detectAiToolActive(): boolean {
  if (_cachedAiToolActive !== null) { return _cachedAiToolActive; }
  _cachedAiToolActive = matchesAiToolHint(vscode.env.appName) ||
    vscode.extensions.all.some(ext =>
      matchesAiToolHint(ext.id) && ext.isActive,
    );
  return _cachedAiToolActive;
}

export interface InsertedBlock {
  text: string;
  startLine: number;
}

/** Return the changed line block in `after`, excluding unchanged prefix/suffix lines. */
export function insertedBlock(before: string, after: string): InsertedBlock | null {
  if (before === after) { return null; }
  const oldLines = before.split('\n');
  const newLines = after.split('\n');
  let prefix = 0;
  while (prefix < oldLines.length && prefix < newLines.length && oldLines[prefix] === newLines[prefix]) {
    prefix++;
  }
  let suffix = 0;
  while (
    suffix < oldLines.length - prefix &&
    suffix < newLines.length - prefix &&
    oldLines[oldLines.length - 1 - suffix] === newLines[newLines.length - 1 - suffix]
  ) {
    suffix++;
  }
  const changed = newLines.slice(prefix, newLines.length - suffix);
  return changed.length > 0 ? { text: changed.join('\n'), startLine: prefix } : null;
}

/** Normalise text for comparison: strip trailing whitespace on each line. */
function normalise(text: string): string {
  return text.split('\n').map(l => l.trimEnd()).join('\n').trim();
}

/**
 * Score how likely `inserted` came from an AI tool (0.0 – 1.0).
 *
 * Rules:
 *  – clipboard matches inserted text → source is 'paste', score 0.0 (caller skips)
 *  – known AI extension is active → +0.35
 *  – base for any multi-line insertion: 0.45
 *  – +0.05 per extra 5 lines beyond the minimum threshold
 *  – capped at 0.95 (we never reach 1.0 without ground-truth)
 *
 * The clipboard read is gated behind lineagelensBase.clipboardPasteDetection
 * (default true). Disable that setting to prevent any clipboard access.
 */
async function scoreInsertion(
  insertedText: string,
  linesAdded: number,
  minLines: number,
  aiExtensionActive: boolean,
): Promise<{ confidence: number; source: CaptureSource }> {
  const cfg = vscode.workspace.getConfiguration('lineagelensBase');
  // Clipboard is read locally to detect pastes and never stored or transmitted.
  // Gate behind the clipboardPasteDetection setting so users can opt out.
  const clipboardEnabled = cfg.get<boolean>('clipboardPasteDetection', true);

  if (clipboardEnabled) {
    let clipboardText = '';
    try {
      clipboardText = await vscode.env.clipboard.readText();
    } catch {
      // clipboard unavailable — treat as non-paste
    }

    const normInserted = normalise(insertedText);
    const normClipboard = normalise(clipboardText);

    // Exact match → almost certainly a paste.
    if (normClipboard && normClipboard === normInserted) {
      return { confidence: 0.0, source: 'paste' };
    }
  }

  let score = 0.45;
  if (aiExtensionActive) { score += 0.35; }
  // Extra lines bonus: +0.05 per 5 lines over the threshold.
  score += Math.floor((linesAdded - minLines) / 5) * 0.05;
  score = Math.min(score, 0.95);

  return { confidence: score, source: score >= 0.5 ? 'ai' : 'unknown' };
}

// ── Outbox (queue mechanics live in sync/outbox.ts) ───────────────────────────

const OUTBOX_KEY = 'lineagelens.base.outbox';

// ── CaptureService ────────────────────────────────────────────────────────────

export class CaptureService {
  private disposables: vscode.Disposable[] = [];
  private statusBar: vscode.StatusBarItem;
  private onCapture: (record: ReturnType<CaptureStore['getById']>) => void;
  private store: CaptureStore;
  private context: vscode.ExtensionContext;
  private _disposed = false;
  private _retryTimer: ReturnType<typeof setTimeout> | null = null;
  // Cached git context, refreshed in the background so capture never shells out
  // synchronously on the typing path.
  private _gitContext: GitContext = GIT_UNAVAILABLE;
  private _gitContextAt = 0;
  private static readonly GIT_TTL_MS = 15_000;
  // Backend ingest capability (legacy vs evidence-v2); detected in the background.
  private _capability: IngestCapability = 'legacy';
  private _fileSnapshots = new Map<string, string>();
  private _fileTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private _createdFiles = new Set<string>();
  private static readonly SNAPSHOT_MAX_FILES = 5_000;
  private static readonly SNAPSHOT_MAX_FILE_BYTES = 512 * 1024;
  private static readonly SNAPSHOT_MAX_TOTAL_BYTES = 64 * 1024 * 1024;

  constructor(
    store: CaptureStore,
    statusBar: vscode.StatusBarItem,
    context: vscode.ExtensionContext,
    onCapture: () => void,
  ) {
    this.store = store;
    this.statusBar = statusBar;
    this.context = context;
    this.onCapture = onCapture;
  }

  start(): void {
    const listener = vscode.workspace.onDidChangeTextDocument(e => {
      // Fire-and-forget: async detection must not block the VS Code event loop.
      this.handleChange(e).catch(err =>
        console.error('LineageLens Base capture failed:', err),
      );
    });
    this.disposables.push(listener);

    // Built-in and CLI agents may write directly to disk without producing a
    // TextDocument change event in this extension host.
    void this.startFilesystemCapture();

    // Invalidate the AI-tool cache whenever extensions are installed/uninstalled/activated.
    this.disposables.push(
      vscode.extensions.onDidChange(() => { _cachedAiToolActive = null; }),
    );

    // Warm the git context cache for the first capture (non-blocking).
    const cwd = this.gitCwd();
    if (cwd) { void this.refreshGitContext(cwd); }

    // Detect the backend ingest capability in the background (non-blocking).
    void this.refreshCapability();
  }

  /** Detect whether the configured backend accepts the richer evidence-v2 payload. */
  private async refreshCapability(): Promise<void> {
    const backendUrl = (vscode.workspace
      .getConfiguration('lineagelensBase')
      .get<string>('backendUrl', '') ?? '').trim().replace(/\/$/, '');
    if (!backendUrl) { return; }
    this._capability = await detectCapability(backendUrl);
  }

  /** Resolve the repo cwd for a document, falling back to the first workspace folder. */
  private gitCwd(doc?: vscode.TextDocument): string | undefined {
    if (doc) {
      const folder = vscode.workspace.getWorkspaceFolder(doc.uri);
      if (folder) { return folder.uri.fsPath; }
    }
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  }

  /** Refresh the cached git context (best-effort; never throws). */
  private async refreshGitContext(cwd: string): Promise<void> {
    this._gitContext = await captureGitContext(cwd);
    this._gitContextAt = Date.now();
  }

  private snapshotKey(filePath: string): string {
    const resolved = path.resolve(filePath);
    return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
  }

  private isExcluded(filePath: string, patterns: string[]): boolean {
    return patterns.some(p => minimatch(filePath, p, { matchBase: true }));
  }

  private async readSnapshot(filePath: string): Promise<string | undefined> {
    try {
      const stat = await fs.promises.stat(filePath);
      if (!stat.isFile() || stat.size > CaptureService.SNAPSHOT_MAX_FILE_BYTES) { return undefined; }
      const bytes = await fs.promises.readFile(filePath);
      if (bytes.includes(0)) { return undefined; }
      return bytes.toString('utf8');
    } catch {
      return undefined;
    }
  }

  private async startFilesystemCapture(): Promise<void> {
    const cfg = vscode.workspace.getConfiguration('lineagelensBase');
    const excludePatterns: string[] = cfg.get('excludePatterns', []);
    let totalBytes = 0;
    try {
      const files = await vscode.workspace.findFiles('**/*', undefined, CaptureService.SNAPSHOT_MAX_FILES);
      for (const uri of files) {
        if (this._disposed || uri.scheme !== 'file' || this.isExcluded(uri.fsPath, excludePatterns)) { continue; }
        const content = await this.readSnapshot(uri.fsPath);
        if (content === undefined) { continue; }
        totalBytes += Buffer.byteLength(content);
        if (totalBytes > CaptureService.SNAPSHOT_MAX_TOTAL_BYTES) { break; }
        const key = this.snapshotKey(uri.fsPath);
        // A document event may have populated a newer in-memory value while the
        // activation scan was reading this file.
        if (!this._fileSnapshots.has(key)) { this._fileSnapshots.set(key, content); }
      }
    } catch {
      // Workspace enumeration is best-effort; the editor capture path remains active.
    }
    if (this._disposed) { return; }

    const watcher = vscode.workspace.createFileSystemWatcher('**/*');
    const schedule = (uri: vscode.Uri, created: boolean) => {
      if (uri.scheme !== 'file') { return; }
      const key = this.snapshotKey(uri.fsPath);
      if (created) { this._createdFiles.add(key); }
      const existing = this._fileTimers.get(key);
      if (existing) { clearTimeout(existing); }
      this._fileTimers.set(key, setTimeout(() => {
        this._fileTimers.delete(key);
        const wasCreated = this._createdFiles.delete(key);
        void this.handleFilesystemChange(uri, wasCreated).catch(err =>
          console.error('LineageLens Base filesystem capture failed:', err),
        );
      }, 250));
    };
    this.disposables.push(
      watcher,
      watcher.onDidChange(uri => schedule(uri, false)),
      watcher.onDidCreate(uri => schedule(uri, true)),
      watcher.onDidDelete(uri => {
        const key = this.snapshotKey(uri.fsPath);
        const timer = this._fileTimers.get(key);
        if (timer) { clearTimeout(timer); }
        this._fileTimers.delete(key);
        this._createdFiles.delete(key);
        this._fileSnapshots.delete(key);
      }),
    );
  }

  private async handleFilesystemChange(uri: vscode.Uri, created: boolean): Promise<void> {
    if (this._disposed) { return; }
    const cfg = vscode.workspace.getConfiguration('lineagelensBase');
    if (!cfg.get('captureEnabled', true)) { return; }
    if (this.isExcluded(uri.fsPath, cfg.get<string[]>('excludePatterns', []))) { return; }

    const content = await this.readSnapshot(uri.fsPath);
    if (content === undefined) { return; }
    const key = this.snapshotKey(uri.fsPath);
    const previous = this._fileSnapshots.get(key);
    this._fileSnapshots.set(key, content);
    // A missing baseline is only trustworthy for a newly-created file.
    if (previous === undefined && !created) { return; }

    const block = insertedBlock(previous ?? '', content);
    if (!block) { return; }
    const linesAdded = block.text.split('\n').length;
    const minLines = cfg.get<number>('minInsertionLines', 4);
    if (linesAdded < minLines) { return; }

    const { confidence, source } = await scoreInsertion(
      block.text,
      linesAdded,
      minLines,
      detectAiToolActive(),
    );
    if (source === 'paste') { return; }

    const workspaceFolder = vscode.workspace.getWorkspaceFolder(uri);
    const cwd = workspaceFolder?.uri.fsPath ?? this.gitCwd();
    if (cwd && Date.now() - this._gitContextAt > CaptureService.GIT_TTL_MS) {
      this._gitContextAt = Date.now();
      void this.refreshGitContext(cwd);
    }
    const git = this._gitContext;
    const record = this.store.add({
      filePath: uri.fsPath,
      fileName: path.basename(uri.fsPath),
      language: path.extname(uri.fsPath).replace(/^\./, '') || 'plaintext',
      insertedCode: block.text,
      linesAdded,
      workspaceFolder: workspaceFolder?.name ?? null,
      confidence,
      source,
      startLine: block.startLine,
      endLine: block.startLine + linesAdded - 1,
      gitBranch: git.branch,
      gitCommit: git.commit,
    });
    this._postToBackend(record);
    this.updateStatusBar();
    this.onCapture(record);
  }

  private async handleChange(event: vscode.TextDocumentChangeEvent): Promise<void> {
    if (this._disposed) { return; }

    // Skip undo/redo — these replay existing content, not new AI insertions.
    if (
      event.reason === vscode.TextDocumentChangeReason.Undo ||
      event.reason === vscode.TextDocumentChangeReason.Redo
    ) {
      return;
    }

    const cfg = vscode.workspace.getConfiguration('lineagelensBase');
    if (!cfg.get('captureEnabled', true)) { return; }

    const doc = event.document;
    if (doc.uri.scheme !== 'file') { return; }

    const excludePatterns: string[] = cfg.get('excludePatterns', []);
    const filePath = doc.uri.fsPath;
    if (this.isExcluded(filePath, excludePatterns)) { return; }

    const minLines: number = cfg.get('minInsertionLines', 4);
    const aiExtensionActive = detectAiToolActive();

    const wholeDocumentReplacement = event.contentChanges.some(change =>
      !doc.isDirty &&
      change.range.start.line === 0 &&
      change.range.start.character === 0 &&
      change.range.end.isEqual(doc.lineAt(doc.lineCount - 1).range.end),
    );
    // Keep disk snapshots aligned with normal editor changes so the subsequent
    // save event cannot double-capture them. External whole-file reloads are left
    // for the filesystem watcher, which still has the previous disk contents.
    if (!wholeDocumentReplacement && typeof doc.getText === 'function') {
      this._fileSnapshots.set(this.snapshotKey(filePath), doc.getText());
    }

    for (const change of event.contentChanges) {
      const addedText = change.text;
      if (!addedText) { continue; }

      const newLines = (addedText.match(/\n/g) || []).length;
      if (newLines < minLines - 1) { continue; }

      // Skip whole-document replacements on a non-dirty document — these are
      // typically caused by git checkout or an external tool rewriting the file,
      // not by an AI insertion in the editor.
      if (wholeDocumentReplacement) {
        continue;
      }

      const { confidence, source } = await scoreInsertion(
        addedText,
        newLines + 1,
        minLines,
        aiExtensionActive,
      );

      // Skip confirmed pastes to reduce false positives.
      if (source === 'paste') { continue; }

      const workspaceFolder = vscode.workspace.getWorkspaceFolder(doc.uri);

      // Record the inserted range (zero-based) so IDE surfaces can locate, and
      // later relocate, the block. The store computes rangeContentHash from
      // insertedCode automatically.
      const startLine = change.range.start.line;
      const endLine = startLine + newLines;

      // Refresh the git context in the background if stale; use the cached value
      // for this capture so we never block typing on git.
      const cwd = this.gitCwd(doc);
      if (cwd && Date.now() - this._gitContextAt > CaptureService.GIT_TTL_MS) {
        this._gitContextAt = Date.now(); // mark immediately to avoid a refresh stampede
        void this.refreshGitContext(cwd);
      }
      const git = this._gitContext;

      const record = this.store.add({
        filePath: doc.uri.fsPath,
        fileName: doc.fileName.split(/[\\/]/).filter(Boolean).pop() || doc.fileName,
        language: doc.languageId,
        insertedCode: addedText,
        linesAdded: newLines + 1,
        workspaceFolder: workspaceFolder?.name ?? null,
        confidence,
        source,
        startLine,
        endLine,
        gitBranch: git.branch,
        gitCommit: git.commit,
      });

      this._postToBackend(record);
      this.updateStatusBar();
      this.onCapture(record);
    }
  }

  // ── Backend sync with persistent outbox ─────────────────────────────────────

  /**
   * Resolve the ingest token: prioritize encrypted SecretStorage
   * (lineagelensBase.ingestToken) over plaintext settings.json for backward compatibility.
   */
  private async _getIngestToken(): Promise<string> {
    try {
      const secret = await this.context.secrets.get('lineagelensBase.ingestToken');
      if (secret && secret.trim()) {
        return secret.trim();
      }
    } catch {
      // SecretStorage read failed or unavailable
    }
    const cfg = vscode.workspace.getConfiguration('lineagelensBase');
    return (cfg.get<string>('ingestToken', '') ?? '').trim();
  }

  private async _postToBackend(record: import('./store').CaptureRecord): Promise<void> {
    const cfg = vscode.workspace.getConfiguration('lineagelensBase');
    const backendUrl = (cfg.get<string>('backendUrl', '') ?? '').trim().replace(/\/$/, '');
    const ingestToken = await this._getIngestToken();
    const workspaceId = (cfg.get<string>('workspaceId', 'vscode-capture') ?? 'vscode-capture').trim();

    if (!backendUrl || !ingestToken) { return; }

    const redact = cfg.get<boolean>('redactSecretsOnEgress', true);
    const payload = buildIngestPayload(record, { workspaceId, redact, capability: this._capability });

    this._sendOnce(record.id, payload, backendUrl, ingestToken).catch(() => {
      // First attempt failed — add to persistent outbox for retry.
      this._enqueue({ id: record.id, payload, attempts: 1, nextRetryAt: Date.now() + RETRY_BASE_MS });
    });
  }

  private async _sendOnce(id: string, payload: object, backendUrl: string, ingestToken: string): Promise<void> {
    const resp = await fetch(`${backendUrl}/ingest`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${ingestToken}`,
        'X-Idempotency-Key': id,
      },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      throw new Error(`ingest returned ${resp.status}`);
    }
  }

  private _loadOutbox(): OutboxEntry[] {
    try {
      return (this.context.globalState.get<OutboxEntry[]>(OUTBOX_KEY) ?? []);
    } catch {
      return [];
    }
  }

  private _saveOutbox(entries: OutboxEntry[]): void {
    this.context.globalState.update(OUTBOX_KEY, entries).then(undefined, () => {});
  }

  private _enqueue(entry: OutboxEntry): void {
    // enqueueEntry dedupes by id (idempotency key) and caps the queue.
    this._saveOutbox(enqueueEntry(this._loadOutbox(), entry));
    this.updateStatusBar();
    this._scheduleRetry();
  }

  private _scheduleRetry(): void {
    if (this._retryTimer !== null) { return; }  // already scheduled
    this._retryTimer = setTimeout(() => {
      this._retryTimer = null;
      this.retryOutbox().catch(() => {});
    }, RETRY_BASE_MS);
  }

  /** Retry all outbox entries whose nextRetryAt is in the past.
   *  Called automatically on a timer and on extension activation. */
  public async retryOutbox(): Promise<void> {
    if (this._disposed) { return; }
    const cfg = vscode.workspace.getConfiguration('lineagelensBase');
    const backendUrl = (cfg.get<string>('backendUrl', '') ?? '').trim().replace(/\/$/, '');
    const ingestToken = await this._getIngestToken();
    if (!backendUrl || !ingestToken) { return; }

    const outbox = this._loadOutbox();
    if (outbox.length === 0) { return; }

    const now = Date.now();
    let changed = false;

    for (const entry of dueEntries(outbox, now)) {
      try {
        await this._sendOnce(entry.id, entry.payload, backendUrl, ingestToken);
        // Success — mark for removal.
        entry.attempts = -1;  // sentinel: remove
        changed = true;
      } catch {
        // Exponential backoff: 30s → 60s → 120s → … up to 10min.
        entry.attempts++;
        entry.nextRetryAt = now + backoffDelay(entry.attempts);
        changed = true;
      }
    }

    if (changed) {
      const remaining = outbox.filter(e => e.attempts !== -1);
      this._saveOutbox(remaining);
      this.updateStatusBar();
      if (remaining.length > 0) {
        this._scheduleRetry();
      }
    }
  }

  // ── Status bar ────────────────────────────────────────────────────────────────

  updateStatusBar(): void {
    const count = this.store.count;
    const pending = this._loadOutbox().length;
    this.statusBar.text = `$(history) ${count} capture${count !== 1 ? 's' : ''}`;
    if (pending > 0) {
      this.statusBar.tooltip = `LineageLens Base: ${count} AI insertion${count !== 1 ? 's' : ''} captured — ${pending} pending sync`;
    } else {
      this.statusBar.tooltip = `LineageLens Base: ${count} AI insertion${count !== 1 ? 's' : ''} captured`;
    }
  }

  /** @deprecated use updateStatusBar() */
  refreshStatusBar(): void {
    this.updateStatusBar();
  }

  dispose(): void {
    if (this._disposed) { return; }
    this._disposed = true;

    if (this._retryTimer !== null) {
      clearTimeout(this._retryTimer);
      this._retryTimer = null;
    }

    for (const timer of this._fileTimers.values()) { clearTimeout(timer); }
    this._fileTimers.clear();
    this._createdFiles.clear();
    this._fileSnapshots.clear();

    for (const disposable of this.disposables) {
      try {
        disposable.dispose();
      } catch (error) {
        console.error('LineageLens Base disposable cleanup failed:', error);
      }
    }

    this.disposables = [];
  }
}
