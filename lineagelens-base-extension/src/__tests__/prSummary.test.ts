import { buildPrSummary } from '../review/prSummary';
import { CaptureRecord } from '../store';

function rec(over: Partial<CaptureRecord> & { id: string }): CaptureRecord {
  return {
    timestamp: '2026-06-25T10:00:00.000Z',
    filePath: '/repo/a.ts',
    fileName: 'a.ts',
    language: 'typescript',
    insertedCode: 'x',
    linesAdded: 10,
    workspaceFolder: null,
    confidence: 0.8,
    source: 'ai',
    schemaVersion: 2,
    reviewState: 'unreviewed',
    lineageState: 'original',
    ...over,
  };
}

test('empty set produces a clear no-captures summary', () => {
  expect(buildPrSummary([])).toBe('## AI-assisted changes\n\nNo AI captures recorded.\n');
});

test('aggregates counts deterministically across files and states', () => {
  const records = [
    rec({ id: '1', filePath: '/repo/a.ts', linesAdded: 100, reviewState: 'reviewed', lineageState: 'original' }),
    rec({ id: '2', filePath: '/repo/a.ts', linesAdded: 47, reviewState: 'unreviewed', lineageState: 'modified' }),
    rec({ id: '3', filePath: '/repo/b.ts', linesAdded: 20, reviewState: 'needs_changes', lineageState: 'deleted' }),
  ];
  expect(buildPrSummary(records)).toBe(
    [
      '## AI-assisted changes',
      '',
      '- 3 AI captures across 2 files',
      '- 167 generated lines, 147 still present',
      '- 1 reviewed · 1 unreviewed · 1 needs changes',
      '- 2 drifted since capture',
      '- 0 high-risk signals',
      '',
      '_Generated locally by LineageLens Base — no code leaves your machine._',
      '',
    ].join('\n'),
  );
});

test('counts high-risk captures', () => {
  const records = [
    rec({ id: '1', riskSignals: [{ id: 'x', label: 'auth', category: 'auth', severity: 'high', message: 'm' }] }),
    rec({ id: '2' }),
  ];
  expect(buildPrSummary(records)).toContain('- 1 high-risk signal');
});

test('singularizes a single capture in a single file', () => {
  const out = buildPrSummary([rec({ id: '1' })]);
  expect(out).toContain('- 1 AI capture across 1 file');
});

test('output is stable across calls', () => {
  const records = [rec({ id: '1' }), rec({ id: '2', filePath: '/repo/b.ts' })];
  expect(buildPrSummary(records)).toBe(buildPrSummary(records));
});
