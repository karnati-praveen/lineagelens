import * as vscode from 'vscode';

export class DiffViewPanel {
    public static currentPanel: DiffViewPanel | undefined;
    private readonly _panel: vscode.WebviewPanel;
    private _disposables: vscode.Disposable[] = [];

    private constructor(panel: vscode.WebviewPanel, private readonly _extensionUri: vscode.Uri) {
        this._panel = panel;
        this._panel.onDidDispose(() => this.dispose(), null, this._disposables);
    }

    public static createOrShow(extensionUri: vscode.Uri): DiffViewPanel {
        const column = vscode.window.activeTextEditor?.viewColumn ?? vscode.ViewColumn.One;

        if (DiffViewPanel.currentPanel) {
            DiffViewPanel.currentPanel._panel.reveal(column);
            return DiffViewPanel.currentPanel;
        }

        const panel = vscode.window.createWebviewPanel(
            'lineagelens.diffView',
            'LineageLens: Code Evolution',
            column,
            { enableScripts: true, retainContextWhenHidden: true }
        );

        DiffViewPanel.currentPanel = new DiffViewPanel(panel, extensionUri);
        return DiffViewPanel.currentPanel;
    }

    public showFileDiff(filePath: string, records: DiffRecord[]): void {
        this._panel.title = `LineageLens: ${filePath.split('/').pop() ?? filePath}`;
        this._panel.webview.html = this._buildHtml(filePath, records);
    }

    public showCompareDiff(diff: CompareDiffResult): void {
        this._panel.title = 'LineageLens: Record Diff';
        this._panel.webview.html = this._buildCompareHtml(diff);
    }

    private _buildHtml(filePath: string, records: DiffRecord[]): string {
        const rows = records.map((r, i) => `
            <tr class="rec-row" data-uuid="${this._escHtml(r.uuid ?? '')}">
                <td>${i + 1}</td>
                <td>${this._escHtml(r.timestampIso ?? '')}</td>
                <td>${this._escHtml(r.modelName ?? '—')}</td>
                <td><span class="risk risk-${this._riskClass(r.riskScore)}">${r.riskScore ?? '—'}</span></td>
                <td class="snippet">${this._escHtml((r.insertedCodeSnippet ?? '').substring(0, 80))}</td>
            </tr>
        `).join('');

        return `<!DOCTYPE html><html lang="en">
<head><meta charset="UTF-8"><title>Code Evolution</title>
<style>
body{font-family:var(--vscode-font-family);font-size:var(--vscode-font-size);background:var(--vscode-editor-background);color:var(--vscode-editor-foreground);padding:16px}
h2{margin-top:0}
table{width:100%;border-collapse:collapse;margin-top:12px}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--vscode-widget-border)}
th{background:var(--vscode-sideBarSectionHeader-background);font-weight:600}
.rec-row:hover{background:var(--vscode-list-hoverBackground);cursor:pointer}
.risk{padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600}
.risk-high{background:#f003;color:#f44}
.risk-med{background:#fa03;color:#fa0}
.risk-low{background:#0f03;color:#4c4}
.snippet{font-family:var(--vscode-editor-font-family);font-size:11px;opacity:.8}
</style>
</head>
<body>
<h2>Code Evolution: <code>${this._escHtml(filePath)}</code></h2>
<p>${records.length} AI insertion(s) found for this file.</p>
<table>
<thead><tr><th>#</th><th>Timestamp</th><th>Model</th><th>Risk</th><th>Code Snippet</th></tr></thead>
<tbody>${rows || '<tr><td colspan="5">No records found.</td></tr>'}</tbody>
</table>
</body></html>`;
    }

    private _buildCompareHtml(diff: CompareDiffResult): string {
        const diffHtml = this._colorDiff(diff.diffLines ?? []);
        return `<!DOCTYPE html><html lang="en">
<head><meta charset="UTF-8"><title>Record Diff</title>
<style>
body{font-family:var(--vscode-font-family);font-size:var(--vscode-font-size);background:var(--vscode-editor-background);color:var(--vscode-editor-foreground);padding:16px}
.meta{display:flex;gap:32px;margin-bottom:16px}
.meta-box{background:var(--vscode-sideBarSectionHeader-background);padding:10px 16px;border-radius:4px;flex:1}
.diff-view{font-family:var(--vscode-editor-font-family);font-size:12px;white-space:pre;overflow-x:auto}
.add{background:#0f0a;color:#4d4}
.rem{background:#f004;color:#d44}
.hunk{color:var(--vscode-textLink-foreground);font-weight:600}
</style>
</head>
<body>
<h2>Record Comparison</h2>
<div class="meta">
  <div class="meta-box"><b>A</b><br>${this._escHtml(diff.a?.uuid ?? '')} — ${this._escHtml(diff.a?.timestampIso ?? '')}<br>Risk: ${diff.a?.riskScore ?? '—'}</div>
  <div class="meta-box"><b>B</b><br>${this._escHtml(diff.b?.uuid ?? '')} — ${this._escHtml(diff.b?.timestampIso ?? '')}<br>Risk: ${diff.b?.riskScore ?? '—'}</div>
</div>
<div class="diff-view">${diffHtml}</div>
</body></html>`;
    }

    private _colorDiff(lines: string[]): string {
        return lines.map(line => {
            const escaped = this._escHtml(line);
            if (line.startsWith('+')) return `<span class="add">${escaped}</span>`;
            if (line.startsWith('-')) return `<span class="rem">${escaped}</span>`;
            if (line.startsWith('@@')) return `<span class="hunk">${escaped}</span>`;
            return escaped;
        }).join('\n');
    }

    private _riskClass(score: number | null | undefined): 'high' | 'med' | 'low' {
        if (score != null && score >= 80) return 'high';
        if (score != null && score >= 50) return 'med';
        return 'low';
    }

    private _escHtml(s: string): string {
        return s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
    }

    public dispose(): void {
        DiffViewPanel.currentPanel = undefined;
        this._panel.dispose();
        while (this._disposables.length) {
            this._disposables.pop()?.dispose();
        }
    }
}

export interface DiffRecord {
    uuid: string;
    filePath?: string;
    modelName?: string | null;
    riskScore?: number | null;
    timestampIso?: string | null;
    insertedCodeSnippet?: string;
    isRedacted?: boolean;
}

export interface CompareDiffResult {
    a?: DiffRecord;
    b?: DiffRecord;
    diff?: string;
    diffLines?: string[];
    linesAdded?: number;
    linesRemoved?: number;
}
