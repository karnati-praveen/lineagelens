import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { CaptureStore, CaptureSource } from './store';
import { CaptureService } from './capture';
import { buildDetailPanel } from './sidebar';
import { CaptureWebviewProvider } from './webviewSidebar';
import { captureStoreTojsonl } from './agentTrace';
import { redactRecords } from './secrets';

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

  // Full UI sync after any store change: re-render the cards + status bar count.
  const syncUi = () => {
    webviewProvider.refresh();
    statusBar.text = `$(history) ${store.count} captures`;
  };

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
    vscode.env.clipboard.writeText(record.insertedCode).then(() => {
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

  // Capture service
  const captureService = new CaptureService(store, statusBar, context, () => {
    webviewProvider.refresh();
    statusBar.text = `$(history) ${store.count} captures`;
  });
  captureService.start();
  // Retry any pending outbox entries left over from a previous session.
  captureService.retryOutbox().catch(() => {});
  context.subscriptions.push({ dispose: () => captureService.dispose() });

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
      buildDetailPanel(panel, record);

      panel.webview.onDidReceiveMessage(async (msg: { type: string; source?: string }) => {
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
              if (updated) { syncUi(); buildDetailPanel(panel, updated); }
            }
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
        vscode.workspace.openTextDocument(uri).then(doc => vscode.window.showTextDocument(doc)).catch(() => {});
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
        vscode.workspace.openTextDocument(uri).then(doc => vscode.window.showTextDocument(doc)).catch(() => {});
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

  // Welcome message on first install
  const hasSeenWelcome = context.globalState.get<boolean>('lineagelens.welcomeShown', false);
  if (!hasSeenWelcome) {
    context.globalState.update('lineagelens.welcomeShown', true);
    vscode.window.showInformationMessage(
      'LineageLens Base is active — AI code insertions will be captured automatically.',
      'Open Sidebar',
    ).then(action => {
      if (action === 'Open Sidebar') {
        vscode.commands.executeCommand('lineagelens.captures.focus');
      }
    }).catch(() => {});
  }
}

export function deactivate(): void {
  // VS Code extension API requires this export. All cleanup happens via
  // context.subscriptions disposables registered in activate().
}
