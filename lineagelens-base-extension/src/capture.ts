import * as vscode from 'vscode';
import { minimatch } from 'minimatch';
import { CaptureStore, CaptureSource } from './store';
import { redactSecrets } from './secrets';

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
];

// Scanning all extensions on every text-change event is O(extensions).
// Cache the result and invalidate only when the extension set actually changes.
let _cachedAiToolActive: boolean | null = null;

function detectAiToolActive(): boolean {
  if (_cachedAiToolActive !== null) { return _cachedAiToolActive; }
  _cachedAiToolActive = vscode.extensions.all.some(ext =>
    AI_TOOL_HINTS.some(pattern => pattern.test(ext.id)) && ext.isActive,
  );
  return _cachedAiToolActive;
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

// ── Outbox types and constants ────────────────────────────────────────────────

const OUTBOX_KEY = 'lineagelens.base.outbox';
const MAX_OUTBOX = 200;
const RETRY_BASE_MS = 30_000;   // 30 s
const RETRY_MAX_MS = 600_000;   // 10 min

interface OutboxEntry {
  id: string;
  payload: object;
  attempts: number;
  nextRetryAt: number;
}

// ── CaptureService ────────────────────────────────────────────────────────────

export class CaptureService {
  private disposables: vscode.Disposable[] = [];
  private statusBar: vscode.StatusBarItem;
  private onCapture: (record: ReturnType<CaptureStore['getById']>) => void;
  private store: CaptureStore;
  private context: vscode.ExtensionContext;
  private _disposed = false;
  private _retryTimer: ReturnType<typeof setTimeout> | null = null;

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

    // Invalidate the AI-tool cache whenever extensions are installed/uninstalled/activated.
    this.disposables.push(
      vscode.extensions.onDidChange(() => { _cachedAiToolActive = null; }),
    );
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
    if (excludePatterns.some(p => minimatch(filePath, p, { matchBase: true }))) { return; }

    const minLines: number = cfg.get('minInsertionLines', 4);
    const aiExtensionActive = detectAiToolActive();

    for (const change of event.contentChanges) {
      const addedText = change.text;
      if (!addedText) { continue; }

      const newLines = (addedText.match(/\n/g) || []).length;
      if (newLines < minLines - 1) { continue; }

      // Skip whole-document replacements on a non-dirty document — these are
      // typically caused by git checkout or an external tool rewriting the file,
      // not by an AI insertion in the editor.
      if (
        !doc.isDirty &&
        change.range.start.line === 0 &&
        change.range.start.character === 0 &&
        change.range.end.isEqual(doc.lineAt(doc.lineCount - 1).range.end)
      ) {
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

      const record = this.store.add({
        filePath: doc.uri.fsPath,
        fileName: doc.fileName.split(/[\\/]/).filter(Boolean).pop() || doc.fileName,
        language: doc.languageId,
        insertedCode: addedText,
        linesAdded: newLines + 1,
        workspaceFolder: workspaceFolder?.name ?? null,
        confidence,
        source,
      });

      this._postToBackend(record);
      this.updateStatusBar();
      this.onCapture(record);
    }
  }

  // ── Backend sync with persistent outbox ─────────────────────────────────────

  private _postToBackend(record: import('./store').CaptureRecord): void {
    const cfg = vscode.workspace.getConfiguration('lineagelensBase');
    const backendUrl = (cfg.get<string>('backendUrl', '') ?? '').trim().replace(/\/$/, '');
    const ingestToken = (cfg.get<string>('ingestToken', '') ?? '').trim();
    const workspaceId = (cfg.get<string>('workspaceId', 'vscode-capture') ?? 'vscode-capture').trim();

    if (!backendUrl || !ingestToken) { return; }

    const redact = cfg.get<boolean>('redactSecretsOnEgress', true);
    const insertedText = redact ? redactSecrets(record.insertedCode).text : record.insertedCode;

    const payload = {
      id: record.id,
      timestampIso: record.timestamp,
      filePath: record.filePath,
      insertedText,
      netAddedLines: record.linesAdded,
      workspaceId,
      languageId: record.language,
      captureStatus: 'file_diff',
      source: { shim: 'lineagelens-base-extension', ide: 'vscode' },
    };

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
    const outbox = this._loadOutbox();
    const existing = outbox.findIndex(e => e.id === entry.id);
    if (existing >= 0) {
      outbox[existing] = entry;  // update in place (same idempotency key)
    } else {
      outbox.push(entry);
      // Cap at MAX_OUTBOX, drop the oldest (front of array) to make room.
      if (outbox.length > MAX_OUTBOX) {
        outbox.splice(0, outbox.length - MAX_OUTBOX);
      }
    }
    this._saveOutbox(outbox);
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
    const ingestToken = (cfg.get<string>('ingestToken', '') ?? '').trim();
    if (!backendUrl || !ingestToken) { return; }

    const outbox = this._loadOutbox();
    if (outbox.length === 0) { return; }

    const now = Date.now();
    let changed = false;

    for (const entry of outbox) {
      if (entry.nextRetryAt > now) { continue; }
      try {
        await this._sendOnce(entry.id, entry.payload, backendUrl, ingestToken);
        // Success — mark for removal.
        entry.attempts = -1;  // sentinel: remove
        changed = true;
      } catch {
        // Exponential backoff: 30s → 60s → 120s → … up to 10min.
        entry.attempts++;
        const delay = Math.min(RETRY_BASE_MS * Math.pow(2, entry.attempts - 1), RETRY_MAX_MS);
        entry.nextRetryAt = now + delay;
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
