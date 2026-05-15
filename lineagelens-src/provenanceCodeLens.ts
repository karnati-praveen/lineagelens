import * as vscode from 'vscode';
import type { ProvenanceStorageService } from './storage/StorageService';

export class ProvenanceCodeLensProvider implements vscode.CodeLensProvider, vscode.Disposable {
    private _onDidChangeCodeLenses = new vscode.EventEmitter<void>();
    readonly onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;

    constructor(
        private readonly _getStorage: () => ProvenanceStorageService,
        private readonly _log: (message: string) => void
    ) {}

    dispose(): void {
        this._onDidChangeCodeLenses.dispose();
    }

    refresh(): void {
        this._onDidChangeCodeLenses.fire();
    }

    async provideCodeLenses(document: vscode.TextDocument): Promise<vscode.CodeLens[]> {
        const filePath = document.uri.fsPath;
        const lenses: vscode.CodeLens[] = [];

        try {
            const storage = this._getStorage();
            const records = await this._getRecordsForFile(storage, filePath);

            for (const record of records) {
                const line = this._findLineForRecord(document, record);
                if (line >= 0) {
                    const range = new vscode.Range(line, 0, line, 0);
                    const modelName = this._extractModelName(record);
                    const riskScore = this._extractRiskScore(record);
                    lenses.push(new vscode.CodeLens(range, {
                        title: `⚡ AI-generated (${modelName} · risk ${riskScore})`,
                        command: 'lineagelens.showProvenance',
                        arguments: [this._extractUuid(record)],
                    }));
                }
            }
        } catch (error: unknown) {
            // Silently fail — CodeLens is best-effort
            this._log('CodeLens provider error (non-fatal): ' + toErrorMessage(error));
        }

        return lenses;
    }

    private async _getRecordsForFile(
        storage: ProvenanceStorageService,
        filePath: string
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
                    limit: 50
                },
                undefined
            );
            // Prefer embedded record objects; fall back to the result item itself
            return results.map((item) => (item.record ?? item) as any);
        } catch {
            return [];
        }
    }

    private _findLineForRecord(document: vscode.TextDocument, record: any): number {
        // Cursor line is stored 1-based in the schema; convert to 0-based
        const rawLine = record?.insertion?.cursorPosition?.line ?? record?.cursorLine;
        if (typeof rawLine === 'number' && rawLine >= 1) {
            return Math.min(rawLine - 1, document.lineCount - 1);
        }

        // Fall back to matching the first line of the inserted code block
        const insertedCode: string = (
            record?.insertion?.extractedInsertedCodeBlock ??
            record?.insertedCode ??
            record?.insertedText ??
            ''
        ).trim();

        const firstLine = insertedCode.split('\n')[0]?.trim() ?? '';
        if (!firstLine || firstLine.length < 8) {
            return -1;
        }

        for (let i = 0; i < document.lineCount; i++) {
            if (document.lineAt(i).text.includes(firstLine)) {
                return i;
            }
        }

        return -1;
    }

    private _extractModelName(record: any): string {
        const name =
            record?.prompt?.modelName ??
            record?.modelName ??
            record?.model;
        if (typeof name === 'string' && name.trim().length > 0) {
            return name.trim();
        }
        return 'unknown model';
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

    private _extractUuid(record: any): string {
        return String(record?.uuid ?? record?.id ?? '');
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

// Quick action CodeLens — shown below the main provenance CodeLens
export class ProvenanceQuickActionProvider implements vscode.CodeLensProvider, vscode.Disposable {
    private _onDidChangeCodeLenses = new vscode.EventEmitter<void>();
    readonly onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    private _storage: any; // StorageService

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    constructor(storage: any) {
        this._storage = storage;
    }

    refresh(): void {
        this._onDidChangeCodeLenses.fire();
    }

    async provideCodeLenses(document: vscode.TextDocument): Promise<vscode.CodeLens[]> {
        const lenses: vscode.CodeLens[] = [];

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        let records: any[];
        try {
            records = await this._storage.search(
                {
                    keywords: '',
                    model: '',
                    dateFrom: '',
                    dateTo: '',
                    currentFileOnly: true,
                    currentFilePath: document.uri.fsPath,
                    limit: 50
                },
                undefined
            );
        } catch {
            return lenses;
        }

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        for (const record of records) {
            const line = Math.max(0, ((record.normalizedEvent?.cursorLine ?? record.cursorLine ?? 1) - 1));
            const range = new vscode.Range(line, 0, line, 0);
            const uuid = record.normalizedEvent?.eventId ?? record.uuid ?? record.id;

            if (!uuid) { continue; }

            lenses.push(
                new vscode.CodeLens(range, {
                    title: '$(comment) Explain',
                    command: 'lineagelens.explainRecord',
                    arguments: [uuid],
                    tooltip: 'Explain this AI-generated code',
                }),
                new vscode.CodeLens(range, {
                    title: '$(warning) Flag',
                    command: 'lineagelens.flagRecord',
                    arguments: [uuid],
                    tooltip: 'Flag this record for review',
                }),
                new vscode.CodeLens(range, {
                    title: '$(checklist) Review',
                    command: 'lineagelens.addToReview',
                    arguments: [uuid],
                    tooltip: 'Add to reviewer queue',
                })
            );
        }

        return lenses;
    }

    dispose(): void {
        this._onDidChangeCodeLenses.dispose();
    }
}
