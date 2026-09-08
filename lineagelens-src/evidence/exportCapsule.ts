/**
 * Local evidence capsule (`.llcapsule`) — pure, no vscode.
 */

import { type CaptureRecord, CAPTURE_SCHEMA_VERSION } from './types';
import { GENESIS_HASH } from './hashChain';
import { verifyEvidence, type EvidenceReport } from './verifier';

export const CAPSULE_FORMAT = 'lineagelens-capsule';
export const CAPSULE_VERSION = 1;

const VERIFIER_INSTRUCTIONS =
  'For each record, recompute eventHash = sha256(canonicalJSON({id,timestamp,filePath,' +
  'language,insertedCode,linesAdded,startLine,endLine}) + prevHash) and compare to the stored ' +
  'eventHash. The first record links to genesisHash. A prevHash that matches no record indicates ' +
  'a deleted capture (continuity break), not tampering.';

export interface Capsule {
  format: string;
  capsuleVersion: number;
  schemaVersion: number;
  exportedAt: string;
  workspaceId: string;
  genesisHash: string;
  verification: EvidenceReport;
  records: CaptureRecord[];
  verifierInstructions: string;
}

export function buildCapsule(
  records: CaptureRecord[],
  workspaceId: string,
  exportedAt: string,
): Capsule {
  return {
    format: CAPSULE_FORMAT,
    capsuleVersion: CAPSULE_VERSION,
    schemaVersion: CAPTURE_SCHEMA_VERSION,
    exportedAt,
    workspaceId,
    genesisHash: GENESIS_HASH,
    verification: verifyEvidence(records),
    records,
    verifierInstructions: VERIFIER_INSTRUCTIONS,
  };
}
