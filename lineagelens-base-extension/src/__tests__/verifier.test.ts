import { verifyEvidence } from '../evidence/verifier';
import { sealMissing } from '../evidence/hashChain';
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

test('empty store reports nothing to verify', () => {
  const report = verifyEvidence([]);
  expect(report.summary).toMatch(/no captures/i);
});

test('an intact chain verifies', () => {
  const report = verifyEvidence(sealed(3));
  expect(report.ok).toBe(true);
  expect(report.summary).toMatch(/intact/i);
  expect(report.schemaIssues).toEqual([]);
});

test('tampering is reported in the summary and ok is false', () => {
  const records = sealed(3);
  records[1].insertedCode = 'tampered';
  const report = verifyEvidence(records);
  expect(report.ok).toBe(false);
  expect(report.tampered).toContain(records[1].id);
  expect(report.summary).toMatch(/failed/i);
});

test('a record from a newer schema is flagged', () => {
  const records = sealed(2);
  records[0].schemaVersion = CAPTURE_SCHEMA_VERSION + 1;
  const report = verifyEvidence(records);
  expect(report.schemaIssues).toContain(records[0].id);
  expect(report.summary).toMatch(/newer schema/i);
});

test('continuity breaks from a deletion are surfaced but still verify', () => {
  const records = sealed(3).filter((r) => r.id !== 'r1');
  const report = verifyEvidence(records);
  expect(report.ok).toBe(true);
  expect(report.breaks).toBe(1);
  expect(report.summary).toMatch(/break/i);
});
