import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { randomUUID } from 'crypto';

export interface CaptureRecord {
  id: string;
  timestamp: string;
  filePath: string;
  fileName: string;
  language: string;
  insertedCode: string;
  linesAdded: number;
  workspaceFolder: string | null;
}

const STORE_FILE = 'captures.json';

export class CaptureStore {
  private storePath: string;
  private records: CaptureRecord[] = [];
  private maxCaptures: number;

  constructor(context: vscode.ExtensionContext) {
    const dir = context.globalStorageUri.fsPath;
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    this.storePath = path.join(dir, STORE_FILE);
    this.maxCaptures = vscode.workspace.getConfiguration('lineagelensBase').get('maxStoredCaptures', 1000);
    this.load();
  }

  private load(): void {
    try {
      if (fs.existsSync(this.storePath)) {
        const raw = fs.readFileSync(this.storePath, 'utf-8');
        this.records = JSON.parse(raw) as CaptureRecord[];
      }
    } catch {
      this.records = [];
    }
  }

  private save(): void {
    const tmp = this.storePath + '.tmp';
    try {
      fs.writeFileSync(tmp, JSON.stringify(this.records, null, 2), 'utf-8');
      fs.renameSync(tmp, this.storePath);
    } catch (error) {
      try {
        if (fs.existsSync(tmp)) { fs.unlinkSync(tmp); }
      } catch {
        // best-effort cleanup; ignore secondary failure
      }
      console.error('LineageLens Base: failed to persist captures:', error);
    }
  }

  add(data: Omit<CaptureRecord, 'id' | 'timestamp'>): CaptureRecord {
    const record: CaptureRecord = {
      id: randomUUID(),
      timestamp: new Date().toISOString(),
      ...data,
    };
    this.records.unshift(record);
    // Re-read the cap on each insert so a user changing the setting takes
    // effect immediately instead of requiring an extension reload.
    const cap = vscode.workspace.getConfiguration('lineagelensBase').get<number>('maxStoredCaptures', 1000);
    this.maxCaptures = cap;
    if (this.records.length > cap) {
      this.records = this.records.slice(0, cap);
    }
    this.save();
    return record;
  }

  getAll(): CaptureRecord[] {
    return this.records;
  }

  getById(id: string): CaptureRecord | undefined {
    return this.records.find(r => r.id === id);
  }

  /**
   * Move one or more records so they appear immediately before `targetId`.
   * When `targetId` is undefined the dragged records move to the bottom.
   * Persists immediately.
   */
  reorder(draggedIds: string[], targetId: string | undefined): void {
    const idSet = new Set(draggedIds);
    const dragged = draggedIds
      .map(id => this.records.find(r => r.id === id))
      .filter((r): r is CaptureRecord => r !== undefined);
    if (dragged.length === 0) { return; }

    const rest = this.records.filter(r => !idSet.has(r.id));
    if (targetId === undefined) {
      this.records = [...rest, ...dragged];
    } else {
      const idx = rest.findIndex(r => r.id === targetId);
      if (idx === -1) {
        this.records = [...dragged, ...rest];
      } else {
        this.records = [...rest.slice(0, idx), ...dragged, ...rest.slice(idx)];
      }
    }
    this.save();
  }

  clear(): void {
    this.records = [];
    this.save();
  }

  exportJson(): string {
    return JSON.stringify(this.records, null, 2);
  }

  get count(): number {
    return this.records.length;
  }
}
