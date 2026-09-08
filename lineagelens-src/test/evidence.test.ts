import assert from 'node:assert/strict';
import test from 'node:test';
import { sha256Hex, canonicalJson, rangeContentHash } from '../evidence/hash';
import { GENESIS_HASH, eventHashFor, sealMissing, verifyChain } from '../evidence/hashChain';
import { verifyEvidence } from '../evidence/verifier';
import { buildCapsule } from '../evidence/exportCapsule';
import { buildPrSummary } from '../review/prSummary';
import { selectForGate, hasBlockingFindings, gateSummaryLine } from '../review/precommit';
import type { CaptureRecord } from '../evidence/types';

test('sha256Hex computes correct hex digest', () => {
  assert.equal(
    sha256Hex('hello world'),
    'b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9',
  );
});

test('canonicalJson produces deterministic sorted keys', () => {
  const a = { z: 1, a: 2, m: { b: 1, a: 2 } };
  const b = { a: 2, m: { a: 2, b: 1 }, z: 1 };
  assert.equal(canonicalJson(a), canonicalJson(b));
});

test('tamper-evident hash chain seals and verifies intact records', () => {
  const records: CaptureRecord[] = [
    {
      id: 'rec-1',
      timestamp: '2026-09-08T10:00:00Z',
      filePath: '/src/main.ts',
      language: 'typescript',
      insertedCode: 'const a = 1;',
      linesAdded: 1,
    },
    {
      id: 'rec-2',
      timestamp: '2026-09-08T10:05:00Z',
      filePath: '/src/main.ts',
      language: 'typescript',
      insertedCode: 'const b = 2;',
      linesAdded: 1,
    },
  ];

  // Records are newest-first in store
  const store = [records[1], records[0]];
  const sealed = sealMissing(store);
  assert.equal(sealed, true);

  assert.equal(records[0].prevHash, GENESIS_HASH);
  assert.equal(records[1].prevHash, records[0].eventHash);

  const verification = verifyChain(store);
  assert.equal(verification.ok, true);
  assert.equal(verification.verified, 2);
  assert.equal(verification.tampered.length, 0);
});

test('tamper detection flags modified insertedCode', () => {
  const record: CaptureRecord = {
    id: 'rec-1',
    timestamp: '2026-09-08T10:00:00Z',
    filePath: '/src/main.ts',
    language: 'typescript',
    insertedCode: 'const a = 1;',
  };
  sealMissing([record]);

  // Tamper with code
  record.insertedCode = 'const a = 999;';
  const report = verifyEvidence([record]);
  assert.equal(report.ok, false);
  assert.equal(report.tampered.includes('rec-1'), true);
  assert.match(report.summary, /Evidence check failed/);
});

test('buildCapsule generates valid portable capsule structure', () => {
  const record: CaptureRecord = {
    id: 'rec-1',
    timestamp: '2026-09-08T10:00:00Z',
    filePath: '/src/main.ts',
    language: 'typescript',
    insertedCode: 'const a = 1;',
  };
  sealMissing([record]);

  const capsule = buildCapsule([record], 'ws-test', '2026-09-08T12:00:00Z');
  assert.equal(capsule.format, 'lineagelens-capsule');
  assert.equal(capsule.capsuleVersion, 1);
  assert.equal(capsule.records.length, 1);
  assert.equal(capsule.verification.ok, true);
});

test('buildPrSummary generates markdown statistics', () => {
  const records: CaptureRecord[] = [
    {
      id: 'rec-1',
      timestamp: '2026-09-08T10:00:00Z',
      filePath: '/src/main.ts',
      language: 'typescript',
      insertedCode: 'const a = 1;',
      linesAdded: 5,
      reviewState: 'reviewed',
    },
  ];
  const summary = buildPrSummary(records);
  assert.match(summary, /## AI-assisted changes/);
  assert.match(summary, /5 generated lines/);
  assert.match(summary, /1 reviewed/);
});

test('selectForGate filters staged files and flags unreviewed findings', () => {
  const records: CaptureRecord[] = [
    {
      id: 'rec-staged',
      timestamp: '2026-09-08T10:00:00Z',
      filePath: 'src/app.ts',
      language: 'typescript',
      insertedCode: 'const a = 1;',
      reviewState: 'unreviewed',
    },
    {
      id: 'rec-unstaged',
      timestamp: '2026-09-08T10:00:00Z',
      filePath: 'src/other.ts',
      language: 'typescript',
      insertedCode: 'const b = 2;',
      reviewState: 'unreviewed',
    },
  ];

  const buckets = selectForGate(records, ['src/app.ts']);
  assert.equal(buckets.unreviewed.length, 1);
  assert.equal(buckets.unreviewed[0].id, 'rec-staged');
  assert.equal(hasBlockingFindings(buckets), true);
  assert.match(gateSummaryLine(buckets), /1 unreviewed/);
});
