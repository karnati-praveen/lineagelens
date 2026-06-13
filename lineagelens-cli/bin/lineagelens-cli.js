#!/usr/bin/env node
'use strict';

const { Command } = require('commander');
const { start } = require('../src/commands/lineagelens-cli-start');
const { stop } = require('../src/commands/lineagelens-cli-stop');
const { status } = require('../src/commands/lineagelens-cli-status');
const { logs } = require('../src/commands/lineagelens-cli-logs');
const { restart } = require('../src/commands/lineagelens-cli-restart');
const { update } = require('../src/commands/lineagelens-cli-update');
const { backup } = require('../src/commands/lineagelens-cli-backup');
const { upgrade } = require('../src/commands/lineagelens-cli-upgrade');
const { rollback } = require('../src/commands/lineagelens-cli-rollback');
const { configCmd } = require('../src/commands/lineagelens-cli-config-cmd');
const { blame } = require('../src/commands/lineagelens-cli-blame');
const { report } = require('../src/commands/lineagelens-cli-report');
const { setJsonMode, isJsonMode } = require('../src/utils/lineagelens-cli-output');
const { getActiveMode } = require('../src/utils/lineagelens-cli-config');
const pkg = require('../package.json');

const VALID_MODES = ['plus', 'max'];

function assertMode(mode) {
  if (!VALID_MODES.includes(mode)) {
    console.error(`Invalid mode "${mode}". Choose: ${VALID_MODES.join(', ')}`);
    process.exit(1);
  }
}

/**
 * Resolve mode: use explicitly provided value, or fall back to persisted active mode.
 * Exits with error if neither is available.
 * @param {string|undefined} provided - mode from CLI option
 * @param {string} [fallback] - default fallback if no active mode is saved
 * @returns {string}
 */
function resolveMode(provided, fallback) {
  if (provided) return provided;
  const active = getActiveMode();
  if (active && VALID_MODES.includes(active)) {
    if (!isJsonMode()) {
      console.log(`Using active mode: ${active}`);
    }
    return active;
  }
  if (active && !isJsonMode()) {
    console.warn(`Ignoring unsupported saved mode: ${active}`);
  }
  if (fallback) return fallback;
  console.error('No mode specified and no active mode is saved. Use --mode plus or --mode max.');
  process.exit(1);
}

const program = new Command();

program
  .name('lineagelens')
  .description('Manage LineageLens Plus and Max backends')
  .version(pkg.version)
  .option('--json', 'Output results as JSON', false)
  .option('-y, --yes', 'Non-interactive mode: answer all prompts with defaults', false)
  .hook('preAction', (thisCommand) => {
    const opts = thisCommand.opts();
    setJsonMode(!!opts.json);
  });

program
  .command('start')
  .description('Start the LineageLens backend')
  .option('-m, --mode <mode>', 'Backend mode: plus | max')
  .action(async (opts) => {
    const globalOpts = program.opts();
    const mode = resolveMode(opts.mode, 'plus');
    assertMode(mode);
    await start(mode, { nonInteractive: !!globalOpts.yes });
  });

program
  .command('stop')
  .description('Stop the LineageLens backend')
  .option('-m, --mode <mode>', 'Backend mode: plus | max')
  .option('-v, --volumes', 'Also remove persistent volumes (wipes data)', false)
  .option('--confirm-wipe', 'Required when using --volumes: confirms permanent data deletion', false)
  .action((opts) => {
    const mode = resolveMode(opts.mode, 'plus');
    assertMode(mode);
    stop(mode, opts);
  });

program
  .command('status')
  .description('Show running container status for all configured modes')
  .option('-m, --mode <mode>', 'Limit to a specific mode: plus | max')
  .action((opts) => {
    if (opts.mode) assertMode(opts.mode);
    status(opts);
  });

program
  .command('logs')
  .description('Tail logs from a running backend')
  .option('-m, --mode <mode>', 'Backend mode: plus | max')
  .option('-s, --service <service>', 'Filter to one service: backend | proxy | postgres | neo4j')
  .option('-n, --tail <lines>', 'Number of lines to show from the end', '100')
  .action((opts) => {
    const mode = resolveMode(opts.mode, 'plus');
    assertMode(mode);
    opts.mode = mode;
    logs(opts);
  });

program
  .command('restart')
  .description('Stop then start the LineageLens backend (data preserved)')
  .option('-m, --mode <mode>', 'Backend mode: plus | max')
  .action(async (opts) => {
    const mode = resolveMode(opts.mode, 'plus');
    assertMode(mode);
    await restart(mode, opts);
  });

program
  .command('update')
  .description('Pull latest images and recreate containers (data preserved)')
  .option('-m, --mode <mode>', 'Backend mode: plus | max')
  .action(async (opts) => {
    const mode = resolveMode(opts.mode, 'plus');
    assertMode(mode);
    await update(mode);
  });

program
  .command('backup')
  .description('Dump the Postgres database to a local file')
  .option('-m, --mode <mode>', 'Backend mode: plus | max')
  .option('-o, --output <file>', 'Output file path (default: lineagelens-backup-<mode>-<timestamp>.dump)')
  .action((opts) => {
    const mode = resolveMode(opts.mode, 'plus');
    assertMode(mode);
    backup(mode, opts);
  });

program
  .command('upgrade')
  .description('Safely upgrade: backup DB, pull new images, force-recreate containers, health check')
  .option('-m, --mode <mode>', 'Backend mode: plus | max')
  .action(async (opts) => {
    const globalOpts = program.opts();
    const mode = resolveMode(opts.mode, 'plus');
    assertMode(mode);
    await upgrade(mode, { nonInteractive: !!globalOpts.yes });
  });

program
  .command('rollback')
  .description('Restore database from a dump file: stop backend, pg_restore, restart, health check')
  .option('-m, --mode <mode>', 'Backend mode: plus | max')
  .option('-f, --file <file>', 'Path to the .dump file to restore from')
  .action(async (opts) => {
    const mode = resolveMode(opts.mode, 'plus');
    assertMode(mode);
    await rollback(mode, opts);
  });

program
  .command('blame <file>')
  .description('Per-line AI attribution for a file — git blame, but it tells you which AI')
  .option('-i, --input <records>', 'Record source: extension captures.json, agent-trace .jsonl, or saved /search response')
  .option('-u, --url <backendUrl>', 'LineageLens backend URL (or env LINEAGELENS_URL)')
  .option('-t, --token <jwt>', 'Backend access token (or env LINEAGELENS_TOKEN)')
  .option('-w, --workspace <id>', 'Workspace id for backend mode (or env LINEAGELENS_WORKSPACE)')
  .option('--review-status <status>', 'Filter by review status: unreviewed | pending | reviewed (backend mode only)')
  .option('--category <slug>', 'Filter by risk category: auth | secrets | sql | shell | dom | payments | eval | large-block (backend mode only)')
  .option('--stats', 'Print only the summary, not the annotated file', false)
  .option('--min-confidence <n>', 'Ignore records below this confidence (0–1)', parseFloat)
  .option('--no-color', 'Disable ANSI colors')
  .action(async (file, opts) => {
    await blame(file, opts);
  });

program
  .command('report [dir]')
  .description('Repo-wide AI attribution report — how much of this codebase did AI write?')
  .option('-i, --input <records>', 'Record source: extension captures.json, agent-trace .jsonl, or saved /search response')
  .option('-u, --url <backendUrl>', 'LineageLens backend URL (or env LINEAGELENS_URL)')
  .option('-t, --token <jwt>', 'Backend access token (or env LINEAGELENS_TOKEN)')
  .option('-w, --workspace <id>', 'Workspace id for backend mode (or env LINEAGELENS_WORKSPACE)')
  .option('--review-status <status>', 'Filter by review status: unreviewed | pending | reviewed (backend mode only)')
  .option('--category <slug>', 'Filter by risk category: auth | secrets | sql | shell | dom | payments | eval | large-block (backend mode only)')
  .option('--md', 'Output a paste-ready markdown report (for READMEs and PRs)', false)
  .option('--top <n>', 'Show at most n files in the table', '25')
  .option('--min-confidence <n>', 'Ignore records below this confidence (0–1)', parseFloat)
  .option('--no-color', 'Disable ANSI colors')
  .action(async (dir, opts) => {
    await report(dir, opts);
  });

program
  .command('config')
  .description('Print the current LineageLens CLI config (~/.lineagelens/config.json)')
  .action(() => {
    configCmd();
  });

program.parse(process.argv);
