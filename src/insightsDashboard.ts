import * as vscode from 'vscode';
import { getStoragePathForUri } from './storagePath';
import type { CodeReviewResult, ProvenanceReviewerService } from './reviewer';
import type {
  InsightsDashboardPayload,
  InsightsFilters,
  ProvenanceStorageService
} from './storage/StorageService';

type DashboardMessage =
  | {
      type: 'refresh';
      payload?: Partial<InsightsFilters>;
    }
  | {
      type: 'reviewCurrentFile';
    }
  | {
      type: 'configureReviewerKey';
    }
  | {
      type: 'exportMarkdown';
    };

export class InsightsDashboardViewProvider
  implements vscode.WebviewViewProvider, vscode.Disposable
{
  public static readonly viewType = 'aiInsertionDetector.insightsDashboard';

  private view: vscode.WebviewView | undefined;
  private readonly disposables: vscode.Disposable[] = [];
  private lastDashboard: InsightsDashboardPayload | undefined;
  private lastReview: CodeReviewResult | undefined;
  private currentFilters: InsightsFilters = {
    dateFrom: '',
    dateTo: '',
    currentFileOnly: false,
    currentFilePath: undefined
  };

  public constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly log: (message: string) => void,
    private readonly getStorageService: () => ProvenanceStorageService,
    private readonly reviewerService: ProvenanceReviewerService
  ) {}

  public resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this.extensionUri]
    };
    webviewView.webview.html = this.getHtml(webviewView.webview);

    this.disposables.push(
      webviewView.webview.onDidReceiveMessage((message: DashboardMessage) => {
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
        filters: this.currentFilters
      }
    });
    void this.refreshDashboard(this.currentFilters);
  }

  public async focus(): Promise<void> {
    await vscode.commands.executeCommand('workbench.view.explorer');

    try {
      await vscode.commands.executeCommand(InsightsDashboardViewProvider.viewType + '.focus');
    } catch {
      // Best-effort focus only.
    }

    if (this.view) {
      this.view.show?.(true);
      await this.refreshDashboard(this.currentFilters);
    }
  }

  public async showReviewResult(review: CodeReviewResult): Promise<void> {
    this.lastReview = review;

    if (this.view) {
      await this.postMessage({
        type: 'review',
        payload: review
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

  private async handleMessage(message: DashboardMessage): Promise<void> {
    if (message.type === 'refresh') {
      const nextFilters = this.mergeFilters(message.payload);
      await this.refreshDashboard(nextFilters);
      return;
    }

    if (message.type === 'reviewCurrentFile') {
      await this.runCurrentFileReview();
      return;
    }

    if (message.type === 'configureReviewerKey') {
      const stored = await this.reviewerService.configureApiKey();
      await this.postMessage({
        type: 'status',
        payload: {
          text: stored ? 'Reviewer API key stored.' : 'Reviewer API key was not updated.'
        }
      });
      return;
    }

    if (message.type === 'exportMarkdown') {
      await this.exportMarkdownReport();
    }
  }

  private mergeFilters(payload?: Partial<InsightsFilters>): InsightsFilters {
    const currentFileOnly = Boolean(payload?.currentFileOnly);

    this.currentFilters = {
      dateFrom: (payload?.dateFrom ?? this.currentFilters.dateFrom ?? '').trim(),
      dateTo: (payload?.dateTo ?? this.currentFilters.dateTo ?? '').trim(),
      currentFileOnly,
      currentFilePath: currentFileOnly ? this.getCurrentFilePath() : undefined
    };

    return this.currentFilters;
  }

  private async refreshDashboard(filters: InsightsFilters): Promise<void> {
    await this.postMessage({
      type: 'loading',
      payload: {
        text: 'Refreshing governance and analytics dashboard...'
      }
    });

    try {
      const dashboard = await this.getStorageService().getInsightsDashboard(
        {
          ...filters,
          currentFilePath: filters.currentFileOnly ? this.getCurrentFilePath() : undefined
        },
        vscode.window.activeTextEditor?.document.uri
      );

      this.lastDashboard = dashboard;
      await this.postMessage({
        type: 'dashboard',
        payload: {
          dashboard,
          currentFile: this.getCurrentFilePath() ?? '',
          filters
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

  private async runCurrentFileReview(): Promise<void> {
    await this.postMessage({
      type: 'loading',
      payload: {
        text: 'Running provenance-aware review for the current file...'
      }
    });

    try {
      const review = await this.reviewerService.reviewCurrentFile(
        this.getStorageService(),
        vscode.window.activeTextEditor?.document.uri
      );
      this.lastReview = review;

      await this.postMessage({
        type: 'review',
        payload: review
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

  private async exportMarkdownReport(): Promise<void> {
    if (!this.lastDashboard) {
      await this.postMessage({
        type: 'error',
        payload: {
          error: 'Load the dashboard before exporting a report.'
        }
      });
      return;
    }

    const defaultName =
      'ai-governance-report-' + new Date().toISOString().slice(0, 10) + '.md';

    const targetUri = await vscode.window.showSaveDialog({
      title: 'Export Governance Report',
      defaultUri: vscode.workspace.workspaceFolders?.[0]
        ? vscode.Uri.joinPath(vscode.workspace.workspaceFolders[0].uri, defaultName)
        : undefined,
      filters: {
        Markdown: ['md']
      }
    });

    if (!targetUri) {
      return;
    }

    const markdown = renderDashboardMarkdown(this.lastDashboard, this.lastReview);
    await vscode.workspace.fs.writeFile(targetUri, Buffer.from(markdown, 'utf8'));

    await this.postMessage({
      type: 'status',
      payload: {
        text: 'Governance report exported to ' + targetUri.fsPath
      }
    });
  }

  private getCurrentFilePath(): string | undefined {
    const activeEditor = vscode.window.activeTextEditor;
    if (!activeEditor || activeEditor.document.uri.scheme !== 'file') {
      return undefined;
    }

    return getStoragePathForUri(activeEditor.document.uri);
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
  <title>AI Governance Dashboard</title>
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

    .toolbar {
      display: grid;
      gap: 8px;
      margin-bottom: 10px;
    }

    .toolbar-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }

    .toolbar-row label {
      display: grid;
      gap: 4px;
      font-size: 12px;
      color: var(--vscode-descriptionForeground);
    }

    input, button {
      border: 1px solid var(--vscode-input-border);
      background: var(--vscode-input-background);
      color: var(--vscode-input-foreground);
      border-radius: 4px;
      padding: 6px;
      font: inherit;
    }

    button { cursor: pointer; }

    .status {
      font-size: 12px;
      color: var(--vscode-descriptionForeground);
      margin-bottom: 10px;
    }

    .grid {
      display: grid;
      gap: 10px;
    }

    .summary-grid {
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
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

    .metric {
      padding: 8px;
      border-radius: 6px;
      border: 1px solid var(--vscode-panel-border);
      background: color-mix(in srgb, var(--vscode-editor-background) 92%, var(--vscode-panel-border));
    }

    .metric .label {
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
    }

    .metric .value {
      font-size: 18px;
      font-weight: 700;
      margin-top: 2px;
    }

    .list {
      margin: 0;
      padding-left: 18px;
    }

    .list li {
      margin-bottom: 8px;
    }

    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      border: 1px solid var(--vscode-panel-border);
      margin-right: 6px;
    }

    .pill.pass { background: color-mix(in srgb, var(--vscode-testing-iconPassed) 20%, transparent); }
    .pill.warning { background: color-mix(in srgb, var(--vscode-testing-iconQueued) 22%, transparent); }
    .pill.fail { background: color-mix(in srgb, var(--vscode-testing-iconFailed) 20%, transparent); }
    .pill.low { background: color-mix(in srgb, var(--vscode-editorInfo-foreground) 18%, transparent); }
    .pill.medium { background: color-mix(in srgb, var(--vscode-testing-iconQueued) 22%, transparent); }
    .pill.high { background: color-mix(in srgb, var(--vscode-testing-iconFailed) 18%, transparent); }
    .pill.critical { background: color-mix(in srgb, var(--vscode-testing-iconFailed) 28%, transparent); }

    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: var(--vscode-editor-font-family, var(--vscode-font-family));
      font-size: 12px;
      background: color-mix(in srgb, var(--vscode-editor-background) 92%, var(--vscode-panel-border));
      border: 1px solid var(--vscode-panel-border);
      padding: 8px;
      border-radius: 4px;
      overflow: auto;
      max-height: 280px;
    }

    .table {
      display: grid;
      gap: 8px;
    }

    .row {
      border: 1px solid var(--vscode-panel-border);
      border-radius: 6px;
      padding: 8px;
      background: color-mix(in srgb, var(--vscode-editor-background) 92%, var(--vscode-panel-border));
    }

    .row .title {
      font-weight: 600;
      margin-bottom: 4px;
      word-break: break-word;
    }

    .meta {
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 6px;
    }

    .muted {
      color: var(--vscode-descriptionForeground);
    }

    @media (max-width: 420px) {
      .summary-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="toolbar">
    <div class="toolbar-row">
      <label>
        Date From
        <input id="date-from" type="datetime-local" />
      </label>
      <label>
        Date To
        <input id="date-to" type="datetime-local" />
      </label>
      <label style="display:flex;gap:6px;align-items:center;margin-top:18px;">
        <input id="current-file-only" type="checkbox" />
        <span>Current File Only</span>
      </label>
    </div>
    <div class="toolbar-row">
      <button id="refresh-btn">Refresh</button>
      <button id="review-btn">Review Current File</button>
      <button id="config-btn">Configure Reviewer Key</button>
      <button id="export-btn">Export Markdown</button>
    </div>
    <div class="muted" id="current-file">Current file: n/a</div>
  </div>

  <div class="status" id="status">Loading governance dashboard...</div>

  <div class="grid">
    <section class="card">
      <h3>Governance Summary</h3>
      <div class="summary-grid" id="summary-grid"></div>
    </section>

    <section class="card">
      <h3>Compliance Controls</h3>
      <div id="controls" class="table"></div>
    </section>

    <section class="card">
      <h3>High-Risk Records</h3>
      <div id="high-risk" class="table"></div>
    </section>

    <section class="card">
      <h3>File Hotspots</h3>
      <div id="hotspots" class="table"></div>
    </section>

    <section class="card">
      <h3>Model Analytics</h3>
      <div id="models" class="table"></div>
    </section>

    <section class="card">
      <h3>Risk Trends</h3>
      <div id="trends" class="table"></div>
    </section>

    <section class="card">
      <h3>Agent Sessions</h3>
      <div id="sessions" class="table"></div>
    </section>

    <section class="card">
      <h3>Team Members</h3>
      <div id="members" class="table"></div>
    </section>

    <section class="card">
      <h3>Reviewer Output</h3>
      <pre id="review-output">Run a provenance-aware review for the current file.</pre>
    </section>
  </div>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();

    const summaryGrid = document.getElementById('summary-grid');
    const controlsView = document.getElementById('controls');
    const highRiskView = document.getElementById('high-risk');
    const hotspotsView = document.getElementById('hotspots');
    const modelsView = document.getElementById('models');
    const trendsView = document.getElementById('trends');
    const sessionsView = document.getElementById('sessions');
    const membersView = document.getElementById('members');
    const reviewOutput = document.getElementById('review-output');
    const statusView = document.getElementById('status');
    const dateFromInput = document.getElementById('date-from');
    const dateToInput = document.getElementById('date-to');
    const currentFileOnlyInput = document.getElementById('current-file-only');
    const currentFileView = document.getElementById('current-file');

    document.getElementById('refresh-btn').addEventListener('click', () => {
      vscode.postMessage({
        type: 'refresh',
        payload: collectFilters()
      });
    });

    document.getElementById('review-btn').addEventListener('click', () => {
      vscode.postMessage({ type: 'reviewCurrentFile' });
    });

    document.getElementById('config-btn').addEventListener('click', () => {
      vscode.postMessage({ type: 'configureReviewerKey' });
    });

    document.getElementById('export-btn').addEventListener('click', () => {
      vscode.postMessage({ type: 'exportMarkdown' });
    });

    window.addEventListener('message', (event) => {
      const message = event.data;
      if (!message || typeof message !== 'object') {
        return;
      }

      if (message.type === 'ready') {
        const payload = message.payload || {};
        renderCurrentFile(payload.currentFile || '');
        hydrateFilters(payload.filters || {});
        return;
      }

      if (message.type === 'loading') {
        setStatus(message.payload && message.payload.text ? String(message.payload.text) : 'Loading...');
        return;
      }

      if (message.type === 'status') {
        setStatus(message.payload && message.payload.text ? String(message.payload.text) : 'Ready');
        return;
      }

      if (message.type === 'error') {
        const errorText = message.payload && message.payload.error ? String(message.payload.error) : 'Unknown error.';
        setStatus(errorText);
        return;
      }

      if (message.type === 'dashboard') {
        const payload = message.payload || {};
        renderDashboard(payload.dashboard || null);
        renderCurrentFile(payload.currentFile || '');
        hydrateFilters(payload.filters || {});
        return;
      }

      if (message.type === 'review') {
        renderReview(message.payload || null);
      }
    });

    function collectFilters() {
      return {
        dateFrom: normalizeDate(dateFromInput.value),
        dateTo: normalizeDate(dateToInput.value),
        currentFileOnly: currentFileOnlyInput.checked
      };
    }

    function hydrateFilters(filters) {
      if (filters.dateFrom) {
        dateFromInput.value = toDateInputValue(filters.dateFrom);
      }

      if (filters.dateTo) {
        dateToInput.value = toDateInputValue(filters.dateTo);
      }

      currentFileOnlyInput.checked = Boolean(filters.currentFileOnly);
    }

    function renderCurrentFile(filePath) {
      currentFileView.textContent = 'Current file: ' + (filePath && filePath.length > 0 ? filePath : 'n/a');
    }

    function renderDashboard(dashboard) {
      if (!dashboard || typeof dashboard !== 'object') {
        setStatus('No dashboard data available.');
        return;
      }

      const summary = dashboard.summary || {};
      const memberStats = Array.isArray(dashboard.memberStats) ? dashboard.memberStats : [];
      const adminCount = memberStats.filter((item) => String(item.role || '').toLowerCase() === 'admin').length;
      const activeContributors = memberStats.filter((item) => Number(item.recordCount || 0) > 0).length;
      const summaryItems = [
        ['Records', summary.totalRecords || 0],
        ['Prompt Capture', percent(summary.promptCaptureRate)],
        ['Avg Risk', summary.avgRiskScore || 0],
        ['High Risk', summary.highRiskRecords || 0],
        ['Critical', summary.criticalRecords || 0],
        ['Files', summary.uniqueFiles || 0],
        ['Models', summary.uniqueModels || 0],
        ['Agent Sessions', summary.uniqueAgentSessions || 0],
        ['Agentic Records', summary.agenticRecords || 0],
        ['AI Net Lines', summary.totalNetAddedLines || 0],
        ['Team Members', memberStats.length],
        ['Admins', adminCount],
        ['Active Contributors', activeContributors]
      ];

      summaryGrid.innerHTML = summaryItems
        .map((item) => (
          '<div class="metric"><div class="label">' + escapeHtml(String(item[0])) + '</div><div class="value">' +
          escapeHtml(String(item[1])) + '</div></div>'
        ))
        .join('');

      renderRows(controlsView, dashboard.complianceControls || [], (item) => {
        return (
          '<div class="row">' +
            '<div class="title"><span class="pill ' + escapeHtml(item.status || 'warning') + '">' +
            escapeHtml(item.status || 'warning') + '</span>' + escapeHtml(item.title || 'Control') + '</div>' +
            '<div class="meta"><span>metric=' + escapeHtml(String(item.metric || 'n/a')) + '</span></div>' +
            '<div>' + escapeHtml(item.summary || '') + '</div>' +
          '</div>'
        );
      }, 'No compliance controls available.');

      renderRows(highRiskView, dashboard.highRiskRecords || [], (item) => {
        return (
          '<div class="row">' +
            '<div class="title">' + escapeHtml(item.filePath || item.uuid || 'record') + '</div>' +
            '<div class="meta">' +
              '<span class="pill ' + escapeHtml(item.riskLevel || 'medium') + '">' + escapeHtml(item.riskLevel || 'medium') + '</span>' +
              '<span>risk=' + escapeHtml(String(item.riskScore || 0)) + '</span>' +
              '<span>model=' + escapeHtml(item.model || 'n/a') + '</span>' +
              '<span>time=' + escapeHtml(item.timestampIso || 'n/a') + '</span>' +
            '</div>' +
            '<div>' + escapeHtml(item.summary || '') + '</div>' +
            '<div class="meta"><span>uuid=' + escapeHtml(item.uuid || '') + '</span><span>tool=' + escapeHtml(item.toolName || 'n/a') + '</span><span>adapter=' + escapeHtml(item.adapterName || 'n/a') + '</span><span>confidence=' + escapeHtml(percent(item.adapterConfidence || 0)) + '</span><span>capture=' + escapeHtml(item.captureStatus || 'n/a') + '</span></div>' +
          '</div>'
        );
      }, 'No high-risk records in the selected scope.');

      renderRows(hotspotsView, dashboard.hotspots || [], (item) => {
        return (
          '<div class="row">' +
            '<div class="title">' + escapeHtml(item.filePath || 'n/a') + '</div>' +
            '<div class="meta"><span>records=' + escapeHtml(String(item.recordCount || 0)) + '</span><span>high-risk=' + escapeHtml(String(item.highRiskCount || 0)) + '</span><span>avg-risk=' + escapeHtml(String(item.avgRiskScore || 0)) + '</span></div>' +
            '<div class="muted">latest=' + escapeHtml(item.latestTimestampIso || 'n/a') + '</div>' +
          '</div>'
        );
      }, 'No file hotspots available.');

      renderRows(modelsView, dashboard.modelAnalytics || [], (item) => {
        return (
          '<div class="row">' +
            '<div class="title">' + escapeHtml(item.model || 'unknown') + '</div>' +
            '<div class="meta"><span>records=' + escapeHtml(String(item.recordCount || 0)) + '</span><span>capture=' + escapeHtml(percent(item.promptCaptureRate)) + '</span><span>avg-risk=' + escapeHtml(String(item.avgRiskScore || 0)) + '</span><span>high-risk=' + escapeHtml(String(item.highRiskCount || 0)) + '</span></div>' +
          '</div>'
        );
      }, 'No model analytics available.');

      renderRows(trendsView, dashboard.riskTrends || [], (item) => {
        return (
          '<div class="row">' +
            '<div class="title">' + escapeHtml(item.bucketLabel || 'unknown') + '</div>' +
            '<div class="meta"><span>records=' + escapeHtml(String(item.recordCount || 0)) + '</span><span>high-risk=' + escapeHtml(String(item.highRiskCount || 0)) + '</span><span>avg-risk=' + escapeHtml(String(item.avgRiskScore || 0)) + '</span><span>capture=' + escapeHtml(percent(item.promptCaptureRate)) + '</span></div>' +
          '</div>'
        );
      }, 'No trend data available.');

      renderRows(sessionsView, dashboard.agentSessions || [], (item) => {
        const files = Array.isArray(item.files) ? item.files.join(', ') : '';
        const evidence = Array.isArray(item.evidence) ? item.evidence.join(' | ') : '';
        return (
          '<div class="row">' +
            '<div class="title">' + escapeHtml(item.toolName || item.provider || 'Unknown Session') + '</div>' +
            '<div class="meta"><span>kind=' + escapeHtml(item.sessionKind || 'unknown') + '</span><span>model=' + escapeHtml(item.modelName || 'n/a') + '</span><span>records=' + escapeHtml(String(item.recordCount || 0)) + '</span><span>high-risk=' + escapeHtml(String(item.highRiskCount || 0)) + '</span><span>adapter=' + escapeHtml(item.adapterName || 'n/a') + '</span><span>confidence=' + escapeHtml(percent(item.adapterConfidence || 0)) + '</span></div>' +
            '<div class="muted">sessionId=' + escapeHtml(item.sessionId || 'n/a') + ' | runId=' + escapeHtml(item.runId || 'n/a') + ' | conversationId=' + escapeHtml(item.conversationId || 'n/a') + '</div>' +
            '<div class="muted">started=' + escapeHtml(item.startedAtIso || 'n/a') + ' | ended=' + escapeHtml(item.endedAtIso || 'n/a') + '</div>' +
            '<div class="muted">files=' + escapeHtml(files || 'n/a') + '</div>' +
            '<div class="muted">evidence=' + escapeHtml(evidence || 'n/a') + '</div>' +
          '</div>'
        );
      }, 'No agent sessions detected in the selected scope.');

      renderRows(membersView, memberStats, (item) => {
        return (
          '<div class="row">' +
            '<div class="title">' + escapeHtml(item.username || 'Unknown Member') + '</div>' +
            '<div class="meta">' +
              '<span class="pill ' + escapeHtml(String(item.role || 'member')) + '">' + escapeHtml(String(item.role || 'member')) + '</span>' +
              '<span>records=' + escapeHtml(String(item.recordCount || 0)) + '</span>' +
              '<span>joined=' + escapeHtml(item.joinedAtIso || 'n/a') + '</span>' +
            '</div>' +
            '<div class="muted">id=' + escapeHtml(item.id || 'n/a') + '</div>' +
          '</div>'
        );
      }, 'No team member data available.');

      const warnings = Array.isArray(dashboard.warnings) ? dashboard.warnings : [];
      setStatus(
        'Dashboard refreshed at ' + escapeHtml(dashboard.generatedAtIso || 'unknown') +
        (warnings.length > 0 ? ' | ' + warnings.join(' ') : '')
      );
    }

    function renderReview(review) {
      if (!review || typeof review !== 'object') {
        reviewOutput.textContent = 'No reviewer output available.';
        return;
      }

      reviewOutput.textContent = JSON.stringify(review, null, 2);
      setStatus('Review completed for ' + (review.filePath || 'current file') + '.');
    }

    function renderRows(target, rows, renderRow, emptyText) {
      if (!Array.isArray(rows) || rows.length === 0) {
        target.innerHTML = '<div class="muted">' + escapeHtml(emptyText) + '</div>';
        return;
      }

      target.innerHTML = rows.map((row) => renderRow(row)).join('');
    }

    function normalizeDate(value) {
      if (!value || String(value).trim().length === 0) {
        return '';
      }

      const parsed = new Date(value);
      return Number.isNaN(parsed.getTime()) ? '' : parsed.toISOString();
    }

    function toDateInputValue(value) {
      if (!value) {
        return '';
      }

      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) {
        return '';
      }

      const offset = parsed.getTimezoneOffset();
      const normalized = new Date(parsed.getTime() - offset * 60000);
      return normalized.toISOString().slice(0, 16);
    }

    function percent(value) {
      const numeric = typeof value === 'number' ? value : Number(value || 0);
      return (numeric * 100).toFixed(1) + '%';
    }

    function setStatus(text) {
      statusView.textContent = text;
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

function renderDashboardMarkdown(
  dashboard: InsightsDashboardPayload,
  review?: CodeReviewResult
): string {
  const lines: string[] = [];
  const memberStats = Array.isArray(dashboard.memberStats) ? dashboard.memberStats : [];
  const adminCount = memberStats.filter((item) => String(item.role || '').toLowerCase() === 'admin').length;
  const activeContributors = memberStats.filter((item) => Number(item.recordCount || 0) > 0).length;

  lines.push('# AI Governance Dashboard Report');
  lines.push('');
  lines.push('Generated: ' + dashboard.generatedAtIso);
  lines.push('Mode: ' + dashboard.mode);
  lines.push('');
  lines.push('## Summary');
  lines.push('');
  lines.push('- Total records: ' + String(dashboard.summary.totalRecords));
  lines.push('- Prompt capture rate: ' + formatPercentage(dashboard.summary.promptCaptureRate));
  lines.push('- Average risk score: ' + String(dashboard.summary.avgRiskScore));
  lines.push('- High-risk records: ' + String(dashboard.summary.highRiskRecords));
  lines.push('- Critical records: ' + String(dashboard.summary.criticalRecords));
  lines.push('- Unique files: ' + String(dashboard.summary.uniqueFiles));
  lines.push('- Unique models: ' + String(dashboard.summary.uniqueModels));
  lines.push('- Unique agent sessions: ' + String(dashboard.summary.uniqueAgentSessions));
  lines.push('- Agentic records: ' + String(dashboard.summary.agenticRecords));
  lines.push('- AI net added lines: ' + String(dashboard.summary.totalNetAddedLines));
  lines.push('- Team members: ' + String(memberStats.length));
  lines.push('- Admins: ' + String(adminCount));
  lines.push('- Active contributors: ' + String(activeContributors));
  lines.push('');
  lines.push('## Compliance Controls');
  lines.push('');

  for (const control of dashboard.complianceControls) {
    lines.push(
      '- ' +
        control.title +
        ': ' +
        control.status.toUpperCase() +
        ' (' +
        control.metric +
        ') - ' +
        control.summary
    );
  }

  lines.push('');
  lines.push('## High-Risk Records');
  lines.push('');
  for (const record of dashboard.highRiskRecords) {
    lines.push(
      '- ' +
        record.filePath +
        ' [' +
        record.riskLevel.toUpperCase() +
        ' ' +
        String(record.riskScore) +
        '] ' +
        record.summary +
        ' adapter=' +
        String(record.adapterName || 'n/a') +
        ' confidence=' +
        String(record.adapterConfidence ?? 0) +
        ' (uuid=' +
        record.uuid +
        ')'
    );
  }

  lines.push('');
  lines.push('## File Hotspots');
  lines.push('');
  for (const hotspot of dashboard.hotspots) {
    lines.push(
      '- ' +
        hotspot.filePath +
        ': records=' +
        String(hotspot.recordCount) +
        ', highRisk=' +
        String(hotspot.highRiskCount) +
        ', avgRisk=' +
        String(hotspot.avgRiskScore)
    );
  }

  lines.push('');
  lines.push('## Model Analytics');
  lines.push('');
  for (const model of dashboard.modelAnalytics) {
    lines.push(
      '- ' +
        model.model +
        ': records=' +
        String(model.recordCount) +
        ', promptCapture=' +
        formatPercentage(model.promptCaptureRate) +
        ', avgRisk=' +
        String(model.avgRiskScore) +
        ', highRisk=' +
        String(model.highRiskCount)
    );
  }

  lines.push('');
  lines.push('## Agent Sessions');
  lines.push('');
  for (const session of dashboard.agentSessions) {
    lines.push(
      '- ' +
        (session.toolName ?? session.provider ?? 'Unknown') +
        ': ' +
        session.sessionKind +
        ', adapter=' +
        String(session.adapterName || 'n/a') +
        ', confidence=' +
        String(session.adapterConfidence ?? 0) +
        ', records=' +
        String(session.recordCount) +
        ', highRisk=' +
        String(session.highRiskCount) +
        ', sessionId=' +
        String(session.sessionId) +
        ', files=' +
        session.files.join(', ')
    );
  }

  lines.push('');
  lines.push('## Team Members');
  lines.push('');
  if (Array.isArray(dashboard.memberStats) && dashboard.memberStats.length > 0) {
    for (const member of dashboard.memberStats) {
      lines.push(
        '- ' +
          member.username +
          ' [' +
          member.role.toUpperCase() +
          '] records=' +
          String(member.recordCount) +
          ' joined=' +
          String(member.joinedAtIso) +
          ' (id=' +
          member.id +
          ')'
      );
    }
  } else {
    lines.push('- No team member data available.');
  }

  if (review) {
    lines.push('');
    lines.push('## Reviewer Output');
    lines.push('');
    lines.push('- Source: ' + review.source);
    lines.push('- Model: ' + review.model);
    lines.push('- File: ' + review.filePath);
    lines.push('- Summary: ' + review.summary);
    lines.push('');
    for (const finding of review.findings) {
      lines.push(
        '- ' +
          finding.severity.toUpperCase() +
          ': ' +
          finding.title +
          ' - ' +
          finding.detail +
          ' Suggestion: ' +
          finding.suggestion
      );
    }
  }

  return lines.join('\n');
}

function formatPercentage(value: number): string {
  return (value * 100).toFixed(1) + '%';
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
