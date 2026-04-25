import neo4j, { Driver, Session } from 'neo4j-driver';
import simpleGit, { SimpleGit } from 'simple-git';
import { v4 as uuidv4 } from 'uuid';
import { normalizeAST } from './provenance';

const DEFAULT_JACCARD_THRESHOLD = 0.7;
const MAX_BLOCK_SOURCE_LENGTH = 200_000;

type LineageParserLanguage = 'javascript' | 'typescript' | 'tsx' | 'python';

type TreeSitterParser = {
  setLanguage: (language: unknown) => void;
  parse: (source: string) => { rootNode?: TreeSitterNode };
};

type TreeSitterPoint = {
  row: number;
  column: number;
};

type TreeSitterNode = {
  type?: string;
  startIndex?: number;
  endIndex?: number;
  startPosition?: TreeSitterPoint;
  endPosition?: TreeSitterPoint;
  namedChildCount?: number;
  namedChild?: (index: number) => TreeSitterNode | null;
};

type CommitFileChangeStatus = 'A' | 'M' | 'D' | 'R' | 'C' | 'T' | 'U' | 'X' | 'B';

type CommitFileChange = {
  status: CommitFileChangeStatus;
  oldPath: string | null;
  newPath: string | null;
};

type ParsedSourceBlock = {
  runtimeId: string;
  filePath: string;
  language: LineageParserLanguage;
  code: string;
  astTokens: string[];
  startLine: number;
  endLine: number;
};

type TrackedBlock = {
  blockId: string;
  versionId: string;
  filePath: string;
  language: string | null;
  code: string;
  astTokens: string[];
  startLine: number | null;
  endLine: number | null;
};

type BlockMatch = {
  nextBlock: ParsedSourceBlock;
  similarity: number;
  previousTokens: string[];
};

export type LineageRelationshipType =
  | 'EXTENDED'
  | 'REFACTORED'
  | 'DELETED'
  | 'MOVED'
  | 'SPLIT';

const RELATIONSHIP_TYPE_TO_CYPHER: Record<LineageRelationshipType, string> = {
  EXTENDED: 'EXTENDED',
  REFACTORED: 'REFACTORED',
  DELETED: 'DELETED',
  MOVED: 'MOVED',
  SPLIT: 'SPLIT'
};

export type LineageBuilderOptions = {
  repositoryPath: string;
  jaccardThreshold?: number;
  logger?: (message: string) => void;
  neo4j: {
    uri: string;
    username: string;
    password: string;
    database?: string;
  };
};

export type LineageProcessResult = {
  commitHash: string;
  parentCommitHash: string | null;
  changedFiles: number;
  trackedBlocks: number;
  relationshipCounts: Record<LineageRelationshipType, number>;
  processedAtIso: string;
};

const parserLanguageCache = new Map<LineageParserLanguage, unknown>();

export class LineageBuilder {
  private readonly git: SimpleGit;
  private readonly driver: Driver;
  private readonly database: string | undefined;
  private readonly jaccardThreshold: number;
  private readonly logger: (message: string) => void;

  public constructor(options: LineageBuilderOptions) {
    this.git = simpleGit(options.repositoryPath);
    this.driver = neo4j.driver(
      options.neo4j.uri,
      neo4j.auth.basic(options.neo4j.username, options.neo4j.password)
    );
    this.database = options.neo4j.database;
    this.jaccardThreshold = clampThreshold(options.jaccardThreshold ?? DEFAULT_JACCARD_THRESHOLD);
    this.logger = options.logger ?? (() => undefined);
  }

  public async close(): Promise<void> {
    await this.driver.close();
  }

  public async processCommit(commitHash: string): Promise<LineageProcessResult> {
    const normalizedCommitHash = commitHash.trim();
    if (normalizedCommitHash.length === 0) {
      throw new Error('processCommit requires a non-empty commitHash.');
    }

    const parentCommitHash = await this.resolveParentCommitHash(normalizedCommitHash);
    const changedFiles = await this.getChangedFiles(normalizedCommitHash, parentCommitHash);
    const changedFilePaths = collectUniqueChangedPaths(changedFiles);
    const afterBlocks = await this.collectAfterBlocks(changedFiles, normalizedCommitHash);

    const result: LineageProcessResult = {
      commitHash: normalizedCommitHash,
      parentCommitHash,
      changedFiles: changedFiles.length,
      trackedBlocks: 0,
      relationshipCounts: {
        EXTENDED: 0,
        REFACTORED: 0,
        DELETED: 0,
        MOVED: 0,
        SPLIT: 0
      },
      processedAtIso: new Date().toISOString()
    };

    if (changedFilePaths.length === 0) {
      this.logger('LineageBuilder: no changed files found for commit ' + normalizedCommitHash + '.');
      return result;
    }

    const session = this.createSession();

    try {
      const trackedBlocks = await this.loadTrackedBlocks(session, changedFilePaths);
      result.trackedBlocks = trackedBlocks.length;

      if (trackedBlocks.length === 0) {
        this.logger(
          'LineageBuilder: no tracked AI-generated blocks found in changed files for commit ' +
            normalizedCommitHash +
            '.'
        );
        return result;
      }

      const assignedAfterBlockIds = new Set<string>();

      for (const trackedBlock of trackedBlocks) {
        const previousTokens =
          trackedBlock.astTokens.length > 0
            ? trackedBlock.astTokens
            : normalizeAST(trackedBlock.code, trackedBlock.language ?? trackedBlock.filePath);

        const candidateMatches = this.findCandidateMatches(
          trackedBlock,
          previousTokens,
          afterBlocks,
          assignedAfterBlockIds
        );

        if (candidateMatches.length === 0) {
          await this.persistDeletedVersion(session, trackedBlock, normalizedCommitHash);
          result.relationshipCounts.DELETED += 1;
          continue;
        }

        if (candidateMatches.length > 1) {
          await this.persistSplitVersions(
            session,
            trackedBlock,
            candidateMatches,
            normalizedCommitHash,
            assignedAfterBlockIds,
            result
          );
          continue;
        }

        const bestMatch = candidateMatches[0];
        assignedAfterBlockIds.add(bestMatch.nextBlock.runtimeId);

        const relationshipType = classifyRelationshipType({
          previousFilePath: trackedBlock.filePath,
          nextFilePath: bestMatch.nextBlock.filePath,
          previousTokens,
          nextTokens: bestMatch.nextBlock.astTokens,
          similarity: bestMatch.similarity
        });

        await this.persistMatchedVersion(
          session,
          trackedBlock.blockId,
          trackedBlock.versionId,
          bestMatch,
          relationshipType,
          normalizedCommitHash
        );

        result.relationshipCounts[relationshipType] += 1;
      }
    } finally {
      await session.close();
    }

    return result;
  }

  private createSession(): Session {
    if (this.database) {
      return this.driver.session({ database: this.database });
    }

    return this.driver.session();
  }

  private async resolveParentCommitHash(commitHash: string): Promise<string | null> {
    const line = (await this.git.raw(['rev-list', '--parents', '-n', '1', commitHash])).trim();
    if (!line) {
      return null;
    }

    const tokens = line.split(/\s+/).filter((token) => token.length > 0);
    if (tokens.length < 2) {
      return null;
    }

    return tokens[1] ?? null;
  }

  private async getChangedFiles(
    commitHash: string,
    parentCommitHash: string | null
  ): Promise<CommitFileChange[]> {
    const diffOutput = parentCommitHash
      ? await this.git.diff([
          '--name-status',
          '--find-renames',
          '--find-copies',
          parentCommitHash + '..' + commitHash
        ])
      : await this.git.show([
          '--name-status',
          '--pretty=format:',
          '--find-renames',
          '--find-copies',
          commitHash
        ]);

    return parseNameStatusOutput(diffOutput);
  }

  private async collectAfterBlocks(
    changedFiles: readonly CommitFileChange[],
    commitHash: string
  ): Promise<ParsedSourceBlock[]> {
    const allBlocks: ParsedSourceBlock[] = [];

    for (const changedFile of changedFiles) {
      if (!changedFile.newPath || changedFile.status === 'D') {
        continue;
      }

      const afterSource = await this.readFileAtCommit(commitHash, changedFile.newPath);
      if (!afterSource || afterSource.trim().length === 0) {
        continue;
      }

      const parsedBlocks = extractBlocksFromSource(afterSource, changedFile.newPath);
      allBlocks.push(...parsedBlocks);
    }

    return allBlocks;
  }

  private async readFileAtCommit(commitHash: string, filePath: string): Promise<string | null> {
    try {
      const source = await this.git.show([commitHash + ':' + filePath]);
      return source;
    } catch {
      return null;
    }
  }

  private async loadTrackedBlocks(
    session: Session,
    changedFilePaths: readonly string[]
  ): Promise<TrackedBlock[]> {
    const query = `
      MATCH (b:AIGeneratedBlock)-[:LATEST_VERSION]->(v:ProvenanceBlockVersion)
      WHERE v.filePath IN $filePaths
      RETURN
        b.blockId AS blockId,
        v.versionId AS versionId,
        v.filePath AS filePath,
        v.language AS language,
        v.code AS code,
        v.astTokens AS astTokens,
        v.startLine AS startLine,
        v.endLine AS endLine
    `;

    const queryResult = await session.run(query, {
      filePaths: changedFilePaths
    });

    return queryResult.records.map((record) => {
      const astTokens = toStringArray(record.get('astTokens'));

      return {
        blockId: toStringValue(record.get('blockId')),
        versionId: toStringValue(record.get('versionId')),
        filePath: toStringValue(record.get('filePath')),
        language: toNullableString(record.get('language')),
        code: toStringValue(record.get('code')),
        astTokens,
        startLine: toNullableNumber(record.get('startLine')),
        endLine: toNullableNumber(record.get('endLine'))
      };
    });
  }

  private findCandidateMatches(
    trackedBlock: TrackedBlock,
    previousTokens: readonly string[],
    afterBlocks: readonly ParsedSourceBlock[],
    assignedAfterBlockIds: ReadonlySet<string>
  ): BlockMatch[] {
    if (previousTokens.length === 0) {
      return [];
    }

    const candidates: BlockMatch[] = [];

    for (const nextBlock of afterBlocks) {
      if (assignedAfterBlockIds.has(nextBlock.runtimeId)) {
        continue;
      }

      if (nextBlock.astTokens.length === 0) {
        continue;
      }

      const similarity = computeJaccardSimilarity(previousTokens, nextBlock.astTokens);
      if (similarity < this.jaccardThreshold) {
        continue;
      }

      candidates.push({
        nextBlock,
        similarity,
        previousTokens: [...previousTokens]
      });
    }

    candidates.sort((left, right) => {
      if (left.similarity !== right.similarity) {
        return right.similarity - left.similarity;
      }

      if (left.nextBlock.filePath === trackedBlock.filePath && right.nextBlock.filePath !== trackedBlock.filePath) {
        return -1;
      }

      if (right.nextBlock.filePath === trackedBlock.filePath && left.nextBlock.filePath !== trackedBlock.filePath) {
        return 1;
      }

      return left.nextBlock.startLine - right.nextBlock.startLine;
    });

    return candidates;
  }

  private async persistMatchedVersion(
    session: Session,
    blockId: string,
    previousVersionId: string,
    match: BlockMatch,
    relationshipType: Exclude<LineageRelationshipType, 'DELETED' | 'SPLIT'>,
    commitHash: string
  ): Promise<void> {
    const versionId = uuidv4();
    const nowIso = new Date().toISOString();

    await this.upsertVersionNode(session, {
      blockId,
      versionId,
      commitHash,
      filePath: match.nextBlock.filePath,
      language: match.nextBlock.language,
      code: match.nextBlock.code,
      astTokens: match.nextBlock.astTokens,
      startLine: match.nextBlock.startLine,
      endLine: match.nextBlock.endLine,
      deleted: false,
      createdAtIso: nowIso
    });

    await this.createLineageEdge(session, {
      relationshipType,
      fromVersionId: previousVersionId,
      toVersionId: versionId,
      similarity: match.similarity,
      commitHash,
      metadata: {
        mode: 'single-match'
      }
    });
  }

  private async persistSplitVersions(
    session: Session,
    trackedBlock: TrackedBlock,
    candidateMatches: readonly BlockMatch[],
    commitHash: string,
    assignedAfterBlockIds: Set<string>,
    result: LineageProcessResult
  ): Promise<void> {
    const nowIso = new Date().toISOString();

    for (let index = 0; index < candidateMatches.length; index += 1) {
      const match = candidateMatches[index];
      const isPrimary = index === 0;
      const targetBlockId = isPrimary ? trackedBlock.blockId : uuidv4();
      const versionId = uuidv4();

      await this.upsertVersionNode(session, {
        blockId: targetBlockId,
        versionId,
        commitHash,
        filePath: match.nextBlock.filePath,
        language: match.nextBlock.language,
        code: match.nextBlock.code,
        astTokens: match.nextBlock.astTokens,
        startLine: match.nextBlock.startLine,
        endLine: match.nextBlock.endLine,
        deleted: false,
        createdAtIso: nowIso
      });

      await this.createLineageEdge(session, {
        relationshipType: 'SPLIT',
        fromVersionId: trackedBlock.versionId,
        toVersionId: versionId,
        similarity: match.similarity,
        commitHash,
        metadata: {
          mode: 'split-match',
          primarySplitBranch: isPrimary
        }
      });

      assignedAfterBlockIds.add(match.nextBlock.runtimeId);
      result.relationshipCounts.SPLIT += 1;
    }
  }

  private async persistDeletedVersion(
    session: Session,
    trackedBlock: TrackedBlock,
    commitHash: string
  ): Promise<void> {
    const versionId = uuidv4();
    const nowIso = new Date().toISOString();

    await this.upsertVersionNode(session, {
      blockId: trackedBlock.blockId,
      versionId,
      commitHash,
      filePath: trackedBlock.filePath,
      language: trackedBlock.language ?? detectLanguageFromFilePath(trackedBlock.filePath),
      code: '',
      astTokens: [],
      startLine: trackedBlock.startLine,
      endLine: trackedBlock.endLine,
      deleted: true,
      createdAtIso: nowIso
    });

    await this.createLineageEdge(session, {
      relationshipType: 'DELETED',
      fromVersionId: trackedBlock.versionId,
      toVersionId: versionId,
      similarity: 0,
      commitHash,
      metadata: {
        mode: 'no-match'
      }
    });
  }

  private async upsertVersionNode(
    session: Session,
    input: {
      blockId: string;
      versionId: string;
      commitHash: string;
      filePath: string;
      language: string;
      code: string;
      astTokens: string[];
      startLine: number | null;
      endLine: number | null;
      deleted: boolean;
      createdAtIso: string;
    }
  ): Promise<void> {
    const query = `
      MERGE (b:AIGeneratedBlock {blockId: $blockId})
      ON CREATE SET b.createdAtIso = $createdAtIso
      SET b.updatedAtIso = $createdAtIso

      MERGE (v:ProvenanceBlockVersion {versionId: $versionId})
      SET
        v.blockId = $blockId,
        v.commitHash = $commitHash,
        v.filePath = $filePath,
        v.language = $language,
        v.code = $code,
        v.astTokens = $astTokens,
        v.startLine = $startLine,
        v.endLine = $endLine,
        v.deleted = $deleted,
        v.updatedAtIso = $createdAtIso,
        v.createdAtIso = coalesce(v.createdAtIso, $createdAtIso)

      MERGE (b)-[:HAS_VERSION]->(v)

      WITH b, v
      OPTIONAL MATCH (b)-[oldLatest:LATEST_VERSION]->(:ProvenanceBlockVersion)
      DELETE oldLatest
      MERGE (b)-[:LATEST_VERSION]->(v)
    `;

    await session.run(query, {
      blockId: input.blockId,
      versionId: input.versionId,
      commitHash: input.commitHash,
      filePath: input.filePath,
      language: input.language,
      code: input.code,
      astTokens: input.astTokens,
      startLine: input.startLine,
      endLine: input.endLine,
      deleted: input.deleted,
      createdAtIso: input.createdAtIso
    });
  }

  private async createLineageEdge(
    session: Session,
    input: {
      relationshipType: LineageRelationshipType;
      fromVersionId: string;
      toVersionId: string;
      similarity: number;
      commitHash: string;
      metadata: Record<string, unknown>;
    }
  ): Promise<void> {
    const nowIso = new Date().toISOString();
    const relationshipType = RELATIONSHIP_TYPE_TO_CYPHER[input.relationshipType];
    if (!relationshipType) {
      throw new Error('Unsupported lineage relationship type.');
    }

    const query = `
      MATCH (previous:ProvenanceBlockVersion {versionId: $fromVersionId})
      MATCH (next:ProvenanceBlockVersion {versionId: $toVersionId})
      MERGE (previous)-[r:${relationshipType}]->(next)
      SET
        r.similarity = $similarity,
        r.jaccardThreshold = $jaccardThreshold,
        r.commitHash = $commitHash,
        r.metadata = $metadata,
        r.updatedAtIso = $nowIso,
        r.createdAtIso = coalesce(r.createdAtIso, $nowIso)
    `;

    await session.run(query, {
      fromVersionId: input.fromVersionId,
      toVersionId: input.toVersionId,
      similarity: Number(input.similarity.toFixed(6)),
      jaccardThreshold: this.jaccardThreshold,
      commitHash: input.commitHash,
      metadata: input.metadata,
      nowIso
    });
  }
}

function extractBlocksFromSource(source: string, filePath: string): ParsedSourceBlock[] {
  const boundedSource = source.slice(0, MAX_BLOCK_SOURCE_LENGTH);
  if (boundedSource.trim().length === 0) {
    return [];
  }

  const language = detectLanguageFromFilePath(filePath, boundedSource);
  const topLevelNodes = parseTopLevelNodes(boundedSource, language);

  const blocks: ParsedSourceBlock[] = [];

  for (const node of topLevelNodes) {
    const startIndex = typeof node.startIndex === 'number' ? node.startIndex : 0;
    const endIndex = typeof node.endIndex === 'number' ? node.endIndex : 0;
    if (endIndex <= startIndex) {
      continue;
    }

    const code = boundedSource.slice(startIndex, endIndex).trim();
    if (!code) {
      continue;
    }

    const astTokens = normalizeAST(code, language);
    if (astTokens.length === 0) {
      continue;
    }

    const startLine = pointRow(node.startPosition) + 1;
    const endLine = Math.max(startLine, pointRow(node.endPosition) + 1);

    blocks.push({
      runtimeId: uuidv4(),
      filePath,
      language,
      code,
      astTokens,
      startLine,
      endLine
    });
  }

  if (blocks.length === 0) {
    const astTokens = normalizeAST(boundedSource, language);
    if (astTokens.length > 0) {
      const totalLineCount = boundedSource.split(/\r\n|\r|\n/).length;
      blocks.push({
        runtimeId: uuidv4(),
        filePath,
        language,
        code: boundedSource,
        astTokens,
        startLine: 1,
        endLine: totalLineCount
      });
    }
  }

  return blocks;
}

function parseTopLevelNodes(source: string, language: LineageParserLanguage): TreeSitterNode[] {
  try {
    const ParserConstructor = resolveParserConstructor();
    if (!ParserConstructor) {
      return [];
    }

    const parser = new ParserConstructor() as TreeSitterParser;
    const grammar = resolveParserGrammar(language);
    if (!grammar) {
      return [];
    }

    parser.setLanguage(grammar);
    const tree = parser.parse(source);
    const rootNode = tree.rootNode;
    if (!rootNode) {
      return [];
    }

    const nodes: TreeSitterNode[] = [];
    const namedChildCount = typeof rootNode.namedChildCount === 'number' ? rootNode.namedChildCount : 0;

    for (let index = 0; index < namedChildCount; index += 1) {
      const child = rootNode.namedChild ? rootNode.namedChild(index) : null;
      if (!child || !shouldIncludeTopLevelNode(child.type ?? '')) {
        continue;
      }

      nodes.push(child);
    }

    return nodes;
  } catch {
    return [];
  }
}

function resolveParserConstructor(): (new () => TreeSitterParser) | undefined {
  let parserModule: unknown;

  try {
    parserModule = require('tree-sitter') as unknown;
  } catch {
    return undefined;
  }

  if (typeof parserModule === 'function') {
    return parserModule as new () => TreeSitterParser;
  }

  if (
    typeof parserModule === 'object' &&
    parserModule !== null &&
    'default' in parserModule &&
    typeof (parserModule as { default?: unknown }).default === 'function'
  ) {
    return (parserModule as { default: new () => TreeSitterParser }).default;
  }

  return undefined;
}

function resolveParserGrammar(language: LineageParserLanguage): unknown | undefined {
  const cached = parserLanguageCache.get(language);
  if (cached) {
    return cached;
  }

  let grammarModule: unknown;

  try {
    if (language === 'python') {
      grammarModule = require('tree-sitter-python');
    } else if (language === 'javascript') {
      grammarModule = require('tree-sitter-javascript');
    } else {
      const typescriptModule = require('tree-sitter-typescript') as {
        typescript?: unknown;
        tsx?: unknown;
        default?: unknown;
      };

      grammarModule = language === 'tsx' ? typescriptModule.tsx : typescriptModule.typescript;
      if (!grammarModule) {
        grammarModule = typescriptModule.default;
      }
    }
  } catch {
    return undefined;
  }

  const normalizedGrammar = unwrapDefaultExport(grammarModule);
  if (!normalizedGrammar) {
    return undefined;
  }

  parserLanguageCache.set(language, normalizedGrammar);
  return normalizedGrammar;
}

function unwrapDefaultExport(moduleValue: unknown): unknown {
  if (
    typeof moduleValue === 'object' &&
    moduleValue !== null &&
    'default' in moduleValue &&
    (moduleValue as { default?: unknown }).default
  ) {
    return (moduleValue as { default: unknown }).default;
  }

  return moduleValue;
}

function shouldIncludeTopLevelNode(nodeType: string): boolean {
  const normalized = nodeType.trim().toLowerCase();
  if (normalized.length === 0) {
    return false;
  }

  if (normalized.includes('comment')) {
    return false;
  }

  if (
    normalized === 'import_statement' ||
    normalized === 'import_from_statement' ||
    normalized === 'import_declaration'
  ) {
    return false;
  }

  return true;
}

function detectLanguageFromFilePath(filePath: string, source?: string): LineageParserLanguage {
  const normalizedFilePath = filePath.toLowerCase();

  if (normalizedFilePath.endsWith('.py') || normalizedFilePath.endsWith('.pyi')) {
    return 'python';
  }

  if (normalizedFilePath.endsWith('.tsx') || normalizedFilePath.endsWith('.jsx')) {
    return 'tsx';
  }

  if (
    normalizedFilePath.endsWith('.ts') ||
    normalizedFilePath.endsWith('.mts') ||
    normalizedFilePath.endsWith('.cts')
  ) {
    return 'typescript';
  }

  if (
    normalizedFilePath.endsWith('.js') ||
    normalizedFilePath.endsWith('.mjs') ||
    normalizedFilePath.endsWith('.cjs')
  ) {
    return 'javascript';
  }

  const sourceSample = (source ?? '').slice(0, 3_000);

  if (looksLikePython(sourceSample)) {
    return 'python';
  }

  if (looksLikeTsx(sourceSample)) {
    return 'tsx';
  }

  if (looksLikeTypeScript(sourceSample)) {
    return 'typescript';
  }

  return 'javascript';
}

function looksLikePython(sample: string): boolean {
  return (
    /(^|\n)\s*def\s+[A-Za-z_][A-Za-z0-9_]*\s*\(/.test(sample) ||
    /(^|\n)\s*from\s+[A-Za-z_][A-Za-z0-9_.]*\s+import\s+/.test(sample) ||
    /(^|\n)\s*import\s+[A-Za-z_][A-Za-z0-9_.]*/.test(sample) ||
    /(^|\n)\s*class\s+[A-Za-z_][A-Za-z0-9_]*\s*(:|\()/.test(sample)
  );
}

function looksLikeTsx(sample: string): boolean {
  return (
    /<\s*[A-Z][A-Za-z0-9]*\b[^>]*>/.test(sample) ||
    /<\s*[A-Z][A-Za-z0-9]*\b[^>]*\/>/.test(sample) ||
    /\bjsx\b/i.test(sample)
  );
}

function looksLikeTypeScript(sample: string): boolean {
  return (
    /\binterface\s+[A-Za-z_][A-Za-z0-9_]*\b/.test(sample) ||
    /\btype\s+[A-Za-z_][A-Za-z0-9_]*\s*=/.test(sample) ||
    /\benum\s+[A-Za-z_][A-Za-z0-9_]*\b/.test(sample) ||
    /\bimplements\s+[A-Za-z_][A-Za-z0-9_,\s]*/.test(sample)
  );
}

function pointRow(point?: TreeSitterPoint): number {
  return typeof point?.row === 'number' ? point.row : 0;
}

function parseNameStatusOutput(output: string): CommitFileChange[] {
  const lines = output.split(/\r\n|\r|\n/);
  const changes: CommitFileChange[] = [];

  for (const rawLine of lines) {
    const change = parseNameStatusLine(rawLine);
    if (change) {
      changes.push(change);
    }
  }

  return changes;
}

function parseNameStatusLine(rawLine: string): CommitFileChange | null {
  if (!rawLine || rawLine.trim().length === 0) {
    return null;
  }

  const parts = rawLine.split('\t');
  const rawStatus = parts[0]?.trim() ?? '';
  if (rawStatus.length === 0) {
    return null;
  }

  const statusCode = rawStatus.charAt(0) as CommitFileChangeStatus;

  if ((statusCode === 'R' || statusCode === 'C') && parts.length >= 3) {
    return { status: statusCode, oldPath: parts[1], newPath: parts[2] };
  }

  const filePath = parts.length >= 2 ? parts[1] : null;
  if (!filePath) {
    return null;
  }

  if (statusCode === 'A') {
    return { status: statusCode, oldPath: null, newPath: filePath };
  }

  if (statusCode === 'D') {
    return { status: statusCode, oldPath: filePath, newPath: null };
  }

  return { status: statusCode, oldPath: filePath, newPath: filePath };
}

function collectUniqueChangedPaths(changes: readonly CommitFileChange[]): string[] {
  const uniquePaths = new Set<string>();

  for (const change of changes) {
    if (change.oldPath) {
      uniquePaths.add(change.oldPath);
    }

    if (change.newPath) {
      uniquePaths.add(change.newPath);
    }
  }

  return [...uniquePaths];
}

function classifyRelationshipType(input: {
  previousFilePath: string;
  nextFilePath: string;
  previousTokens: readonly string[];
  nextTokens: readonly string[];
  similarity: number;
}): Exclude<LineageRelationshipType, 'DELETED' | 'SPLIT'> {
  if (input.previousFilePath !== input.nextFilePath) {
    return 'MOVED';
  }

  const overlap = countTokenSetOverlap(input.previousTokens, input.nextTokens);
  const previousUniqueCount = new Set(input.previousTokens).size;
  const coverage = previousUniqueCount === 0 ? 0 : overlap / previousUniqueCount;
  const growthRatio = input.nextTokens.length / Math.max(1, input.previousTokens.length);

  if (growthRatio >= 1.15 && coverage >= 0.8 && input.similarity >= 0.7) {
    return 'EXTENDED';
  }

  return 'REFACTORED';
}

function computeJaccardSimilarity(left: readonly string[], right: readonly string[]): number {
  const leftSet = new Set(left);
  const rightSet = new Set(right);

  if (leftSet.size === 0 || rightSet.size === 0) {
    return 0;
  }

  const overlap = countTokenSetOverlap(leftSet, rightSet);
  const unionSize = leftSet.size + rightSet.size - overlap;

  if (unionSize <= 0) {
    return 0;
  }

  return overlap / unionSize;
}

function countTokenSetOverlap(
  left: ReadonlySet<string> | readonly string[],
  right: ReadonlySet<string> | readonly string[]
): number {
  const leftSet = left instanceof Set ? left : new Set(left);
  const rightSet = right instanceof Set ? right : new Set(right);

  let overlap = 0;

  for (const token of leftSet) {
    if (rightSet.has(token)) {
      overlap += 1;
    }
  }

  return overlap;
}

function clampThreshold(value: number): number {
  if (!Number.isFinite(value)) {
    return DEFAULT_JACCARD_THRESHOLD;
  }

  return Math.max(0, Math.min(1, value));
}

function toStringValue(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }

  return '';
}

function toNullableString(value: unknown): string | null {
  const normalized = toStringValue(value);
  return normalized.length > 0 ? normalized : null;
}

function toNullableNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .map((entry) => (typeof entry === 'string' ? entry : String(entry)))
    .filter((entry) => entry.length > 0);
}
