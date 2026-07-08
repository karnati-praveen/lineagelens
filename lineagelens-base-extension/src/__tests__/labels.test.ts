import { codeLensTitle, hoverMarkdown, sourceLabel } from '../ide/labels';
import { CaptureRecord, RiskSignal } from '../store';

function sig(over: Partial<RiskSignal> = {}): RiskSignal {
  return { id: 'r', label: 'auth', category: 'auth', severity: 'high', message: 'm', ...over };
}

function rec(over: Partial<CaptureRecord> = {}): CaptureRecord {
  return {
    id: 'id1',
    timestamp: '2026-06-25T10:00:00.000Z',
    filePath: '/proj/bar.ts',
    fileName: 'bar.ts',
    language: 'typescript',
    insertedCode: 'const x = 1;',
    linesAdded: 12,
    workspaceFolder: 'proj',
    confidence: 0.62,
    source: 'ai',
    schemaVersion: 2,
    reviewState: 'unreviewed',
    lineageState: 'original',
    ...over,
  };
}

// ── codeLensTitle ─────────────────────────────────────────────────────────────

test('codeLensTitle shows origin, line count, and unreviewed status', () => {
  expect(codeLensTitle(rec(), 'original')).toBe('$(sparkle) AI capture · 12 lines · unreviewed');
});

test('codeLensTitle reflects review state over lineage', () => {
  expect(codeLensTitle(rec({ reviewState: 'reviewed' }), 'modified'))
    .toBe('$(sparkle) AI capture · 12 lines · reviewed');
});

test('codeLensTitle surfaces lineage drift while unreviewed', () => {
  expect(codeLensTitle(rec(), 'moved')).toContain('moved since capture');
  expect(codeLensTitle(rec(), 'modified')).toContain('modified since capture');
  expect(codeLensTitle(rec(), 'deleted')).toContain('no longer present');
});

test('codeLensTitle singularizes a one-line capture', () => {
  expect(codeLensTitle(rec({ linesAdded: 1 }), 'original')).toContain('· 1 line ·');
});

test('codeLensTitle uses a neutral prefix for non-AI sources', () => {
  expect(codeLensTitle(rec({ source: 'unknown' }), 'original')).toContain('Capture ·');
  expect(codeLensTitle(rec({ source: 'paste' }), 'original')).toContain('Pasted capture ·');
});

test('codeLensTitle appends the top risk label', () => {
  expect(codeLensTitle(rec({ riskSignals: [sig({ label: 'auth' })] }), 'original'))
    .toContain('· risk: auth');
});

test('codeLensTitle shows +N when there are multiple risk signals', () => {
  const out = codeLensTitle(rec({ riskSignals: [sig({ label: 'secrets' }), sig({ label: 'auth' })] }), 'original');
  expect(out).toContain('· risk: secrets +1');
});

test('codeLensTitle has no risk segment when there are no signals', () => {
  expect(codeLensTitle(rec({ riskSignals: [] }), 'original')).not.toContain('risk:');
});

// ── hoverMarkdown ─────────────────────────────────────────────────────────────

test('hoverMarkdown includes file, confidence, lines, and status', () => {
  const md = hoverMarkdown(rec(), 'original');
  expect(md).toContain('`bar.ts`');
  expect(md).toContain('62% confidence');
  expect(md).toContain('+12');
  expect(md).toContain('unreviewed');
  // Honesty: detection is observed, AI origin inferred.
  expect(md).toContain('inferred');
});

test('hoverMarkdown is deterministic for a fixed record', () => {
  expect(hoverMarkdown(rec(), 'original')).toBe(hoverMarkdown(rec(), 'original'));
  expect(hoverMarkdown(rec(), 'original')).toContain('2026-06-25T10:00:00.000Z');
});

test('hoverMarkdown lists risk signals when present', () => {
  const md = hoverMarkdown(rec({ riskSignals: [sig({ label: 'sql', severity: 'medium' })] }), 'original');
  expect(md).toContain('Risk signals:');
  expect(md).toContain('sql (medium)');
});

test('hoverMarkdown omits the risk line when there are no signals', () => {
  expect(hoverMarkdown(rec({ riskSignals: [] }), 'original')).not.toContain('Risk signals:');
});

// ── sourceLabel ───────────────────────────────────────────────────────────────

test('sourceLabel maps each source', () => {
  expect(sourceLabel('ai')).toContain('AI');
  expect(sourceLabel('paste')).toContain('Paste');
  expect(sourceLabel('unknown')).toContain('Unknown');
});
