import {
  selectForGate,
  hasBlockingFindings,
  gateSummaryLine,
} from '../review/precommit';
import { CaptureRecord } from '../store';

function rec(over: Partial<CaptureRecord> & { id: string; filePath: string }): CaptureRecord {
  return {
    timestamp: '2026-06-25T10:00:00.000Z',
    fileName: over.filePath.split('/').pop() ?? '',
    language: 'typescript',
    insertedCode: 'x',
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

test('only captures whose file is staged are in scope', () => {
  const records = [
    rec({ id: '1', filePath: '/repo/a.ts' }),
    rec({ id: '2', filePath: '/repo/b.ts' }),
  ];
  const buckets = selectForGate(records, ['/repo/a.ts']);
  expect(buckets.unreviewed.map((r) => r.id)).toEqual(['1']);
});

test('buckets unreviewed, needs-changes/rejected, and drifted captures', () => {
  const records = [
    rec({ id: 'u', filePath: '/repo/a.ts', reviewState: 'unreviewed' }),
    rec({ id: 'n', filePath: '/repo/a.ts', reviewState: 'needs_changes' }),
    rec({ id: 'x', filePath: '/repo/a.ts', reviewState: 'rejected' }),
    rec({ id: 'd', filePath: '/repo/a.ts', reviewState: 'reviewed', lineageState: 'moved' }),
    rec({ id: 'ok', filePath: '/repo/a.ts', reviewState: 'reviewed', lineageState: 'original' }),
  ];
  const buckets = selectForGate(records, ['/repo/a.ts']);
  expect(buckets.unreviewed.map((r) => r.id)).toEqual(['u']);
  expect(buckets.needsChanges.map((r) => r.id).sort()).toEqual(['n', 'x']);
  expect(buckets.drifted.map((r) => r.id)).toEqual(['d']);
});

test('buckets captures carrying a high-severity risk signal as risky', () => {
  const records = [
    rec({ id: 'r', filePath: '/repo/a.ts', riskSignals: [{ id: 'x', label: 'auth', category: 'auth', severity: 'high', message: 'm' }] }),
    rec({ id: 'safe', filePath: '/repo/a.ts', riskSignals: [{ id: 'y', label: 'deps', category: 'dependencies', severity: 'medium', message: 'm' }] }),
  ];
  const buckets = selectForGate(records, ['/repo/a.ts']);
  expect(buckets.risky.map((r) => r.id)).toEqual(['r']);
});

test('summary surfaces a high-risk count', () => {
  const records = [
    rec({ id: 'r', filePath: '/repo/a.ts', reviewState: 'reviewed', lineageState: 'original', riskSignals: [{ id: 'x', label: 'auth', category: 'auth', severity: 'high', message: 'm' }] }),
  ];
  const buckets = selectForGate(records, ['/repo/a.ts']);
  expect(gateSummaryLine(buckets)).toContain('1 high-risk');
});

test('path matching tolerates separator and case differences', () => {
  const records = [rec({ id: '1', filePath: 'C:\\Repo\\Src\\A.ts' })];
  const buckets = selectForGate(records, ['c:/repo/src/a.ts']);
  expect(buckets.unreviewed).toHaveLength(1);
});

test('hasBlockingFindings and summary reflect an all-clear result', () => {
  const records = [rec({ id: 'ok', filePath: '/repo/a.ts', reviewState: 'reviewed', lineageState: 'original' })];
  const buckets = selectForGate(records, ['/repo/a.ts']);
  expect(hasBlockingFindings(buckets)).toBe(false);
  expect(gateSummaryLine(buckets)).toMatch(/no unreviewed/i);
});

test('summary line lists each non-empty bucket', () => {
  const records = [
    rec({ id: 'u', filePath: '/repo/a.ts', reviewState: 'unreviewed' }),
    rec({ id: 'd', filePath: '/repo/a.ts', reviewState: 'reviewed', lineageState: 'modified' }),
  ];
  const buckets = selectForGate(records, ['/repo/a.ts']);
  expect(hasBlockingFindings(buckets)).toBe(true);
  expect(gateSummaryLine(buckets)).toBe('1 unreviewed · 1 drifted');
});
