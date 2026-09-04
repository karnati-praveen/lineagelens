'use strict';

// Git I/O for the retroactive scanner. Everything here shells out to git and
// returns raw text; parsing lives in the engine so it stays unit-testable.
//
// No network calls, no writes, no config changes. A scan is read-only by design
// — it must be safe to run against someone else's repo on the first try.

const { execFileSync } = require('child_process');

const MAX_BUFFER = 256 * 1024 * 1024; // large monorepo logs

/**
 * Run git in `cwd` and return stdout. Throws on non-zero exit.
 * @param {string[]} args
 * @param {string} cwd
 * @returns {string}
 */
function git(args, cwd) {
  return execFileSync('git', args, {
    cwd,
    encoding: 'utf8',
    maxBuffer: MAX_BUFFER,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
}

/**
 * Run git and return null instead of throwing — for probes where failure is a
 * legitimate answer (no git, not a repo, empty repo).
 * @param {string[]} args
 * @param {string} cwd
 * @returns {string|null}
 */
function gitSafe(args, cwd) {
  try {
    return git(args, cwd);
  } catch {
    return null;
  }
}

/**
 * @typedef {object} RepoInfo
 * @property {string} root
 * @property {string} branch      branch name, or 'DETACHED'
 * @property {string} headSha
 * @property {number} totalCommits
 */

/**
 * Resolve repo identity, or throw a message meant to be shown to the user.
 * @param {string} dir
 * @returns {RepoInfo}
 */
function readRepoInfo(dir) {
  const root = gitSafe(['rev-parse', '--show-toplevel'], dir);
  if (root === null) {
    if (gitSafe(['--version'], dir) === null) {
      throw new Error('git was not found on PATH. `lineagelens scan` reads history directly from git.');
    }
    throw new Error(`Not a git repository: ${dir}`);
  }

  const headSha = gitSafe(['rev-parse', 'HEAD'], dir);
  if (headSha === null) {
    throw new Error('This repository has no commits yet, so there is no history to scan.');
  }

  const branch = (gitSafe(['rev-parse', '--abbrev-ref', 'HEAD'], dir) || '').trim();
  const count = (gitSafe(['rev-list', '--count', 'HEAD'], dir) || '0').trim();

  return {
    root: root.trim(),
    branch: branch === 'HEAD' || !branch ? 'DETACHED' : branch,
    headSha: headSha.trim(),
    totalCommits: Number(count) || 0,
  };
}

/**
 * Full history with per-file line counts.
 * @param {string} cwd
 * @param {string} format the engine's GIT_LOG_FORMAT
 * @param {object} [opts]
 * @param {string} [opts.since] git date expression, e.g. '2026-01-01' or '90 days ago'
 * @param {number} [opts.maxCommits]
 * @returns {string}
 */
function readLog(cwd, format, opts = {}) {
  const args = ['log', '--no-merges', '--numstat', `--format=${format}`];
  // Rename detection keeps attribution attached to the path that exists at HEAD.
  args.push('-M');
  if (opts.since) args.push(`--since=${opts.since}`);
  if (opts.maxCommits) args.push(`--max-count=${opts.maxCommits}`);
  return git(args, cwd);
}

/**
 * Per-hunk blame of the current file contents. `--incremental` emits one header
 * line per contiguous hunk rather than per source line, which is what makes
 * blaming thousands of files viable.
 *
 * Returns null when the file cannot be blamed (deleted, binary, submodule).
 *
 * @param {string} cwd
 * @param {string} filePath repo-relative
 * @returns {string|null}
 */
function readBlame(cwd, filePath) {
  return gitSafe(['blame', '--incremental', '-w', 'HEAD', '--', filePath], cwd);
}

/**
 * Paths tracked at HEAD. Respects .gitignore for free, so vendored trees and
 * build output never enter the denominator.
 * @param {string} cwd
 * @returns {string[]}
 */
function readTrackedFiles(cwd) {
  const raw = git(['ls-files', '-z', '--full-name'], cwd);
  return raw.split('\0').filter(Boolean);
}

module.exports = { git, gitSafe, readRepoInfo, readLog, readBlame, readTrackedFiles };
