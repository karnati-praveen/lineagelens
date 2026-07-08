import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { randomUUID, randomBytes, createCipheriv, createDecipheriv } from 'crypto';
import { rangeContentHash } from './evidence/hash';
import { applyReview } from './review/reviewState';
import { GENESIS_HASH, eventHashFor, sealMissing } from './evidence/hashChain';
import { evaluateRisk } from './risk/rules';

export type CaptureSource = 'ai' | 'paste' | 'unknown';

/** Human review lifecycle for an AI-origin capture (Phase 2 populates beyond default). */
export type ReviewState = 'unreviewed' | 'reviewed' | 'needs_changes' | 'rejected' | 'accepted';

/** Where the captured block sits now relative to where it was inserted (Phase 1). */
export type LineageState = 'original' | 'modified' | 'moved' | 'deleted' | 'unknown';

/** Severity of a local risk signal. */
export type RiskSeverity = 'low' | 'medium' | 'high';

/**
 * A deterministic local risk signal attached to a capture (see risk/rules.ts).
 * These are heuristic "signals", not a security scan / SAST result.
 */
export interface RiskSignal {
  /** Stable rule id, e.g. 'generated-auth-code'. */
  id: string;
  /** Short display word, e.g. 'auth', 'secrets'. */
  label: string;
  /** Review-checklist category key (see review/checklist.ts). */
  category: string;
  severity: RiskSeverity;
  message: string;
}

/** Current evidence schema version written by this build. */
export const CAPTURE_SCHEMA_VERSION = 2;

export interface CaptureRecord {
  id: string;
  timestamp: string;
  filePath: string;
  fileName: string;
  language: string;
  insertedCode: string;
  linesAdded: number;
  workspaceFolder: string | null;
  /** 0.0–1.0 likelihood this insertion came from an AI tool */
  confidence: number;
  /** best-guess origin of the insertion */
  source: CaptureSource;

  // ── v2 evidence layer (all optional for back-compat; see schema migration) ──
  /** Evidence schema version; absent on legacy (v1) records. */
  schemaVersion?: number;
  /** Zero-based line of the first inserted line at capture time. */
  startLine?: number;
  /** Zero-based line of the last inserted line at capture time. */
  endLine?: number;
  /** Whitespace-tolerant hash of the inserted block, for relocation/tamper checks. */
  rangeContentHash?: string;
  /** Current position of the block relative to capture (resolved lazily). */
  lineageState?: LineageState;
  /** Human review lifecycle state. */
  reviewState?: ReviewState;
  /** Optional free-text review note. */
  reviewNote?: string;
  /** ISO timestamp of the last review-state change. */
  reviewedAt?: string;
  /** Branch HEAD was on at capture time (best-effort; null if git unavailable). */
  gitBranch?: string | null;
  /** Commit HEAD pointed at during capture (best-effort; null if git unavailable). */
  gitCommit?: string | null;
  /** Tamper-evident hash of this record's immutable core, chained to {@link prevHash}. */
  eventHash?: string;
  /** eventHash of the previously-added record (genesis sentinel for the first). */
  prevHash?: string;
  /** Local risk signals derived from the captured code/path (Phase 4). */
  riskSignals?: RiskSignal[];
}

const STORE_FILE = 'captures.json';
const SECRET_KEY_NAME = 'lineagelens.base.captureStoreKey';
// Marker prefix identifying an encrypted store file. Legacy plaintext stores
// begin with '[' (a JSON array), so the two formats are unambiguous on read.
const ENC_PREFIX = 'LLENC1:';

/**
 * Resolve (or create) the per-install AES-256 key used to encrypt the capture
 * store. The key lives in VS Code SecretStorage, which is backed by the OS
 * keychain (DPAPI on Windows, Keychain on macOS, libsecret on Linux) — so a
 * stolen captures.json file is useless without access to the keychain too.
 */
async function getOrCreateStoreKey(context: vscode.ExtensionContext): Promise<Buffer> {
  const existing = await context.secrets.get(SECRET_KEY_NAME);
  if (existing) {
    try {
      const buf = Buffer.from(existing, 'base64');
      if (buf.length === 32) { return buf; }
    } catch {
      // fall through and regenerate
    }
  }
  const key = randomBytes(32);
  await context.secrets.store(SECRET_KEY_NAME, key.toString('base64'));
  return key;
}

export class CaptureStore {
  private storePath: string;
  private records: CaptureRecord[] = [];
  private maxCaptures: number;
  private key: Buffer | null;
  private _saveDebounceTimer: ReturnType<typeof setTimeout> | null = null;
  // Tail of the serialized write chain. flush() awaits this so callers (and
  // tests) can deterministically wait for persistence to settle.
  private _pendingWrite: Promise<void> = Promise.resolve();

  /**
   * Construct a store. `key` enables at-rest encryption; when omitted the store
   * reads/writes legacy plaintext (used by unit tests and as a fallback when the
   * OS keychain is unavailable). Production code should use {@link CaptureStore.create}.
   */
  constructor(context: vscode.ExtensionContext, key?: Buffer) {
    const dir = context.globalStorageUri.fsPath;
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    this.storePath = path.join(dir, STORE_FILE);
    this.maxCaptures = vscode.workspace.getConfiguration('lineagelensBase').get('maxStoredCaptures', 1000);
    this.key = key ?? null;
    this.load();
  }

  /**
   * Preferred constructor: acquires the encryption key from the OS keychain,
   * then loads (and transparently migrates any legacy plaintext store to
   * encrypted form on first save).
   */
  static async create(context: vscode.ExtensionContext): Promise<CaptureStore> {
    let key: Buffer | undefined;
    try {
      key = await getOrCreateStoreKey(context);
    } catch (error) {
      console.error('LineageLens Base: keychain unavailable, storing captures unencrypted:', error);
      key = undefined;
    }
    return new CaptureStore(context, key);
  }

  private encrypt(plaintext: string): string {
    if (!this.key) { return plaintext; }
    const iv = randomBytes(12);
    const cipher = createCipheriv('aes-256-gcm', this.key, iv);
    const ciphertext = Buffer.concat([cipher.update(plaintext, 'utf-8'), cipher.final()]);
    const tag = cipher.getAuthTag();
    return ENC_PREFIX + Buffer.concat([iv, tag, ciphertext]).toString('base64');
  }

  private decrypt(raw: string): string {
    const payload = Buffer.from(raw.slice(ENC_PREFIX.length), 'base64');
    const iv = payload.subarray(0, 12);
    const tag = payload.subarray(12, 28);
    const ciphertext = payload.subarray(28);
    if (!this.key) {
      throw new Error('Encrypted capture store found but no key is available.');
    }
    const decipher = createDecipheriv('aes-256-gcm', this.key, iv);
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString('utf-8');
  }

  private load(): void {
    try {
      if (fs.existsSync(this.storePath)) {
        const raw = fs.readFileSync(this.storePath, 'utf-8');
        const wasEncrypted = raw.startsWith(ENC_PREFIX);
        const json = wasEncrypted ? this.decrypt(raw) : raw;
        const parsed = JSON.parse(json) as CaptureRecord[];
        // Backfill fields so older stored records stay valid:
        //  – confidence/source landed in v1.2.3
        //  – the v2 evidence layer (schemaVersion, reviewState, lineageState,
        //    rangeContentHash) lands here. One bump carries all later-phase
        //    fields; they remain optional until a phase populates them.
        this.records = parsed.map(r => ({
          ...r,
          confidence: r.confidence ?? 0.5,
          source: (r.source ?? 'unknown') as CaptureSource,
          schemaVersion: r.schemaVersion ?? CAPTURE_SCHEMA_VERSION,
          reviewState: (r.reviewState ?? 'unreviewed') as ReviewState,
          lineageState: (r.lineageState ?? 'unknown') as LineageState,
          rangeContentHash: r.rangeContentHash ?? rangeContentHash(r.insertedCode ?? ''),
          riskSignals: r.riskSignals ?? evaluateRisk({
            filePath: r.filePath,
            language: r.language,
            insertedCode: r.insertedCode ?? '',
          }),
        }));
        // Seal any records that predate the evidence chain (one-time on upgrade).
        const sealed = sealMissing(this.records);
        // Persist if we migrated plaintext→encrypted and/or sealed the chain.
        if ((!wasEncrypted && this.key) || sealed) {
          this.save();
        }
      }
    } catch {
      this.records = [];
    }
  }

  private save(): void {
    // Serialize writes onto a single chain so a debounced flush and an explicit
    // save can never race into a half-written file. flush() awaits the tail.
    this._pendingWrite = this._pendingWrite
      .catch(() => {})
      .then(() => this._saveAsync())
      .catch((err: NodeJS.ErrnoException) => {
        // Background persistence is best-effort (the in-memory copy is source of
        // truth and the next save retries). Stay quiet on environmental write
        // failures — a vanished storage dir (ENOENT) or a transient OS lock on
        // the temp file (EPERM, common on Windows with AV/indexers/OneDrive).
        if (err && err.code !== 'ENOENT' && err.code !== 'EPERM') {
          console.error('LineageLens Base: failed to persist captures:', err);
        }
      });
  }

  // Used by add() to coalesce rapid consecutive captures into one write.
  private saveDebounced(): void {
    if (this._saveDebounceTimer !== null) { return; }
    const timer = setTimeout(() => {
      this._saveDebounceTimer = null;
      this.save();
    }, 500);
    // Don't let a pending capture-store flush keep the host (or a test runner)
    // process alive on its own.
    timer.unref?.();
    this._saveDebounceTimer = timer;
  }

  /**
   * Flush any pending write to disk and resolve once it has settled. Cancels a
   * debounced save and forces it through. Safe to call when nothing is pending.
   */
  async flush(): Promise<void> {
    if (this._saveDebounceTimer !== null) {
      clearTimeout(this._saveDebounceTimer);
      this._saveDebounceTimer = null;
      this.save();
    }
    await this._pendingWrite;
  }

  /** Cancel any pending debounced save. Call on extension deactivate. */
  dispose(): void {
    if (this._saveDebounceTimer !== null) {
      clearTimeout(this._saveDebounceTimer);
      this._saveDebounceTimer = null;
    }
  }

  private async _saveAsync(): Promise<void> {
    const tmp = this.storePath + '.tmp';
    const content = this.encrypt(JSON.stringify(this.records, null, 2));
    try {
      await fs.promises.writeFile(tmp, content, 'utf-8');
      try {
        await fs.promises.rename(tmp, this.storePath);
      } catch {
        // On Windows, rename throws EPERM when the target is briefly held open.
        await fs.promises.copyFile(tmp, this.storePath);
        await fs.promises.unlink(tmp);
      }
    } catch (error) {
      await fs.promises.unlink(tmp).catch(() => {});
      throw error;
    }
  }

  add(data: Omit<CaptureRecord, 'id' | 'timestamp' | 'confidence' | 'source'> & { confidence?: number; source?: CaptureSource }): CaptureRecord {
    const record: CaptureRecord = {
      confidence: 0.5,
      source: 'unknown',
      schemaVersion: CAPTURE_SCHEMA_VERSION,
      reviewState: 'unreviewed',
      // A freshly captured insertion is, by definition, at its original spot.
      lineageState: 'original',
      id: randomUUID(),
      timestamp: new Date().toISOString(),
      ...data,
    };
    // Compute the content hash from the inserted code when the caller didn't
    // supply one, so every record is relocatable/tamper-checkable.
    if (!record.rangeContentHash) {
      record.rangeContentHash = rangeContentHash(record.insertedCode);
    }
    // Attach local risk signals (derived; not part of the chain core).
    if (!record.riskSignals) {
      record.riskSignals = evaluateRisk({
        filePath: record.filePath,
        language: record.language,
        insertedCode: record.insertedCode,
      });
    }
    // Seal into the tamper-evident chain: link to the current head's hash.
    const prevHash = this.records[0]?.eventHash ?? GENESIS_HASH;
    record.prevHash = prevHash;
    record.eventHash = eventHashFor(record, prevHash);
    this.records.unshift(record);
    // Re-read the cap on each insert so a user changing the setting takes
    // effect immediately instead of requiring an extension reload.
    const cap = vscode.workspace.getConfiguration('lineagelensBase').get<number>('maxStoredCaptures', 1000);
    this.maxCaptures = cap;
    if (this.records.length > cap) {
      this.records = this.records.slice(0, cap);
    }
    this.saveDebounced();
    return record;
  }

  getAll(): CaptureRecord[] {
    return this.records;
  }

  getById(id: string): CaptureRecord | undefined {
    return this.records.find(r => r.id === id);
  }

  /**
   * Move one or more records by `draggedIds` relative to `targetId`.
   *
   * targetId === null      → move to top of the list
   * targetId === undefined → move to bottom (dropped on empty space)
   * targetId === <id>      → insert immediately before that record
   *
   * When `targetId` is one of the dragged ids the drop target's original
   * position is used as the insertion point so the order stays intuitive.
   */
  reorder(draggedIds: string[], targetId: string | null | undefined): void {
    const idSet = new Set(draggedIds);
    const dragged = draggedIds
      .map(id => this.records.find(r => r.id === id))
      .filter((r): r is CaptureRecord => r !== undefined);
    if (dragged.length === 0) { return; }

    const rest = this.records.filter(r => !idSet.has(r.id));

    if (targetId === null) {
      // Dropped on the header — move to top.
      this.records = [...dragged, ...rest];
    } else if (targetId === undefined) {
      // Dropped on empty space — move to bottom.
      this.records = [...rest, ...dragged];
    } else {
      let idx = rest.findIndex(r => r.id === targetId);
      if (idx === -1) {
        // targetId was one of the dragged items — compute its original position
        // among the non-dragged items so we insert where it originally sat.
        const originalIdx = this.records.findIndex(r => r.id === targetId);
        idx = this.records.slice(0, originalIdx).filter(r => !idSet.has(r.id)).length;
      }
      this.records = [...rest.slice(0, idx), ...dragged, ...rest.slice(idx)];
    }
    this.save();
  }

  /** Remove a single record by id. Returns true if a record was removed. */
  remove(id: string): boolean {
    const idx = this.records.findIndex(r => r.id === id);
    if (idx === -1) { return false; }
    this.records.splice(idx, 1);
    this.save();
    return true;
  }

  /**
   * Correct a record's origin classification. A user assertion is treated as
   * high-confidence; pass an explicit `confidence` to override the default.
   */
  setClassification(id: string, source: CaptureSource, confidence?: number): CaptureRecord | undefined {
    const rec = this.records.find(r => r.id === id);
    if (!rec) { return undefined; }
    rec.source = source;
    rec.confidence = confidence ?? (source === 'ai' ? 0.99 : source === 'paste' ? 0.95 : 0.5);
    this.save();
    return rec;
  }

  /**
   * Move a record through the human review lifecycle. Returns the (possibly
   * updated) record, or `undefined` if the id is unknown. An illegal transition
   * leaves the record unchanged and is not persisted.
   */
  setReviewState(id: string, state: ReviewState, note?: string): CaptureRecord | undefined {
    const idx = this.records.findIndex(r => r.id === id);
    if (idx === -1) { return undefined; }
    const updated = applyReview(this.records[idx], state, note);
    if (updated === this.records[idx]) {
      return this.records[idx]; // no-op (illegal transition or nothing changed)
    }
    this.records[idx] = updated;
    this.save();
    return updated;
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
