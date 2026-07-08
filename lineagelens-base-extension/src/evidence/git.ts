/**
 * Best-effort git context for a capture (blueprint §16.2).
 *
 * Records the branch / HEAD / dirty state at capture time so a capture can be
 * tied to durable source history. Never blocks or fails capture: if git is
 * absent or the folder is not a repo, returns {@link GIT_UNAVAILABLE}.
 *
 * The git invocation is injectable (`run`) so the parsing + degradation paths are
 * unit-testable without shelling out.
 */

import { execFile } from 'child_process';
import { promisify } from 'util';

const execFileAsync = promisify(execFile);

export interface GitContext {
  available: boolean;
  branch: string | null;
  commit: string | null;
  dirty: boolean | null;
}

export const GIT_UNAVAILABLE: GitContext = {
  available: false,
  branch: null,
  commit: null,
  dirty: null,
};

/** Runs a git subcommand in a fixed cwd and resolves its stdout. */
export type GitRunner = (args: string[]) => Promise<string>;

function defaultRunner(cwd: string): GitRunner {
  return async (args) => (await execFileAsync('git', args, { cwd, windowsHide: true })).stdout;
}

/** Pure: assemble a GitContext from the three git outputs. */
export function parseGitContext(branchOut: string, commitOut: string, statusOut: string): GitContext {
  const branch = branchOut.trim();
  const commit = commitOut.trim();
  return {
    available: true,
    branch: branch || null,
    commit: commit || null,
    dirty: statusOut.trim().length > 0,
  };
}

/** Resolve the git context for `cwd`, degrading to GIT_UNAVAILABLE on any error. */
export async function captureGitContext(
  cwd: string,
  run: GitRunner = defaultRunner(cwd),
): Promise<GitContext> {
  try {
    const branch = await run(['rev-parse', '--abbrev-ref', 'HEAD']);
    const commit = await run(['rev-parse', 'HEAD']);
    const status = await run(['status', '--porcelain']);
    return parseGitContext(branch, commit, status);
  } catch {
    return GIT_UNAVAILABLE;
  }
}
