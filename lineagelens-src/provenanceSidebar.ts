import * as vscode from 'vscode';
import {
  LoadedProvenancePayload,
  ProvenanceStorageService
} from './storage/StorageService';

const UUID_PATTERN =
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/i;

type WebviewMessage =
  | {
      type: 'loadUuid';
      uuid?: string;
    }
  | {
      type: 'refresh';
    }
  | {
      type: 'explain';
    };

export class ProvenanceSidebarViewProvider implements vscode.WebviewViewProvider, vscode.Disposable {
  public static readonly viewType = 'aiInsertionDetector.provenanceSidebar';

  private view: vscode.WebviewView | undefined;
  private readonly disposables: vscode.Disposable[] = [];
  private currentUuid: string | undefined;
  private pendingUuid: string | undefined;

  public constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly log: (message: string) => void,
    private readonly getStorageService: () => ProvenanceStorageService
  ) {}

  public resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this.extensionUri]
    };
    webviewView.webview.html = this.getHtml(webviewView.webview);

    this.disposables.push(
      webviewView.webview.onDidReceiveMessage((message: WebviewMessage) => {
        void this.handleWebviewMessage(message);
      }),
      webviewView.onDidDispose(() => {
        this.view = undefined;
      })
    );

    if (this.pendingUuid) {
      const pendingUuid = this.pendingUuid;
      this.pendingUuid = undefined;
      void this.loadAndRender(pendingUuid);
      return;
    }

    void this.postMessage({
      type: 'idle',
      payload: {
        hint:
          this.getStorageService().mode === 'local'
            ? 'Local mode active. Select a UUID and run Show Provenance.'
            : 'Backend mode active. Select a UUID and run Show Provenance.'
      }
    });
  }

  public async showProvenance(uuidInput: string): Promise<void> {
    const uuid = extractUuidFromText(uuidInput);
    if (!uuid) {
      throw new Error('No valid UUID found in the provided value.');
    }

    this.currentUuid = uuid;
    this.pendingUuid = uuid;

    await vscode.commands.executeCommand('workbench.view.explorer');

    try {
      await vscode.commands.executeCommand(ProvenanceSidebarViewProvider.viewType + '.focus');
    } catch {
      // Focus command is best-effort and may not always be available.
    }

    if (this.view) {
      this.view.show?.(true);
      await this.loadAndRender(uuid);
    }
  }

  public dispose(): void {
    for (const disposable of this.disposables) {
      disposable.dispose();
    }

    this.disposables.length = 0;
    this.view = undefined;
  }

  private async handleWebviewMessage(message: WebviewMessage): Promise<void> {
    if (message.type === 'loadUuid') {
      if (!message.uuid || message.uuid.trim().length === 0) {
        await this.postMessage({
          type: 'error',
          payload: {
            error: 'Please provide a UUID to load provenance.'
          }
        });
        return;
      }

      try {
        await this.showProvenance(message.uuid);
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

    if (message.type === 'refresh') {
      if (!this.currentUuid) {
        await this.postMessage({
          type: 'error',
          payload: {
            error: 'No UUID loaded yet.'
          }
        });
        return;
      }

      await this.loadAndRender(this.currentUuid);
      return;
    }

    if (message.type === 'explain') {
      if (!this.currentUuid) {
        await this.postMessage({
          type: 'error',
          payload: {
            error: 'No UUID loaded yet.'
          }
        });
        return;
      }

      await this.refreshExplanationOnly(this.currentUuid);
    }
  }

  private async loadAndRender(uuid: string): Promise<void> {
    await this.postMessage({
      type: 'loading',
      payload: {
        uuid
      }
    });

    try {
      const payload = await this.loadProvenancePayload(uuid);
      await this.postMessage({
        type: 'record',
        payload
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

  private async refreshExplanationOnly(uuid: string): Promise<void> {
    try {
      const payload = await this.loadProvenancePayload(uuid);

      await this.postMessage({
        type: 'explanation',
        payload: {
          explanation: payload.explanation,
          explanationError: payload.explanationError
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

  private async loadProvenancePayload(uuid: string): Promise<LoadedProvenancePayload> {
    return await this.getStorageService().getProvenanceByUuid(
      uuid,
      vscode.window.activeTextEditor?.document.uri
    );
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
  <title>AI Provenance</title>
  <style>
    :root {
      color-scheme: light dark;
    }

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
      grid-template-columns: 1fr auto auto auto;
      gap: 6px;
      align-items: center;
      margin-bottom: 10px;
    }

    .toolbar input {
      width: 100%;
      min-width: 0;
    }

    input,
    button,
    select,
    textarea {
      border: 1px solid var(--vscode-input-border);
      background: var(--vscode-input-background);
      color: var(--vscode-input-foreground);
      border-radius: 4px;
      padding: 6px;
      font: inherit;
    }

    button {
      cursor: pointer;
    }

    .status {
      margin: 8px 0 10px 0;
      font-size: 12px;
      color: var(--vscode-descriptionForeground);
    }

    .grid {
      display: grid;
      gap: 10px;
    }

    .card {
      border: 1px solid var(--vscode-panel-border);
      border-radius: 6px;
      padding: 10px;
      background: color-mix(in srgb, var(--vscode-editor-background) 86%, var(--vscode-panel-border));
    }

    .card h3 {
      margin: 0 0 8px 0;
      font-size: 13px;
      font-weight: 600;
    }

    .kvs {
      display: grid;
      gap: 6px;
      grid-template-columns: auto 1fr;
      align-items: start;
      font-size: 12px;
    }

    .kvs dt {
      color: var(--vscode-descriptionForeground);
      font-weight: 600;
      margin: 0;
    }

    .kvs dd {
      margin: 0;
      word-break: break-word;
    }

    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: var(--vscode-editor-font-family, var(--vscode-font-family));
      font-size: 12px;
      background: color-mix(in srgb, var(--vscode-editor-background) 90%, var(--vscode-panel-border));
      border: 1px solid var(--vscode-panel-border);
      padding: 8px;
      border-radius: 4px;
      overflow: auto;
      max-height: 260px;
    }

    details summary {
      cursor: pointer;
      font-weight: 600;
      margin-bottom: 6px;
    }

    .list {
      margin: 0;
      padding-left: 18px;
    }

    .list li {
      margin-bottom: 6px;
    }

    .muted {
      color: var(--vscode-descriptionForeground);
    }

    .diff-set {
      display: grid;
      gap: 8px;
    }

    .diff-block {
      border: 1px solid var(--vscode-panel-border);
      border-radius: 4px;
      overflow: hidden;
    }

    .diff-header {
      padding: 6px 8px;
      font-size: 12px;
      background: color-mix(in srgb, var(--vscode-editor-background) 86%, var(--vscode-panel-border));
      border-bottom: 1px solid var(--vscode-panel-border);
    }

    .diff-line {
      display: grid;
      grid-template-columns: 18px 1fr;
      gap: 8px;
      padding: 1px 8px;
      font-family: var(--vscode-editor-font-family, var(--vscode-font-family));
      font-size: 12px;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .diff-line .prefix {
      color: var(--vscode-descriptionForeground);
      user-select: none;
    }

    .diff-line.context {
      background: transparent;
    }

    .diff-line.added {
      background: color-mix(in srgb, var(--vscode-editorGutter-addedBackground) 35%, transparent);
    }

    .diff-line.removed {
      background: color-mix(in srgb, var(--vscode-editorGutter-deletedBackground) 35%, transparent);
    }

    .mono {
      font-family: var(--vscode-editor-font-family, var(--vscode-font-family));
    }
  </style>
</head>
<body>
  <div class="toolbar">
    <input id="uuid-input" type="text" placeholder="Provenance UUID" />
    <button id="load-btn">Load</button>
    <button id="refresh-btn">Refresh</button>
    <button id="explain-btn">Explain</button>
  </div>

  <div id="status" class="status">Select code with a UUID and run Show Provenance.</div>

  <div class="grid">
    <section class="card">
      <h3>Record Overview</h3>
      <dl id="overview" class="kvs"></dl>
    </section>

    <section class="card">
      <details id="prompt-details">
        <summary>Original Prompt</summary>
        <pre id="prompt-view"></pre>
      </details>
    </section>

    <section class="card">
      <h3>Model + Parameters</h3>
      <pre id="model-params"></pre>
    </section>

    <section class="card">
      <h3>LLM Explanation</h3>
      <pre id="explanation-view"></pre>
    </section>

    <section class="card">
      <h3>Evolution Chain</h3>
      <ol id="evolution-list" class="list"></ol>
    </section>

    <section class="card">
      <h3>Inline Diffs Between Versions</h3>
      <div id="diffs" class="diff-set"></div>
    </section>

    <section class="card">
      <h3>Context Snapshot</h3>
      <pre id="context-view"></pre>
    </section>
  </div>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();

    const state = {
      uuid: '',
      record: null,
      explanation: null,
      explanationError: null
    };

    const uuidInput = document.getElementById('uuid-input');
    const loadButton = document.getElementById('load-btn');
    const refreshButton = document.getElementById('refresh-btn');
    const explainButton = document.getElementById('explain-btn');
    const statusView = document.getElementById('status');
    const overviewView = document.getElementById('overview');
    const promptView = document.getElementById('prompt-view');
    const modelParamsView = document.getElementById('model-params');
    const explanationView = document.getElementById('explanation-view');
    const evolutionList = document.getElementById('evolution-list');
    const diffsView = document.getElementById('diffs');
    const contextView = document.getElementById('context-view');

    loadButton.addEventListener('click', () => {
      vscode.postMessage({
        type: 'loadUuid',
        uuid: uuidInput.value
      });
    });

    refreshButton.addEventListener('click', () => {
      vscode.postMessage({ type: 'refresh' });
    });

    explainButton.addEventListener('click', () => {
      vscode.postMessage({ type: 'explain' });
    });

    uuidInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        vscode.postMessage({
          type: 'loadUuid',
          uuid: uuidInput.value
        });
      }
    });

    window.addEventListener('message', (event) => {
      const message = event.data;
      if (!message || typeof message !== 'object') {
        return;
      }

      if (message.type === 'idle') {
        setStatus(message.payload && message.payload.hint ? String(message.payload.hint) : 'Ready');
        return;
      }

      if (message.type === 'loading') {
        const uuid = message.payload && message.payload.uuid ? String(message.payload.uuid) : 'unknown';
        setStatus('Loading provenance for ' + uuid + '...');
        return;
      }

      if (message.type === 'error') {
        const text = message.payload && message.payload.error ? String(message.payload.error) : 'Unknown error.';
        setStatus(text);
        return;
      }

      if (message.type === 'record') {
        renderRecord(message.payload || {});
        return;
      }

      if (message.type === 'explanation') {
        const explanation = message.payload ? message.payload.explanation : null;
        const explanationError = message.payload ? message.payload.explanationError : null;
        updateExplanation(explanation, explanationError);
      }
    });

    function setStatus(text) {
      statusView.textContent = text;
    }

    function renderRecord(payload) {
      state.uuid = payload.uuid || '';
      state.record = payload.record || {};
      state.explanation = payload.explanation || null;
      state.explanationError = payload.explanationError || null;

      if (state.uuid) {
        uuidInput.value = state.uuid;
      }

      const record = state.record;
      const modelValue = pickFirst(record, [
        ['prompt', 'modelName'],
        ['provenance', 'modelName'],
        ['modelName'],
        ['model']
      ]);

      const parametersValue = pickFirst(record, [
        ['prompt', 'parameters'],
        ['provenance', 'parameters'],
        ['parameters']
      ]);

      const timestampValue = pickFirst(record, [
        ['timestampIso'],
        ['insertionTimestampIso'],
        ['prompt', 'timestampIso'],
        ['provenance', 'proxyResponseTimestampIso']
      ]);

      const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];

      const overviewRows = [
        ['UUID', state.uuid || 'n/a'],
        ['Timestamp', stringifyValue(timestampValue)],
        ['Model', stringifyValue(modelValue)],
        ['Mode', payload.mode || 'n/a'],
        ['Source', payload.sourceLabel || 'n/a'],
        ['Fetched', payload.fetchedAtIso || 'n/a']
      ];

      overviewView.innerHTML = overviewRows
        .map((row) => '<dt>' + escapeHtml(row[0]) + '</dt><dd>' + escapeHtml(row[1]) + '</dd>')
        .join('');

      const promptMessages = pickFirst(record, [
        ['prompt', 'fullMessages'],
        ['provenance', 'fullPromptMessages'],
        ['messages']
      ]);
      promptView.textContent = toPrettyString(promptMessages);

      modelParamsView.textContent = toPrettyString({
        model: modelValue,
        parameters: parametersValue
      });

      const contextSnapshotValue = pickFirst(record, [['contextSnapshot']]);
      contextView.textContent = toPrettyString(contextSnapshotValue);

      const versions = extractVersions(record);
      renderEvolution(versions);
      renderDiffs(versions);
      updateExplanation(state.explanation, state.explanationError);

      setStatus(
        'Loaded provenance ' +
          state.uuid +
          (warnings.length > 0 ? ' | ' + warnings.join(' ') : '')
      );
    }

    function updateExplanation(explanation, explanationError) {
      if (explanation && String(explanation).trim().length > 0) {
        explanationView.textContent = String(explanation);
        return;
      }

      if (explanationError && String(explanationError).trim().length > 0) {
        explanationView.textContent = 'Explanation unavailable: ' + String(explanationError);
        return;
      }

      explanationView.textContent = 'No explanation returned yet.';
    }

    function renderEvolution(versions) {
      if (!Array.isArray(versions) || versions.length === 0) {
        evolutionList.innerHTML = '<li class="muted">No evolution chain found in this record.</li>';
        return;
      }

      evolutionList.innerHTML = versions
        .map((version, index) => {
          const id = pickFirst(version, [['versionId'], ['id'], ['uuid']]);
          const relationship = pickFirst(version, [['relationshipType'], ['relation'], ['edgeType']]);
          const commitHash = pickFirst(version, [['commitHash'], ['commit']]);
          const timestamp = pickFirst(version, [['timestampIso'], ['createdAtIso'], ['updatedAtIso']]);
          const filePath = pickFirst(version, [['filePath'], ['path']]);

          const text =
            '#' +
            String(index + 1) +
            ' ' +
            stringifyValue(id) +
            (relationship ? ' (' + stringifyValue(relationship) + ')' : '') +
            (commitHash ? ' commit=' + stringifyValue(commitHash) : '') +
            (timestamp ? ' time=' + stringifyValue(timestamp) : '') +
            (filePath ? ' file=' + stringifyValue(filePath) : '');

          return '<li>' + escapeHtml(text) + '</li>';
        })
        .join('');
    }

    function renderDiffs(versions) {
      if (!Array.isArray(versions) || versions.length < 2) {
        diffsView.innerHTML = '<div class="muted">At least two versions are needed to render diffs.</div>';
        return;
      }

      const diffBlocks = [];

      for (let index = 1; index < versions.length; index += 1) {
        const previous = versions[index - 1];
        const current = versions[index];

        const previousCode = extractVersionCode(previous);
        const currentCode = extractVersionCode(current);

        if (!previousCode && !currentCode) {
          continue;
        }

        const previousLabel = stringifyValue(pickFirst(previous, [['versionId'], ['id'], ['uuid']])) ||
          'version-' + String(index);
        const currentLabel = stringifyValue(pickFirst(current, [['versionId'], ['id'], ['uuid']])) ||
          'version-' + String(index + 1);

        const lines = computeInlineDiff(previousCode, currentCode)
          .map((line) => {
            return (
              '<div class="diff-line ' +
              escapeHtml(line.kind) +
              '"><span class="prefix">' +
              escapeHtml(line.prefix) +
              '</span><span>' +
              escapeHtml(line.text) +
              '</span></div>'
            );
          })
          .join('');

        diffBlocks.push(
          '<div class="diff-block">' +
            '<div class="diff-header">' +
            escapeHtml(previousLabel + ' -> ' + currentLabel) +
            '</div>' +
            '<div>' +
            lines +
            '</div>' +
          '</div>'
        );
      }

      diffsView.innerHTML =
        diffBlocks.length > 0
          ? diffBlocks.join('')
          : '<div class="muted">No code versions found for inline diff rendering.</div>';
    }

    function computeInlineDiff(beforeText, afterText) {
      const beforeLines = splitLines(beforeText);
      const afterLines = splitLines(afterText);
      const lcsTable = buildLcsTable(beforeLines, afterLines);

      let leftIndex = beforeLines.length;
      let rightIndex = afterLines.length;
      const operations = [];

      while (leftIndex > 0 && rightIndex > 0) {
        const leftLine = beforeLines[leftIndex - 1];
        const rightLine = afterLines[rightIndex - 1];

        if (leftLine === rightLine) {
          operations.push({ kind: 'context', prefix: ' ', text: leftLine });
          leftIndex -= 1;
          rightIndex -= 1;
          continue;
        }

        if (lcsTable[leftIndex - 1][rightIndex] >= lcsTable[leftIndex][rightIndex - 1]) {
          operations.push({ kind: 'removed', prefix: '-', text: leftLine });
          leftIndex -= 1;
        } else {
          operations.push({ kind: 'added', prefix: '+', text: rightLine });
          rightIndex -= 1;
        }
      }

      while (leftIndex > 0) {
        operations.push({ kind: 'removed', prefix: '-', text: beforeLines[leftIndex - 1] });
        leftIndex -= 1;
      }

      while (rightIndex > 0) {
        operations.push({ kind: 'added', prefix: '+', text: afterLines[rightIndex - 1] });
        rightIndex -= 1;
      }

      operations.reverse();
      return operations;
    }

    function buildLcsTable(left, right) {
      const table = new Array(left.length + 1);

      for (let leftIndex = 0; leftIndex <= left.length; leftIndex += 1) {
        table[leftIndex] = new Array(right.length + 1).fill(0);
      }

      for (let leftIndex = 1; leftIndex <= left.length; leftIndex += 1) {
        for (let rightIndex = 1; rightIndex <= right.length; rightIndex += 1) {
          if (left[leftIndex - 1] === right[rightIndex - 1]) {
            table[leftIndex][rightIndex] = table[leftIndex - 1][rightIndex - 1] + 1;
          } else {
            table[leftIndex][rightIndex] = Math.max(
              table[leftIndex - 1][rightIndex],
              table[leftIndex][rightIndex - 1]
            );
          }
        }
      }

      return table;
    }

    function splitLines(text) {
      if (!text || String(text).length === 0) {
        return [];
      }

      return String(text).split(/\r\n|\r|\n/);
    }

    function extractVersions(record) {
      const directArrayCandidates = [
        record && record.evolutionChain,
        record && record.versions,
        record && record.lineage && record.lineage.versions,
        record && record.evolution && record.evolution.chain,
        record && record.lineage && record.lineage.chain
      ];

      for (const candidate of directArrayCandidates) {
        if (Array.isArray(candidate)) {
          return candidate;
        }
      }

      const objectCandidates = [
        record && record.evolutionChain,
        record && record.evolution,
        record && record.lineage
      ];

      for (const candidate of objectCandidates) {
        if (candidate && typeof candidate === 'object' && Array.isArray(candidate.versions)) {
          return candidate.versions;
        }
      }

      return [];
    }

    function extractVersionCode(version) {
      const value = pickFirst(version, [
        ['code'],
        ['source'],
        ['insertedCode'],
        ['extractedInsertedCodeBlock'],
        ['insertion', 'extractedInsertedCodeBlock'],
        ['snapshot', 'code']
      ]);

      if (typeof value === 'string') {
        return value;
      }

      return '';
    }

    function pickFirst(source, pathCandidates) {
      for (const path of pathCandidates) {
        const value = getAtPath(source, path);
        if (typeof value !== 'undefined' && value !== null) {
          return value;
        }
      }

      return null;
    }

    function getAtPath(source, path) {
      let cursor = source;

      for (const segment of path) {
        if (!cursor || typeof cursor !== 'object' || !(segment in cursor)) {
          return undefined;
        }

        cursor = cursor[segment];
      }

      return cursor;
    }

    function stringifyValue(value) {
      if (value === null || typeof value === 'undefined') {
        return 'n/a';
      }

      if (typeof value === 'string') {
        return value;
      }

      if (typeof value === 'number' || typeof value === 'boolean') {
        return String(value);
      }

      return JSON.stringify(value);
    }

    function toPrettyString(value) {
      if (value === null || typeof value === 'undefined') {
        return 'n/a';
      }

      if (typeof value === 'string') {
        return value;
      }

      try {
        return JSON.stringify(value, null, 2);
      } catch {
        return String(value);
      }
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

export function extractUuidFromText(text: string): string | undefined {
  const match = text.match(UUID_PATTERN);
  return match ? match[0].toLowerCase() : undefined;
}

function joinUrl(baseUrl: string, relativePath: string): string {
  const trimmedBase = baseUrl.replace(/\/$/, '');
  return trimmedBase + relativePath;
}

function extractRecordObject(rawBody: string): Record<string, unknown> {
  if (rawBody.trim().length === 0) {
    throw new Error('Empty provenance record response body.');
  }

  const parsed = parseJson(rawBody);

  if (isRecord(parsed)) {
    if (isRecord(parsed.record)) {
      return parsed.record;
    }

    if (isRecord(parsed.data)) {
      return parsed.data;
    }

    return parsed;
  }

  throw new Error('Provenance response did not contain a JSON object record.');
}

function extractExplanationText(rawBody: string): string {
  if (rawBody.trim().length === 0) {
    return '';
  }

  const parsed = parseJson(rawBody);

  if (typeof parsed === 'string') {
    return parsed;
  }

  if (isRecord(parsed)) {
    const explanationCandidate =
      parsed.explanation ?? parsed.explain ?? parsed.summary ?? parsed.result ?? parsed.text;

    if (typeof explanationCandidate === 'string') {
      return explanationCandidate;
    }

    return JSON.stringify(parsed, null, 2);
  }

  return rawBody;
}

function parseJson(rawBody: string): unknown {
  try {
    return JSON.parse(rawBody) as unknown;
  } catch {
    return rawBody;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
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
