'use strict';

const test = require('node:test');
const assert = require('node:assert');

const {
  GIT_LOG_FORMAT,
  parseGitLog,
  normalizeRenamePath,
  detectTool,
  detectModel,
  parseBlameIncremental,
  categorize,
  detectConfiguredTools,
  assessCoverage,
  summarize,
} = require('../src/scan/lineagelens-cli-scan-engine');

const START = '\u0001';
const SEP = '\u001f';
const END = '\u0002';

/** Build a git-log chunk the way `readLog` would produce it. */
function logChunk(fields, numstat = []) {
  const row = [
    fields.sha,
    fields.authorName ?? 'Dev',
    fields.authorEmail ?? 'dev@example.com',
    fields.committerName ?? fields.authorName ?? 'Dev',
    fields.committerEmail ?? fields.authorEmail ?? 'dev@example.com',
    fields.date ?? '2026-06-01T10:00:00+00:00',
    fields.subject ?? 'chore: something',
    fields.body ?? '',
  ].join(SEP);
  return `${START}${row}${END}\n${numstat.join('\n')}\n`;
}

function baseCommit(overrides = {}) {
  return {
    sha: 'a'.repeat(40),
    authorName: 'Dev',
    authorEmail: 'dev@example.com',
    committerName: 'Dev',
    committerEmail: 'dev@example.com',
    date: '2026-06-01T10:00:00+00:00',
    subject: 'chore: something',
    body: '',
    added: 0,
    deleted: 0,
    files: [],
    ...overrides,
  };
}

test('GIT_LOG_FORMAT uses the separators the parser expects', () => {
  assert.ok(GIT_LOG_FORMAT.startsWith(START));
  assert.ok(GIT_LOG_FORMAT.endsWith(END));
  assert.strictEqual(GIT_LOG_FORMAT.split(SEP).length, 8);
});

test('parseGitLog reads fields and numstat totals', () => {
  const raw =
    logChunk({ sha: 'a'.repeat(40), subject: 'feat: add auth' }, ['12\t3\tsrc/auth.py', '4\t0\tsrc/util.py']) +
    logChunk({ sha: 'b'.repeat(40), subject: 'fix: typo' }, ['1\t1\tREADME.md']);

  const commits = parseGitLog(raw);
  assert.strictEqual(commits.length, 2);
  assert.strictEqual(commits[0].sha, 'a'.repeat(40));
  assert.strictEqual(commits[0].subject, 'feat: add auth');
  assert.strictEqual(commits[0].added, 16);
  assert.strictEqual(commits[0].deleted, 3);
  assert.deepStrictEqual(commits[0].files, ['src/auth.py', 'src/util.py']);
  assert.strictEqual(commits[1].added, 1);
});

test('parseGitLog survives multi-line bodies containing blank lines and tabs', () => {
  const body = 'Refactors the thing.\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n\ttabbed line\n';
  const commits = parseGitLog(logChunk({ sha: 'c'.repeat(40), body }, ['5\t0\tsrc/a.ts']));
  assert.strictEqual(commits.length, 1);
  assert.ok(commits[0].body.includes('Co-Authored-By: Claude'));
  assert.deepStrictEqual(commits[0].files, ['src/a.ts']);
});

test('parseGitLog counts binary files as zero lines without dropping the path', () => {
  const commits = parseGitLog(logChunk({ sha: 'd'.repeat(40) }, ['-\t-\tassets/logo.png', '3\t0\tsrc/a.ts']));
  assert.strictEqual(commits[0].added, 3);
  assert.deepStrictEqual(commits[0].files, ['assets/logo.png', 'src/a.ts']);
});

test('parseGitLog returns nothing for empty input', () => {
  assert.deepStrictEqual(parseGitLog(''), []);
  assert.deepStrictEqual(parseGitLog('   \n'), []);
});

test('normalizeRenamePath resolves both numstat rename forms to the new path', () => {
  assert.strictEqual(normalizeRenamePath('src/old.ts => src/new.ts'), 'src/new.ts');
  assert.strictEqual(normalizeRenamePath('src/{old => new}/index.ts'), 'src/new/index.ts');
  assert.strictEqual(normalizeRenamePath('src/plain.ts'), 'src/plain.ts');
});

test('detectTool matches the Claude Code trailer', () => {
  const match = detectTool(baseCommit({
    body: 'Some change.\n\nCo-Authored-By: Claude <noreply@anthropic.com>',
  }));
  assert.strictEqual(match.tool, 'Claude Code');
  assert.strictEqual(match.evidence, 'declared');
  assert.strictEqual(match.matchedField, 'trailer');
});

test('detectTool matches bot identities for Copilot, Devin and Jules', () => {
  const cases = [
    ['Copilot', 'copilot-swe-agent[bot]', 'GitHub Copilot'],
    ['Devin', 'devin-ai-integration[bot]', 'Devin'],
    ['Jules', 'google-labs-jules[bot]', 'Google Jules'],
  ];
  for (const [, name, expected] of cases) {
    const match = detectTool(baseCommit({ authorName: name }));
    assert.strictEqual(match && match.tool, expected, `expected ${expected} for ${name}`);
  }
});

test('detectTool matches the aider subject convention', () => {
  const match = detectTool(baseCommit({ subject: 'aider: refactor the parser' }));
  assert.strictEqual(match.tool, 'Aider');
  assert.strictEqual(match.matchedField, 'subject');
});

test('detectTool returns null for an ordinary human commit', () => {
  assert.strictEqual(detectTool(baseCommit({ subject: 'fix: off-by-one', body: 'Reviewed by Sam.' })), null);
});

test('detectTool does not fire on prose that merely mentions an AI tool', () => {
  const commit = baseCommit({
    subject: 'docs: explain how to use Claude Code with this repo',
    body: 'Cursor and Copilot are also mentioned in the README now.',
  });
  assert.strictEqual(detectTool(commit), null);
});

test('detectModel extracts a model only when the tool named one', () => {
  assert.strictEqual(detectModel('Generated with claude-opus-4-5'), 'claude-opus-4-5');
  assert.strictEqual(detectModel('used gpt-4o-mini here'), 'gpt-4o-mini');
  assert.strictEqual(detectModel('no model mentioned'), null);
});

test('parseBlameIncremental extracts hunk headers and ignores metadata lines', () => {
  const raw = [
    `${'a'.repeat(40)} 1 1 4`,
    'author Dev',
    'summary feat: add auth',
    'filename src/auth.py',
    `${'b'.repeat(40)} 9 5 2`,
    'author Other',
    'filename src/auth.py',
  ].join('\n');

  const hunks = parseBlameIncremental(raw);
  assert.deepStrictEqual(hunks, [
    { sha: 'a'.repeat(40), finalLine: 1, numLines: 4 },
    { sha: 'b'.repeat(40), finalLine: 5, numLines: 2 },
  ]);
});

test('parseBlameIncremental returns nothing for empty or unparseable input', () => {
  assert.deepStrictEqual(parseBlameIncremental(''), []);
  assert.deepStrictEqual(parseBlameIncremental('boundary\nauthor Dev'), []);
});

test('categorize flags risk surfaces from both path and content', () => {
  const slugs = categorize('src/routes/auth.py', [
    'def login(user, password):',
    '    row = db.execute("SELECT * FROM users WHERE name = " + user)',
  ]);
  assert.ok(slugs.includes('auth'), 'path and content both imply auth');
  assert.ok(slugs.includes('sql'), 'raw SQL detected');
  assert.deepStrictEqual(slugs, [...slugs].sort(), 'slugs are sorted');
});

test('categorize marks large blocks and stays quiet on benign code', () => {
  const big = Array.from({ length: 80 }, (_, i) => `const x${i} = ${i};`);
  assert.ok(categorize('src/math.ts', big).includes('large-block'));
  assert.deepStrictEqual(categorize('src/math.ts', ['const x = 1;']), []);
});

test('categorize detects CI and infra surfaces by path', () => {
  assert.ok(categorize('.github/workflows/release.yml', ['run: npm ci']).includes('ci'));
  assert.ok(categorize('infra/main.tf', ['resource "aws_s3_bucket" "b" {}']).includes('infra'));
});

test('detectConfiguredTools finds tracked and untracked agent config', () => {
  const tracked = detectConfiguredTools(['CLAUDE.md', 'src/index.ts']);
  assert.deepStrictEqual(tracked, [{ tool: 'Claude Code', evidence: 'CLAUDE.md', tracked: true }]);

  const untracked = detectConfiguredTools(['src/index.ts'], (rel) => rel === '.cursorrules');
  assert.strictEqual(untracked.length, 1);
  assert.strictEqual(untracked[0].tool, 'Cursor');
  assert.strictEqual(untracked[0].tracked, false);
  assert.match(untracked[0].evidence, /untracked/);
});

test('detectConfiguredTools matches directory artifacts by prefix', () => {
  const found = detectConfiguredTools(['.claude/settings.json']);
  assert.deepStrictEqual(found.map((f) => f.tool), ['Claude Code']);
});

test('assessCoverage calls a low declaration rate a floor, not a measurement', () => {
  const coverage = assessCoverage({
    trackedPaths: ['CLAUDE.md', 'src/a.ts'],
    attributedTools: new Set(['Claude Code']),
    commitsExamined: 200,
    aiCommits: 4,
  });
  assert.strictEqual(coverage.status, 'known_incomplete');
  assert.ok(coverage.declarationRate < 0.5);
  assert.match(coverage.interpretation, /floor, not a measurement/);
  assert.ok(coverage.reasons.some((r) => /invisible to this scan/.test(r)));
});

test('assessCoverage names a configured tool that never declared itself', () => {
  const coverage = assessCoverage({
    trackedPaths: ['.cursorrules'],
    attributedTools: new Set(),
    commitsExamined: 10,
    aiCommits: 0,
  });
  assert.strictEqual(coverage.status, 'known_incomplete');
  assert.deepStrictEqual(coverage.silentTools.map((t) => t.tool), ['Cursor']);
  assert.ok(coverage.reasons.some((r) => /never declared in any commit/.test(r)));
});

test('assessCoverage reports declared-signals-only when nothing suggests a gap', () => {
  const coverage = assessCoverage({
    trackedPaths: ['src/a.ts'],
    attributedTools: new Set(),
    commitsExamined: 10,
    aiCommits: 0,
  });
  assert.strictEqual(coverage.status, 'declared_signals_only');
  assert.deepStrictEqual(coverage.reasons, []);
});

test('assessCoverage treats a well-declared repo as complete', () => {
  const coverage = assessCoverage({
    trackedPaths: ['CLAUDE.md'],
    attributedTools: new Set(['Claude Code']),
    commitsExamined: 10,
    aiCommits: 9,
  });
  assert.strictEqual(coverage.status, 'declared_signals_only');
});

test('summarize rolls up tools, categories and honest totals', () => {
  const toolByCommit = new Map([
    ['a'.repeat(40), { tool: 'Claude Code', evidence: 'declared', matchedField: 'trailer', model: 'claude-opus-4-5' }],
    ['b'.repeat(40), { tool: 'GitHub Copilot', evidence: 'declared', matchedField: 'identity', model: null }],
  ]);
  const aiCommits = [
    baseCommit({ sha: 'a'.repeat(40), date: '2026-05-01T00:00:00Z' }),
    baseCommit({ sha: 'b'.repeat(40), date: '2026-06-01T00:00:00Z' }),
  ];
  const coverage = assessCoverage({
    trackedPaths: ['src/auth.py'],
    attributedTools: new Set(['Claude Code', 'GitHub Copilot']),
    commitsExamined: 2,
    aiCommits: 2,
  });

  const result = summarize({
    files: [
      { path: 'src/auth.py', totalLines: 100, attributedLines: 60, byTool: { 'Claude Code': 60 }, categories: ['auth'], firstSeen: null, lastSeen: null },
      { path: 'src/ui.tsx', totalLines: 50, attributedLines: 10, byTool: { 'GitHub Copilot': 10 }, categories: ['dom'], firstSeen: null, lastSeen: null },
    ],
    totalTrackedLines: 1000,
    totalTrackedFiles: 20,
    aiCommits,
    toolByCommit,
    coverage,
    repo: { root: '/repo', branch: 'main', headSha: 'f'.repeat(40), totalCommits: 2 },
  });

  assert.strictEqual(result.totals.aiAttributedLines, 70);
  assert.strictEqual(result.totals.unattributedLines, 930);
  assert.strictEqual(result.totals.aiAttributedPercent, 7);
  assert.strictEqual(result.byTool['Claude Code'].lines, 60);
  assert.deepStrictEqual(result.byTool['Claude Code'].models, ['claude-opus-4-5']);
  assert.strictEqual(result.byTool['GitHub Copilot'].commits, 1);
  assert.strictEqual(result.byCategory.auth.lines, 60);
  assert.strictEqual(result.files[0].path, 'src/auth.py', 'files sort by attributed lines desc');
  assert.strictEqual(result.scan.firstAiCommit, '2026-05-01T00:00:00Z');
  assert.strictEqual(result.scan.lastAiCommit, '2026-06-01T00:00:00Z');
});

test('summarize never describes unattributed lines as human-written', () => {
  const coverage = assessCoverage({
    trackedPaths: [], attributedTools: new Set(), commitsExamined: 0, aiCommits: 0,
  });
  const result = summarize({
    files: [],
    totalTrackedLines: 500,
    totalTrackedFiles: 5,
    aiCommits: [],
    toolByCommit: new Map(),
    coverage,
    repo: { root: '/repo', branch: 'main', headSha: '0'.repeat(40), totalCommits: 0 },
  });

  assert.strictEqual(result.totals.aiAttributedLines, 0);
  assert.strictEqual(result.totals.unattributedLines, 500);
  assert.match(result.assurance.unattributedMeaning, /not evidence of human authorship/);
  assert.ok(result.assurance.unavailable.includes('prompt'));
  assert.ok(result.assurance.unavailable.includes('review_state'));
});

test('summarize divides safely in an empty repo', () => {
  const coverage = assessCoverage({
    trackedPaths: [], attributedTools: new Set(), commitsExamined: 0, aiCommits: 0,
  });
  const result = summarize({
    files: [],
    totalTrackedLines: 0,
    totalTrackedFiles: 0,
    aiCommits: [],
    toolByCommit: new Map(),
    coverage,
    repo: { root: '/repo', branch: 'main', headSha: '0'.repeat(40), totalCommits: 0 },
  });
  assert.strictEqual(result.totals.aiAttributedPercent, 0);
});
