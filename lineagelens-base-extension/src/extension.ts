import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { CaptureStore } from './store';
import { CaptureService } from './capture';
import { CaptureTreeProvider, CaptureTreeItem, ClearAllTreeItem, buildDetailPanel, CAPTURE_DRAG_MIME } from './sidebar';
import { captureStoreTojsonl } from './agentTrace';

/**
 * VS Code passes the tree item when a command is invoked from the context menu,
 * but passes the raw id string when invoked via the item's own command.arguments.
 * ClearAllTreeItem has no record — guard against it being passed here.
 */
function resolveId(idOrItem: string | CaptureTreeItem | ClearAllTreeItem): string {
  if (typeof idOrItem === 'string') { return idOrItem; }
  if (idOrItem instanceof CaptureTreeItem) { return idOrItem.record.id; }
  return '';  // ClearAllTreeItem — callers must guard against empty string
}

export function activate(context: vscode.ExtensionContext): void {
  const store = new CaptureStore(context);

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

  // Sidebar tree — dragAndDropController enables both in-tree reordering and
  // drag-to-editor code insertion; canSelectMany lets users select + drag multiple items.
  const treeProvider = new CaptureTreeProvider(store);
  const treeView = vscode.window.createTreeView('lineagelens.captures', {
    treeDataProvider: treeProvider,
    dragAndDropController: treeProvider,
    canSelectMany: true,
    showCollapseAll: false,
  });

  // Description appears permanently under the "AI CAPTURES" title — shows live count.
  const refreshDescription = () => {
    treeView.description = store.count > 0
      ? `${store.count} capture${store.count !== 1 ? 's' : ''}`
      : undefined;
  };
  refreshDescription();

  context.subscriptions.push(treeView);
  context.subscriptions.push(treeProvider);

  // Capture service
  const captureService = new CaptureService(store, statusBar, () => {
    treeProvider.refresh();
    refreshDescription();
    statusBar.text = `$(history) ${store.count} captures`;
  });
  captureService.start();
  context.subscriptions.push({ dispose: () => captureService.dispose() });

  // Open capture detail panel
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.openCapture', (idOrItem: string | CaptureTreeItem | ClearAllTreeItem) => {
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
    }),
  );

  // Refresh sidebar
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.refreshSidebar', () => {
      treeProvider.refresh();
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
        treeProvider.refresh();
        refreshDescription();
        statusBar.text = `$(history) 0 captures`;
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
      fs.writeFileSync(uri.fsPath, store.exportJson(), 'utf-8');
      const open = await vscode.window.showInformationMessage(
        `LineageLens: Exported ${store.count} captures.`,
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
      const jsonl = captureStoreTojsonl(store.getAll(), workspaceId);
      fs.writeFileSync(uri.fsPath, jsonl, 'utf-8');
      const open = await vscode.window.showInformationMessage(
        `LineageLens: Exported ${store.count} captures as Agent Trace.`,
        'Open File',
      );
      if (open === 'Open File') {
        vscode.workspace.openTextDocument(uri).then(doc => vscode.window.showTextDocument(doc)).catch(() => {});
      }
    }),
  );

  // Insert capture code at the active editor cursor (also used by the context menu)
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.insertAtCursor', (idOrItem: string | CaptureTreeItem | ClearAllTreeItem) => {
      const id = resolveId(idOrItem);
      const record = id ? store.getById(id) : undefined;
      if (!record) {
        vscode.window.showErrorMessage('LineageLens: Capture not found.');
        return;
      }
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
    }),
  );

  // Copy capture code to clipboard (context menu)
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.copyCode', (idOrItem: string | CaptureTreeItem | ClearAllTreeItem) => {
      const id = resolveId(idOrItem);
      const record = id ? store.getById(id) : undefined;
      if (!record) { return; }
      vscode.env.clipboard.writeText(record.insertedCode).then(() => {
        vscode.window.showInformationMessage(`LineageLens: Copied code from "${record.fileName}".`);
      }).catch(() => {});
    }),
  );

  // Editor drop handler — fires when the user drops a capture onto any open editor.
  // Checks our custom MIME first (carries IDs → look up fresh code from store).
  // Falls back to text/plain (carries the code directly, set in handleDrag).
  context.subscriptions.push(
    vscode.languages.registerDocumentDropEditProvider(
      '*',   // matches every document — plain '*' is the correct catch-all selector
      {
        async provideDocumentDropEdits(
          _document: vscode.TextDocument,
          _position: vscode.Position,
          dataTransfer: vscode.DataTransfer,
          _token: vscode.CancellationToken,
        ): Promise<vscode.DocumentDropEdit | undefined> {
          // Path 1 — custom MIME with IDs (most reliable, IDs are always fresh)
          const mimeItem = dataTransfer.get(CAPTURE_DRAG_MIME);
          if (mimeItem) {
            const ids = (await mimeItem.asString()).split(',').filter(Boolean);
            const codes = ids
              .map(id => store.getById(id)?.insertedCode)
              .filter((c): c is string => c !== undefined);
            if (codes.length > 0) {
              const separator = '\n\n// ── AI capture ──────────────────────────────\n\n';
              const edit = new vscode.DocumentDropEdit(codes.join(separator));
              const firstName = ids.length === 1 ? store.getById(ids[0])?.fileName : undefined;
              edit.label = firstName
                ? `$(file-code) Insert AI capture — ${firstName}`
                : `$(file-code) Insert ${ids.length} AI captures`;
              return edit;
            }
          }

          // Path 2 — text/plain fallback (carries code directly from handleDrag)
          const textItem = dataTransfer.get('text/plain');
          if (textItem) {
            const code = await textItem.asString();
            if (code.trim()) {
              const edit = new vscode.DocumentDropEdit(code);
              edit.label = '$(file-code) Insert AI capture';
              return edit;
            }
          }

          return undefined;
        },
      },
    ),
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
