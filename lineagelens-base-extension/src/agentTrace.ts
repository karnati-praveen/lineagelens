/**
 * Local Agent Trace export for Easy Mode (no backend required).
 *
 * Converts CaptureRecord[] → cursor/agent-trace 0.1.0 JSONL.
 * The field mapping mirrors the Python backend (record_to_agent_trace in
 * lineagelens-backend/app/services/agent_trace_service.py) so that
 * backend-export and extension-export produce identical-shape records.
 *
 * Spec: https://github.com/cursor/agent-trace
 *
 * Fields mapped to null (documented as TODO:SPEC to match the Python side):
 *   vcs          — commit SHA not captured at insertion time
 *   tool.version — capturing tool version not available in extension
 *   content_hash — per-range code hash not computed
 *   url          — conversationId is not a URL
 */

import { CaptureRecord } from './store';

/** cursor/agent-trace spec version */
export const AGENT_TRACE_SPEC_VERSION = '0.1.0';
/** Internal LineageLens schema tag, stored in metadata for backward compat. */
export const LINEAGELENS_SCHEMA_VERSION = 'lineagelens-agent-trace/1';

/** Derive contributor.type from confidence score (spec values are all lowercase). */
function deriveContributorType(
  confidence: number | undefined,
  hasModel: boolean,
): 'human' | 'ai' | 'mixed' | 'unknown' {
  if (!hasModel) { return 'unknown'; }
  if (confidence === undefined) { return 'unknown'; }
  if (confidence >= 0.7) { return 'ai'; }
  if (confidence >= 0.3) { return 'mixed'; }
  return 'unknown';
}

/** Truncate inserted code to 120 chars and replace newlines (same as Python). */
function buildPreview(insertedCode: string): string | null {
  if (!insertedCode) { return null; }
  return insertedCode.slice(0, 120).replace(/\n/g, '↵');
}

/** Build one AgentTraceDocument JSON object from a CaptureRecord. */
export function captureToAgentTraceDoc(
  record: CaptureRecord,
  workspaceId: string,
  exportedAt: string,
): Record<string, unknown> {
  const contributorType = deriveContributorType(record.confidence, false);
  // Easy Mode has no tool/model data; contributor.type is based on source.
  // source === 'ai' means a known AI extension was active during insertion.
  const effectiveType: 'human' | 'ai' | 'mixed' | 'unknown' =
    record.source === 'ai'
      ? deriveContributorType(record.confidence, true)
      : deriveContributorType(record.confidence, false);

  // cursor_line is not available in Easy Mode; start_line defaults to 1.
  const startLine = 1;
  const endLine = Math.max(1, record.linesAdded);

  const contributor: Record<string, unknown> = { type: effectiveType };
  // model_id: Easy Mode cannot identify the model; omit rather than invent.
  // TODO:SPEC: populate model_id when the proxy or Copilot API exposes it.

  const metadata: Record<string, unknown> = {
    'lineagelens.schemaVersion': LINEAGELENS_SCHEMA_VERSION,
    'lineagelens.workspaceId': workspaceId,
    'lineagelens.exportedAt': exportedAt,
    'lineagelens.confidence': { score: record.confidence, level: null },
  };
  const preview = buildPreview(record.insertedCode);
  if (preview !== null) {
    metadata['lineagelens.insertedCodePreview'] = preview;
  }
  metadata['lineagelens.netAddedLines'] = record.linesAdded;

  const doc: Record<string, unknown> = {
    version: AGENT_TRACE_SPEC_VERSION,
    id: record.id,
    timestamp: record.timestamp,
    // vcs: TODO:SPEC — not captured at insertion time in Easy Mode
    files: [
      {
        path: record.filePath,
        conversations: [
          {
            // url: TODO:SPEC — no conversation URL available in Easy Mode
            contributor,
            ranges: [{ start_line: startLine, end_line: endLine }],
          },
        ],
      },
    ],
    metadata,
  };

  return doc;
}

/** Serialize an array of CaptureRecords to Agent Trace JSONL (one doc per line). */
export function captureStoreTojsonl(
  records: CaptureRecord[],
  workspaceId: string,
): string {
  const exportedAt = new Date().toISOString();
  const lines = records.map(r =>
    JSON.stringify(captureToAgentTraceDoc(r, workspaceId, exportedAt)),
  );
  return lines.join('\n') + (lines.length > 0 ? '\n' : '');
}
