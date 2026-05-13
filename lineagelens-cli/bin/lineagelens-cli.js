#!/usr/bin/env node
'use strict';

const { Command } = require('commander');
const { start } = require('../src/commands/lineagelens-cli-start');
const { stop } = require('../src/commands/lineagelens-cli-stop');
const { status } = require('../src/commands/lineagelens-cli-status');
const { logs } = require('../src/commands/lineagelens-cli-logs');
const pkg = require('../package.json');

const VALID_MODES = ['plus', 'max'];

function assertMode(mode) {
  if (!VALID_MODES.includes(mode)) {
    console.error(`Invalid mode "${mode}". Choose: ${VALID_MODES.join(', ')}`);
    process.exit(1);
  }
}

const program = new Command();

program
  .name('lineagelens')
  .description('Manage LineageLens Plus and Max backends')
  .version(pkg.version);

program
  .command('start')
  .description('Start the LineageLens backend (Plus or Max mode)')
  .option('-m, --mode <mode>', 'Backend mode: plus | max', 'plus')
  .action(async (opts) => {
    assertMode(opts.mode);
    await start(opts.mode);
  });

program
  .command('stop')
  .description('Stop the LineageLens backend')
  .option('-m, --mode <mode>', 'Backend mode: plus | max', 'plus')
  .option('-v, --volumes', 'Also remove persistent volumes (wipes data)', false)
  .action((opts) => {
    assertMode(opts.mode);
    stop(opts.mode, opts);
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
  .option('-m, --mode <mode>', 'Backend mode: plus | max', 'plus')
  .option('-s, --service <service>', 'Filter to one service: backend | proxy | postgres | neo4j')
  .option('-n, --tail <lines>', 'Number of lines to show from the end', '100')
  .action((opts) => {
    assertMode(opts.mode);
    logs(opts);
  });

program.parse(process.argv);
