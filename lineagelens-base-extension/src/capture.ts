import * as vscode from 'vscode';
import { minimatch } from 'minimatch';
import { CaptureStore } from './store';

// Known AI-related file name patterns and editor context clues
const AI_TOOL_HINTS = [
  /copilot/i, /cursor/i, /codeium/i, /tabnine/i, /cody/i, /continue/i,
  /github\.copilot/i, /amazonq/i, /supermaven/i,
];

export class CaptureService {
  private disposables: vscode.Disposable[] = [];
  private statusBar: vscode.StatusBarItem;
  private onCapture: (record: ReturnType<CaptureStore['getById']>) => void;
  private store: CaptureStore;

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
      this.handleChange(e);
    });
    this.disposables.push(listener);
  }

  private handleChange(event: vscode.TextDocumentChangeEvent): void {
    const cfg = vscode.workspace.getConfiguration('lineagelensBase');
    if (!cfg.get('captureEnabled', true)) return;

    const doc = event.document;
    if (doc.uri.scheme !== 'file') return;

    const excludePatterns: string[] = cfg.get('excludePatterns', []);
    const filePath = doc.uri.fsPath;
    if (excludePatterns.some(p => minimatch(filePath, p, { matchBase: true }))) return;

    const minLines: number = cfg.get('minInsertionLines', 4);

    for (const change of event.contentChanges) {
      const addedText = change.text;
      if (!addedText) continue;

      const newLines = (addedText.match(/\n/g) || []).length;
      if (newLines < minLines - 1) continue;

      // Heuristic: multi-line insertions that start at the beginning of a line
      // are likely AI completions (not manual typing which adds one char at a time)
      const isLikelyAI = change.rangeLength === 0 && newLines >= minLines - 1;
      if (!isLikelyAI) continue;

      const workspaceFolder = vscode.workspace.getWorkspaceFolder(doc.uri);

      const record = this.store.add({
        filePath: doc.uri.fsPath,
        fileName: doc.fileName.split(/[\\/]/).pop() || doc.fileName,
        language: doc.languageId,
        insertedCode: addedText,
        linesAdded: newLines + 1,
        workspaceFolder: workspaceFolder?.name ?? null,
      });

      this.updateStatusBar();
      this.onCapture(record);
    }
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
    this.disposables.forEach(d => d.dispose());
  }
}
