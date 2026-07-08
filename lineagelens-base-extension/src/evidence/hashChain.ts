/**
 * Tamper-evident event chain — pure, no vscode.
 *
 * Each capture seals an `eventHash` over its immutable core plus the previous
 * record's hash (`prevHash`). Editing a stored capture's content changes its
 * recomputed hash, so verification flags it. The chain covers only the immutable
 * capture facts — NOT mutable annotations (reviewState, source/confidence,
 * lineageState) — so legitimately reclassifying or reviewing a capture never
 * "breaks" the evidence.
 *
 * This is honest local tamper evidence, not an immutable ledger: Base lets users
 * delete captures, which leaves a reported continuity break rather than an error.
 */

import { CaptureRecord } from '../store';
import { sha256Hex, canonicalJson } from './hash';

/** Sentinel prevHash for the first record in the chain. */
export const GENESIS_HASH = '0'.repeat(64);

/** The immutable subset of a record that the chain protects. */
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

/** Compute the event hash for a record given its predecessor's hash. */
export function eventHashFor(record: CaptureRecord, prevHash: string): string {
  return sha256Hex(canonicalJson(chainCore(record)) + prevHash);
}

/**
 * Seal any records missing an `eventHash`, walking chronologically
 * (oldest→newest). `recordsNewestFirst` is the store's array; mutates in place.
 * Records already sealed are left untouched and used as the link for the next.
 * Returns true if anything was sealed (so the caller can persist).
 */
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
  /** True when no record failed its integrity check and none are unsealed. */
  ok: boolean;
  total: number;
  /** Records whose recomputed hash matched (content intact). */
  verified: number;
  /** Ids whose stored hash does not match their content (tampered). */
  tampered: string[];
  /** Ids with no eventHash (never sealed). */
  unsealed: string[];
  /** Continuity gaps: a prevHash pointing at no known record (e.g. a deletion). */
  breaks: number;
}

/**
 * Verify a set of records. Per-record integrity is order-independent (each record
 * recomputes from its own prevHash), so passing the store's newest-first array is
 * fine. Continuity breaks are reported but do not fail verification, since Base
 * allows authorized deletion.
 */
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
