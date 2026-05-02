import * as fs from 'fs/promises';
import * as path from 'path';
import * as vscode from 'vscode';

const SECRET_ENV_KEY_PATTERN = /key|secret|password|token|auth/i;
const NEARBY_IMPORT_WINDOW_LINES = 120;
const MAX_SUMMARY_DEPENDENCIES = 120;
const MAX_CAPTURED_ENV_VARS = 80;
const MAX_ENV_VALUE_CHARS = 256;
const ENVIRONMENT_ALLOWLIST = new Set<string>([
  'CI',
  'GITHUB_ACTIONS',
  'GITHUB_WORKFLOW',
  'NODE_ENV',
  'PYTHONUTF8',
  'TERM',
  'LANG',
  'LC_ALL',
  'OS',
  'PROCESSOR_ARCHITECTURE',
  'PROCESSOR_IDENTIFIER'
]);
const ENVIRONMENT_ALLOWLIST_PREFIXES = ['VSCODE_IPC_HOOK'];

type LanguageProfile = 'python' | 'node' | 'unknown';

type ManifestKind = 'package.json' | 'requirements.txt' | 'pyproject.toml';

type ImportSource = 'currentFile' | 'nearby';

type ImportRecord = {
  module: string;
  importPath: string;
  version: string | null;
  line: number;
  source: ImportSource;
  statement: string;
};

type ManifestSnapshot = {
  fileName: ManifestKind;
  filePath: string;
  captureMode: 'summary' | 'content';
  contentOrSummary: unknown;
};

export type ContextSnapshot = {
  capturedAtIso: string;
  targetFilePath: string;
  languageHint: string;
  projectConfig: {
    profile: LanguageProfile;
    manifests: ManifestSnapshot[];
  };
  environmentVariables: Record<string, string>;
  imports: {
    currentFile: ImportRecord[];
    nearby: ImportRecord[];
  };
};

type ManifestCollectionResult = {
  manifests: ManifestSnapshot[];
  dependencyVersions: Map<string, string>;
};

type ParsedImport = {
  importPath: string;
  module: string;
  line: number;
  statement: string;
};

export async function captureContextSnapshot(filePath: string): Promise<ContextSnapshot> {
  const capturedAtIso = new Date().toISOString();
  const languageInfo = detectLanguageInfo(filePath);

  try {
    const workspaceFolder = vscode.workspace.getWorkspaceFolder(vscode.Uri.file(filePath));
    const workspaceRoot = workspaceFolder?.uri.fsPath ?? path.dirname(filePath);

    const [manifestCollection, fileImportCollection] = await Promise.all([
      collectManifestSnapshots(workspaceRoot, languageInfo.profile),
      collectImportsFromFile(filePath, languageInfo.profile)
    ]);

    const currentFileImports = mapImportsWithVersions(
      fileImportCollection.currentFile,
      languageInfo.profile,
      manifestCollection.dependencyVersions,
      'currentFile'
    );
    const nearbyImports = mapImportsWithVersions(
      fileImportCollection.nearby,
      languageInfo.profile,
      manifestCollection.dependencyVersions,
      'nearby'
    );

    return {
      capturedAtIso,
      targetFilePath: filePath,
      languageHint: languageInfo.languageHint,
      projectConfig: {
        profile: languageInfo.profile,
        manifests: manifestCollection.manifests
      },
      environmentVariables: captureNonSecretEnvironmentVariables(),
      imports: {
        currentFile: currentFileImports,
        nearby: nearbyImports
      }
    };
  } catch {
    return {
      capturedAtIso,
      targetFilePath: filePath,
      languageHint: languageInfo.languageHint,
      projectConfig: {
        profile: languageInfo.profile,
        manifests: []
      },
      environmentVariables: captureNonSecretEnvironmentVariables(),
      imports: {
        currentFile: [],
        nearby: []
      }
    };
  }
}

function detectLanguageInfo(filePath: string): { profile: LanguageProfile; languageHint: string } {
  const extension = path.extname(filePath).toLowerCase();
  const activeDocument = vscode.window.activeTextEditor?.document;
  const languageHint =
    activeDocument && activeDocument.uri.fsPath === filePath ? activeDocument.languageId : extension;

  if (
    extension === '.py' ||
    extension === '.pyi' ||
    languageHint === 'python' ||
    languageHint === 'jupyter'
  ) {
    return { profile: 'python', languageHint };
  }

  if (
    extension === '.ts' ||
    extension === '.tsx' ||
    extension === '.js' ||
    extension === '.jsx' ||
    extension === '.mjs' ||
    extension === '.cjs' ||
    languageHint.includes('typescript') ||
    languageHint.includes('javascript')
  ) {
    return { profile: 'node', languageHint };
  }

  return { profile: 'unknown', languageHint };
}

async function collectManifestSnapshots(
  workspaceRoot: string,
  profile: LanguageProfile
): Promise<ManifestCollectionResult> {
  const manifests: ManifestSnapshot[] = [];
  const dependencyVersions = new Map<string, string>();
  const manifestOrder = getManifestOrder(profile);

  for (const manifestKind of manifestOrder) {
    const manifestPath = path.join(workspaceRoot, manifestKind);
    const content = await readFileIfExists(manifestPath);
    if (!content) {
      continue;
    }

    if (manifestKind === 'package.json') {
      const parsed = summarizePackageJson(manifestPath, content);
      manifests.push(parsed.snapshot);
      mergeVersionMaps(dependencyVersions, parsed.dependencyVersions);
      continue;
    }

    if (manifestKind === 'requirements.txt') {
      const parsed = summarizeRequirementsTxt(manifestPath, content);
      manifests.push(parsed.snapshot);
      mergeVersionMaps(dependencyVersions, parsed.dependencyVersions);
      continue;
    }

    const parsed = summarizePyProjectToml(manifestPath, content);
    manifests.push(parsed.snapshot);
    mergeVersionMaps(dependencyVersions, parsed.dependencyVersions);
  }

  return {
    manifests,
    dependencyVersions
  };
}

function getManifestOrder(profile: LanguageProfile): ManifestKind[] {
  if (profile === 'python') {
    return ['requirements.txt', 'pyproject.toml', 'package.json'];
  }

  if (profile === 'node') {
    return ['package.json', 'requirements.txt', 'pyproject.toml'];
  }

  return ['package.json', 'requirements.txt', 'pyproject.toml'];
}

function summarizePackageJson(
  manifestPath: string,
  content: string
): { snapshot: ManifestSnapshot; dependencyVersions: Map<string, string> } {
  const dependencyVersions = new Map<string, string>();

  try {
    const parsed = JSON.parse(content) as {
      name?: string;
      version?: string;
      scripts?: Record<string, unknown>;
      dependencies?: Record<string, string>;
      devDependencies?: Record<string, string>;
      peerDependencies?: Record<string, string>;
      optionalDependencies?: Record<string, string>;
    };

    const dependencies = parsed.dependencies ?? {};
    const devDependencies = parsed.devDependencies ?? {};
    const peerDependencies = parsed.peerDependencies ?? {};
    const optionalDependencies = parsed.optionalDependencies ?? {};

    addRecordVersions(dependencyVersions, dependencies);
    addRecordVersions(dependencyVersions, devDependencies);
    addRecordVersions(dependencyVersions, peerDependencies);
    addRecordVersions(dependencyVersions, optionalDependencies);

    const snapshot: ManifestSnapshot = {
      fileName: 'package.json',
      filePath: manifestPath,
      captureMode: 'summary',
      contentOrSummary: {
        name: parsed.name ?? null,
        version: parsed.version ?? null,
        scriptNames: Object.keys(parsed.scripts ?? {}),
        dependencyCounts: {
          dependencies: Object.keys(dependencies).length,
          devDependencies: Object.keys(devDependencies).length,
          peerDependencies: Object.keys(peerDependencies).length,
          optionalDependencies: Object.keys(optionalDependencies).length
        },
        dependencyPreview: buildDependencyPreview([
          ...Object.entries(dependencies),
          ...Object.entries(devDependencies),
          ...Object.entries(peerDependencies),
          ...Object.entries(optionalDependencies)
        ])
      }
    };

    return { snapshot, dependencyVersions };
  } catch {
    return {
      snapshot: {
        fileName: 'package.json',
        filePath: manifestPath,
        captureMode: 'content',
        contentOrSummary: content
      },
      dependencyVersions
    };
  }
}

function summarizeRequirementsTxt(
  manifestPath: string,
  content: string
): { snapshot: ManifestSnapshot; dependencyVersions: Map<string, string> } {
  const dependencyVersions = new Map<string, string>();
  const entries: Array<{ name: string; specifier: string | null }> = [];
  const lines = splitLines(content);

  for (const line of lines) {
    const trimmed = stripInlineComment(line).trim();
    if (trimmed.length === 0) {
      continue;
    }

    if (trimmed.startsWith('-') || trimmed.startsWith('git+') || trimmed.startsWith('http')) {
      continue;
    }

    const parsed = parseDependencyExpression(trimmed);
    if (!parsed) {
      continue;
    }

    entries.push({
      name: parsed.name,
      specifier: parsed.specifier
    });

    if (parsed.specifier) {
      dependencyVersions.set(normalizePackageKey(parsed.name), parsed.specifier);
    }
  }

  return {
    snapshot: {
      fileName: 'requirements.txt',
      filePath: manifestPath,
      captureMode: 'summary',
      contentOrSummary: {
        totalEntries: entries.length,
        entries: entries.slice(0, MAX_SUMMARY_DEPENDENCIES)
      }
    },
    dependencyVersions
  };
}

type PyprojectDep = { name: string; specifier: string | null; source: string };

function collectInlineDeps(
  input: string,
  source: string,
  dependencies: PyprojectDep[],
  dependencyVersions: Map<string, string>
): void {
  for (const value of splitDependencyArrayItems(input)) {
    const parsed = parseDependencyExpression(value);
    if (!parsed) {
      continue;
    }
    dependencies.push({ name: parsed.name, specifier: parsed.specifier, source });
    if (parsed.specifier) {
      dependencyVersions.set(normalizePackageKey(parsed.name), parsed.specifier);
    }
  }
}

function processProjectSectionLine(
  trimmed: string,
  state: { inProjectDependenciesArray: boolean },
  dependencies: PyprojectDep[],
  dependencyVersions: Map<string, string>
): void {
  if (!state.inProjectDependenciesArray && trimmed.startsWith('dependencies')) {
    const startBracketIndex = trimmed.indexOf('[');
    if (startBracketIndex >= 0) {
      const remainder = trimmed.slice(startBracketIndex + 1);
      const closingBracketIndex = remainder.indexOf(']');
      if (closingBracketIndex >= 0) {
        collectInlineDeps(remainder.slice(0, closingBracketIndex), 'project.dependencies', dependencies, dependencyVersions);
      } else {
        state.inProjectDependenciesArray = true;
      }
    }
    return;
  }
  if (state.inProjectDependenciesArray) {
    const endIndex = trimmed.indexOf(']');
    collectInlineDeps(endIndex >= 0 ? trimmed.slice(0, endIndex) : trimmed, 'project.dependencies', dependencies, dependencyVersions);
    if (endIndex >= 0) {
      state.inProjectDependenciesArray = false;
    }
  }
}

function processPoetryDependenciesLine(
  trimmed: string,
  dependencies: PyprojectDep[],
  dependencyVersions: Map<string, string>
): void {
  const poetryMatch = trimmed.match(/^([A-Za-z0-9_.-]+)\s*=\s*(.+)$/);
  if (!poetryMatch) {
    return;
  }
  const packageName = poetryMatch[1].trim();
  if (packageName.toLowerCase() === 'python') {
    return;
  }
  const specifier = parsePoetrySpecifier(poetryMatch[2].trim());
  dependencies.push({ name: packageName, specifier, source: 'tool.poetry.dependencies' });
  if (specifier) {
    dependencyVersions.set(normalizePackageKey(packageName), specifier);
  }
}

function summarizePyProjectToml(
  manifestPath: string,
  content: string
): { snapshot: ManifestSnapshot; dependencyVersions: Map<string, string> } {
  const dependencyVersions = new Map<string, string>();
  const dependencies: PyprojectDep[] = [];
  const lines = splitLines(content);
  let currentSection = '';
  const state = { inProjectDependenciesArray: false };

  for (const line of lines) {
    const trimmed = line.trim();
    const sectionMatch = trimmed.match(/^\[([^\]]+)\]$/);
    if (sectionMatch) {
      currentSection = sectionMatch[1].trim();
      state.inProjectDependenciesArray = false;
      continue;
    }
    if (currentSection === 'project') {
      processProjectSectionLine(trimmed, state, dependencies, dependencyVersions);
    }
    if (currentSection === 'tool.poetry.dependencies') {
      processPoetryDependenciesLine(trimmed, dependencies, dependencyVersions);
    }
  }

  return {
    snapshot: {
      fileName: 'pyproject.toml',
      filePath: manifestPath,
      captureMode: 'summary',
      contentOrSummary: {
        totalEntries: dependencies.length,
        entries: dependencies.slice(0, MAX_SUMMARY_DEPENDENCIES)
      }
    },
    dependencyVersions
  };
}

function splitDependencyArrayItems(input: string): string[] {
  return input
    .split(',')
    .map((part) => stripQuotes(part.trim()))
    .filter((part) => part.length > 0);
}

function parsePoetrySpecifier(value: string): string | null {
  const quoted = value.match(/^['\"]([^'\"]+)['\"]$/);
  if (quoted) {
    return quoted[1].trim() || null;
  }

  const objectVersion = value.match(/version\s*=\s*['\"]([^'\"]+)['\"]/);
  if (objectVersion) {
    return objectVersion[1].trim() || null;
  }

  return null;
}

function stripInlineComment(line: string): string {
  const hashIndex = line.indexOf('#');
  if (hashIndex < 0) {
    return line;
  }

  return line.slice(0, hashIndex);
}

function parseDependencyExpression(
  expression: string
): { name: string; specifier: string | null } | undefined {
  const normalizedExpression = stripQuotes(expression.trim());
  if (!normalizedExpression) {
    return undefined;
  }

  const withoutExtras = normalizedExpression.replaceAll(/\[[^\]]+\]/g, '');
  const match = withoutExtras.match(/^([A-Za-z0-9_.-]+)\s*(.*)$/);
  if (!match) {
    return undefined;
  }

  const name = match[1];
  const specifier = match[2].trim();

  return {
    name,
    specifier: specifier.length > 0 ? specifier : null
  };
}

function stripQuotes(value: string): string {
  return value.replaceAll(/^['\"]|['\"]$/g, '');
}

function buildDependencyPreview(entries: Array<[string, string]>): Record<string, string> {
  const preview: Record<string, string> = {};

  for (const [name, version] of entries.slice(0, MAX_SUMMARY_DEPENDENCIES)) {
    preview[name] = version;
  }

  return preview;
}

function addRecordVersions(
  collector: Map<string, string>,
  dependencyRecord: Record<string, string>
): void {
  for (const [name, version] of Object.entries(dependencyRecord)) {
    collector.set(normalizePackageKey(name), version);
  }
}

function mergeVersionMaps(target: Map<string, string>, source: Map<string, string>): void {
  for (const [key, value] of source.entries()) {
    if (!target.has(key)) {
      target.set(key, value);
    }
  }
}

function captureNonSecretEnvironmentVariables(): Record<string, string> {
  const environmentVariables: Record<string, string> = {};
  const sortedEntries = Object.entries(process.env).sort(([left], [right]) =>
    left.localeCompare(right)
  );

  for (const [key, value] of sortedEntries) {
    if (typeof value !== 'string') {
      continue;
    }

    if (SECRET_ENV_KEY_PATTERN.test(key)) {
      continue;
    }

    const normalizedKey = key.toUpperCase();
    const allowedByName = ENVIRONMENT_ALLOWLIST.has(normalizedKey);
    const allowedByPrefix = ENVIRONMENT_ALLOWLIST_PREFIXES.some((prefix) =>
      normalizedKey.startsWith(prefix)
    );

    if (!allowedByName && !allowedByPrefix) {
      continue;
    }

    const normalizedValue = value.trim();
    if (!normalizedValue) {
      continue;
    }

    environmentVariables[key] = normalizedValue.slice(0, MAX_ENV_VALUE_CHARS);

    if (Object.keys(environmentVariables).length >= MAX_CAPTURED_ENV_VARS) {
      break;
    }
  }

  return environmentVariables;
}

async function collectImportsFromFile(
  filePath: string,
  profile: LanguageProfile
): Promise<{ currentFile: ParsedImport[]; nearby: ParsedImport[] }> {
  const source = await readFileIfExists(filePath);
  if (!source) {
    return {
      currentFile: [],
      nearby: []
    };
  }

  const currentFileImports = parseImportsFromSource(source, profile, 1);
  const lines = splitLines(source);
  const anchorLine = resolveAnchorLine(filePath, lines.length);
  const nearbyStartLine = Math.max(1, anchorLine - NEARBY_IMPORT_WINDOW_LINES);
  const nearbyEndLine = Math.min(lines.length, anchorLine + NEARBY_IMPORT_WINDOW_LINES);
  const nearbySource = lines.slice(nearbyStartLine - 1, nearbyEndLine).join('\n');

  const nearbyImports = parseImportsFromSource(nearbySource, profile, nearbyStartLine);

  return {
    currentFile: dedupeParsedImports(currentFileImports),
    nearby: dedupeParsedImports(nearbyImports)
  };
}

function resolveAnchorLine(filePath: string, totalLines: number): number {
  const activeEditor = vscode.window.activeTextEditor;
  if (!activeEditor || activeEditor.document.uri.fsPath !== filePath) {
    return Math.min(totalLines, 1 + Math.floor(totalLines / 2));
  }

  return activeEditor.selection.active.line + 1;
}

function parseImportsFromSource(
  source: string,
  profile: LanguageProfile,
  startLine: number
): ParsedImport[] {
  if (profile === 'python') {
    return parsePythonImports(source, startLine);
  }

  if (profile === 'node') {
    return parseNodeImports(source, startLine);
  }

  return [...parseNodeImports(source, startLine), ...parsePythonImports(source, startLine)];
}

function parseNodeImports(source: string, startLine: number): ParsedImport[] {
  const imports: ParsedImport[] = [];
  const lines = splitLines(source);

  const fromPattern = /\bimport\s+.+\s+from\s+['\"]([^'\"]+)['\"]/;
  const sideEffectPattern = /\bimport\s+['\"]([^'\"]+)['\"]/;
  const requirePattern = /\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)/;
  const dynamicImportPattern = /\bimport\(\s*['\"]([^'\"]+)['\"]\s*\)/;

  for (let index = 0; index < lines.length; index += 1) {
    const statement = lines[index];
    const lineNumber = startLine + index;

    const fromMatch = statement.match(fromPattern);
    if (fromMatch) {
      imports.push({
        importPath: fromMatch[1],
        module: resolveModuleName(fromMatch[1], 'node'),
        line: lineNumber,
        statement: statement.trim()
      });
    }

    const sideEffectMatch = statement.match(sideEffectPattern);
    if (sideEffectMatch) {
      imports.push({
        importPath: sideEffectMatch[1],
        module: resolveModuleName(sideEffectMatch[1], 'node'),
        line: lineNumber,
        statement: statement.trim()
      });
    }

    const requireMatch = statement.match(requirePattern);
    if (requireMatch) {
      imports.push({
        importPath: requireMatch[1],
        module: resolveModuleName(requireMatch[1], 'node'),
        line: lineNumber,
        statement: statement.trim()
      });
    }

    const dynamicImportMatch = statement.match(dynamicImportPattern);
    if (dynamicImportMatch) {
      imports.push({
        importPath: dynamicImportMatch[1],
        module: resolveModuleName(dynamicImportMatch[1], 'node'),
        line: lineNumber,
        statement: statement.trim()
      });
    }
  }

  return imports;
}

function parsePythonImports(source: string, startLine: number): ParsedImport[] {
  const imports: ParsedImport[] = [];
  const lines = splitLines(source);

  for (let index = 0; index < lines.length; index += 1) {
    const statement = lines[index].trim();
    const lineNumber = startLine + index;

    if (statement.length === 0 || statement.startsWith('#')) {
      continue;
    }

    const importMatch = statement.match(/^import\s+(.+)$/);
    if (importMatch) {
      const modules = importMatch[1].split(',').map((part) => part.trim());
      for (const moduleEntry of modules) {
        const modulePath = moduleEntry.split(/\s+as\s+/i)[0].trim();
        if (!modulePath) {
          continue;
        }

        imports.push({
          importPath: modulePath,
          module: resolveModuleName(modulePath, 'python'),
          line: lineNumber,
          statement
        });
      }

      continue;
    }

    const fromImportMatch = statement.match(/^from\s+([A-Za-z0-9_\.]+)\s+import\s+/);
    if (fromImportMatch) {
      const modulePath = fromImportMatch[1].trim();
      imports.push({
        importPath: modulePath,
        module: resolveModuleName(modulePath, 'python'),
        line: lineNumber,
        statement
      });
    }
  }

  return imports;
}

function dedupeParsedImports(imports: ParsedImport[]): ParsedImport[] {
  const deduped: ParsedImport[] = [];
  const seen = new Set<string>();

  for (const entry of imports) {
    const key = entry.importPath + '|' + String(entry.line) + '|' + entry.statement;
    if (seen.has(key)) {
      continue;
    }

    seen.add(key);
    deduped.push(entry);
  }

  return deduped;
}

function mapImportsWithVersions(
  imports: ParsedImport[],
  profile: LanguageProfile,
  dependencyVersions: Map<string, string>,
  source: ImportSource
): ImportRecord[] {
  return imports
    .map((entry) => {
      const localImport = isLocalImport(entry.importPath);
      const version = localImport
        ? null
        : dependencyVersions.get(normalizePackageKey(resolveModuleName(entry.importPath, profile))) ??
          null;

      return {
        module: entry.module,
        importPath: entry.importPath,
        version,
        line: entry.line,
        source,
        statement: entry.statement
      };
    })
    .sort((left, right) => left.line - right.line);
}

function resolveModuleName(importPath: string, profile: LanguageProfile): string {
  if (profile === 'node') {
    if (isLocalImport(importPath)) {
      return importPath;
    }

    if (importPath.startsWith('@')) {
      const segments = importPath.split('/');
      return segments.length >= 2 ? segments.slice(0, 2).join('/') : importPath;
    }

    const segments = importPath.split('/');
    return segments[0];
  }

  if (profile === 'python') {
    if (isLocalImport(importPath)) {
      return importPath;
    }

    return importPath.split('.')[0];
  }

  if (importPath.startsWith('@')) {
    const scopedSegments = importPath.split('/');
    return scopedSegments.length >= 2 ? scopedSegments.slice(0, 2).join('/') : importPath;
  }

  return importPath.split(/[/.]/)[0];
}

function isLocalImport(importPath: string): boolean {
  return (
    importPath.startsWith('./') ||
    importPath.startsWith('../') ||
    importPath.startsWith('/') ||
    importPath.startsWith('.')
  );
}

function normalizePackageKey(name: string): string {
  return name.toLowerCase().replaceAll('_', '-');
}

async function readFileIfExists(filePath: string): Promise<string | undefined> {
  try {
    return await fs.readFile(filePath, 'utf8');
  } catch {
    return undefined;
  }
}

function splitLines(content: string): string[] {
  return content.split(/\r\n|\r|\n/);
}
