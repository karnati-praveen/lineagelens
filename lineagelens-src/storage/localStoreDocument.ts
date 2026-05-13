import type { ProvenanceRecord } from '../provenance';

export type LineageRelationshipType =
  | 'INITIAL'
  | 'EXTENDED'
  | 'REFACTORED'
  | 'MOVED'
  | 'DELETED'
  | 'UNKNOWN';

export type LocalLineage = {
  parentUuid: string | null;
  relationshipType: LineageRelationshipType;
  similarity: number | null;
  commitHash: string | null;
  updatedAtIso: string;
};

export type LocalRecordEntry = {
  uuid: string;
  record: ProvenanceRecord;
  searchText: string;
  storedAtIso: string;
  updatedAtIso: string;
  lineage: LocalLineage;
};

export type LocalStoreDocument = {
  schemaVersion: 1;
  records: LocalRecordEntry[];
  updatedAtIso: string;
};

export function createEmptyStore(): LocalStoreDocument {
  return {
    schemaVersion: 1,
    records: [],
    updatedAtIso: new Date().toISOString()
  };
}

export function sanitizeStoreDocument(value: unknown): LocalStoreDocument {
  if (!isRecord(value)) {
    return createEmptyStore();
  }

  const recordsValue = Array.isArray(value.records) ? value.records : [];
  const records: LocalRecordEntry[] = [];

  for (const entry of recordsValue) {
    if (!isRecord(entry)) {
      continue;
    }

    const uuid = toNonEmptyString(entry.uuid)?.toLowerCase();
    if (!uuid) {
      continue;
    }

    const recordCandidate = entry.record;
    if (!isRecord(recordCandidate)) {
      continue;
    }

    const normalizedRecord = recordCandidate as unknown as ProvenanceRecord;
    const lineage = sanitizeLineage(entry.lineage);

    records.push({
      uuid,
      record: normalizedRecord,
      searchText: toNonEmptyString(entry.searchText) ?? buildSearchText(normalizedRecord),
      storedAtIso: toNonEmptyString(entry.storedAtIso) ?? new Date().toISOString(),
      updatedAtIso: toNonEmptyString(entry.updatedAtIso) ?? new Date().toISOString(),
      lineage
    });
  }

  return {
    schemaVersion: 1,
    records,
    updatedAtIso: toNonEmptyString(value.updatedAtIso) ?? new Date().toISOString()
  };
}

export function buildSearchText(record: ProvenanceRecord): string {
  const parts: string[] = [];

  parts.push(record.uuid);
  parts.push(record.file.path);
  parts.push(record.file.languageId);
  parts.push(record.repository.gitBranch ?? '');
  parts.push(stringify(record.prompt.modelName));
  parts.push(stringify(record.prompt.fullMessages));
  parts.push(record.insertion.extractedInsertedCodeBlock);
  parts.push(record.insertion.surroundingContext.before);
  parts.push(record.insertion.surroundingContext.after);
  parts.push(stringify(record.contextSnapshot));

  return parts.join('\n').toLowerCase();
}

function sanitizeLineage(value: unknown): LocalLineage {
  if (!isRecord(value)) {
    return {
      parentUuid: null,
      relationshipType: 'INITIAL',
      similarity: null,
      commitHash: null,
      updatedAtIso: new Date().toISOString()
    };
  }

  const relationshipType = toNonEmptyString(value.relationshipType);

  return {
    parentUuid: toNonEmptyString(value.parentUuid) ?? null,
    relationshipType: isValidLineageRelationship(relationshipType) ? relationshipType : 'UNKNOWN',
    similarity: toFiniteNumber(value.similarity),
    commitHash: toNonEmptyString(value.commitHash) ?? null,
    updatedAtIso: toNonEmptyString(value.updatedAtIso) ?? new Date().toISOString()
  };
}

function isValidLineageRelationship(value?: string): value is LineageRelationshipType {
  return (
    value === 'INITIAL' ||
    value === 'EXTENDED' ||
    value === 'REFACTORED' ||
    value === 'MOVED' ||
    value === 'DELETED' ||
    value === 'UNKNOWN'
  );
}

function stringify(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }

  if (value === null || typeof value === 'undefined') {
    return 'n/a';
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function toNonEmptyString(value: unknown): string | undefined {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : undefined;
  }

  if (value === null || typeof value === 'undefined') {
    return undefined;
  }

  const text = String(value).trim();
  return text.length > 0 ? text : undefined;
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === 'string') {
    const parsed = Number(value.trim());
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}