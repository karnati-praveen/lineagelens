/**
 * Offline evidence verifier — pure, no vscode (blueprint §7 Feature 15).
 *
 * Re-walks the local capture store: confirms the tamper-evident chain, that no
 * record uses a schema newer than this build understands, and produces a plain
 * human summary for the `Verify Local Evidence Store` command and the capsule.
 */

import { CaptureRecord, CAPTURE_SCHEMA_VERSION } from '../store';
import { verifyChain, ChainVerification } from './hashChain';

export interface EvidenceReport extends ChainVerification {
  /** Ids whose schemaVersion is newer than this build supports. */
  schemaIssues: string[];
  /** Human one-line verdict. */
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

/** Verify the integrity of a set of capture records. */
export function verifyEvidence(records: CaptureRecord[]): EvidenceReport {
  const chain = verifyChain(records);
  const schemaIssues = records
    .filter((r) => (r.schemaVersion ?? 0) > CAPTURE_SCHEMA_VERSION)
    .map((r) => r.id);
  return { ...chain, schemaIssues, summary: buildSummary(chain, schemaIssues) };
}
