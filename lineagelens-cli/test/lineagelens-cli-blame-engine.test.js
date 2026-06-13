'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
  normalizeRecords,
  filterRecordsForFile,
  blameLines,
  computeStats,
  normLine,
  isSignificantLine,
} = require('../src/blame/lineagelens-cli-blame-engine');

// ── Fixtures ──────────────────────────────────────────────────────────────────

const FILE_TEXT = [
  'import os',
  '',
  'def fetch_user(user_id):',
  '    """Load a user by id."""',
  '    conn = get_connection()',
  '    row = conn.execute(QUERY, (user_id,)).fetchone()',
  '    return User.from_row(row)',
  '',
  'def main():',
  '    print("hello")',
  '',
].join('\n');

const AI_BLOCK = [
  'def fetch_user(user_id):',
  '    """Load a user by id."""',
  '    conn = get_connection()',
  '    row = conn.execute(QUERY, (user_id,)).fetchone()',
  '    return User.from_row(row)',
].join('\n');

function captureRecord(overrides = {}) {
  return {
    id: 'rec-1',
    timestamp: '2026-06-01T10:00:00Z',
    filePath: '/home/dev/project/users.py',
    fileName: 'users.py',
    language: 'python',
    insertedCode: AI_BLOCK,
    linesAdded: 5,
    workspaceFolder: 'project',
    confidence: 0.8,
    source: 'ai',
    ...overrides,
  };
}

// ── normalizeRecords ──────────────────────────────────────────────────────────

test('normalizeRecords parses extension export (CaptureRecord[])', () => {
  const { records, format } = normalizeRecords(JSON.stringify([captureRecord()]));
  assert.equal(format, 'extension-export');
  assert.equal(records.length, 1);
  assert.equal(records[0].insertedCode, AI_BLOCK);
  assert.equal(records[0].confidence, 0.8);
});

test('normalizeRecords parses backend search response', () => {
  const response = {
    results: [
      {
        uuid: 'u-1',
        model: 'claude-opus-4-8',
        timestampIso: '2026-06-02T09:00:00Z',
        filePath: 'users.py',
        snippet: AI_BLOCK.slice(0, 50),
        record: { insertedCode: AI_BLOCK, modelName: 'claude-opus-4-8' },
      },
    ],
  };
  const { records, format } = normalizeRecords(JSON.stringify(response));
  assert.equal(format, 'backend-search');
  assert.equal(records.length, 1);
  assert.equal(records[0].model, 'claude-opus-4-8');
  assert.equal(records[0].insertedCode, AI_BLOCK);
});

test('normalizeRecords parses agent-trace JSONL with preview reconstruction', () => {
  const preview = 'def fetch_user(user_id):↵    """Load a user by id."""';
  const doc = {
    version: '0.1.0',
    id: 'at-1',
    timestamp: '2026-06-03T08:00:00Z',
    files: [
      {
        path: 'users.py',
        conversations: [{ contributor: { type: 'ai' }, ranges: [{ start_line: 1, end_line: 5 }] }],
      },
    ],
    metadata: {
      'lineagelens.insertedCodePreview': preview,
      'lineagelens.confidence': { score: 0.75, level: null },
    },
  };
  const { records, format, warnings } = normalizeRecords(JSON.stringify(doc) + '\n');
  assert.equal(format, 'agent-trace');
  assert.equal(records.length, 1);
  assert.equal(records[0].insertedCode.split('\n').length, 2);
  assert.equal(records[0].confidence, 0.75);
  assert.ok(warnings.length >= 1);
});

test('normalizeRecords drops the truncated last preview line at the 120-char cap', () => {
  const fullLine = 'x'.repeat(60);
  const preview = (fullLine + '↵' + 'y'.repeat(80)).slice(0, 120);
  const doc = {
    version: '0.1.0',
    id: 'at-2',
    timestamp: '2026-06-03T08:00:00Z',
    files: [{ path: 'a.py', conversations: [{ contributor: { type: 'ai' }, ranges: [] }] }],
    metadata: { 'lineagelens.insertedCodePreview': preview },
  };
  const { records } = normalizeRecords(JSON.stringify(doc));
  assert.equal(records.length, 1);
  assert.equal(records[0].insertedCode, fullLine);
  assert.equal(records[0].contentTruncated, true);
});

test('normalizeRecords handles empty and garbage input', () => {
  assert.equal(normalizeRecords('').records.length, 0);
  const { records, warnings } = normalizeRecords('not json\n{"insertedCode":"const a = compute_total();","filePath":"a.js"}');
  assert.equal(records.length, 1);
  assert.equal(warnings.length, 1);
});

// ── filterRecordsForFile ──────────────────────────────────────────────────────

test('filterRecordsForFile matches absolute paths from another machine by basename', () => {
  const records = [
    captureRecord({ filePath: '/home/dev/project/users.py' }),
    captureRecord({ id: 'rec-2', filePath: 'C:\\work\\other\\billing.py' }),
  ];
  const matched = filterRecordsForFile(records, 'C:\\Users\\karna\\repo\\users.py');
  assert.equal(matched.length, 1);
  assert.equal(matched[0].id, 'rec-1');
});

// ── blameLines ────────────────────────────────────────────────────────────────

test('exact contiguous block is attributed to the record', () => {
  const blame = blameLines(FILE_TEXT, [captureRecord()]);
  const attributed = blame.filter((l) => l.attribution);
  assert.equal(attributed.length, 5);
  assert.deepEqual(attributed.map((l) => l.lineNo), [3, 4, 5, 6, 7]);
  assert.ok(attributed.every((l) => l.attribution.matchType === 'exact'));
  assert.ok(blame[0].attribution === null); // 'import os' stays human
});

test('whitespace drift still matches (normalized comparison)', () => {
  const reindented = AI_BLOCK.split('\n').map((l) => '  ' + l.replace(/    /g, '\t')).join('\n');
  const blame = blameLines(FILE_TEXT, [captureRecord({ insertedCode: reindented })]);
  assert.equal(blame.filter((l) => l.attribution).length, 5);
});

test('newest record wins overlapping lines', () => {
  const older = captureRecord({ id: 'old', timestamp: '2026-05-01T00:00:00Z' });
  const newer = captureRecord({
    id: 'new',
    timestamp: '2026-06-05T00:00:00Z',
    insertedCode: '    conn = get_connection()\n    row = conn.execute(QUERY, (user_id,)).fetchone()',
  });
  const blame = blameLines(FILE_TEXT, [newer, older]); // order in array must not matter
  assert.equal(blame[4].attribution.recordId, 'new');
  assert.equal(blame[2].attribution.recordId, 'old');
});

test('edited code falls back to partial per-line matching of significant lines', () => {
  // Record whose block no longer exists contiguously (one middle line changed in file).
  const record = captureRecord({
    insertedCode: [
      'def fetch_user(user_id):',
      '    """Load a user by id, eagerly."""', // differs from the file
      '    conn = get_connection()',
      '    return User.from_row(row)',
    ].join('\n'),
  });
  const blame = blameLines(FILE_TEXT, [record]);
  const attributed = blame.filter((l) => l.attribution);
  assert.ok(attributed.length >= 2);
  assert.ok(attributed.every((l) => l.attribution.matchType === 'partial'));
});

test('trivial single-line records are never attributed', () => {
  const blame = blameLines(FILE_TEXT, [captureRecord({ insertedCode: '}' })]);
  assert.equal(blame.filter((l) => l.attribution).length, 0);
});

test('minConfidence filters low-confidence records', () => {
  const blame = blameLines(FILE_TEXT, [captureRecord({ confidence: 0.4 })], { minConfidence: 0.7 });
  assert.equal(blame.filter((l) => l.attribution).length, 0);
});

test('no records means no attribution, not an error', () => {
  const blame = blameLines(FILE_TEXT, []);
  assert.equal(blame.length, FILE_TEXT.split('\n').length);
  assert.ok(blame.every((l) => l.attribution === null));
});

// ── computeStats ──────────────────────────────────────────────────────────────

test('computeStats summarizes by model and ignores the trailing blank line', () => {
  const blame = blameLines(FILE_TEXT, [captureRecord({ source: 'ai' })]);
  const stats = computeStats(blame);
  assert.equal(stats.totalLines, 10); // 11 split segments, trailing '' dropped
  assert.equal(stats.aiLines, 5);
  assert.equal(stats.exactLines, 5);
  assert.equal(stats.percent, 50);
  assert.deepEqual(stats.byModel, [{ model: 'unknown-ai', lines: 5 }]);
});

// ── helpers ───────────────────────────────────────────────────────────────────

test('normLine collapses whitespace', () => {
  assert.equal(normLine('  const   x =\t1;  '), 'const x = 1;');
});

test('isSignificantLine rejects punctuation-only and short lines', () => {
  assert.equal(isSignificantLine('});'), false);
  assert.equal(isSignificantLine('ab'), false);
  assert.equal(isSignificantLine('return User.from_row(row)'), true);
});
