import {
  parseGitContext,
  captureGitContext,
  GIT_UNAVAILABLE,
  GitRunner,
} from '../evidence/git';

// ── parseGitContext ───────────────────────────────────────────────────────────

test('parseGitContext reads branch, commit, and dirty flag', () => {
  const ctx = parseGitContext('main\n', 'abc123\n', ' M src/file.ts\n');
  expect(ctx).toEqual({ available: true, branch: 'main', commit: 'abc123', dirty: true });
});

test('parseGitContext treats empty status as a clean tree', () => {
  const ctx = parseGitContext('main\n', 'abc123\n', '   \n');
  expect(ctx.dirty).toBe(false);
});

test('parseGitContext maps empty branch/commit to null', () => {
  const ctx = parseGitContext('', '', '');
  expect(ctx.branch).toBeNull();
  expect(ctx.commit).toBeNull();
});

// ── captureGitContext ─────────────────────────────────────────────────────────

test('captureGitContext parses output from an injected runner', async () => {
  const run: GitRunner = async (args) => {
    if (args[1] === '--abbrev-ref') { return 'feature/x\n'; }
    if (args[0] === 'rev-parse') { return 'deadbeef\n'; }
    return ''; // clean status
  };
  const ctx = await captureGitContext('/repo', run);
  expect(ctx).toEqual({ available: true, branch: 'feature/x', commit: 'deadbeef', dirty: false });
});

test('captureGitContext degrades cleanly when git throws (missing/not a repo)', async () => {
  const run: GitRunner = async () => { throw new Error('git not found'); };
  await expect(captureGitContext('/nope', run)).resolves.toEqual(GIT_UNAVAILABLE);
});
