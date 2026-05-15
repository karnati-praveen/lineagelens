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
const { setJsonMode, isJsonMode } = require('../src/utils/lineagelens-cli-output');
const { getActiveMode } = require('../src/utils/lineagelens-cli-config');
const pkg = require('../package.json');

const VALID_MODES = ['base', 'plus', 'max'];

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
  if (active) {
    if (!isJsonMode()) {
      console.log(`Using active mode: ${active}`);
    }
    return active;
  }
  if (fallback) return fallback;
  console.error('No mode specified and no active mode is saved. Use --mode base, --mode plus, or --mode max.');
  process.exit(1);
}

const program = new Command();

program
  .name('lineagelens')
  .description('Manage LineageLens backends (Base, Plus, Max modes)')
  .version(pkg.version)
  .option('--json', 'Output results as JSON', false)
  .option('-y, --yes', 'Non-interactive mode: answer all prompts with defaults', false)
  .hook('preAction', (thisCommand) => {
    const opts = thisCommand.opts();
    setJsonMode(!!opts.json);
  });

program
  .command('start')
  .description('Start the LineageLens backend (Plus or Max mode)')
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
  .command('config')
  .description('Print the current LineageLens CLI config (~/.lineagelens/config.json)')
  .action(() => {
    configCmd();
  });

program.parse(process.argv);
