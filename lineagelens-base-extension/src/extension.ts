import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { CaptureStore } from './store';
import { CaptureService } from './capture';
import { CaptureTreeProvider, CaptureTreeItem, ClearAllTreeItem, buildDetailPanel, CAPTURE_DRAG_MIME } from './sidebar';

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

  // Status bar
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = 'lineagelens.refreshSidebar';
  statusBar.text = `$(history) ${store.count} captures`;
  statusBar.tooltip = 'LineageLens Base — click to refresh sidebar';
  statusBar.show();
  context.subscriptions.push(statusBar);

  // Sidebar tree — dragAndDropController enables both in-tree reordering and
  // drag-to-editor code insertion; canSelectMany lets users select + drag multiple items.
  const treeProvider = new CaptureTreeProvider(store);
  const treeView = vscode.window.createTreeView('lineagelens.captures', {
    treeDataProvider: treeProvider,
    dragAndDropController: treeProvider,
    canSelectMany: true,
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

  // Editor drop handler — when the user drags a capture from the sidebar and drops
  // it onto an open editor tab, this inserts the captured code at the drop position.
  context.subscriptions.push(
    vscode.languages.registerDocumentDropEditProvider(
      { language: '*' },
      {
        async provideDocumentDropEdits(
          _document: vscode.TextDocument,
          _position: vscode.Position,
          dataTransfer: vscode.DataTransfer,
          _token: vscode.CancellationToken,
        ): Promise<vscode.DocumentDropEdit | undefined> {
          const item = dataTransfer.get(CAPTURE_DRAG_MIME);
          if (!item) { return undefined; }
          const ids = (await item.asString()).split(',').filter(Boolean);
          const codes = ids
            .map(id => store.getById(id)?.insertedCode)
            .filter((c): c is string => c !== undefined);
          if (codes.length === 0) { return undefined; }
          const edit = new vscode.DocumentDropEdit(codes.join('\n'));
          const firstName = ids.length === 1 ? store.getById(ids[0])?.fileName : undefined;
          edit.label = firstName
            ? `Insert AI capture — ${firstName}`
            : `Insert ${ids.length} AI captures`;
          return edit;
        },
      },
    ),
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
