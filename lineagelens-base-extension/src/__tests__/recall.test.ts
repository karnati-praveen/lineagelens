import { buildRecallSet, buildRecallReport } from '../risk/recall';
import { CaptureRecord } from '../store';

function rec(over: Partial<CaptureRecord> & { id: string; insertedCode: string }): CaptureRecord {
  return {
    timestamp: '2026-06-25T10:00:00.000Z',
    filePath: `/repo/${over.id}.ts`,
    fileName: `${over.id}.ts`,
    language: 'typescript',
    linesAdded: 3,
    workspaceFolder: null,
    confidence: 0.8,
    source: 'ai',
    schemaVersion: 2,
    reviewState: 'unreviewed',
    lineageState: 'original',
    ...over,
  };
}

const BLOCK = 'function add(a, b) {\n  return a + b;\n}';

test('an exact duplicate is matched with similarity 1 and exact=true', () => {
  const target = rec({ id: 't', insertedCode: BLOCK });
  const dup = rec({ id: 'd', insertedCode: BLOCK });
  const other = rec({ id: 'o', insertedCode: 'const z = 99;' });
  const set = buildRecallSet(target, [target, dup, other]);
  expect(set.matches).toHaveLength(1);
  expect(set.matches[0].record.id).toBe('d');
  expect(set.matches[0].exact).toBe(true);
  expect(set.matches[0].similarity).toBe(1);
});

test('whitespace-only differences still count as exact (content hash)', () => {
  const target = rec({ id: 't', insertedCode: BLOCK });
  const reindented = rec({ id: 'd', insertedCode: '\nfunction add(a, b) {\n  return a + b;\n}  \n' });
  const set = buildRecallSet(target, [target, reindented]);
  expect(set.matches[0].exact).toBe(true);
});

test('a near-duplicate above threshold is included, below is excluded', () => {
  const target = rec({ id: 't', insertedCode: 'a()\nb()\nc()\nd()' });
  const near = rec({ id: 'n', insertedCode: 'a()\nb()\nc()\nZ()' }); // 3/5 shared → 0.6
  const far = rec({ id: 'f', insertedCode: 'x()\ny()' }); // 0 shared
  const set = buildRecallSet(target, [target, near, far], { threshold: 0.5 });
  const ids = set.matches.map((m) => m.record.id);
  expect(ids).toContain('n');
  expect(ids).not.toContain('f');
});

test('the target itself is never in its own matches', () => {
  const target = rec({ id: 't', insertedCode: BLOCK });
  const set = buildRecallSet(target, [target]);
  expect(set.matches).toHaveLength(0);
});

test('matches are sorted by similarity descending', () => {
  const target = rec({ id: 't', insertedCode: 'a()\nb()\nc()\nd()' });
  const exact = rec({ id: 'e', insertedCode: 'a()\nb()\nc()\nd()' });
  const near = rec({ id: 'n', insertedCode: 'a()\nb()\nc()\nZ()' });
  const set = buildRecallSet(target, [target, near, exact]);
  expect(set.matches.map((m) => m.record.id)).toEqual(['e', 'n']);
});

test('files lists distinct paths with the target first', () => {
  const target = rec({ id: 't', insertedCode: BLOCK, filePath: '/repo/t.ts' });
  const dup = rec({ id: 'd', insertedCode: BLOCK, filePath: '/repo/other.ts' });
  const set = buildRecallSet(target, [target, dup]);
  expect(set.files).toEqual(['/repo/t.ts', '/repo/other.ts']);
});

// ── report ────────────────────────────────────────────────────────────────────

test('report lists matches and affected files', () => {
  const target = rec({ id: 't', insertedCode: BLOCK });
  const dup = rec({ id: 'd', insertedCode: BLOCK });
  const report = buildRecallReport(buildRecallSet(target, [target, dup]));
  expect(report).toContain('AI recall report');
  expect(report).toContain('1 similar capture');
  expect(report).toContain('[exact]');
  expect(report).toContain('Files to inspect');
});

test('report handles the no-match case', () => {
  const target = rec({ id: 't', insertedCode: BLOCK });
  const report = buildRecallReport(buildRecallSet(target, [target]));
  expect(report).toMatch(/nothing else to recall/i);
});
