/**
 * Tamper-evident event chain — pure, no vscode.
 */

import type { CaptureRecord } from './types';
import { sha256Hex, canonicalJson } from './hash';

export const GENESIS_HASH = '0'.repeat(64);

function chainCore(record: CaptureRecord): Record<string, unknown> {
  return {
    id: record.id,
    timestamp: record.timestamp,
    filePath: record.filePath,
    language: record.language,
    insertedCode: record.insertedCode,
    linesAdded: record.linesAdded,
    startLine: record.startLine ?? null,
    endLine: record.endLine ?? null,
  };
}

export function eventHashFor(record: CaptureRecord, prevHash: string): string {
  return sha256Hex(canonicalJson(chainCore(record)) + prevHash);
}

export function sealMissing(recordsNewestFirst: CaptureRecord[]): boolean {
  let changed = false;
  let prev = GENESIS_HASH;
  for (let i = recordsNewestFirst.length - 1; i >= 0; i--) {
    const r = recordsNewestFirst[i];
    if (!r.eventHash) {
      r.prevHash = prev;
      r.eventHash = eventHashFor(r, prev);
      changed = true;
    }
    prev = r.eventHash;
  }
  return changed;
}

export interface ChainVerification {
  ok: boolean;
  total: number;
  verified: number;
  tampered: string[];
  unsealed: string[];
  breaks: number;
}

export function verifyChain(records: CaptureRecord[]): ChainVerification {
  const tampered: string[] = [];
  const unsealed: string[] = [];
  let verified = 0;
  let breaks = 0;

  const eventHashes = new Set<string>();
  for (const r of records) {
    if (r.eventHash) { eventHashes.add(r.eventHash); }
  }

  for (const r of records) {
    if (!r.eventHash) {
      unsealed.push(r.id);
      continue;
    }
    const prev = r.prevHash ?? GENESIS_HASH;
    if (eventHashFor(r, prev) === r.eventHash) {
      verified++;
    } else {
      tampered.push(r.id);
    }
    if (prev !== GENESIS_HASH && !eventHashes.has(prev)) {
      breaks++;
    }
  }

  return {
    ok: tampered.length === 0 && unsealed.length === 0,
    total: records.length,
    verified,
    tampered,
    unsealed,
    breaks,
  };
}
