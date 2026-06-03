/**
 * Tests for the local Agent Trace export path (Easy Mode, no backend).
 *
 * Verifies that captureToAgentTraceDoc and captureStoreTojsonl produce
 * cursor/agent-trace 0.1.0 records with the same field mapping as the
 * Python backend (record_to_agent_trace in agent_trace_service.py).
 */

import {
  captureToAgentTraceDoc,
  captureStoreTojsonl,
  AGENT_TRACE_SPEC_VERSION,
  LINEAGELENS_SCHEMA_VERSION,
} from '../agentTrace';
import { CaptureRecord } from '../store';

// ── helpers ───────────────────────────────────────────────────────────────────

function makeRecord(overrides: Partial<CaptureRecord> = {}): CaptureRecord {
  return {
    id: '550e8400-e29b-41d4-a716-446655440000',
    timestamp: '2026-06-03T10:00:00.000Z',
    filePath: 'src/auth.ts',
    fileName: 'auth.ts',
    language: 'typescript',
    insertedCode: 'const x = 1;\nconst y = 2;\n',
    linesAdded: 2,
    workspaceFolder: '/project',
    confidence: 0.8,
    source: 'ai',
    ...overrides,
  };
}

// ── spec version ──────────────────────────────────────────────────────────────

test('AGENT_TRACE_SPEC_VERSION is 0.1.0', () => {
  expect(AGENT_TRACE_SPEC_VERSION).toBe('0.1.0');
});

// ── top-level spec fields ─────────────────────────────────────────────────────

test('captureToAgentTraceDoc emits correct version', () => {
  const doc = captureToAgentTraceDoc(makeRecord(), 'ws-test', new Date().toISOString());
  expect(doc.version).toBe(AGENT_TRACE_SPEC_VERSION);
});

test('captureToAgentTraceDoc uses record.id as document id', () => {
  const doc = captureToAgentTraceDoc(makeRecord({ id: 'my-uuid' }), 'ws-test', '2026-06-03T00:00:00Z');
  expect(doc.id).toBe('my-uuid');
});

test('captureToAgentTraceDoc uses record.timestamp', () => {
  const doc = captureToAgentTraceDoc(makeRecord({ timestamp: '2026-01-01T00:00:00Z' }), 'ws-test', '2026-06-03T00:00:00Z');
  expect(doc.timestamp).toBe('2026-01-01T00:00:00Z');
});

test('captureToAgentTraceDoc omits vcs (not captured in Easy Mode)', () => {
  const doc = captureToAgentTraceDoc(makeRecord(), 'ws-test', '2026-06-03T00:00:00Z') as any;
  expect(doc.vcs).toBeUndefined();
});

// ── files structure ───────────────────────────────────────────────────────────

test('captureToAgentTraceDoc puts filePath in files[0].path', () => {
  const doc = captureToAgentTraceDoc(makeRecord({ filePath: 'src/foo.ts' }), 'ws', '2026-06-03T00:00:00Z') as any;
  expect(doc.files).toHaveLength(1);
  expect(doc.files[0].path).toBe('src/foo.ts');
});

test('captureToAgentTraceDoc has one conversation per file', () => {
  const doc = captureToAgentTraceDoc(makeRecord(), 'ws', '2026-06-03T00:00:00Z') as any;
  expect(doc.files[0].conversations).toHaveLength(1);
});

test('captureToAgentTraceDoc has one range per conversation', () => {
  const doc = captureToAgentTraceDoc(makeRecord(), 'ws', '2026-06-03T00:00:00Z') as any;
  expect(doc.files[0].conversations[0].ranges).toHaveLength(1);
});

// ── line ranges (1-indexed, snake_case) ──────────────────────────────────────

test('captureToAgentTraceDoc start_line is 1 (Easy Mode has no cursor position)', () => {
  const doc = captureToAgentTraceDoc(makeRecord({ linesAdded: 5 }), 'ws', '2026-06-03T00:00:00Z') as any;
  expect(doc.files[0].conversations[0].ranges[0].start_line).toBe(1);
});

test('captureToAgentTraceDoc end_line equals linesAdded', () => {
  const doc = captureToAgentTraceDoc(makeRecord({ linesAdded: 7 }), 'ws', '2026-06-03T00:00:00Z') as any;
  expect(doc.files[0].conversations[0].ranges[0].end_line).toBe(7);
});

test('captureToAgentTraceDoc end_line minimum is 1', () => {
  const doc = captureToAgentTraceDoc(makeRecord({ linesAdded: 0 }), 'ws', '2026-06-03T00:00:00Z') as any;
  expect(doc.files[0].conversations[0].ranges[0].end_line).toBeGreaterThanOrEqual(1);
});

// ── contributor type (lowercase, same thresholds as Python) ──────────────────

test('contributor.type is "ai" for source=ai and confidence>=0.7', () => {
  const doc = captureToAgentTraceDoc(
    makeRecord({ source: 'ai', confidence: 0.8 }), 'ws', '2026-06-03T00:00:00Z',
  ) as any;
  expect(doc.files[0].conversations[0].contributor.type).toBe('ai');
});

test('contributor.type is "mixed" for source=ai and confidence 0.3-0.7', () => {
  const doc = captureToAgentTraceDoc(
    makeRecord({ source: 'ai', confidence: 0.5 }), 'ws', '2026-06-03T00:00:00Z',
  ) as any;
  expect(doc.files[0].conversations[0].contributor.type).toBe('mixed');
});

test('contributor.type is "unknown" for source=unknown', () => {
  const doc = captureToAgentTraceDoc(
    makeRecord({ source: 'unknown', confidence: 0.9 }), 'ws', '2026-06-03T00:00:00Z',
  ) as any;
  expect(doc.files[0].conversations[0].contributor.type).toBe('unknown');
});

test('contributor.type is "unknown" for source=paste', () => {
  const doc = captureToAgentTraceDoc(
    makeRecord({ source: 'paste', confidence: 0.95 }), 'ws', '2026-06-03T00:00:00Z',
  ) as any;
  expect(doc.files[0].conversations[0].contributor.type).toBe('unknown');
});

// ── metadata carries LineageLens-specific fields ──────────────────────────────

test('metadata contains lineagelens.workspaceId', () => {
  const doc = captureToAgentTraceDoc(makeRecord(), 'ws-acme', '2026-06-03T00:00:00Z') as any;
  expect(doc.metadata['lineagelens.workspaceId']).toBe('ws-acme');
});

test('metadata contains lineagelens.schemaVersion tag', () => {
  const doc = captureToAgentTraceDoc(makeRecord(), 'ws', '2026-06-03T00:00:00Z') as any;
  expect(doc.metadata['lineagelens.schemaVersion']).toBe(LINEAGELENS_SCHEMA_VERSION);
});

test('metadata contains lineagelens.exportedAt', () => {
  const exportedAt = '2026-06-03T12:00:00Z';
  const doc = captureToAgentTraceDoc(makeRecord(), 'ws', exportedAt) as any;
  expect(doc.metadata['lineagelens.exportedAt']).toBe(exportedAt);
});

test('metadata insertedCodePreview truncated to 120 chars', () => {
  const long = 'x = 1;\n'.repeat(50);
  const doc = captureToAgentTraceDoc(makeRecord({ insertedCode: long }), 'ws', '2026-06-03T00:00:00Z') as any;
  const preview = doc.metadata['lineagelens.insertedCodePreview'] as string;
  expect(preview).toBeDefined();
  expect(preview.length).toBeLessThanOrEqual(120);
});

test('metadata insertedCodePreview newlines replaced with ↵', () => {
  const doc = captureToAgentTraceDoc(makeRecord({ insertedCode: 'a = 1;\nb = 2;\n' }), 'ws', '2026-06-03T00:00:00Z') as any;
  const preview = doc.metadata['lineagelens.insertedCodePreview'] as string;
  expect(preview).not.toContain('\n');
  expect(preview).toContain('↵');
});

test('metadata insertedCodePreview absent when insertedCode is empty', () => {
  const doc = captureToAgentTraceDoc(makeRecord({ insertedCode: '' }), 'ws', '2026-06-03T00:00:00Z') as any;
  expect(doc.metadata['lineagelens.insertedCodePreview']).toBeUndefined();
});

test('metadata contains lineagelens.netAddedLines', () => {
  const doc = captureToAgentTraceDoc(makeRecord({ linesAdded: 4 }), 'ws', '2026-06-03T00:00:00Z') as any;
  expect(doc.metadata['lineagelens.netAddedLines']).toBe(4);
});

// ── JSONL output ──────────────────────────────────────────────────────────────

test('captureStoreTojsonl returns empty string for no records', () => {
  expect(captureStoreTojsonl([], 'ws')).toBe('');
});

test('captureStoreTojsonl produces one line per record', () => {
  const records = [makeRecord({ id: 'r1' }), makeRecord({ id: 'r2' })];
  const jsonl = captureStoreTojsonl(records, 'ws');
  const lines = jsonl.trim().split('\n').filter(Boolean);
  expect(lines).toHaveLength(2);
});

test('captureStoreTojsonl each line is valid JSON', () => {
  const records = [makeRecord({ id: 'r1' }), makeRecord({ id: 'r2' })];
  const jsonl = captureStoreTojsonl(records, 'ws');
  for (const line of jsonl.trim().split('\n').filter(Boolean)) {
    expect(() => JSON.parse(line)).not.toThrow();
  }
});

test('captureStoreTojsonl each record has correct spec version', () => {
  const records = [makeRecord({ id: 'r1' })];
  const jsonl = captureStoreTojsonl(records, 'ws');
  const parsed = JSON.parse(jsonl.trim().split('\n')[0]);
  expect(parsed.version).toBe(AGENT_TRACE_SPEC_VERSION);
});

test('captureStoreTojsonl file path roundtrips', () => {
  const records = [makeRecord({ id: 'r1', filePath: 'src/utils/helper.ts' })];
  const jsonl = captureStoreTojsonl(records, 'ws');
  const parsed = JSON.parse(jsonl.trim().split('\n')[0]) as any;
  expect(parsed.files[0].path).toBe('src/utils/helper.ts');
});

// ── key naming matches Python backend (snake_case) ────────────────────────────

test('range fields use snake_case (start_line, end_line) not camelCase', () => {
  const doc = captureToAgentTraceDoc(makeRecord(), 'ws', '2026-06-03T00:00:00Z') as any;
  const range = doc.files[0].conversations[0].ranges[0];
  expect('start_line' in range).toBe(true);
  expect('end_line' in range).toBe(true);
  expect('startLine' in range).toBe(false);
  expect('endLine' in range).toBe(false);
});

test('contributor uses model_id not modelId', () => {
  // model_id should be absent (Easy Mode has no model) — the key should not exist
  // with camelCase alternative.
  const doc = captureToAgentTraceDoc(makeRecord(), 'ws', '2026-06-03T00:00:00Z') as any;
  const contrib = doc.files[0].conversations[0].contributor;
  expect('modelId' in contrib).toBe(false);
});
