/**
 * Local risk rules — pure (no vscode), deterministic, data-driven (blueprint §16.3).
 *
 * These are fast heuristic *signals* that point a reviewer at AI-generated code in
 * sensitive areas — NOT a security scanner / SAST. Each rule matches on file path
 * (glob), language, inserted content (regex), and/or a custom predicate, and emits
 * a {@link RiskSignal}. Rules are intentionally transparent and editable.
 */

import { minimatch } from 'minimatch';
import { RiskSignal, RiskSeverity } from '../store';
import { containsSecret } from '../secrets';

export interface RiskInput {
  filePath: string;
  language: string;
  insertedCode: string;
}

interface RiskRule {
  id: string;
  label: string;
  category: string;
  severity: RiskSeverity;
  message: string;
  /** Path must match at least one (glob, '/'-normalized, case-insensitive). */
  pathPatterns?: string[];
  /** Path must NOT match any of these (e.g. exclude test files). */
  pathExcludePatterns?: string[];
  /** languageId must be in this list, when present. */
  languages?: string[];
  /** Content must match at least one of these (non-global regexes). */
  contentPatterns?: RegExp[];
  /** Arbitrary predicate (must return true), e.g. secret detection. */
  custom?: (input: RiskInput) => boolean;
}

const TEST_PATHS = ['**/*.test.*', '**/*.spec.*', '**/test/**', '**/tests/**', '**/__tests__/**'];

const RULES: RiskRule[] = [
  {
    id: 'hardcoded-secret',
    label: 'secrets',
    category: 'crypto',
    severity: 'high',
    message: 'Generated code appears to contain a hardcoded secret (API key, token, or private key).',
    custom: (input) => containsSecret(input.insertedCode),
  },
  {
    id: 'generated-auth-code',
    label: 'auth',
    category: 'auth',
    severity: 'high',
    message: 'AI-generated authentication/session code should receive focused review.',
    pathPatterns: ['**/auth/**', '**/*session*', '**/*login*', '**/*jwt*', '**/*oauth*'],
    contentPatterns: [/\b(authenticate|authorize|session|jwt|bcrypt|passport|set-cookie|access[_-]?token)\b/i],
  },
  {
    id: 'generated-crypto-code',
    label: 'crypto',
    category: 'crypto',
    severity: 'high',
    message: 'AI-generated cryptography code — verify algorithms, key handling, and randomness.',
    contentPatterns: [/\b(createcipher|createhash|crypto\.|hashlib|aes-|rsa|encrypt\(|decrypt\(|secretbox|pbkdf2)\b/i],
  },
  {
    id: 'generated-sql',
    label: 'sql',
    category: 'database',
    severity: 'medium',
    message: 'AI-generated SQL — confirm queries are parameterized and scoped to the right user/tenant.',
    contentPatterns: [/\b(select\s+[\s\S]*\bfrom\b|insert\s+into|update\s+\w+\s+set|delete\s+from)\b/i],
  },
  {
    id: 'generated-shell-exec',
    label: 'shell',
    category: 'infra',
    severity: 'high',
    message: 'AI-generated process/shell execution — confirm inputs are not attacker-controlled.',
    contentPatterns: [/\b(child_process|execSync|spawnSync|os\.system|subprocess\.(run|call|popen)|Runtime\.getRuntime)\b/i],
  },
  {
    id: 'dynamic-eval',
    label: 'eval',
    category: 'generic',
    severity: 'high',
    message: 'AI-generated dynamic code execution (eval / new Function) — avoid if at all possible.',
    contentPatterns: [/(\beval\s*\(|\bnew\s+Function\s*\(|\bexec\s*\()/],
  },
  {
    id: 'dependency-change',
    label: 'deps',
    category: 'dependencies',
    severity: 'medium',
    message: 'AI-generated dependency/package change — confirm each dependency is necessary and trusted.',
    pathPatterns: [
      '**/package.json', '**/package-lock.json', '**/yarn.lock', '**/pnpm-lock.yaml',
      '**/requirements*.txt', '**/Pipfile*', '**/poetry.lock', '**/Cargo.toml', '**/Cargo.lock',
      '**/go.mod', '**/go.sum', '**/Gemfile*', '**/pom.xml', '**/build.gradle*',
    ],
  },
  {
    id: 'ci-change',
    label: 'ci',
    category: 'ci',
    severity: 'medium',
    message: 'AI-generated CI/CD config — check for leaked secrets, unpinned actions, and widened permissions.',
    pathPatterns: ['**/.github/workflows/**', '**/.gitlab-ci.yml', '**/azure-pipelines.yml', '**/Jenkinsfile', '**/.circleci/**'],
  },
  {
    id: 'infra-change',
    label: 'infra',
    category: 'infra',
    severity: 'medium',
    message: 'AI-generated infrastructure config — confirm least-privilege and nothing is publicly exposed.',
    pathPatterns: ['**/*.tf', '**/*.tfvars', '**/Dockerfile*', '**/docker-compose*', '**/kubernetes/**', '**/helm/**', '**/*.k8s.yaml'],
  },
  {
    id: 'security-bypass',
    label: 'bypass',
    category: 'generic',
    severity: 'medium',
    message: 'AI-generated code disables or suppresses a security/validation check.',
    contentPatterns: [
      /\b(nosec|noqa|eslint-disable|@ts-ignore|InsecureSkipVerify|verify\s*=\s*False|rejectUnauthorized\s*:\s*false|NODE_TLS_REJECT_UNAUTHORIZED)\b/i,
    ],
  },
  {
    id: 'untested-generated-logic',
    label: 'untested',
    category: 'tests',
    severity: 'low',
    message: 'Generated logic in a non-test file — confirm it has test coverage.',
    pathExcludePatterns: TEST_PATHS,
    contentPatterns: [/\b(function|def|class|func|fn|public|private|module\.exports)\s+\w/],
  },
];

const SEVERITY_RANK: Record<RiskSeverity, number> = { high: 3, medium: 2, low: 1 };

function normPath(p: string): string {
  return p.replace(/\\/g, '/');
}

function ruleMatches(rule: RiskRule, input: RiskInput, normalized: string): boolean {
  // Filters: constraints that must hold for the rule to apply at all.
  if (rule.languages && !rule.languages.includes(input.language)) {
    return false;
  }
  if (rule.pathExcludePatterns && rule.pathExcludePatterns.some((p) => minimatch(normalized, p, { nocase: true, dot: true }))) {
    return false;
  }
  // Triggers: the rule fires if ANY present trigger matches (e.g. auth fires on
  // an auth path OR auth-looking content). A rule with no trigger never fires.
  const triggers: boolean[] = [];
  if (rule.pathPatterns) {
    triggers.push(rule.pathPatterns.some((p) => minimatch(normalized, p, { nocase: true, dot: true })));
  }
  if (rule.contentPatterns) {
    triggers.push(rule.contentPatterns.some((re) => re.test(input.insertedCode)));
  }
  if (rule.custom) {
    triggers.push(rule.custom(input));
  }
  return triggers.length > 0 && triggers.some(Boolean);
}

/** Evaluate all rules against a capture, returning signals sorted high→low severity. */
export function evaluateRisk(input: RiskInput): RiskSignal[] {
  const normalized = normPath(input.filePath);
  const signals: RiskSignal[] = [];
  for (const rule of RULES) {
    if (ruleMatches(rule, input, normalized)) {
      signals.push({
        id: rule.id,
        label: rule.label,
        category: rule.category,
        severity: rule.severity,
        message: rule.message,
      });
    }
  }
  return signals.sort(
    (a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] || a.id.localeCompare(b.id),
  );
}

/** True if any signal is high severity (used for gating/decoration). */
export function hasHighRisk(signals: RiskSignal[] | undefined): boolean {
  return !!signals && signals.some((s) => s.severity === 'high');
}
