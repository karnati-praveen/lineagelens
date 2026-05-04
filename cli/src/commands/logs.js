'use strict';

const path = require('node:path');
const fs = require('node:fs');
const { checkDocker, runCompose } = require('../utils/docker');
const { envFilePath } = require('../utils/env');

const COMPOSE_DIR = path.join(__dirname, '..', '..', 'deploy');

function logs(opts) {
  checkDocker();

  const mode = opts.mode || 'plus';
  const composeFile = path.join(COMPOSE_DIR, `docker-compose.${mode}.yml`);
  const envFile = envFilePath(mode);

  if (!fs.existsSync(envFile)) {
    console.error(`No config found for ${mode} mode. Run  lineagelens start --mode ${mode}  first.`);
    process.exit(1);
  }

  const args = ['logs', '--follow', '--tail', String(opts.tail || 100)];
  if (opts.service) args.push(opts.service);

  const serviceSuffix = opts.service ? ' / ' + opts.service : '';
  console.log(`Tailing logs for LineageLens ${mode.toUpperCase()}${serviceSuffix}  (Ctrl+C to stop)\n`);

  const proc = runCompose(composeFile, envFile, args, { mode });
  proc.stdout?.pipe(process.stdout);
  proc.stderr?.pipe(process.stderr);

  proc.on('close', code => process.exit(code ?? 0));
}

module.exports = { logs };
