import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { CaptureStore, CaptureSource, ReviewState } from './store';
import { CaptureService } from './capture';
import { selectForGate, hasBlockingFindings, gateSummaryLine } from './review/precommit';
import { buildPrSummary } from './review/prSummary';
import { execFile } from 'child_process';
import { promisify } from 'util';
import { buildDetailPanel } from './sidebar';
import { CaptureWebviewProvider } from './webviewSidebar';
import { captureStoreTojsonl } from './agentTrace';
import { redactRecords, containsSecret } from './secrets';
import { verifyEvidence } from './evidence/verifier';
import { buildCapsule } from './evidence/exportCapsule';
import { buildRecallSet, buildRecallReport } from './risk/recall';
import { scanInstructionFiles, InstructionFile } from './risk/instructionScan';
import { runWelcomeFlow, promptAndSaveEmail, removeEmail } from './welcomeFlow';
import { openWelcomePanel } from './welcome';
import { CaptureCodeLensProvider } from './ide/codeLens';
import { CaptureHoverProvider } from './ide/hover';
import { CaptureDecorations } from './ide/decorations';
import { openFileTimeline } from './ide/timeline';

/** Whether secrets should be scrubbed from data leaving the machine (default on). */
function shouldRedactOnEgress(): boolean {
  return vscode.workspace.getConfiguration('lineagelensBase').get<boolean>('redactSecretsOnEgress', true);
}

/**
 * Commands are invoked either with a raw id string (from the webview / detail
 * panel) or, from the palette, with no argument. Normalise to an id string.
 */
function resolveId(idOrItem: unknown): string {
  if (typeof idOrItem === 'string') { return idOrItem; }
  if (idOrItem && typeof idOrItem === 'object' && 'record' in idOrItem) {
    const rec = (idOrItem as { record?: { id?: string } }).record;
    return typeof rec?.id === 'string' ? rec.id : '';
  }
  return '';
}

const execFileAsync = promisify(execFile);

/**
 * Best-effort list of absolute staged file paths for the pre-commit gate.
 * Returns null when git is unavailable or the folder is not a repo (the caller
 * then falls back to checking every captured file) — capture never depends on git.
 */
async function getStagedFiles(cwd: string): Promise<string[] | null> {
  try {
    const root = (await execFileAsync('git', ['rev-parse', '--show-toplevel'], { cwd })).stdout.trim();
    const out = (await execFileAsync('git', ['diff', '--cached', '--name-only'], { cwd })).stdout;
    return out
      .split('\n')
      .map(s => s.trim())
      .filter(Boolean)
      .map(rel => path.join(root, rel));
  } catch {
    return null;
  }
}

/** Inventory AI instruction files for the workspace folder containing `filePath`. */
function instructionFilesFor(filePath: string): InstructionFile[] {
  const folder = vscode.workspace.getWorkspaceFolder(vscode.Uri.file(filePath));
  const root = folder?.uri.fsPath ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  return root ? scanInstructionFiles(root) : [];
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const store = await CaptureStore.create(context);

  // Status bar — capture count (right side, click to refresh)
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = 'lineagelens.refreshSidebar';
  statusBar.text = `$(history) ${store.count} captures`;
  statusBar.tooltip = 'LineageLens — click to refresh';
  statusBar.show();
  context.subscriptions.push(statusBar);

  // Status bar — trash button (left side, always visible, click to clear all)
  const clearBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 0);
  clearBar.command = 'lineagelens.clearAll';
  clearBar.text = `$(trash) LL: clear`;
  clearBar.tooltip = 'LineageLens: delete all AI captures';
  clearBar.show();
  context.subscriptions.push(clearBar);

  // Status bar — mode indicator (right side, sits left of capture count at priority 100)
  const modeBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
  modeBar.command = 'lineagelens.showModeInfo';
  modeBar.text = '$(sync~spin) LL: checking...';
  modeBar.tooltip = 'LineageLens mode — click for details';
  modeBar.show();
  context.subscriptions.push(modeBar);

  // Sidebar — custom card-stack webview (replaces the native tree so the panel
  // can carry its own background, elevation, and inline search/grouping).
  const webviewProvider = new CaptureWebviewProvider(store, () => syncUi());
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(CaptureWebviewProvider.VIEW_ID, webviewProvider),
  );

  // Full UI sync after any store change: re-render the cards + status bar count
  // + the in-editor surfaces (CodeLens / decorations).
  const syncUi = () => {
    webviewProvider.refresh();
    statusBar.text = `$(history) ${store.count} captures`;
    refreshIdeSurfaces();
  };

  // ── IDE-native surfaces: CodeLens, hover receipts, gutter decorations ─────────
  const codeLensProvider = new CaptureCodeLensProvider(store);
  const decorations = new CaptureDecorations(store);
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider({ scheme: 'file' }, codeLensProvider),
    vscode.languages.registerHoverProvider({ scheme: 'file' }, new CaptureHoverProvider(store)),
    decorations,
  );

  // Hoisted so syncUi (defined above) can call it; only invoked at runtime once
  // codeLensProvider/decorations are initialised.
  function refreshIdeSurfaces(): void {
    codeLensProvider.refresh();
    decorations.apply(vscode.window.activeTextEditor);
  }

  // Repaint decorations when the user switches files.
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(editor => decorations.apply(editor)),
  );

  // Captured ranges shift as the user types — debounce a repaint of the active
  // file so CodeLens/decorations follow the code without thrashing on each keypress.
  let ideDebounce: ReturnType<typeof setTimeout> | null = null;
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(e => {
      if (e.document !== vscode.window.activeTextEditor?.document) { return; }
      if (ideDebounce !== null) { clearTimeout(ideDebounce); }
      ideDebounce = setTimeout(() => { ideDebounce = null; refreshIdeSurfaces(); }, 400);
    }),
    { dispose: () => { if (ideDebounce !== null) { clearTimeout(ideDebounce); } } },
  );

  // Paint whatever file is already open at activation.
  decorations.apply(vscode.window.activeTextEditor);

  // ── Shared record actions (reused by tree commands and the detail panel) ──────
  function insertRecord(record: { insertedCode: string; fileName: string }): void {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showWarningMessage('LineageLens: No active editor — open a file first.');
      return;
    }
    editor.edit(editBuilder => {
      for (const sel of editor.selections) {
        editBuilder.insert(sel.active, record.insertedCode);
      }
    });
  }

  function copyRecord(record: { insertedCode: string; fileName: string }): void {
    Promise.resolve(vscode.env.clipboard.writeText(record.insertedCode)).then(() => {
      vscode.window.showInformationMessage(`LineageLens: Copied code from "${record.fileName}".`);
    }).catch(() => {});
  }

  async function revealRecord(record: { filePath: string }): Promise<void> {
    if (!record.filePath) {
      vscode.window.showWarningMessage('LineageLens: This capture has no file path.');
      return;
    }
    try {
      const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(record.filePath));
      await vscode.window.showTextDocument(doc, { preview: true, viewColumn: vscode.ViewColumn.One });
    } catch {
      vscode.window.showWarningMessage(`LineageLens: Could not open ${record.filePath} — the file may have moved or been deleted.`);
    }
  }

  /** Confirm + delete one record. Returns true if it was removed. */
  async function deleteRecord(id: string, fileName: string): Promise<boolean> {
    const answer = await vscode.window.showWarningMessage(
      `Delete this capture from "${fileName}"? This cannot be undone.`,
      { modal: true },
      'Delete',
    );
    if (answer !== 'Delete') { return false; }
    const removed = store.remove(id);
    if (removed) { syncUi(); }
    return removed;
  }

  // Capture service — refresh every surface (cards, status bar, CodeLens,
  // decorations) the moment a new insertion is captured.
  const captureService = new CaptureService(store, statusBar, context, () => syncUi());
  captureService.start();
  // Retry any pending outbox entries left over from a previous session.
  captureService.retryOutbox().catch(() => {});
  context.subscriptions.push({ dispose: () => captureService.dispose() });
  // Flush/cancel any pending capture-store write on deactivate.
  context.subscriptions.push({ dispose: () => store.dispose() });

  // Open capture detail panel
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.openCapture', (idOrItem: unknown) => {
      const id = resolveId(idOrItem);
      const record = id ? store.getById(id) : undefined;
      if (!record) {
        vscode.window.showErrorMessage('LineageLens: Capture not found.');
        return;
      }
      const panel = vscode.window.createWebviewPanel(
        'lineagelens.captureDetail',
        `Capture: ${record.fileName}`,
        vscode.ViewColumn.Beside,
        { enableScripts: true },
      );
      // Inventory AI instruction files once; reused across panel rebuilds.
      const instructionFiles = instructionFilesFor(record.filePath);
      buildDetailPanel(panel, record, instructionFiles);

      panel.webview.onDidReceiveMessage(async (msg: { type: string; source?: string; state?: string; note?: string }) => {
        // Always re-read from the store — the record may have changed since open.
        const current = store.getById(id);
        if (!current) {
          if (msg.type !== 'delete') { vscode.window.showWarningMessage('LineageLens: This capture no longer exists.'); }
          panel.dispose();
          return;
        }
        switch (msg.type) {
          case 'insert': insertRecord(current); break;
          case 'copy':   copyRecord(current); break;
          case 'reveal': await revealRecord(current); break;
          case 'delete':
            if (await deleteRecord(id, current.fileName)) { panel.dispose(); }
            break;
          case 'reclassify': {
            const src = msg.source as CaptureSource;
            if (src === 'ai' || src === 'paste' || src === 'unknown') {
              const updated = store.setClassification(id, src);
              if (updated) { syncUi(); buildDetailPanel(panel, updated, instructionFiles); }
            }
            break;
          }
          case 'review': {
            const valid: ReviewState[] = ['unreviewed', 'reviewed', 'needs_changes', 'rejected', 'accepted'];
            // A button click carries a target state; a bare "Save note" keeps the
            // current state and only updates the note.
            const target = msg.state && valid.includes(msg.state as ReviewState)
              ? (msg.state as ReviewState)
              : (current.reviewState ?? 'unreviewed');
            const updated = store.setReviewState(id, target, msg.note);
            if (updated) { syncUi(); buildDetailPanel(panel, updated, instructionFiles); }
            break;
          }
          case 'recall': {
            const set = buildRecallSet(current, store.getAll());
            if (set.matches.length === 0) {
              vscode.window.showInformationMessage(
                'LineageLens: no other captures resemble this block — nothing to recall.',
              );
              break;
            }
            const doc = await vscode.workspace.openTextDocument({
              content: buildRecallReport(set),
              language: 'markdown',
            });
            await vscode.window.showTextDocument(doc, { preview: false, viewColumn: vscode.ViewColumn.Beside });
            break;
          }
        }
      }, undefined, context.subscriptions);
    }),
  );

  // Refresh sidebar
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.refreshSidebar', () => {
      webviewProvider.refresh();
      captureService.refreshStatusBar();
    }),
  );

  // Clear all captures
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.clearAll', async () => {
      const answer = await vscode.window.showWarningMessage(
        `Clear all ${store.count} captured records? This cannot be undone.`,
        { modal: true },
        'Clear All',
      );
      if (answer === 'Clear All') {
        store.clear();
        syncUi();
        vscode.window.showInformationMessage('LineageLens: All captures cleared.');
      }
    }),
  );

  // Export as JSON
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.exportJson', async () => {
      const uri = await vscode.window.showSaveDialog({
        defaultUri: vscode.Uri.file(path.join(
          vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '',
          `lineagelens-captures-${Date.now()}.json`,
        )),
        filters: { JSON: ['json'] },
      });
      if (!uri) return;
      const redact = shouldRedactOnEgress();
      const { records, total } = redact
        ? redactRecords(store.getAll())
        : { records: store.getAll(), total: 0 };
      fs.writeFileSync(uri.fsPath, JSON.stringify(records, null, 2), 'utf-8');
      const secretNote = total > 0 ? ` (${total} secret${total !== 1 ? 's' : ''} redacted)` : '';
      const open = await vscode.window.showInformationMessage(
        `LineageLens: Exported ${store.count} captures${secretNote}.`,
        'Open File',
      );
      if (open === 'Open File') {
        Promise.resolve(vscode.workspace.openTextDocument(uri).then(doc => vscode.window.showTextDocument(doc))).catch(() => {});
      }
    }),
  );

  // Export Agent Trace (cursor/agent-trace 0.1.0) — works in Easy Mode, no backend needed
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.exportAgentTrace', async () => {
      const cfg = vscode.workspace.getConfiguration('lineagelensBase');
      const workspaceId = cfg.get<string>('workspaceId', 'vscode-capture') || 'vscode-capture';
      const uri = await vscode.window.showSaveDialog({
        defaultUri: vscode.Uri.file(path.join(
          vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '',
          `lineagelens-agent-trace-${Date.now()}.jsonl`,
        )),
        filters: { 'Agent Trace JSONL': ['jsonl'] },
      });
      if (!uri) { return; }
      const redact = shouldRedactOnEgress();
      const { records, total } = redact
        ? redactRecords(store.getAll())
        : { records: store.getAll(), total: 0 };
      const jsonl = captureStoreTojsonl(records, workspaceId);
      fs.writeFileSync(uri.fsPath, jsonl, 'utf-8');
      const secretNote = total > 0 ? ` (${total} secret${total !== 1 ? 's' : ''} redacted)` : '';
      const open = await vscode.window.showInformationMessage(
        `LineageLens: Exported ${store.count} captures as Agent Trace${secretNote}.`,
        'Open File',
      );
      if (open === 'Open File') {
        Promise.resolve(vscode.workspace.openTextDocument(uri).then(doc => vscode.window.showTextDocument(doc))).catch(() => {});
      }
    }),
  );

  // Insert capture code at the active editor cursor (also used by the context menu)
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.insertAtCursor', (idOrItem: unknown) => {
      const id = resolveId(idOrItem);
      const record = id ? store.getById(id) : undefined;
      if (!record) {
        vscode.window.showErrorMessage('LineageLens: Capture not found.');
        return;
      }
      insertRecord(record);
    }),
  );

  // Copy capture code to clipboard (context menu)
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.copyCode', (idOrItem: unknown) => {
      const id = resolveId(idOrItem);
      const record = id ? store.getById(id) : undefined;
      if (!record) { return; }
      copyRecord(record);
    }),
  );

  // Delete a single capture (inline trash icon + context menu)
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.deleteCapture', async (idOrItem: unknown) => {
      const id = resolveId(idOrItem);
      const record = id ? store.getById(id) : undefined;
      if (!record) { return; }
      await deleteRecord(id, record.fileName);
    }),
  );

  // Reveal the source file for a capture
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.revealInFile', async (idOrItem: unknown) => {
      const id = resolveId(idOrItem);
      const record = id ? store.getById(id) : undefined;
      if (!record) { return; }
      await revealRecord(record);
    }),
  );

  // Show the AI timeline for the active file
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.showFileTimeline', () => {
      openFileTimeline(store, context);
    }),
  );

  // Toggle gutter focus mode (only drift / needs-changes captures)
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.toggleFocusMode', () => {
      const on = decorations.toggleFocusMode();
      decorations.apply(vscode.window.activeTextEditor);
      vscode.window.showInformationMessage(
        on
          ? 'LineageLens AI Focus Mode on — showing only drifted or needs-changes captures.'
          : 'LineageLens AI Focus Mode off — showing all captures.',
      );
    }),
  );

  // Pre-commit AI review gate
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.checkBeforeCommit', async () => {
      const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      const staged = folder ? await getStagedFiles(folder) : null;

      // git available but nothing staged → nothing to gate.
      if (staged !== null && staged.length === 0) {
        vscode.window.showInformationMessage('LineageLens: no staged changes to check.');
        return;
      }

      // Fall back to all captured files when git is unavailable.
      const targetPaths = staged ?? store.getAll().map(r => r.filePath);
      const scope = staged ? 'staged changes' : 'captured files (git unavailable)';
      const buckets = selectForGate(store.getAll(), targetPaths);

      if (!hasBlockingFindings(buckets)) {
        vscode.window.showInformationMessage(`LineageLens: ✓ ${gateSummaryLine(buckets)}`);
        return;
      }
      const action = await vscode.window.showWarningMessage(
        `LineageLens: ${gateSummaryLine(buckets)} in ${scope}.`,
        'Show captures',
      );
      if (action === 'Show captures') {
        void vscode.commands.executeCommand('lineagelens.captures.focus');
      }
    }),
  );

  // Deterministic PR summary of AI-assisted changes (opens as a markdown doc)
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.exportPrSummary', async () => {
      const summary = buildPrSummary(store.getAll());
      const doc = await vscode.workspace.openTextDocument({ content: summary, language: 'markdown' });
      await vscode.window.showTextDocument(doc, { preview: false });
    }),
  );

  // Verify the local evidence store (tamper-evident chain + schema check)
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.verifyEvidence', () => {
      const report = verifyEvidence(store.getAll());
      if (report.ok) {
        vscode.window.showInformationMessage(`LineageLens: ${report.summary}`);
      } else {
        vscode.window.showWarningMessage(`LineageLens: ${report.summary}`);
      }
    }),
  );

  // Export a verifiable evidence capsule (.llcapsule)
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.exportCapsule', async () => {
      const records = store.getAll();
      if (records.length === 0) {
        vscode.window.showInformationMessage('LineageLens: no captures to export.');
        return;
      }
      // Capsules are full-fidelity so they stay verifiable — warn before writing
      // one that carries detected secrets (redaction would break verification).
      if (shouldRedactOnEgress() && records.some(r => containsSecret(r.insertedCode))) {
        const proceed = await vscode.window.showWarningMessage(
          'This evidence capsule contains code with detected secrets. Redacting would break ' +
            'verification, so the capsule keeps the original code. Export anyway?',
          { modal: true },
          'Export anyway',
        );
        if (proceed !== 'Export anyway') { return; }
      }
      const uri = await vscode.window.showSaveDialog({
        defaultUri: vscode.Uri.file(path.join(
          vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '',
          `lineagelens-capsule-${Date.now()}.llcapsule`,
        )),
        filters: { 'LineageLens Capsule': ['llcapsule'] },
      });
      if (!uri) { return; }
      const cfg = vscode.workspace.getConfiguration('lineagelensBase');
      const workspaceId = cfg.get<string>('workspaceId', 'vscode-capture') || 'vscode-capture';
      const capsule = buildCapsule(records, workspaceId, new Date().toISOString());
      fs.writeFileSync(uri.fsPath, JSON.stringify(capsule, null, 2), 'utf-8');
      const open = await vscode.window.showInformationMessage(
        `LineageLens: Exported evidence capsule — ${capsule.verification.summary}`,
        'Open File',
      );
      if (open === 'Open File') {
        Promise.resolve(vscode.workspace.openTextDocument(uri).then(doc => vscode.window.showTextDocument(doc))).catch(() => {});
      }
    }),
  );

  // Mode detection — polls proxy health to switch status bar between Easy and Power
  async function checkMode(): Promise<void> {
    const cfg = vscode.workspace.getConfiguration('lineagelensBase');
    const proxyHealthUrl = (cfg.get<string>('proxyHealthUrl', 'http://localhost:8788/proxy-health') ?? '').trim();
    const backendUrl = (cfg.get<string>('backendUrl', '') ?? '').trim();

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 1500);
      const resp = await fetch(proxyHealthUrl, { method: 'GET', signal: controller.signal });
      clearTimeout(timeoutId);
      if (resp.ok) {
        modeBar.text = '$(shield) LL: Power';
        modeBar.tooltip = 'LineageLens Power Mode — proxy is running, full prompt + model capture active';
        return;
      }
    } catch {
      // proxy not reachable — fall through to Easy mode
    }

    if (backendUrl) {
      modeBar.text = '$(zap) LL: Easy';
      modeBar.tooltip = 'LineageLens Easy Mode — file-level captures sent to backend (confidence ~0.35). Start the proxy for Power Mode.';
    } else {
      modeBar.text = '$(zap) LL: Easy (local)';
      modeBar.tooltip = 'LineageLens Easy Mode — captures stored locally only. Set lineagelensBase.backendUrl to sync to a backend.';
    }
  }

  checkMode().catch(() => {});

  const modeCheckInterval = setInterval(() => { checkMode().catch(() => {}); }, 30_000);
  context.subscriptions.push({ dispose: () => clearInterval(modeCheckInterval) });

  // One-time hint to upgrade to Power Mode when backend is configured but proxy is not running
  async function maybeShowUpgradeHint(): Promise<void> {
    const cfg = vscode.workspace.getConfiguration('lineagelensBase');
    const backendUrl = (cfg.get<string>('backendUrl', '') ?? '').trim();
    if (!backendUrl) { return; }

    const hasSeenHint = context.globalState.get<boolean>('lineagelens.proxyUpgradeHintShown', false);
    if (hasSeenHint) { return; }

    try {
      const proxyHealthUrl = (cfg.get<string>('proxyHealthUrl', 'http://localhost:8788/proxy-health') ?? '').trim();
      const controller = new AbortController();
      setTimeout(() => controller.abort(), 1500);
      const resp = await fetch(proxyHealthUrl, { signal: controller.signal });
      if (resp.ok) { return; }
    } catch { /* proxy down — show the hint */ }

    context.globalState.update('lineagelens.proxyUpgradeHintShown', true);
    const action = await vscode.window.showInformationMessage(
      'LineageLens Easy Mode: file-level captures are flowing. Start the proxy for full prompt + model lineage (Power Mode).',
      'Learn how',
      'Dismiss',
    );
    if (action === 'Learn how') {
      vscode.env.openExternal(vscode.Uri.parse('https://github.com/karnati-praveen/lineagelens#proxy'));
    }
  }

  setTimeout(() => { maybeShowUpgradeHint().catch(() => {}); }, 5000);

  // Show mode status on status bar click
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.showModeInfo', () => {
      checkMode().then(() => {
        vscode.window.showInformationMessage(modeBar.tooltip as string ?? 'LineageLens mode status');
      }).catch(() => {});
    }),
  );

  // First-run welcome + optional email capture (once per install, non-blocking)
  void runWelcomeFlow(context);

  // Email management commands
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.openWelcome', () => {
      openWelcomePanel(context);
    }),
    vscode.commands.registerCommand('lineagelens.saveEmail', async () => {
      await promptAndSaveEmail(context, true);
    }),
    vscode.commands.registerCommand('lineagelens.removeEmail', async () => {
      await removeEmail(context);
    }),
  );
}

export function deactivate(): void {
  // VS Code extension API requires this export. All cleanup happens via
  // context.subscriptions disposables registered in activate().
}
