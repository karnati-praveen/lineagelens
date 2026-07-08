/**
 * Backend ingest payload builder — pure, no vscode.
 *
 * Two shapes: `legacy` (the original file-level payload every backend accepts)
 * and `evidence-v2` (the legacy fields plus the local trust layer — range,
 * hashes, review state, lineage, risk signals, git — sent losslessly when the
 * backend advertises support). Secrets are scrubbed at this egress boundary.
 */

import { CaptureRecord } from '../store';
import { redactSecrets } from '../secrets';

export type IngestCapability = 'legacy' | 'evidence-v2';

export interface PayloadOptions {
  workspaceId: string;
  /** Scrub detected secrets from the inserted text before it leaves the machine. */
  redact: boolean;
  capability: IngestCapability;
}

/** Build the ingest payload for a record at the given backend capability level. */
export function buildIngestPayload(
  record: CaptureRecord,
  opts: PayloadOptions,
): Record<string, unknown> {
  const insertedText = opts.redact ? redactSecrets(record.insertedCode).text : record.insertedCode;

  const base: Record<string, unknown> = {
    id: record.id,
    timestampIso: record.timestamp,
    filePath: record.filePath,
    insertedText,
    netAddedLines: record.linesAdded,
    workspaceId: opts.workspaceId,
    languageId: record.language,
    captureStatus: 'file_diff',
    source: { shim: 'lineagelens-base-extension', ide: 'vscode' },
  };

  if (opts.capability === 'legacy') {
    return base;
  }

  // evidence-v2: attach the local trust layer without losing anything.
  return {
    ...base,
    schemaVersion: record.schemaVersion ?? null,
    confidence: record.confidence ?? null,
    // `source` above is the shim descriptor; the detected origin goes separately.
    detectedSource: record.source ?? null,
    startLine: record.startLine ?? null,
    endLine: record.endLine ?? null,
    rangeContentHash: record.rangeContentHash ?? null,
    lineageState: record.lineageState ?? null,
    reviewState: record.reviewState ?? null,
    reviewNote: record.reviewNote ?? null,
    gitBranch: record.gitBranch ?? null,
    gitCommit: record.gitCommit ?? null,
    eventHash: record.eventHash ?? null,
    prevHash: record.prevHash ?? null,
    riskSignals: record.riskSignals ?? [],
  };
}
