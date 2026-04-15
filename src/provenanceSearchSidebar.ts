import * as vscode from 'vscode';
import { extractUuidFromText } from './provenanceSidebar';
import {
  ProvenanceSearchFilters,
  ProvenanceStorageService
} from './storage/StorageService';

type SearchFilters = ProvenanceSearchFilters;

type SearchMessage =
  | {
      type: 'search';
      payload?: Partial<SearchFilters>;
    }
  | {
      type: 'openResult';
      payload?: {
        uuid?: string;
        filePath?: string | null;
      };
    }
  | {
      type: 'refreshCurrentFile';
    };

export type SearchSelection = {
  uuid: string;
  filePath: string | null;
};

export class ProvenanceSearchSidebarViewProvider
  implements vscode.WebviewViewProvider, vscode.Disposable
{
  public static readonly viewType = 'aiInsertionDetector.provenanceSearchSidebar';

  private view: vscode.WebviewView | undefined;
  private readonly disposables: vscode.Disposable[] = [];

  public constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly log: (message: string) => void,
    private readonly getStorageService: () => ProvenanceStorageService,
    private readonly onOpenResult: (selection: SearchSelection) => Promise<void>
  ) {}

  public resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this.extensionUri]
    };

    webviewView.webview.html = this.getHtml(webviewView.webview);

    this.disposables.push(
      webviewView.webview.onDidReceiveMessage((message: SearchMessage) => {
        void this.handleMessage(message);
      }),
      webviewView.onDidDispose(() => {
        this.view = undefined;
      })
    );

    void this.postMessage({
      type: 'ready',
      payload: {
        currentFile: this.getCurrentFilePath() ?? '',
        hint:
          this.getStorageService().mode === 'local'
            ? 'Local keyword/date search. Vector similarity is backend-only.'
            : 'Backend search by prompt keywords, date range, model, or current file.'
      }
    });
  }

  public async focus(): Promise<void> {
    await vscode.commands.executeCommand('workbench.view.explorer');

    try {
      await vscode.commands.executeCommand(ProvenanceSearchSidebarViewProvider.viewType + '.focus');
    } catch {
      // Focus command availability can vary by VS Code version.
    }

    if (this.view) {
      this.view.show?.(true);
      await this.postMessage({
        type: 'ready',
        payload: {
          currentFile: this.getCurrentFilePath() ?? '',
          hint:
            this.getStorageService().mode === 'local'
              ? 'Local keyword/date search. Vector similarity is backend-only.'
              : 'Backend search by prompt keywords, date range, model, or current file.'
        }
      });
    }
  }

  public dispose(): void {
    for (const disposable of this.disposables) {
      disposable.dispose();
    }

    this.disposables.length = 0;
    this.view = undefined;
  }

  private async handleMessage(message: SearchMessage): Promise<void> {
    if (message.type === 'refreshCurrentFile') {
      await this.postMessage({
        type: 'currentFile',
        payload: {
          currentFile: this.getCurrentFilePath() ?? ''
        }
      });
      return;
    }

    if (message.type === 'openResult') {
      const uuid = extractUuidFromText(message.payload?.uuid ?? '');
      if (!uuid) {
        await this.postMessage({
          type: 'error',
          payload: {
            error: 'Selected result does not contain a valid provenance UUID.'
          }
        });
        return;
      }

      try {
        await this.onOpenResult({
          uuid,
          filePath: sanitizeFilePath(message.payload?.filePath)
        });
      } catch (error: unknown) {
        await this.postMessage({
          type: 'error',
          payload: {
            error: toErrorMessage(error)
          }
        });
      }

      return;
    }

    if (message.type === 'search') {
      const currentFilePath = this.getCurrentFilePath();
      const filters: SearchFilters = {
        keywords: (message.payload?.keywords ?? '').trim(),
        model: (message.payload?.model ?? '').trim(),
        dateFrom: (message.payload?.dateFrom ?? '').trim(),
        dateTo: (message.payload?.dateTo ?? '').trim(),
        currentFileOnly: Boolean(message.payload?.currentFileOnly),
        currentFilePath
      };

      const storageService = this.getStorageService();

      await this.postMessage({
        type: 'loading',
        payload: {
          text:
            storageService.mode === 'local'
              ? 'Running local keyword/date search...'
              : 'Running backend similarity search...'
        }
      });

      try {
        const searchResults = await storageService.search(
          filters,
          vscode.window.activeTextEditor?.document.uri
        );

        await this.postMessage({
          type: 'results',
          payload: {
            results: searchResults,
            count: searchResults.length,
            currentFile: currentFilePath ?? ''
          }
        });
      } catch (error: unknown) {
        await this.postMessage({
          type: 'error',
          payload: {
            error: toErrorMessage(error)
          }
        });
      }
    }
  }

  private getCurrentFilePath(): string | undefined {
    const activeEditor = vscode.window.activeTextEditor;
    if (!activeEditor || activeEditor.document.uri.scheme !== 'file') {
      return undefined;
    }

    return activeEditor.document.uri.fsPath;
  }

  private async postMessage(message: unknown): Promise<void> {
    if (!this.view) {
      return;
    }

    await this.view.webview.postMessage(message);
  }

  private getHtml(webview: vscode.Webview): string {
    const nonce = createNonce();

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta
    http-equiv="Content-Security-Policy"
    content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';"
  />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI Provenance Search</title>
  <style>
    :root { color-scheme: light dark; }

    body {
      margin: 0;
      padding: 10px;
      color: var(--vscode-editor-foreground);
      background: var(--vscode-editor-background);
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      line-height: 1.45;
    }

    .panel {
      display: grid;
      gap: 10px;
    }

    .card {
      border: 1px solid var(--vscode-panel-border);
      border-radius: 6px;
      padding: 10px;
      background: color-mix(in srgb, var(--vscode-editor-background) 88%, var(--vscode-panel-border));
    }

    .card h3 {
      margin: 0 0 8px 0;
      font-size: 13px;
      font-weight: 600;
    }

    label {
      display: grid;
      gap: 4px;
      font-size: 12px;
      color: var(--vscode-descriptionForeground);
    }

    input,
    button,
    select {
      border: 1px solid var(--vscode-input-border);
      background: var(--vscode-input-background);
      color: var(--vscode-input-foreground);
      border-radius: 4px;
      padding: 6px;
      font: inherit;
    }

    button { cursor: pointer; }

    .grid {
      display: grid;
      gap: 8px;
      grid-template-columns: 1fr;
    }

    .row {
      display: grid;
      gap: 8px;
      grid-template-columns: 1fr 1fr;
    }

    .toolbar {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }

    .status {
      font-size: 12px;
      color: var(--vscode-descriptionForeground);
    }

    .results {
      display: grid;
      gap: 8px;
      max-height: calc(100vh - 260px);
      overflow: auto;
    }

    .result-item {
      border: 1px solid var(--vscode-panel-border);
      border-radius: 6px;
      padding: 8px;
      background: color-mix(in srgb, var(--vscode-editor-background) 90%, var(--vscode-panel-border));
      display: grid;
      gap: 6px;
    }

    .result-header {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: baseline;
      flex-wrap: wrap;
    }

    .result-uuid {
      font-weight: 600;
      font-family: var(--vscode-editor-font-family, var(--vscode-font-family));
      font-size: 12px;
      word-break: break-all;
    }

    .meta {
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    .snippet {
      margin: 0;
      font-size: 12px;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 120px;
      overflow: auto;
    }

    .muted { color: var(--vscode-descriptionForeground); }

    .inline {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      color: var(--vscode-descriptionForeground);
    }

    @media (max-width: 420px) {
      .row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="panel">
    <section class="card">
      <h3>Search Filters</h3>
      <div class="grid">
        <label>
          Prompt Keywords
          <input id="keywords" type="text" placeholder="e.g. auth middleware retry" />
        </label>
        <label>
          Model Used
          <input id="model" type="text" placeholder="e.g. gpt-4.1, claude-3.5" />
        </label>
        <div class="row">
          <label>
            Date From
            <input id="date-from" type="datetime-local" />
          </label>
          <label>
            Date To
            <input id="date-to" type="datetime-local" />
          </label>
        </div>
        <div class="inline">
          <input id="current-file-only" type="checkbox" />
          <label for="current-file-only" style="display:inline;cursor:pointer;">Current File Only</label>
        </div>
        <div class="inline muted" id="current-file-display">Current file: n/a</div>
        <div class="toolbar">
          <button id="search-btn">Search</button>
          <button id="refresh-file-btn">Refresh Current File</button>
        </div>
      </div>
    </section>

    <section class="card">
      <h3>Results</h3>
      <div class="status" id="status">Run a search to load provenance matches.</div>
      <div class="results" id="results"></div>
    </section>
  </div>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();

    const keywordsInput = document.getElementById('keywords');
    const modelInput = document.getElementById('model');
    const dateFromInput = document.getElementById('date-from');
    const dateToInput = document.getElementById('date-to');
    const currentFileOnlyInput = document.getElementById('current-file-only');
    const searchButton = document.getElementById('search-btn');
    const refreshFileButton = document.getElementById('refresh-file-btn');
    const statusView = document.getElementById('status');
    const resultsView = document.getElementById('results');
    const currentFileDisplay = document.getElementById('current-file-display');

    searchButton.addEventListener('click', () => {
      submitSearch();
    });

    refreshFileButton.addEventListener('click', () => {
      vscode.postMessage({ type: 'refreshCurrentFile' });
    });

    keywordsInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        submitSearch();
      }
    });

    window.addEventListener('message', (event) => {
      const message = event.data;
      if (!message || typeof message !== 'object') {
        return;
      }

      if (message.type === 'ready') {
        const hint = message.payload && message.payload.hint ? String(message.payload.hint) : 'Ready';
        const currentFile = message.payload && message.payload.currentFile ? String(message.payload.currentFile) : '';
        setStatus(hint);
        renderCurrentFile(currentFile);
        return;
      }

      if (message.type === 'loading') {
        const text = message.payload && message.payload.text ? String(message.payload.text) : 'Loading...';
        setStatus(text);
        return;
      }

      if (message.type === 'error') {
        const text = message.payload && message.payload.error ? String(message.payload.error) : 'Unknown error.';
        setStatus(text);
        return;
      }

      if (message.type === 'currentFile') {
        const currentFile = message.payload && message.payload.currentFile ? String(message.payload.currentFile) : '';
        renderCurrentFile(currentFile);
        return;
      }

      if (message.type === 'results') {
        const payload = message.payload || {};
        const currentFile = payload.currentFile ? String(payload.currentFile) : '';
        renderCurrentFile(currentFile);
        renderResults(Array.isArray(payload.results) ? payload.results : []);
        setStatus('Found ' + String(payload.count || 0) + ' result(s). Click one to open file and provenance.');
      }
    });

    function submitSearch() {
      vscode.postMessage({
        type: 'search',
        payload: {
          keywords: keywordsInput.value,
          model: modelInput.value,
          dateFrom: normalizeDateTime(dateFromInput.value),
          dateTo: normalizeDateTime(dateToInput.value),
          currentFileOnly: currentFileOnlyInput.checked
        }
      });
    }

    function normalizeDateTime(value) {
      if (!value || String(value).trim().length === 0) {
        return '';
      }

      const normalized = new Date(value);
      return Number.isNaN(normalized.getTime()) ? '' : normalized.toISOString();
    }

    function setStatus(text) {
      statusView.textContent = text;
    }

    function renderCurrentFile(filePath) {
      currentFileDisplay.textContent = 'Current file: ' + (filePath && filePath.length > 0 ? filePath : 'n/a');
    }

    function renderResults(results) {
      if (!Array.isArray(results) || results.length === 0) {
        resultsView.innerHTML = '<div class="muted">No matches found for the current filters.</div>';
        return;
      }

      resultsView.innerHTML = results
        .map((result, index) => {
          const uuid = result.uuid ? String(result.uuid) : '';
          const filePath = result.filePath ? String(result.filePath) : '';
          const score = typeof result.score === 'number' ? result.score.toFixed(4) : 'n/a';
          const model = result.model ? String(result.model) : 'n/a';
          const timestamp = result.timestampIso ? String(result.timestampIso) : 'n/a';
          const snippet = result.snippet ? String(result.snippet) : '';
          const canOpen = uuid.length > 0;
          const safeUuid = escapeHtml(uuid);
          const safeFile = escapeHtml(filePath);

          return (
            '<div class="result-item">' +
              '<div class="result-header">' +
                '<div class="result-uuid">' + safeUuid + '</div>' +
                '<button data-index="' + String(index) + '" data-uuid="' + safeUuid + '" data-file="' + safeFile + '" ' +
                  (canOpen ? '' : 'disabled') + '>Open</button>' +
              '</div>' +
              '<div class="meta">' +
                '<span>score=' + escapeHtml(score) + '</span>' +
                '<span>model=' + escapeHtml(model) + '</span>' +
                '<span>time=' + escapeHtml(timestamp) + '</span>' +
              '</div>' +
              '<div class="meta"><span>file=' + escapeHtml(filePath || 'n/a') + '</span></div>' +
              '<pre class="snippet">' + escapeHtml(snippet || '(no snippet)') + '</pre>' +
            '</div>'
          );
        })
        .join('');

      const buttons = resultsView.querySelectorAll('button[data-uuid]');
      buttons.forEach((button) => {
        button.addEventListener('click', () => {
          const uuid = button.getAttribute('data-uuid') || '';
          const filePath = button.getAttribute('data-file') || '';
          vscode.postMessage({
            type: 'openResult',
            payload: {
              uuid,
              filePath
            }
          });
        });
      });
    }

    function escapeHtml(text) {
      return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }
  </script>
</body>
</html>`;
  }
}

function sanitizeFilePath(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
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

function createNonce(): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let nonce = '';

  for (let index = 0; index < 32; index += 1) {
    nonce += alphabet.charAt(Math.floor(Math.random() * alphabet.length));
  }

  return nonce;
}
