import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import * as vscode from 'vscode';
import { LocalStorageService } from '../lineagelens-src/storage/LocalStorageService';
import type { ProvenanceRecord } from '../lineagelens-src/provenance';

/**
 * Flaw 3: LocalStorage Concurrent Write Overwrite
 * Location: lineagelens-src/storage/LocalStorageService.ts
 * Description: updateLineageFromLatestCommit and exportAuditCsv perform readStore / writeStore
 * calls without chaining on `this.writeLock`. When ingest() runs concurrently with updateLineageFromLatestCommit,
 * updateLineageFromLatestCommit overwrites records.json with its stale snapshot, permanently destroying newly ingested records.
 */
async function reproduceFlaw3LocalStorageOverwrite(): Promise<void> {
  console.log('--- Reproducing Flaw 3: LocalStorage Concurrent Write Overwrite ---');

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lineagelens-flaw3-'));
  const nowIso = new Date().toISOString();

  const dummyContext = {
    globalStorageUri: vscode.Uri.file(tmpDir),
    globalState: {
      get: () => undefined,
      update: () => Promise.resolve()
    },
    secrets: {
      get: () => Promise.resolve(undefined),
      store: () => Promise.resolve(),
      delete: () => Promise.resolve()
    }
  } as unknown as vscode.ExtensionContext;

  const storageService = new LocalStorageService(dummyContext, () => {});

  const record0: ProvenanceRecord = {
    uuid: '00000000-0000-0000-0000-000000000000',
    timestampIso: new Date(Date.now() - 10000).toISOString(),
    file: { path: 'main.ts', languageId: 'typescript' },
    repository: { gitBranch: 'main' },
    insertion: {
      netAddedLines: 3,
      extractedInsertedCodeBlock: 'const base = 0;',
      surroundingContext: { before: '', after: '' }
    },
    prompt: { modelName: 'gpt-4o', fullMessages: [] },
    contextSnapshot: {},
    astSnapshot: { normalizedNodeTypes: ['identifier'] },
    metadata: { schemaVersion: '1.0.0' }
  };

  const record1: ProvenanceRecord = {
    uuid: '11111111-1111-1111-1111-111111111111',
    timestampIso: nowIso,
    file: { path: 'main.ts', languageId: 'typescript' },
    repository: { gitBranch: 'main' },
    insertion: {
      netAddedLines: 5,
      extractedInsertedCodeBlock: 'const a = 1;',
      surroundingContext: { before: '', after: '' }
    },
    prompt: { modelName: 'gpt-4o', fullMessages: [] },
    contextSnapshot: {},
    astSnapshot: { normalizedNodeTypes: ['identifier'] },
    metadata: { schemaVersion: '1.0.0' }
  };

  const record2: ProvenanceRecord = {
    uuid: '22222222-2222-2222-2222-222222222222',
    timestampIso: new Date(Date.now() + 5000).toISOString(),
    file: { path: 'main.ts', languageId: 'typescript' },
    repository: { gitBranch: 'main' },
    insertion: {
      netAddedLines: 10,
      extractedInsertedCodeBlock: 'const b = 2;',
      surroundingContext: { before: '', after: '' }
    },
    prompt: { modelName: 'gpt-4o', fullMessages: [] },
    contextSnapshot: {},
    astSnapshot: { normalizedNodeTypes: ['identifier'] },
    metadata: { schemaVersion: '1.0.0' }
  };

  try {
    // 1. Ingest initial records 0 and 1 so lineage updates will produce recordsUpdated > 0
    await storageService.ingest(record0);
    await storageService.ingest(record1);

    // 2. Trigger updateLineageFromLatestCommit (reads store without writeLock)
    const updatePromise = storageService.updateLineageFromLatestCommit();

    // 3. Concurrently ingest record 2 (acquires writeLock, appends record 2, writes to disk)
    const ingestPromise = storageService.ingest(record2);

    await Promise.all([updatePromise, ingestPromise]);

    // ASSERTION FOR EXPECTED CORRECT BEHAVIOR:
    // Store should contain BOTH record 0/1 AND record 2.
    // FLAW BEHAVIOR: updateLineageFromLatestCommit called writeStore without writeLock, overwriting records.json with its stale snapshot and destroying record 2!
    try {
      const retrievedRecord2 = await storageService.getProvenanceByUuid(record2.uuid);
      assert.ok(retrievedRecord2, 'Record 2 should exist in store');
      console.log('Record 2 found in store successfully.');
    } catch (err: any) {
      assert.fail(`[FLAW DEMONSTRATED] Record 2 was concurrently overwritten and permanently lost due to un-locked writeStore! Error: ${err.message}`);
    }
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}

if (require.main === module) {
  reproduceFlaw3LocalStorageOverwrite().catch((err) => {
    console.error('Test Failed as Expected (Demonstrating Flaw 3):');
    console.error(err.message);
    process.exit(1);
  });
}

export { reproduceFlaw3LocalStorageOverwrite };
