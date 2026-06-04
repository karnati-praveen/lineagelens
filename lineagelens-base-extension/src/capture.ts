import * as vscode from 'vscode';
import { minimatch } from 'minimatch';
import { CaptureStore, CaptureSource } from './store';

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

function detectAiToolActive(): boolean {
  return vscode.extensions.all.some(ext =>
    AI_TOOL_HINTS.some(pattern => pattern.test(ext.id)) && ext.isActive,
  );
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
 */
async function scoreInsertion(
  insertedText: string,
  linesAdded: number,
  minLines: number,
  aiExtensionActive: boolean,
): Promise<{ confidence: number; source: CaptureSource }> {
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

  let score = 0.45;
  if (aiExtensionActive) { score += 0.35; }
  // Extra lines bonus: +0.05 per 5 lines over the threshold.
  score += Math.floor((linesAdded - minLines) / 5) * 0.05;
  score = Math.min(score, 0.95);

  return { confidence: score, source: score >= 0.5 ? 'ai' : 'unknown' };
}

export class CaptureService {
  private disposables: vscode.Disposable[] = [];
  private statusBar: vscode.StatusBarItem;
  private onCapture: (record: ReturnType<CaptureStore['getById']>) => void;
  private store: CaptureStore;
  private _disposed = false;

  constructor(
    store: CaptureStore,
    statusBar: vscode.StatusBarItem,
    onCapture: () => void,
  ) {
    this.store = store;
    this.statusBar = statusBar;
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
  }

  private async handleChange(event: vscode.TextDocumentChangeEvent): Promise<void> {
    if (this._disposed) { return; }

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

      this.postToBackend(record);
      this.updateStatusBar();
      this.onCapture(record);
    }
  }

  private postToBackend(record: import('./store').CaptureRecord): void {
    const cfg = vscode.workspace.getConfiguration('lineagelensBase');
    const backendUrl = (cfg.get<string>('backendUrl', '') ?? '').trim().replace(/\/$/, '');
    const ingestToken = (cfg.get<string>('ingestToken', '') ?? '').trim();
    const workspaceId = (cfg.get<string>('workspaceId', 'vscode-capture') ?? 'vscode-capture').trim();

    if (!backendUrl || !ingestToken) { return; }

    const payload = {
      id: record.id,
      timestampIso: record.timestamp,
      filePath: record.filePath,
      insertedText: record.insertedCode,
      netAddedLines: record.linesAdded,
      workspaceId,
      languageId: record.language,
      captureStatus: 'file_diff',
      source: { shim: 'lineagelens-base-extension', ide: 'vscode' },
    };

    fetch(`${backendUrl}/ingest`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${ingestToken}`,
        'X-Idempotency-Key': record.id,
      },
      body: JSON.stringify(payload),
    }).catch(err => {
      console.error('LineageLens: backend ingest failed:', err);
    });
  }

  private updateStatusBar(): void {
    const count = this.store.count;
    this.statusBar.text = `$(history) ${count} capture${count !== 1 ? 's' : ''}`;
    this.statusBar.tooltip = `LineageLens Base: ${count} AI insertion${count !== 1 ? 's' : ''} captured`;
  }

  refreshStatusBar(): void {
    this.updateStatusBar();
  }

  dispose(): void {
    if (this._disposed) { return; }
    this._disposed = true;

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
