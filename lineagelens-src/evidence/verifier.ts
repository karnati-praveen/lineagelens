/**
 * Offline evidence verifier — pure, no vscode.
 */

import { type CaptureRecord, CAPTURE_SCHEMA_VERSION } from './types';
import { verifyChain, type ChainVerification } from './hashChain';

export interface EvidenceReport extends ChainVerification {
  schemaIssues: string[];
  summary: string;
}

function buildSummary(chain: ChainVerification, schemaIssues: string[]): string {
  if (chain.total === 0) {
    return 'No captures to verify.';
  }
  if (!chain.ok) {
    const parts: string[] = [];
    if (chain.tampered.length) { parts.push(`${chain.tampered.length} tampered`); }
    if (chain.unsealed.length) { parts.push(`${chain.unsealed.length} unsealed`); }
    return `✗ Evidence check failed: ${parts.join(', ')} of ${chain.total} captures.`;
  }
  if (schemaIssues.length) {
    return `✓ ${chain.verified} captures verified, but ${schemaIssues.length} use a newer schema — update LineageLens.`;
  }
  if (chain.breaks > 0) {
    return `✓ ${chain.verified} captures verified; ${chain.breaks} chain break(s) (likely deleted captures).`;
  }
  return `✓ All ${chain.verified} captures verified — evidence chain intact.`;
}

export function verifyEvidence(records: CaptureRecord[]): EvidenceReport {
  const chain = verifyChain(records);
  const schemaIssues = records
    .filter((r) => (r.schemaVersion ?? 0) > CAPTURE_SCHEMA_VERSION)
    .map((r) => r.id);
  return { ...chain, schemaIssues, summary: buildSummary(chain, schemaIssues) };
}
