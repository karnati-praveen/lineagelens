import * as path from 'path';
import type { PromptCorrelationResult } from './correlation';
import type { ContextSnapshot } from './contextSnapshot';

type SupportedParserLanguage = 'javascript' | 'typescript' | 'tsx' | 'python';

type TreeSitterParser = {
  setLanguage: (language: unknown) => void;
  parse: (source: string) => { rootNode?: TreeSitterNode };
};

type TreeSitterNode = {
  type?: string;
  namedChildCount?: number;
  namedChildren?: TreeSitterNode[];
  namedChild?: (index: number) => TreeSitterNode | null;
};

const MAX_SIMILARITY_CODE_LENGTH = 200_000;

const IGNORED_NODE_TYPE_EXACT = new Set<string>([
  'identifier',
  'type_identifier',
  'field_identifier',
  'property_identifier',
  'shorthand_property_identifier',
  'private_property_identifier',
  'string',
  'string_literal',
  'template_string',
  'number',
  'number_literal',
  'integer',
  'float',
  'true',
  'false',
  'null',
  'none',
  'comment',
  'escape_sequence'
]);

const IGNORED_NODE_TYPE_PATTERN: RegExp[] = [
  /identifier$/i,
  /literal$/i,
  /^string/i,
  /^number/i,
  /^comment/i,
  /_literal$/i,
  /_token$/i,
  /escape/i
];

const parserLanguageCache = new Map<SupportedParserLanguage, unknown>();

export type ProvenanceCursorPosition = {
  line: number;
  column: number;
  offset?: number;
};

export type ProvenanceInsertedChunk = {
  text: string;
  start: ProvenanceCursorPosition;
  end: ProvenanceCursorPosition;
  addedLines: number;
  removedLines: number;
};

export type ProvenanceEmbedding = {
  provider: string | null;
  model: string | null;
  vector: number[];
  dimensions: number;
  generatedAtIso: string | null;
  source: 'prompt' | 'response' | 'inserted-code' | 'context-snapshot' | 'other';
};

export type ProvenanceEmbeddingBundle = {
  prompt?: ProvenanceEmbedding;
  response?: ProvenanceEmbedding;
  insertedCode?: ProvenanceEmbedding;
  contextSnapshot?: ProvenanceEmbedding;
  additional?: ProvenanceEmbedding[];
};

export type ProvenanceAstSnapshot = {
  parserEngine: 'tree-sitter';
  normalizationVersion: 'node-type-sequence-v1';
  languageDetected: string;
  rootNodeType: string | null;
  normalizedNodeTypes: string[];
  nodeCount: number;
  parseSucceeded: boolean;
  parseError: string | null;
  createdAtIso: string;
};

export interface ProvenanceRecord {
  uuid: string;
  requestUuid: string | null;
  timestampIso: string;
  insertionTimestampIso: string;
  promptStatus: 'captured' | 'not-captured';
  prompt: {
    fullMessages: unknown;
    modelName: unknown;
    parameters: Record<string, unknown> | null;
    rawModelResponse: string | null;
    rawModelResponseBase64: string | null;
  };
  insertion: {
    extractedInsertedCodeBlock: string;
    insertedChunks: ProvenanceInsertedChunk[];
    netAddedLines: number;
    cursorPosition: ProvenanceCursorPosition;
    surroundingContext: {
      before: string;
      after: string;
      tokenWindow: number;
    };
  };
  file: {
    path: string;
    uri: string;
    languageId: string;
  };
  repository: {
    gitBranch: string | null;
  };
  contextSnapshot: ContextSnapshot | null;
  embeddings: ProvenanceEmbeddingBundle;
  astSnapshot: ProvenanceAstSnapshot;
  correlation: PromptCorrelationResult;
  metadata: {
    similarityThreshold: number;
    correlationWindowMs: number;
    timingDifferenceMs: number | null;
    featureVersion: string;
    [key: string]: unknown;
  };
}

export function normalizeAST(code: string, language?: string): string[] {
  const sourceCode = (code ?? '').slice(0, MAX_SIMILARITY_CODE_LENGTH);
  if (sourceCode.trim().length === 0) {
    return [];
  }

  const parserLanguage = detectParserLanguage(sourceCode, language);

  try {
    const ParserConstructor = loadParserConstructor();
    const parser = new ParserConstructor() as TreeSitterParser;
    const runtimeLanguage = loadRuntimeLanguage(parserLanguage);

    parser.setLanguage(runtimeLanguage);

    const tree = parser.parse(sourceCode);
    const rootNode = tree?.rootNode;
    if (!rootNode) {
      return [];
    }

    const normalizedNodeTypes: string[] = [];
    traverseTreeForNodeTypes(rootNode, normalizedNodeTypes);

    return normalizedNodeTypes;
  } catch {
    return [];
  }
}

function loadParserConstructor(): new () => TreeSitterParser {
  const parserModule = require('tree-sitter') as unknown;

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

  throw new Error('Unable to resolve tree-sitter parser constructor.');
}

function loadRuntimeLanguage(language: SupportedParserLanguage): unknown {
  const cached = parserLanguageCache.get(language);
  if (cached) {
    return cached;
  }

  let languageModule: unknown;

  if (language === 'javascript') {
    languageModule = require('tree-sitter-javascript');
  } else if (language === 'python') {
    languageModule = require('tree-sitter-python');
  } else {
    const tsModule = require('tree-sitter-typescript') as {
      typescript?: unknown;
      tsx?: unknown;
      default?: unknown;
    };

    languageModule = language === 'tsx' ? tsModule.tsx : tsModule.typescript;
    if (!languageModule) {
      languageModule = tsModule.default;
    }
  }

  const normalizedLanguage = unwrapDefaultExport(languageModule);
  if (!normalizedLanguage) {
    throw new Error('Unable to resolve tree-sitter language grammar for ' + language + '.');
  }

  parserLanguageCache.set(language, normalizedLanguage);
  return normalizedLanguage;
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

function detectParserLanguage(code: string, language?: string): SupportedParserLanguage {
  const explicit = normalizeLanguageHint(language);
  if (explicit) {
    return explicit;
  }

  const sample = code.slice(0, 3_000);

  if (looksLikePython(sample)) {
    return 'python';
  }

  if (looksLikeTsx(sample)) {
    return 'tsx';
  }

  if (looksLikeTypeScript(sample)) {
    return 'typescript';
  }

  return 'javascript';
}

function normalizeLanguageHint(language?: string): SupportedParserLanguage | undefined {
  if (!language || language.trim().length === 0) {
    return undefined;
  }

  const normalizedHint = language.trim().toLowerCase();

  const extensionHint = path.extname(normalizedHint);
  if (extensionHint) {
    if (extensionHint === '.py' || extensionHint === '.pyi') {
      return 'python';
    }

    if (extensionHint === '.tsx') {
      return 'tsx';
    }

    if (extensionHint === '.ts' || extensionHint === '.cts' || extensionHint === '.mts') {
      return 'typescript';
    }

    if (
      extensionHint === '.js' ||
      extensionHint === '.jsx' ||
      extensionHint === '.cjs' ||
      extensionHint === '.mjs'
    ) {
      return extensionHint === '.jsx' ? 'tsx' : 'javascript';
    }
  }

  if (
    normalizedHint === 'python' ||
    normalizedHint === 'py' ||
    normalizedHint === 'python3' ||
    normalizedHint === 'pyi'
  ) {
    return 'python';
  }

  if (normalizedHint === 'typescriptreact' || normalizedHint === 'tsx' || normalizedHint === 'jsx') {
    return 'tsx';
  }

  if (
    normalizedHint === 'typescript' ||
    normalizedHint === 'ts' ||
    normalizedHint === 'mts' ||
    normalizedHint === 'cts'
  ) {
    return 'typescript';
  }

  if (
    normalizedHint === 'javascript' ||
    normalizedHint === 'js' ||
    normalizedHint === 'node' ||
    normalizedHint === 'nodejs'
  ) {
    return 'javascript';
  }

  return undefined;
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
    /\bimplements\s+[A-Za-z_][A-Za-z0-9_,\s]*/.test(sample) ||
    /\bpublic\s+[A-Za-z_][A-Za-z0-9_]*\s*:\s*/.test(sample) ||
    /:\s*[A-Z][A-Za-z0-9_<>{}\[\]|, ]*(\s*[=;,)])/m.test(sample)
  );
}

function traverseTreeForNodeTypes(node: TreeSitterNode, output: string[]): void {
  const nodeType = typeof node.type === 'string' ? node.type : '';
  if (nodeType && shouldIncludeNodeType(nodeType)) {
    output.push(nodeType);
  }

  const childCount = getNamedChildCount(node);
  for (let index = 0; index < childCount; index += 1) {
    const childNode = getNamedChild(node, index);
    if (childNode) {
      traverseTreeForNodeTypes(childNode, output);
    }
  }
}

function shouldIncludeNodeType(nodeType: string): boolean {
  const normalizedType = nodeType.trim().toLowerCase();
  if (normalizedType.length === 0) {
    return false;
  }

  if (IGNORED_NODE_TYPE_EXACT.has(normalizedType)) {
    return false;
  }

  for (const pattern of IGNORED_NODE_TYPE_PATTERN) {
    if (pattern.test(normalizedType)) {
      return false;
    }
  }

  return true;
}

function getNamedChildCount(node: TreeSitterNode): number {
  if (typeof node.namedChildCount === 'number') {
    return node.namedChildCount;
  }

  if (Array.isArray(node.namedChildren)) {
    return node.namedChildren.length;
  }

  return 0;
}

function getNamedChild(node: TreeSitterNode, index: number): TreeSitterNode | null {
  if (typeof node.namedChild === 'function') {
    return node.namedChild(index);
  }

  if (Array.isArray(node.namedChildren)) {
    return node.namedChildren[index] ?? null;
  }

  return null;
}
