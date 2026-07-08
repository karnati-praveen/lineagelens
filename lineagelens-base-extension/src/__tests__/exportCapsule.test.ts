import { buildCapsule, CAPSULE_FORMAT, CAPSULE_VERSION } from '../evidence/exportCapsule';
import { verifyEvidence } from '../evidence/verifier';
import { sealMissing, GENESIS_HASH } from '../evidence/hashChain';
import { CaptureRecord, CAPTURE_SCHEMA_VERSION } from '../store';

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
    schemaVersion: CAPTURE_SCHEMA_VERSION,
    reviewState: 'unreviewed',
    lineageState: 'original',
    ...over,
  };
}

function sealed(n: number): CaptureRecord[] {
  const records: CaptureRecord[] = [];
  // r0 oldest (last), r(n-1) newest (first) — mirrors the store's add().
  for (let i = 0; i < n; i++) {
    records.unshift(rec({ id: `r${i}`, timestamp: `2026-06-25T10:0${i}:00.000Z` }));
  }
  sealMissing(records);
  return records;
}

test('capsule carries metadata, records, and a passing verification', () => {
  const records = sealed(3);
  const capsule = buildCapsule(records, 'ws-1', '2026-06-25T12:00:00.000Z');
  expect(capsule.format).toBe(CAPSULE_FORMAT);
  expect(capsule.capsuleVersion).toBe(CAPSULE_VERSION);
  expect(capsule.schemaVersion).toBe(CAPTURE_SCHEMA_VERSION);
  expect(capsule.workspaceId).toBe('ws-1');
  expect(capsule.genesisHash).toBe(GENESIS_HASH);
  expect(capsule.records).toHaveLength(3);
  expect(capsule.verification.ok).toBe(true);
  expect(capsule.verifierInstructions).toMatch(/eventHash/);
});

test('capsule records round-trip through the verifier', () => {
  const capsule = buildCapsule(sealed(4), 'ws', '2026-06-25T12:00:00.000Z');
  // Re-verifying the records pulled from the capsule still passes.
  expect(verifyEvidence(capsule.records).ok).toBe(true);
});

test('capsule reflects tampering detected at export time', () => {
  const records = sealed(2);
  records[0].insertedCode = 'tampered';
  const capsule = buildCapsule(records, 'ws', '2026-06-25T12:00:00.000Z');
  expect(capsule.verification.ok).toBe(false);
  expect(capsule.verification.tampered).toContain(records[0].id);
});

test('capsule is serializable to JSON', () => {
  const capsule = buildCapsule(sealed(2), 'ws', '2026-06-25T12:00:00.000Z');
  const round = JSON.parse(JSON.stringify(capsule));
  expect(round.records).toHaveLength(2);
  expect(round.verification.ok).toBe(true);
});
