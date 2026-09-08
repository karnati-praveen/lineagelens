export type ReviewState = 'unreviewed' | 'reviewed' | 'needs_changes' | 'rejected' | 'accepted';
export type LineageState = 'original' | 'modified' | 'moved' | 'deleted' | 'unknown';

export interface RiskSignal {
  id: string;
  severity: 'low' | 'medium' | 'high';
  message: string;
}

export interface CaptureRecord {
  id: string;
  timestamp: string;
  filePath: string;
  fileName?: string;
  language: string;
  insertedCode: string;
  linesAdded?: number;
  startLine?: number;
  endLine?: number;
  modelName?: string;
  reviewState?: ReviewState;
  lineageState?: LineageState;
  riskSignals?: RiskSignal[];
  eventHash?: string;
  prevHash?: string;
  schemaVersion?: number;
}

export const CAPTURE_SCHEMA_VERSION = 2;
