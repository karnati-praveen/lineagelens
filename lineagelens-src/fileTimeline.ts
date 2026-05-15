import * as vscode from 'vscode';
import type { ProvenanceStorageService } from './storage/StorageService';

class TimelineItem extends vscode.TreeItem {
    constructor(public readonly record: any) {
        const timestampIso: string = record?.timestampIso ?? record?.insertionTimestampIso ?? '';
        const dateDisplay = timestampIso.length > 0
            ? new Date(timestampIso).toLocaleDateString()
            : 'unknown date';
        const modelName = extractModelName(record);
        const label = dateDisplay + ' — ' + modelName;

        super(label, vscode.TreeItemCollapsibleState.None);

        this.description = 'risk ' + extractRiskScore(record);

        const inserted: string =
            record?.insertion?.extractedInsertedCodeBlock ??
            record?.insertedCode ??
            record?.insertedText ??
            '';
        this.tooltip = inserted.slice(0, 100) + (inserted.length > 100 ? '...' : '');

        const uuid = String(record?.uuid ?? record?.id ?? '');
        this.command = {
            command: 'lineagelens.showProvenance',
            title: 'View Record',
            arguments: [uuid],
        };

        const riskScoreNum = extractRiskScoreNumber(record);
        this.iconPath = new vscode.ThemeIcon(
            riskScoreNum >= 65 ? 'warning' : 'history'
        );

        this.contextValue = 'timelineItem';
    }
}

export class FileTimelineProvider implements vscode.TreeDataProvider<TimelineItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;
    private _currentFile: string | undefined;

    constructor(
        private readonly _getStorage: () => ProvenanceStorageService,
        private readonly _log: (message: string) => void
    ) {}

    refresh(filePath?: string): void {
        this._currentFile = filePath;
        this._onDidChangeTreeData.fire();
    }

    getTreeItem(element: TimelineItem): vscode.TreeItem {
        return element;
    }

    async getChildren(_element?: TimelineItem): Promise<TimelineItem[]> {
        if (!this._currentFile) {
            return [];
        }

        try {
            const storage = this._getStorage();
            const results = await storage.search(
                {
                    keywords: '',
                    model: '',
                    dateFrom: '',
                    dateTo: '',
                    currentFileOnly: true,
                    currentFilePath: this._currentFile,
                    limit: 50
                },
                undefined
            );

            const records = results
                .map((item) => (item.record ?? item) as any)
                .sort((a: any, b: any) => {
                    const aTs = a?.timestampIso ?? a?.insertionTimestampIso ?? '';
                    const bTs = b?.timestampIso ?? b?.insertionTimestampIso ?? '';
                    return new Date(bTs).getTime() - new Date(aTs).getTime();
                });

            return records.map((r: any) => new TimelineItem(r));
        } catch (error: unknown) {
            this._log('FileTimeline getChildren error (non-fatal): ' + toErrorMessage(error));
            return [];
        }
    }
}

function extractModelName(record: any): string {
    const name =
        record?.prompt?.modelName ??
        record?.modelName ??
        record?.model;
    if (typeof name === 'string' && name.trim().length > 0) {
        return name.trim();
    }
    return 'unknown';
}

function extractRiskScoreNumber(record: any): number {
    const score =
        record?.metadata?.riskAssessment?.score ??
        record?.metadata?.riskScore ??
        record?.riskScore;
    return typeof score === 'number' ? score : 0;
}

function extractRiskScore(record: any): string {
    const score = extractRiskScoreNumber(record);
    return score > 0 ? String(Math.round(score)) : '?';
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
