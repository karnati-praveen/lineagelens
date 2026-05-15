import * as vscode from 'vscode';

export type CaptureStatus = 'capturing' | 'success' | 'offline' | 'error' | 'policy_blocked';

interface CaptureEvent {
    status: CaptureStatus;
    filePath?: string;
    model?: string;
    risk?: number;
    message?: string;
    uuid?: string;
}

export class CaptureStatusToastManager {
    private static readonly SUCCESS_RISK_THRESHOLD = 65;
    private _statusBarItem: vscode.StatusBarItem;
    private _pendingTimer: ReturnType<typeof setTimeout> | null = null;

    constructor() {
        this._statusBarItem = vscode.window.createStatusBarItem(
            vscode.StatusBarAlignment.Right,
            90
        );
        this._statusBarItem.command = 'lineagelens.showDashboard';
        this._updateIdle();
        this._statusBarItem.show();
    }

    notify(event: CaptureEvent): void {
        if (this._pendingTimer) {
            clearTimeout(this._pendingTimer);
            this._pendingTimer = null;
        }

        switch (event.status) {
            case 'capturing':
                this._statusBarItem.text = '$(sync~spin) LineageLens: capturing…';
                this._statusBarItem.tooltip = `Capturing AI insertion in ${event.filePath ?? 'unknown file'}`;
                this._statusBarItem.color = undefined;
                break;

            case 'success': {
                const risk = event.risk ?? 0;
                const riskLabel = risk >= 80 ? '🔴' : risk >= 65 ? '🟡' : '🟢';
                this._statusBarItem.text = `$(check) LineageLens: captured ${riskLabel}`;
                this._statusBarItem.tooltip = [
                    `Captured: ${event.filePath ?? 'unknown'}`,
                    event.model ? `Model: ${event.model}` : '',
                    `Risk: ${risk}`,
                    event.uuid ? `UUID: ${event.uuid}` : '',
                ].filter(Boolean).join('\n');
                this._statusBarItem.color = undefined;

                if (risk >= CaptureStatusToastManager.SUCCESS_RISK_THRESHOLD) {
                    vscode.window.showWarningMessage(
                        `LineageLens: High-risk AI insertion detected (risk ${risk}) in ${event.filePath ?? 'file'}`,
                        'View Record',
                        'Dismiss'
                    ).then(action => {
                        if (action === 'View Record' && event.uuid) {
                            void vscode.commands.executeCommand('lineagelens.showProvenance', event.uuid);
                        }
                    });
                }

                this._pendingTimer = setTimeout(() => this._updateIdle(), 8000);
                break;
            }

            case 'offline':
                this._statusBarItem.text = '$(cloud-offline) LineageLens: offline';
                this._statusBarItem.tooltip = `Backend unreachable. Queuing locally.\n${event.message ?? ''}`;
                this._statusBarItem.color = new vscode.ThemeColor('statusBar.warningBackground');
                this._pendingTimer = setTimeout(() => this._updateIdle(), 5000);
                break;

            case 'error':
                this._statusBarItem.text = '$(error) LineageLens: capture error';
                this._statusBarItem.tooltip = event.message ?? 'Capture failed';
                this._statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorBackground');
                this._pendingTimer = setTimeout(() => this._updateIdle(), 5000);
                break;

            case 'policy_blocked':
                this._statusBarItem.text = '$(shield) LineageLens: policy blocked';
                this._statusBarItem.tooltip = `Policy blocked this insertion: ${event.message ?? ''}`;
                this._statusBarItem.color = new vscode.ThemeColor('statusBarItem.warningBackground');
                vscode.window.showWarningMessage(
                    `LineageLens: A policy blocked this AI insertion. ${event.message ?? ''}`,
                    'View Policies'
                ).then(action => {
                    if (action === 'View Policies') {
                        void vscode.commands.executeCommand('aiInsertionDetector.openInsightsDashboard');
                    }
                });
                this._pendingTimer = setTimeout(() => this._updateIdle(), 8000);
                break;
        }
    }

    private _updateIdle(): void {
        this._statusBarItem.text = '$(eye) LineageLens';
        this._statusBarItem.tooltip = 'LineageLens: AI provenance active. Click to open dashboard.';
        this._statusBarItem.color = undefined;
    }

    dispose(): void {
        if (this._pendingTimer) {
            clearTimeout(this._pendingTimer);
        }
        this._statusBarItem.dispose();
    }
}
