'use strict';

const path = require('path');
const fs = require('fs');
const { checkDocker, runComposeSync } = require('../utils/docker');
const { envFilePath } = require('../utils/env');

const COMPOSE_DIR = path.join(__dirname, '..', '..', 'deploy');

function stop(mode, opts) {
  checkDocker();

  const composeFile = path.join(COMPOSE_DIR, `docker-compose.${mode}.yml`);
  const envFile = envFilePath(mode);

  if (!fs.existsSync(envFile)) {
    console.error(`No config found for ${mode} mode. Nothing to stop.`);
    process.exit(1);
  }

  const args = opts.volumes ? ['down', '--volumes'] : ['down'];
  console.log(`Stopping LineageLens ${mode.toUpperCase()}${opts.volumes ? ' (and removing volumes)' : ''}...\n`);

  const result = runComposeSync(composeFile, envFile, args, { mode });
  process.exit(result.status ?? 0);
}

module.exports = { stop };
