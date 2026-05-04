'use strict';

const path = require('node:path');
const fs = require('node:fs');
const { checkDocker, runComposeSync } = require('../utils/docker');
const { envFilePath } = require('../utils/env');

const COMPOSE_DIR = path.join(__dirname, '..', '..', 'deploy');
const MODES = ['plus', 'max'];

function status(opts) {
  checkDocker();

  const modes = opts.mode ? [opts.mode] : MODES;
  let any = false;

  for (const mode of modes) {
    const composeFile = path.join(COMPOSE_DIR, `docker-compose.${mode}.yml`);
    const envFile = envFilePath(mode);

    if (!fs.existsSync(envFile)) continue;

    any = true;
    console.log(`\n--- LineageLens ${mode.toUpperCase()} ---`);
    runComposeSync(composeFile, envFile, ['ps'], { mode });
  }

  if (!any) {
    console.log('No LineageLens backends are configured yet.');
    console.log('Run  lineagelens start --mode plus  to get started.');
  }
}

module.exports = { status };
