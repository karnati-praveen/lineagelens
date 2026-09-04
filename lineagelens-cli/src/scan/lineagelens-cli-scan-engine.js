'use strict';

// Retroactive AI attribution engine — pure functions, no I/O, no git calls.
//
// The engine answers: of the lines that survive in HEAD right now, which ones
// came from a commit that declared AI involvement, and which risk surfaces do
// those lines sit on?
//
// Honesty rules baked in (these are product invariants, not style choices):
//   1. A line with no AI signal is `unattributed`, never "human-written".
//   2. Only self-declared AI involvement counts as `declared` evidence.
//      Shape-based guesses are `inferred` and are opt-in at the command layer.
//   3. A retroactive scan can never recover prompts, model reasoning, review
//      state, or accepted/rejected status. Those stay `unavailable`.

const {
  TOOL_SIGNATURES,
  MODEL_PATTERNS,
  TOOL_ARTIFACTS,
} = require('./lineagelens-cli-scan-signatures');

// Field separators used in the git log format string. Chosen because they
// cannot appear in a commit message written by any real tool.
const COMMIT_START = '\u0001';
const FIELD_SEP = '\u001f';
const HEADER_END = '\u0002';

/** git log format string that `parseGitLog` expects. */
const GIT_LOG_FORMAT =
  `${COMMIT_START}%H${FIELD_SEP}%an${FIELD_SEP}%ae${FIELD_SEP}%cn${FIELD_SEP}%ce` +
  `${FIELD_SEP}%aI${FIELD_SEP}%s${FIELD_SEP}%b${HEADER_END}`;

/**
 * @typedef {object} ScanCommit
 * @property {string} sha
 * @property {string} authorName
 * @property {string} authorEmail
 * @property {string} committerName
 * @property {string} committerEmail
 * @property {string} date          ISO-8601 author date
 * @property {string} subject
 * @property {string} body
 * @property {number} added         lines added across all files in the commit
 * @property {number} deleted
 * @property {string[]} files       repo-relative paths touched
 */

/**
 * Parse `git log --numstat --format=GIT_LOG_FORMAT` output into commits.
 *
 * Commit bodies contain arbitrary newlines, so the parser keys off the control
 * characters rather than line structure.
 *
 * @param {string} raw
 * @returns {ScanCommit[]}
 */
function parseGitLog(raw) {
  /** @type {ScanCommit[]} */
  const commits = [];
  if (!raw) return commits;

  for (const chunk of raw.split(COMMIT_START)) {
    if (!chunk.trim()) continue;
    const split = chunk.indexOf(HEADER_END);
    if (split === -1) continue;

    const fields = chunk.slice(0, split).split(FIELD_SEP);
    if (fields.length < 8) continue;

    const [authorName, authorEmail, committerName, committerEmail, date, subject] = fields.slice(1);
    /** @type {ScanCommit} */
    const commit = {
      sha: fields[0].trim(),
      authorName,
      authorEmail,
      committerName,
      committerEmail,
      date,
      subject,
      body: fields[7],
      added: 0,
      deleted: 0,
      files: [],
    };

    for (const line of chunk.slice(split + 1).split('\n')) {
      // numstat: "<added>\t<deleted>\t<path>"; binary files report "-".
      const m = /^(\d+|-)\t(\d+|-)\t(.+)$/.exec(line);
      if (!m) continue;
      if (m[1] !== '-') commit.added += Number(m[1]);
      if (m[2] !== '-') commit.deleted += Number(m[2]);
      commit.files.push(normalizeRenamePath(m[3]));
    }

    commits.push(commit);
  }

  return commits;
}

/**
 * numstat writes renames as `old => new` or `dir/{old => new}/file`. Resolve to
 * the post-rename path, which is the one that exists at HEAD.
 * @param {string} raw
 * @returns {string}
 */
function normalizeRenamePath(raw) {
  const braced = /^(.*)\{(.*) => (.*)\}(.*)$/.exec(raw);
  if (braced) {
    return `${braced[1]}${braced[3]}${braced[4]}`.replace(/\/{2,}/g, '/');
  }
  const plain = /^(.*) => (.*)$/.exec(raw);
  if (plain) return plain[2];
  return raw;
}

/**
 * @typedef {object} ToolMatch
 * @property {string} tool
 * @property {'declared'} evidence
 * @property {'trailer'|'subject'|'identity'} matchedField
 * @property {string|null} model  model named by the tool itself, when present
 */

/**
 * Identify the AI tool that declared involvement in a commit.
 *
 * Returns null when nothing in the commit declares AI authorship. Null means
 * "no signal" — it is not a claim that a human wrote the commit.
 *
 * @param {ScanCommit} commit
 * @returns {ToolMatch|null}
 */
function detectTool(commit) {
  const identity =
    `${commit.authorName} <${commit.authorEmail}> ${commit.committerName} <${commit.committerEmail}>`;
  const haystacks = {
    trailer: `${commit.body}\n${commit.subject}`,
    subject: commit.subject,
    identity,
  };

  for (const sig of TOOL_SIGNATURES) {
    const haystack = haystacks[sig.field];
    if (!haystack) continue;
    if (sig.patterns.some((p) => p.test(haystack))) {
      return {
        tool: sig.tool,
        evidence: 'declared',
        matchedField: sig.field,
        model: detectModel(`${commit.subject}\n${commit.body}`),
      };
    }
  }

  return null;
}

/**
 * Extract a model name a tool wrote into its own commit message.
 * @param {string} text
 * @returns {string|null}
 */
function detectModel(text) {
  for (const pattern of MODEL_PATTERNS) {
    const m = pattern.exec(text);
    if (m) return m[1].toLowerCase();
  }
  return null;
}

/**
 * @typedef {object} BlameHunk
 * @property {string} sha
 * @property {number} finalLine  1-indexed first line in the current file
 * @property {number} numLines
 */

/**
 * Parse `git blame --incremental` output into hunks.
 *
 * The incremental format opens each hunk with
 * `<40-hex sha> <orig-line> <final-line> <num-lines>` followed by key/value
 * metadata we do not need — one line per hunk instead of one per source line,
 * which is what makes a repo-wide scan affordable.
 *
 * @param {string} raw
 * @returns {BlameHunk[]}
 */
function parseBlameIncremental(raw) {
  /** @type {BlameHunk[]} */
  const hunks = [];
  if (!raw) return hunks;
  for (const line of raw.split('\n')) {
    const m = /^([0-9a-f]{40}) (\d+) (\d+) (\d+)$/.exec(line.trim());
    if (!m) continue;
    hunks.push({ sha: m[1], finalLine: Number(m[3]), numLines: Number(m[4]) });
  }
  return hunks;
}

// Risk rules, kept slug-compatible with the backend's risk_service so that
// `scan --category auth` and `report --category auth` mean the same thing.
const CODE_RULES = [
  { slug: 'secrets', patterns: [/api[_-]?key/i, /access[_-]?token/i, /private[_-]?key/i, /client[_-]?secret/i] },
  { slug: 'eval', patterns: [/\beval\s*\(/, /new Function\s*\(/, /\bexec\s*\(/, /\bexecSync\s*\(/] },
  { slug: 'shell', patterns: [/\bsubprocess\./, /\bos\.system\b/, /\bchild_process\b/, /\bspawn(?:Sync)?\s*\(/] },
  { slug: 'dom', patterns: [/dangerouslySetInnerHTML/, /\binnerHTML\s*=/] },
  { slug: 'sql', patterns: [/\bSELECT\s+.+\bFROM\b/i, /\bINSERT\s+INTO\b/i, /\bUPDATE\s+\w+\s+SET\b/i, /\bDELETE\s+FROM\b/i] },
  { slug: 'auth', patterns: [/\bpassword\b/i, /\bjwt\b/i, /\bbearer\b/i, /\bcredential/i, /\bauthenticat/i] },
];

const PATH_RULES = [
  { slug: 'auth', patterns: [/auth/i, /security/i, /permission/i, /oauth/i, /token/i, /secret/i, /credential/i] },
  { slug: 'payments', patterns: [/payment/i, /billing/i, /invoice/i, /ledger/i, /finance/i] },
  { slug: 'ci', patterns: [/^\.github\/workflows\//i, /Dockerfile/i, /docker-compose/i, /\.gitlab-ci/i, /Jenkinsfile/i] },
  { slug: 'infra', patterns: [/\.tf$/i, /\bk8s\b/i, /\bhelm\b/i, /\bterraform\b/i, /\bansible\b/i] },
];

/** Blocks at or above this many attributed lines in one file are large-block. */
const LARGE_BLOCK_LINES = 80;

/**
 * Risk category slugs for a set of AI-attributed lines in one file.
 *
 * These are deterministic local signals over the surviving text — not SAST, and
 * not a severity score. They exist to answer "which sensitive surfaces does the
 * unattributed-to-a-human code sit on?"
 *
 * @param {string} filePath repo-relative path
 * @param {string[]} attributedLines the surviving AI-attributed lines only
 * @returns {string[]} sorted unique slugs
 */
function categorize(filePath, attributedLines) {
  const slugs = new Set();
  const normPath = String(filePath).replace(/\\/g, '/');

  for (const rule of PATH_RULES) {
    if (rule.patterns.some((p) => p.test(normPath))) slugs.add(rule.slug);
  }

  const text = attributedLines.join('\n');
  for (const rule of CODE_RULES) {
    if (rule.patterns.some((p) => p.test(text))) slugs.add(rule.slug);
  }

  if (attributedLines.length >= LARGE_BLOCK_LINES) slugs.add('large-block');

  return [...slugs].sort();
}

/**
 * Which AI tools are configured for this repo.
 *
 * Checks tracked paths *and* the working tree, because agent config is very
 * often gitignored — a `.claude/` or `.cursorrules` that never got committed is
 * still proof the tool was in use here. Missing that is how a scan reports a
 * reassuring "0% AI" for a repo an agent largely wrote.
 *
 * Configuration attributes no lines on its own. It establishes that a tool was
 * in use, which is what makes a low declaration rate meaningful rather than
 * reassuring.
 *
 * @param {string[]} trackedPaths repo-relative paths tracked at HEAD
 * @param {(relPath: string) => boolean} [existsInWorktree] probe for untracked config
 * @returns {{tool: string, evidence: string, tracked: boolean}[]}
 */
function detectConfiguredTools(trackedPaths, existsInWorktree) {
  const normalized = trackedPaths.map((p) => String(p).replace(/\\/g, '/'));
  /** @type {{tool: string, evidence: string, tracked: boolean}[]} */
  const configured = [];

  for (const artifact of TOOL_ARTIFACTS) {
    const tracked = artifact.paths.find((needle) =>
      needle.endsWith('/')
        ? normalized.some((p) => p === needle.slice(0, -1) || p.startsWith(needle))
        : normalized.some((p) => p === needle || p.endsWith(`/${needle}`)),
    );
    if (tracked) {
      configured.push({ tool: artifact.tool, evidence: tracked, tracked: true });
      continue;
    }
    if (!existsInWorktree) continue;
    const untracked = artifact.paths.find((needle) =>
      existsInWorktree(needle.endsWith('/') ? needle.slice(0, -1) : needle),
    );
    if (untracked) {
      configured.push({ tool: artifact.tool, evidence: `${untracked} (untracked)`, tracked: false });
    }
  }

  return configured;
}

/** Below this share of declaring commits, attribution is treated as incomplete. */
const LOW_DECLARATION_RATE = 0.5;

/**
 * Judge how complete the attribution is — the single most important honesty
 * output of a retroactive scan.
 *
 * The failure mode this exists to prevent: a repo where an agent wrote most of
 * the code but the developer stripped or never enabled commit trailers scans as
 * "1% AI", and a reader concludes the codebase is 99% human. It is not. It is
 * 99% *undeclared*. When AI tooling is configured but few commits declare it,
 * the scan says the number is a floor, not a measurement.
 *
 * @param {object} input
 * @param {string[]} input.trackedPaths
 * @param {Set<string>} input.attributedTools tools that declared themselves in history
 * @param {number} input.commitsExamined
 * @param {number} input.aiCommits commits that declared a tool
 * @param {(relPath: string) => boolean} [input.existsInWorktree]
 * @returns {object} coverage assessment, JSON-serializable
 */
function assessCoverage({ trackedPaths, attributedTools, commitsExamined, aiCommits, existsInWorktree }) {
  const configured = detectConfiguredTools(trackedPaths, existsInWorktree);
  const silentTools = configured.filter((c) => !attributedTools.has(c.tool));
  const declarationRate = commitsExamined > 0 ? aiCommits / commitsExamined : 0;

  // Under-declaring is meaningful in two situations: a tool is demonstrably
  // configured, or a tool declared itself sometimes but rarely. Both mean the
  // repo's own history is an unreliable denominator for AI authorship.
  const underDeclaring =
    (configured.length > 0 || aiCommits > 0) && declarationRate < LOW_DECLARATION_RATE;

  /** @type {string[]} */
  const reasons = [];
  if (silentTools.length > 0) {
    reasons.push(
      `${silentTools.map((t) => t.tool).join(', ')} ${silentTools.length === 1 ? 'is' : 'are'} ` +
      'configured in this repo but never declared in any commit',
    );
  }
  if (underDeclaring) {
    reasons.push(
      `only ${aiCommits} of ${commitsExamined} commits (${(declarationRate * 100).toFixed(1)}%) ` +
      'declare an AI tool, so any AI work committed without a trailer is invisible to this scan',
    );
  }

  const status = reasons.length > 0 ? 'known_incomplete' : 'declared_signals_only';

  return {
    status,
    configuredTools: configured,
    silentTools,
    declarationRate,
    commitsExamined,
    declaringCommits: aiCommits,
    reasons,
    // The headline consequence, phrased so it can be printed verbatim.
    interpretation: status === 'known_incomplete'
      ? 'The AI percentage below is a floor, not a measurement — undeclared AI code is counted as unattributed.'
      : 'No configured-but-silent AI tooling was detected; declared signals are the only signals available either way.',
  };
}

/**
 * @typedef {object} FileAttribution
 * @property {string} path
 * @property {number} totalLines
 * @property {number} attributedLines
 * @property {Record<string, number>} byTool     tool -> surviving line count
 * @property {string[]} categories
 * @property {string|null} firstSeen             earliest AI commit date
 * @property {string|null} lastSeen              latest AI commit date
 */

/**
 * Roll per-file attributions into the scan summary.
 *
 * @param {object} input
 * @param {FileAttribution[]} input.files          files with >=1 attributed line
 * @param {number} input.totalTrackedLines         denominator across all tracked text files
 * @param {number} input.totalTrackedFiles
 * @param {ScanCommit[]} input.aiCommits
 * @param {Map<string, ToolMatch>} input.toolByCommit
 * @param {object} input.coverage                  output of assessCoverage()
 * @param {object} input.repo                      { root, branch, headSha, totalCommits }
 * @returns {object} the full scan result, JSON-serializable
 */
function summarize(input) {
  const {
    files,
    totalTrackedLines,
    totalTrackedFiles,
    aiCommits,
    toolByCommit,
    coverage,
    repo,
  } = input;

  const attributedLines = files.reduce((n, f) => n + f.attributedLines, 0);

  /** @type {Record<string, {lines: number, commits: number, files: number, models: string[]}>} */
  const byTool = {};
  for (const file of files) {
    for (const [tool, lines] of Object.entries(file.byTool)) {
      if (!byTool[tool]) byTool[tool] = { lines: 0, commits: 0, files: 0, models: [] };
      byTool[tool].lines += lines;
      byTool[tool].files += 1;
    }
  }
  const modelsByTool = new Map();
  for (const commit of aiCommits) {
    const match = toolByCommit.get(commit.sha);
    if (!match) continue;
    if (!byTool[match.tool]) byTool[match.tool] = { lines: 0, commits: 0, files: 0, models: [] };
    byTool[match.tool].commits += 1;
    if (match.model) {
      if (!modelsByTool.has(match.tool)) modelsByTool.set(match.tool, new Set());
      modelsByTool.get(match.tool).add(match.model);
    }
  }
  for (const [tool, models] of modelsByTool) {
    byTool[tool].models = [...models].sort();
  }

  /** @type {Record<string, {lines: number, files: number}>} */
  const byCategory = {};
  for (const file of files) {
    for (const slug of file.categories) {
      if (!byCategory[slug]) byCategory[slug] = { lines: 0, files: 0 };
      byCategory[slug].lines += file.attributedLines;
      byCategory[slug].files += 1;
    }
  }

  const dates = aiCommits.map((c) => c.date).filter(Boolean).sort();

  return {
    schemaVersion: 1,
    scan: {
      kind: 'retroactive',
      generatedAt: new Date().toISOString(),
      repoRoot: repo.root,
      branch: repo.branch,
      headSha: repo.headSha,
      commitsExamined: repo.totalCommits,
      aiCommits: aiCommits.length,
      firstAiCommit: dates[0] ?? null,
      lastAiCommit: dates[dates.length - 1] ?? null,
    },
    totals: {
      trackedFiles: totalTrackedFiles,
      trackedLines: totalTrackedLines,
      aiAttributedFiles: files.length,
      aiAttributedLines: attributedLines,
      // Rounded to one decimal so JSON consumers and the terminal agree, and so
      // float noise never leaks into a number people quote.
      aiAttributedPercent: totalTrackedLines > 0
        ? Math.round((attributedLines / totalTrackedLines) * 1000) / 10
        : 0,
      unattributedLines: Math.max(0, totalTrackedLines - attributedLines),
    },
    byTool,
    byCategory,
    files: [...files].sort((a, b) => b.attributedLines - a.attributedLines),
    coverage,
    // Assurance block. A retroactive scan is a lower-fidelity capture path and
    // says so in its own output — nothing downstream has to infer it.
    assurance: {
      evidenceClass: 'declared',
      basis: 'self-declared AI authorship in git commit metadata',
      unavailable: ['prompt', 'model_reasoning', 'accepted_rejected_status', 'review_state', 'human_attestation'],
      unattributedMeaning:
        'no AI signal found in the commits that introduced these lines; this is not evidence of human authorship',
      completeness: coverage.status,
      measurementKind: coverage.status === 'known_incomplete' ? 'lower_bound' : 'declared_signal_total',
    },
  };
}

module.exports = {
  GIT_LOG_FORMAT,
  LARGE_BLOCK_LINES,
  LOW_DECLARATION_RATE,
  parseGitLog,
  normalizeRenamePath,
  detectTool,
  detectModel,
  parseBlameIncremental,
  categorize,
  detectConfiguredTools,
  assessCoverage,
  summarize,
};
