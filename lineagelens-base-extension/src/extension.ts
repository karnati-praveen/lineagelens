import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { CaptureStore } from './store';
import { CaptureService } from './capture';
import { CaptureTreeProvider, buildDetailPanel } from './sidebar';

export function activate(context: vscode.ExtensionContext): void {
  const store = new CaptureStore(context);

  // Status bar
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = 'lineagelens.refreshSidebar';
  statusBar.text = `$(history) ${store.count} captures`;
  statusBar.tooltip = 'LineageLens Base — click to refresh sidebar';
  statusBar.show();
  context.subscriptions.push(statusBar);

  // Sidebar tree
  const treeProvider = new CaptureTreeProvider(store);
  const treeView = vscode.window.createTreeView('lineagelens.captures', {
    treeDataProvider: treeProvider,
    showCollapseAll: false,
  });
  context.subscriptions.push(treeView);
  context.subscriptions.push(treeProvider);

  // Capture service
  const captureService = new CaptureService(store, statusBar, () => {
    treeProvider.refresh();
    statusBar.text = `$(history) ${store.count} captures`;
  });
  captureService.start();
  context.subscriptions.push({ dispose: () => captureService.dispose() });

  // Open capture detail panel
  context.subscriptions.push(
    vscode.commands.registerCommand('lineagelens.openCapture', (id: string) => {
      const record = store.getById(id);
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

export function deactivate(): void {}
