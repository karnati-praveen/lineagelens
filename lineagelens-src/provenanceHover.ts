import * as vscode from 'vscode';
import type { ProvenanceStorageService } from './storage/StorageService';

export class ProvenanceHoverProvider implements vscode.HoverProvider {
    constructor(
        private readonly _getStorage: () => ProvenanceStorageService,
        private readonly _log: (message: string) => void
    ) {}

    async provideHover(
        document: vscode.TextDocument,
        position: vscode.Position
    ): Promise<vscode.Hover | undefined> {
        const line = position.line;
        const filePath = document.uri.fsPath;

        try {
            const storage = this._getStorage();
            const records = await this._getRecordsNearLine(storage, filePath, line);
            if (records.length === 0) {
                return undefined;
            }

            const record = records[0];
            const md = new vscode.MarkdownString(undefined, true);
            md.isTrusted = true;

            md.appendMarkdown('**LineageLens — AI Provenance**\n\n');
            md.appendMarkdown('| Field | Value |\n|---|---|\n');
            md.appendMarkdown('| Model | `' + this._extractModelName(record) + '` |\n');
            md.appendMarkdown('| Tool | `' + this._extractToolName(record) + '` |\n');
            md.appendMarkdown('| Risk | ' + this._extractRiskScore(record) + '/100 |\n');

            const timestampIso: string =
                record?.timestampIso ??
                record?.insertionTimestampIso ??
                '';
            const timestampDisplay = timestampIso.length > 0
                ? new Date(timestampIso).toLocaleString()
                : 'unknown';
            md.appendMarkdown('| Captured | ' + timestampDisplay + ' |\n');

            const promptStatus: string = record?.promptStatus ?? 'not-captured';
            if (promptStatus && promptStatus !== 'not-captured') {
                md.appendMarkdown('| Prompt | Available |\n');
            }

            const uuid = String(record?.uuid ?? record?.id ?? '');
            if (uuid.length > 0) {
                md.appendMarkdown(
                    '\n[View full record](command:lineagelens.showProvenance?' +
                    encodeURIComponent(JSON.stringify([uuid])) +
                    ')'
                );
            }

            return new vscode.Hover(md);
        } catch (error: unknown) {
            this._log('Hover provider error (non-fatal): ' + toErrorMessage(error));
            return undefined;
        }
    }

    private async _getRecordsNearLine(
        storage: ProvenanceStorageService,
        filePath: string,
        line: number
    ): Promise<any[]> {
        try {
            const results = await storage.search(
                {
                    keywords: '',
                    model: '',
                    dateFrom: '',
                    dateTo: '',
                    currentFileOnly: true,
                    currentFilePath: filePath,
                    limit: 20
                },
                undefined
            );

            const allRecords = results.map((item) => (item.record ?? item) as any);

            // Filter to records whose cursor line is within ±5 lines of current position
            // Cursor line is stored 1-based; position.line is 0-based
            return allRecords.filter((r: any) => {
                const rawLine =
                    r?.insertion?.cursorPosition?.line ??
                    r?.cursor?.line ??
                    r?.cursorLine;
                if (typeof rawLine !== 'number') {
                    return false;
                }
                const zeroBased = rawLine >= 1 ? rawLine - 1 : rawLine;
                return Math.abs(zeroBased - line) <= 5;
            });
        } catch {
            return [];
        }
    }

    private _extractModelName(record: any): string {
        const name =
            record?.prompt?.modelName ??
            record?.modelName ??
            record?.model;
        if (typeof name === 'string' && name.trim().length > 0) {
            return name.trim();
        }
        return 'unknown';
    }

    private _extractToolName(record: any): string {
        const tool =
            record?.metadata?.agentContext?.toolName ??
            record?.agentContext?.toolName;
        if (typeof tool === 'string' && tool.trim().length > 0) {
            return tool.trim();
        }
        return 'unknown';
    }

    private _extractRiskScore(record: any): string {
        const score =
            record?.metadata?.riskAssessment?.score ??
            record?.metadata?.riskScore ??
            record?.riskScore;
        if (typeof score === 'number') {
            return String(Math.round(score));
        }
        return '?';
    }
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
