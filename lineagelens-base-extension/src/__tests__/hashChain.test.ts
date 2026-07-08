import {
  GENESIS_HASH,
  eventHashFor,
  sealMissing,
  verifyChain,
} from '../evidence/hashChain';
import { CaptureRecord } from '../store';

function rec(over: Partial<CaptureRecord> & { id: string }): CaptureRecord {
  return {
    timestamp: '2026-06-25T10:00:00.000Z',
    filePath: '/repo/a.ts',
    fileName: 'a.ts',
    language: 'typescript',
    insertedCode: `code-${over.id}`,
    linesAdded: 4,
    workspaceFolder: null,
    confidence: 0.8,
    source: 'ai',
    schemaVersion: 2,
    reviewState: 'unreviewed',
    lineageState: 'original',
    ...over,
  };
}

/** Build a newest-first array of n sealed records: r0 is oldest (last), r(n-1) newest (first). */
function sealedChain(n: number): CaptureRecord[] {
  const records: CaptureRecord[] = [];
  // unshift in id order so r0 ends up last (oldest) — mirrors the store's add().
  for (let i = 0; i < n; i++) {
    records.unshift(rec({ id: `r${i}`, timestamp: `2026-06-25T10:0${i}:00.000Z` }));
  }
  sealMissing(records);
  return records;
}

// ── sealMissing / eventHashFor ────────────────────────────────────────────────

test('the oldest record chains to the genesis hash', () => {
  const records = sealedChain(3); // newest-first: r2, r1, r0
  const oldest = records[records.length - 1];
  expect(oldest.id).toBe('r0');
  expect(oldest.prevHash).toBe(GENESIS_HASH);
  expect(oldest.eventHash).toBe(eventHashFor(oldest, GENESIS_HASH));
});

test('each record links to the previous record eventHash', () => {
  const records = sealedChain(3);
  // chronological: r0, r1, r2
  const chrono = [...records].reverse();
  expect(chrono[1].prevHash).toBe(chrono[0].eventHash);
  expect(chrono[2].prevHash).toBe(chrono[1].eventHash);
});

test('sealMissing leaves already-sealed records untouched', () => {
  const records = sealedChain(2);
  const before = records.map((r) => r.eventHash);
  expect(sealMissing(records)).toBe(false);
  expect(records.map((r) => r.eventHash)).toEqual(before);
});

// ── verifyChain ───────────────────────────────────────────────────────────────

test('a freshly sealed chain verifies with no breaks', () => {
  const v = verifyChain(sealedChain(4));
  expect(v.ok).toBe(true);
  expect(v.verified).toBe(4);
  expect(v.tampered).toEqual([]);
  expect(v.breaks).toBe(0);
});

test('mutating a record content is detected as tampering', () => {
  const records = sealedChain(3);
  records[1].insertedCode = 'malicious'; // edit without resealing
  const v = verifyChain(records);
  expect(v.ok).toBe(false);
  expect(v.tampered).toContain(records[1].id);
});

test('mutable annotations do not break the chain', () => {
  const records = sealedChain(3);
  records[0].reviewState = 'reviewed';
  records[1].source = 'unknown';
  records[1].confidence = 0.1;
  records[2].lineageState = 'moved';
  expect(verifyChain(records).ok).toBe(true);
});

test('an unsealed record is reported and fails verification', () => {
  const records = sealedChain(2);
  records.unshift(rec({ id: 'fresh' })); // no eventHash
  const v = verifyChain(records);
  expect(v.ok).toBe(false);
  expect(v.unsealed).toContain('fresh');
});

test('deleting a middle record leaves a continuity break but intact integrity', () => {
  const records = sealedChain(3); // r2, r1, r0
  const kept = records.filter((r) => r.id !== 'r1');
  const v = verifyChain(kept);
  expect(v.tampered).toEqual([]); // surviving records still verify
  expect(v.breaks).toBe(1); // r2's prevHash now points at the deleted r1
});

test('reordering the array does not affect verification', () => {
  const records = sealedChain(4);
  const shuffled = [records[2], records[0], records[3], records[1]];
  expect(verifyChain(shuffled)).toEqual(verifyChain(records));
});
