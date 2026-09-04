'use strict';

// lineagelens scan — retroactive AI attribution for a repo that has never had
// LineageLens installed.
//
// Why this command exists: every other attribution path (extension capture, the
// proxy, blame, report, the dashboard) can only describe code written *after*
// LineageLens was installed. A team with 200k lines of AI-written code already
// in production gets nothing from those paths on day one. `scan` closes that
// gap by attributing from evidence the AI tools already wrote into git history,
// then blaming HEAD to count only the lines that actually survived.
//
// It is deliberately a lower-fidelity path and reports itself as such: no
// prompts, no accepted/rejected status, no review state. The output ends by
// naming exactly what a live install would add.

const fs = require('fs');
const path = require('path');

const {
  GIT_LOG_FORMAT,
  parseGitLog,
  detectTool,
  parseBlameIncremental,
  categorize,
  assessCoverage,
  summarize,
} = require('../scan/lineagelens-cli-scan-engine');
const {
  readRepoInfo,
  readLog,
  readBlame,
  readTrackedFiles,
} = require('../scan/lineagelens-cli-scan-git');
const { isJsonMode, out, err } = require('../utils/lineagelens-cli-output');
const {
  makePalette,
  bar: renderBar,
  num,
  pct,
  plural,
  readTextLines,
} = require('../utils/lineagelens-cli-render');

// Extensions that are text but not source worth attributing line-by-line.
const SKIP_EXTENSIONS = new Set([
  '.lock', '.snap', '.map', '.min.js', '.min.css', '.svg', '.csv', '.tsv',
]);

/** True when the path is text-but-not-source we should exclude. */
function isSkippedPath(relPath) {
  const lower = relPath.toLowerCase();
  if (SKIP_EXTENSIONS.has(path.extname(lower))) return true;
  return [...SKIP_EXTENSIONS].some((ext) => ext.includes('.min.') && lower.endsWith(ext));
}

/** Scan's bars are wider than the shared default. */
function bar(percent, width = 24) {
  return renderBar(percent, width);
}

function shortDate(iso) {
  return iso ? String(iso).slice(0, 10) : 'unknown';
}

/**
 * Terminal report. The order is deliberate: the headline number first, then who
 * wrote it, then which risk surfaces it landed on, then what is *not* known.
 *
 * @param {object} result output of summarize()
 * @param {ReturnType<makePalette>} c
 * @param {number} topN
 * @returns {string}
 */
function renderTerminal(result, c, topN) {
  const { scan, totals, byTool, byCategory, files, coverage, assurance } = result;
  const L = [];
  const isFloor = assurance.measurementKind === 'lower_bound';

  L.push('');
  L.push(c.bold(`── lineagelens scan ─ ${path.basename(scan.repoRoot) || scan.repoRoot}`) +
    c.dim(` @ ${scan.branch} · ${scan.headSha.slice(0, 8)}`));
  L.push('');

  if (totals.aiAttributedLines === 0) {
    // The most common first run. An empty result is easy to misread as "this repo
    // has no AI code", so it has to explain itself and end with a next step —
    // otherwise the tool looks broken to the exact user it should convert.
    L.push(`  ${c.bold("No AI tool declared authorship anywhere in this repo's history.")}`);
    L.push(c.dim(`  ${plural(scan.commitsExamined, 'commit')} examined · ${plural(totals.trackedLines, 'tracked line')}`));
    L.push('');
    L.push(`  ${c.yellow('That is not the same as "no AI code here."')} It means nothing in git says so.`);
    L.push('');
    L.push(`  ${c.bold('Two reasons a repo looks like this')}`);
    L.push('    1. No AI tool was used — nothing to find.');
    L.push('    2. AI tools were used but left no commit trailer. This is the common case:');
    L.push('       most editors and agents attribute nothing by default.');

    if (coverage.configuredTools.length > 0) {
      L.push('');
      L.push(`  ${c.yellow(c.bold('Reason 2 is likely here.'))} These tools are configured in this repo but`);
      L.push(`  ${c.yellow('never declared themselves in a commit:')}`);
      const toolWidth = Math.max(...coverage.configuredTools.map((t) => t.tool.length));
      for (const tool of coverage.configuredTools) {
        L.push(`    ${c.yellow(tool.tool.padEnd(toolWidth))}${c.dim(`  ${tool.evidence}`)}`);
      }
    }

    L.push('');
    L.push(`  ${c.bold('Make future AI work attributable')}`);
    L.push(c.dim('    · Agents that commit for you (Claude Code, Copilot coding agent, Devin,'));
    L.push(c.dim('      Jules, Aider) add trailers themselves — they will appear here from now on.'));
    L.push(c.dim('    · Editor tools (Cursor, Copilot autocomplete, Windsurf) never reach git,'));
    L.push(c.dim('      so capture them at write time instead:'));
    L.push(`        ${c.magenta('code --install-extension karnatipraveen.lineagelens-base')}`);
    L.push('');
    return L.join('\n');
  }

  L.push(`  ${c.bold(isFloor
    ? 'AI-written code live in this repo right now — at least'
    : 'AI-written code live in this repo right now')}`);
  L.push('');
  L.push(`    ${c.red(bar(totals.aiAttributedPercent))}  ${c.bold(`${isFloor ? '≥' : ''}${pct(totals.aiAttributedPercent)}`)}` +
    `  ${c.bold(num(totals.aiAttributedLines))} of ${num(totals.trackedLines)} surviving lines`);
  L.push(c.dim(`    across ${num(totals.aiAttributedFiles)} of ${num(totals.trackedFiles)} tracked files` +
    ` · ${plural(scan.aiCommits, 'AI commit')} · ${shortDate(scan.firstAiCommit)} → ${shortDate(scan.lastAiCommit)}`));

  if (coverage.status === 'known_incomplete') {
    L.push('');
    L.push(`  ${c.yellow(c.bold('⚠ This number is a floor, not a measurement.'))}`);
    for (const reason of coverage.reasons) {
      L.push(`    ${c.yellow('·')} ${reason}`);
    }
    L.push(c.dim('    Undeclared AI code is counted as unattributed. A live install removes this gap.'));
  }

  const tools = Object.entries(byTool).sort((a, b) => b[1].lines - a[1].lines);
  if (tools.length > 0) {
    L.push('');
    L.push(`  ${c.bold('Who wrote it')}`);
    const width = Math.max(...tools.map(([t]) => t.length));
    for (const [tool, stats] of tools) {
      const models = stats.models.length > 0 ? c.dim(`  ${stats.models.join(', ')}`) : '';
      L.push(`    ${c.cyan(tool.padEnd(width))}  ${num(stats.lines).padStart(8)} lines` +
        c.dim(`  ${plural(stats.commits, 'commit')} · ${plural(stats.files, 'file')}`) + models);
    }
  }

  const cats = Object.entries(byCategory).sort((a, b) => b[1].lines - a[1].lines);
  if (cats.length > 0) {
    L.push('');
    L.push(`  ${c.bold('Risk surfaces this AI code sits on')}` + c.dim('  (local signals, not SAST)'));
    const width = Math.max(...cats.map(([s]) => s.length));
    for (const [slug, stats] of cats) {
      const emphasis = ['auth', 'secrets', 'payments'].includes(slug) ? c.yellow : c.dim;
      L.push(`    ${emphasis(slug.padEnd(width))}  ${num(stats.lines).padStart(8)} lines` +
        c.dim(`  ${plural(stats.files, 'file')}`));
    }
  }

  if (files.length > 0) {
    L.push('');
    L.push(`  ${c.bold('Most AI-written files')}`);
    const shown = files.slice(0, topN);
    const width = Math.max(...shown.map((f) => f.path.length));
    for (const f of shown) {
      const filePct = f.totalLines > 0 ? (f.attributedLines / f.totalLines) * 100 : 0;
      const tags = f.categories.length > 0 ? c.yellow(`  [${f.categories.join(' ')}]`) : '';
      L.push(`    ${f.path.padEnd(width)}  ${c.red(bar(filePct, 12))} ${pct(filePct).padStart(6)}` +
        c.dim(`  ${num(f.attributedLines)}/${num(f.totalLines)}`) + tags);
    }
    if (files.length > shown.length) {
      L.push(c.dim(`    … and ${num(files.length - shown.length)} more (use --top)`));
    }
  }

  if (coverage.configuredTools.length > 0) {
    L.push('');
    L.push(`  ${c.bold('AI tooling configured in this repo')}`);
    const width = Math.max(...coverage.configuredTools.map((t) => t.tool.length));
    const evidenceWidth = Math.max(...coverage.configuredTools.map((t) => t.evidence.length));
    for (const tool of coverage.configuredTools) {
      const silent = coverage.silentTools.some((s) => s.tool === tool.tool);
      const name = tool.tool.padEnd(width);
      L.push(`    ${silent ? c.yellow(name) : c.cyan(name)}` +
        c.dim(`  ${tool.evidence.padEnd(silent ? evidenceWidth : 0)}`) +
        (silent ? c.yellow('  never declared in a commit') : ''));
    }
  }

  L.push('');
  L.push(`  ${c.bold('What this scan does and does not prove')}`);
  L.push(`    ${c.green('proven')}       ${num(totals.aiAttributedLines)} lines came from commits that declared an AI tool`);
  L.push(`    ${c.dim('unattributed')} ${num(totals.unattributedLines).padStart(String(num(totals.aiAttributedLines)).length)} lines carry no AI signal — ` +
    c.dim('this is not evidence of human authorship'));
  L.push(`    ${c.dim('unavailable')}  ${assurance.unavailable.join(', ')}`);
  L.push('');
  L.push(c.dim('  Retroactive scans read git metadata only. To capture prompts, models,'));
  L.push(c.dim('  accepted/rejected status and review state going forward:'));
  L.push(`    ${c.magenta('code --install-extension karnatipraveen.lineagelens-base')}`);
  L.push('');

  return L.join('\n');
}

/**
 * Paste-ready markdown, for PR descriptions and internal write-ups.
 * @param {object} result
 * @param {number} topN
 * @returns {string}
 */
function renderMarkdown(result, topN) {
  const { scan, totals, byTool, byCategory, files, coverage, assurance } = result;
  const L = [];
  const isFloor = assurance.measurementKind === 'lower_bound';

  L.push(`## AI code inventory — \`${path.basename(scan.repoRoot)}\` @ \`${scan.branch}\``);
  L.push('');
  L.push(`**${isFloor ? 'At least ' : ''}${pct(totals.aiAttributedPercent)}** of the code live in this repo right now ` +
    `was written by an AI tool (${num(totals.aiAttributedLines)} of ${num(totals.trackedLines)} surviving lines, ` +
    `${num(totals.aiAttributedFiles)} of ${num(totals.trackedFiles)} files).`);
  if (isFloor) {
    L.push('');
    L.push(`> ⚠️ **This is a floor, not a measurement.** ${coverage.reasons.join('; ')}. ` +
      'Undeclared AI code is counted as unattributed.');
  }
  L.push('');
  L.push(`Attributed from ${num(scan.aiCommits)} AI-declared commits across ${num(scan.commitsExamined)} examined ` +
    `(${shortDate(scan.firstAiCommit)} → ${shortDate(scan.lastAiCommit)}).`);

  const tools = Object.entries(byTool).sort((a, b) => b[1].lines - a[1].lines);
  if (tools.length > 0) {
    L.push('');
    L.push('| Tool | Surviving lines | Commits | Files | Models named |');
    L.push('|---|---:|---:|---:|---|');
    for (const [tool, s] of tools) {
      L.push(`| ${tool} | ${num(s.lines)} | ${num(s.commits)} | ${num(s.files)} | ${s.models.join(', ') || '—'} |`);
    }
  }

  const cats = Object.entries(byCategory).sort((a, b) => b[1].lines - a[1].lines);
  if (cats.length > 0) {
    L.push('');
    L.push('**Risk surfaces touched by AI code** (deterministic local signals, not SAST): ' +
      cats.map(([slug, s]) => `\`${slug}\` (${num(s.lines)} lines / ${num(s.files)} ${s.files === 1 ? 'file' : 'files'})`).join(', '));
  }

  if (files.length > 0) {
    L.push('');
    L.push('| File | AI lines | Total | AI % | Signals |');
    L.push('|---|---:|---:|---:|---|');
    for (const f of files.slice(0, topN)) {
      const filePct = f.totalLines > 0 ? (f.attributedLines / f.totalLines) * 100 : 0;
      L.push(`| \`${f.path}\` | ${num(f.attributedLines)} | ${num(f.totalLines)} | ${pct(filePct)} | ${f.categories.join(', ') || '—'} |`);
    }
  }

  if (coverage.configuredTools.length > 0) {
    L.push('');
    L.push('**AI tooling configured here:** ' +
      coverage.configuredTools
        .map((t) => `${t.tool} (\`${t.evidence}\`)${coverage.silentTools.some((s) => s.tool === t.tool) ? ' — never declared in a commit' : ''}`)
        .join(', ') + '.');
  }

  L.push('');
  L.push('> **What this proves.** Lines are attributed only where the AI tool itself declared authorship in git ' +
    'metadata. Unattributed lines carry no AI signal — that is not evidence of human authorship. ' +
    `Not recoverable retroactively: ${assurance.unavailable.join(', ')}.`);
  L.push('');
  L.push('_Generated by [`lineagelens scan`](https://github.com/karnati-praveen/lineagelens) — no backend, no install, read-only._');

  return L.join('\n');
}

/**
 * lineagelens scan [dir] — retroactive AI attribution from git history.
 *
 * @param {string|undefined} dir
 * @param {object} opts
 */
async function scan(dir, opts = {}) {
  const target = path.resolve(dir || '.');

  /** @type {import('../scan/lineagelens-cli-scan-git').RepoInfo} */
  let repo;
  try {
    repo = readRepoInfo(target);
  } catch (e) {
    err(e.message);
    process.exit(1);
  }

  let commits;
  try {
    commits = parseGitLog(readLog(repo.root, GIT_LOG_FORMAT, {
      since: opts.since,
      maxCommits: opts.maxCommits ? Number(opts.maxCommits) : undefined,
    }));
  } catch (e) {
    err(`Could not read git history: ${e.message}`);
    process.exit(1);
  }

  // 1. Which commits declared an AI tool.
  const toolByCommit = new Map();
  const dateByCommit = new Map();
  const aiCommits = [];
  for (const commit of commits) {
    dateByCommit.set(commit.sha, commit.date);
    const match = detectTool(commit);
    if (!match) continue;
    toolByCommit.set(commit.sha, match);
    aiCommits.push(commit);
  }

  const tracked = readTrackedFiles(repo.root);
  const trackedSet = new Set(tracked);

  // 2. Only blame files an AI commit actually touched and that still exist.
  const candidates = new Set();
  for (const commit of aiCommits) {
    for (const file of commit.files) {
      if (trackedSet.has(file) && !isSkippedPath(file)) candidates.add(file);
    }
  }

  // 3. Denominator: every tracked text file, so the percentage is honest about
  //    the whole repo rather than only the files AI touched.
  let totalTrackedLines = 0;
  let totalTrackedFiles = 0;
  /** @type {Map<string, string[]>} */
  const linesCache = new Map();
  for (const rel of tracked) {
    if (isSkippedPath(rel)) continue;
    const lines = readTextLines(path.join(repo.root, rel));
    if (lines === null) continue;
    totalTrackedFiles += 1;
    totalTrackedLines += lines.length;
    if (candidates.has(rel)) linesCache.set(rel, lines);
  }

  // 4. Blame each candidate and keep only the lines that survived to HEAD.
  /** @type {import('../scan/lineagelens-cli-scan-engine').FileAttribution[]} */
  const files = [];
  const categoryFilter = opts.category ? String(opts.category).toLowerCase() : null;
  const toolFilter = opts.tool ? String(opts.tool).toLowerCase() : null;

  for (const rel of candidates) {
    const fileLines = linesCache.get(rel);
    if (!fileLines) continue;

    const raw = readBlame(repo.root, rel);
    if (raw === null) continue;

    /** @type {Record<string, number>} */
    const byTool = {};
    /** @type {string[]} */
    const attributedText = [];
    let attributedLines = 0;
    let firstSeen = null;
    let lastSeen = null;

    for (const hunk of parseBlameIncremental(raw)) {
      const match = toolByCommit.get(hunk.sha);
      if (!match) continue;
      if (toolFilter && match.tool.toLowerCase() !== toolFilter) continue;

      byTool[match.tool] = (byTool[match.tool] || 0) + hunk.numLines;
      attributedLines += hunk.numLines;
      for (let i = 0; i < hunk.numLines; i += 1) {
        const line = fileLines[hunk.finalLine - 1 + i];
        if (line !== undefined) attributedText.push(line);
      }

      const commitDate = dateByCommit.get(hunk.sha);
      if (commitDate) {
        if (!firstSeen || commitDate < firstSeen) firstSeen = commitDate;
        if (!lastSeen || commitDate > lastSeen) lastSeen = commitDate;
      }
    }

    if (attributedLines === 0) continue;

    const categories = categorize(rel, attributedText);
    if (categoryFilter && !categories.includes(categoryFilter)) continue;

    files.push({
      path: rel,
      totalLines: fileLines.length,
      attributedLines,
      byTool,
      categories,
      firstSeen,
      lastSeen,
    });
  }

  const attributedTools = new Set(aiCommits.map((c) => toolByCommit.get(c.sha).tool));
  const coverage = assessCoverage({
    trackedPaths: tracked,
    attributedTools,
    commitsExamined: commits.length,
    aiCommits: aiCommits.length,
    existsInWorktree: (rel) => fs.existsSync(path.join(repo.root, rel)),
  });

  const result = summarize({
    files,
    totalTrackedLines,
    totalTrackedFiles,
    aiCommits: toolFilter
      ? aiCommits.filter((c) => toolByCommit.get(c.sha).tool.toLowerCase() === toolFilter)
      : aiCommits,
    toolByCommit,
    coverage,
    repo,
  });

  const topN = Number.parseInt(opts.top, 10) > 0 ? Number.parseInt(opts.top, 10) : 15;

  if (isJsonMode()) {
    out(result);
    return result;
  }
  if (opts.md) {
    out(renderMarkdown(result, topN));
    return result;
  }
  const palette = makePalette(opts.color !== false && process.stdout.isTTY);
  out(renderTerminal(result, palette, topN));
  return result;
}

module.exports = { scan, renderTerminal, renderMarkdown, isSkippedPath };
