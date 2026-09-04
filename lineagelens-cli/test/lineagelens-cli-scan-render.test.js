'use strict';

// Tests for what a user actually reads. The scan engine is covered separately;
// this file guards the output contract — above all that an empty result never
// reads as "this repo has no AI code" and never dead-ends without a next step.

const test = require('node:test');
const assert = require('node:assert');

const { renderTerminal, renderMarkdown } = require('../src/commands/lineagelens-cli-scan');
const { makePalette, plural } = require('../src/utils/lineagelens-cli-render');
const { assessCoverage, summarize } = require('../src/scan/lineagelens-cli-scan-engine');

const plain = makePalette(false);

/**
 * Build a scan result the way the command does.
 * @param {object} [over]
 */
function makeResult(over = {}) {
  const {
    files = [],
    trackedLines = 500,
    trackedFiles = 10,
    aiCommits = [],
    toolByCommit = new Map(),
    trackedPaths = ['src/a.ts'],
    attributedTools = new Set(),
    commitsExamined = 10,
    existsInWorktree,
  } = over;

  const coverage = assessCoverage({
    trackedPaths,
    attributedTools,
    commitsExamined,
    aiCommits: aiCommits.length,
    existsInWorktree,
  });

  return summarize({
    files,
    totalTrackedLines: trackedLines,
    totalTrackedFiles: trackedFiles,
    aiCommits,
    toolByCommit,
    coverage,
    repo: { root: '/repo/acme', branch: 'main', headSha: 'a'.repeat(40), totalCommits: commitsExamined },
  });
}

const CLAUDE = { tool: 'Claude Code', evidence: 'declared', matchedField: 'trailer', model: 'claude-opus-4-5' };

function populated() {
  const sha = 'b'.repeat(40);
  return makeResult({
    files: [{
      path: 'src/auth.py',
      totalLines: 11,
      attributedLines: 10,
      byTool: { 'Claude Code': 10 },
      categories: ['auth', 'secrets'],
      firstSeen: '2026-07-01T00:00:00Z',
      lastSeen: '2026-07-01T00:00:00Z',
    }],
    trackedLines: 20,
    trackedFiles: 5,
    aiCommits: [{ sha, date: '2026-07-01T00:00:00Z' }],
    toolByCommit: new Map([[sha, CLAUDE]]),
    attributedTools: new Set(['Claude Code']),
    commitsExamined: 1,
  });
}

test('plural agrees with its count', () => {
  assert.strictEqual(plural(1, 'commit'), '1 commit');
  assert.strictEqual(plural(2, 'commit'), '2 commits');
  assert.strictEqual(plural(0, 'file'), '0 files');
  assert.strictEqual(plural(1000, 'line'), '1,000 lines');
  assert.strictEqual(plural(1, 'entry', 'entries'), '1 entry');
  assert.strictEqual(plural(3, 'entry', 'entries'), '3 entries');
});

test('empty result never implies the repo is free of AI code', () => {
  const text = renderTerminal(makeResult(), plain, 15);

  assert.ok(
    /not the same as "no AI code here\."/.test(text),
    'must explicitly separate "nothing declared" from "no AI code"',
  );
  assert.ok(!/\bhuman-written\b/.test(text), 'must not describe unattributed code as human-written');
  assert.ok(!/0\.0%/.test(text), 'must not headline a 0% figure that reads as a clean bill of health');
});

test('empty result explains both causes and ends with a next step', () => {
  const text = renderTerminal(makeResult(), plain, 15);

  assert.ok(/No AI tool was used/.test(text), 'names the benign cause');
  assert.ok(/left no commit trailer/.test(text), 'names the common cause');
  assert.ok(/Make future AI work attributable/.test(text), 'offers a next step');
  assert.ok(/--install-extension/.test(text), 'gives a runnable command');
});

test('empty result singularises a one-commit repo', () => {
  const text = renderTerminal(makeResult({ commitsExamined: 1, trackedLines: 1 }), plain, 15);
  assert.ok(/1 commit examined/.test(text));
  assert.ok(!/1 commits/.test(text));
  assert.ok(/1 tracked line\b/.test(text));
});

test('empty result points at configured-but-silent tooling when present', () => {
  const text = renderTerminal(
    makeResult({ trackedPaths: ['.cursorrules', 'CLAUDE.md'] }),
    plain,
    15,
  );
  assert.ok(/Reason 2 is likely here/.test(text), 'escalates when config proves a tool was in use');
  assert.ok(/Cursor/.test(text));
  assert.ok(/Claude Code/.test(text));
});

test('empty result stays quiet about causes it cannot evidence', () => {
  const text = renderTerminal(makeResult(), plain, 15);
  assert.ok(!/Reason 2 is likely here/.test(text), 'no config found, so no escalation');
});

test('populated result headlines a floor when declaration coverage is low', () => {
  const result = makeResult({
    files: [{
      path: 'src/a.ts', totalLines: 100, attributedLines: 40,
      byTool: { 'Claude Code': 40 }, categories: [], firstSeen: null, lastSeen: null,
    }],
    trackedLines: 1000,
    aiCommits: [{ sha: 'c'.repeat(40), date: '2026-07-01T00:00:00Z' }],
    toolByCommit: new Map([['c'.repeat(40), CLAUDE]]),
    attributedTools: new Set(['Claude Code']),
    commitsExamined: 100,
  });

  assert.strictEqual(result.assurance.measurementKind, 'lower_bound');
  const text = renderTerminal(result, plain, 15);
  assert.ok(/≥4\.0%/.test(text), 'headline is prefixed with the floor marker');
  assert.ok(/floor, not a measurement/.test(text));
});

test('populated result reports surviving lines, tools, models and risk surfaces', () => {
  const text = renderTerminal(populated(), plain, 15);

  assert.ok(/10 of 20 surviving lines/.test(text));
  assert.ok(/Claude Code/.test(text));
  assert.ok(/claude-opus-4-5/.test(text), 'model named by the tool is surfaced');
  assert.ok(/auth/.test(text));
  assert.ok(/src\/auth\.py/.test(text));
  assert.ok(/1 AI commit\b/.test(text), 'singular for one commit');
  assert.ok(!/1 commits|1 files|1 lines\b/.test(text), 'no disagreeing plurals');
});

test('populated result always states what it cannot prove', () => {
  const text = renderTerminal(populated(), plain, 15);
  assert.ok(/does and does not prove/.test(text));
  assert.ok(/not evidence of human authorship/.test(text));
  for (const field of ['prompt', 'review_state', 'accepted_rejected_status']) {
    assert.ok(text.includes(field), `must name ${field} as unavailable`);
  }
});

test('markdown output carries the floor warning and the limits note', () => {
  const md = renderMarkdown(
    makeResult({
      files: [{
        path: 'src/a.ts', totalLines: 100, attributedLines: 40,
        byTool: { 'Claude Code': 40 }, categories: ['auth'], firstSeen: null, lastSeen: null,
      }],
      trackedLines: 1000,
      aiCommits: [{ sha: 'd'.repeat(40), date: '2026-07-01T00:00:00Z' }],
      toolByCommit: new Map([['d'.repeat(40), CLAUDE]]),
      attributedTools: new Set(['Claude Code']),
      commitsExamined: 100,
    }),
    15,
  );

  assert.ok(/^## AI code inventory/m.test(md));
  assert.ok(/At least /.test(md), 'floor is stated in prose, not just a symbol');
  assert.ok(/floor, not a measurement/.test(md));
  assert.ok(/not evidence of human authorship/.test(md));
  assert.ok(/\| File \| AI lines \|/.test(md), 'emits a file table');
});

test('renderers never emit ANSI codes when colour is disabled', () => {
  const text = renderTerminal(populated(), plain, 15);
  // eslint-disable-next-line no-control-regex
  assert.ok(!/\x1b\[/.test(text), 'palette must collapse to identity for pipes and --no-color');
});
