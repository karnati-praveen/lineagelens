/**
 * Local evidence capsule (`.llcapsule`) — pure, no vscode (blueprint §7 Feature 6).
 *
 * A self-describing, verifiable bundle of the capture store: full records with
 * their range bindings, review states, and tamper-evident chain, plus the
 * verification verdict at export time and instructions for an offline verifier.
 *
 * Capsules are full-fidelity by design — redacting `insertedCode` would defeat
 * content verification — so the export command warns before writing one that
 * contains detected secrets.
 */

import { CaptureRecord, CAPTURE_SCHEMA_VERSION } from '../store';
import { GENESIS_HASH } from './hashChain';
import { verifyEvidence, EvidenceReport } from './verifier';

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

/** Build an evidence capsule from the given records (caller decides what to pass). */
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
