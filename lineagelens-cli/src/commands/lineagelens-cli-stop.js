'use strict';

const path = require('node:path');
const fs = require('node:fs');
const { checkDocker, runComposeSync } = require('../utils/lineagelens-cli-docker');
const { envFilePath } = require('../utils/lineagelens-cli-env');
const { out, err, isJsonMode } = require('../utils/lineagelens-cli-output');
const { setActiveMode } = require('../utils/lineagelens-cli-config');

const COMPOSE_DIR = path.join(__dirname, '..', '..', 'deploy');

function stop(mode, opts, options = {}) {
  const exitProcess = options.exitProcess !== false;
  checkDocker();

  const composeFile = path.join(COMPOSE_DIR, `lineagelens-cli-docker-compose.${mode}.yml`);
  const envFile = envFilePath(mode);

  const finish = (code) => {
    if (exitProcess) {
      process.exit(code);
    }
    return code;
  };

  if (!fs.existsSync(envFile)) {
    if (isJsonMode()) {
      err({ error: `No config found for ${mode} mode. Nothing to stop.` });
    } else {
      console.error(`No config found for ${mode} mode. Nothing to stop.`);
    }
    return finish(1);
  }

  if (opts.volumes && !opts.confirmWipe) {
    if (isJsonMode()) {
      err({ error: '--volumes will permanently delete all data (database, Neo4j, Redis). Re-run with --confirm-wipe to proceed.' });
    } else {
      console.error('Error: --volumes will permanently delete all data (database, Neo4j, Redis).');
      console.error('Re-run with --confirm-wipe to proceed.');
    }
    return finish(1);
  }

  const args = opts.volumes ? ['down', '--volumes'] : ['down'];

  if (!isJsonMode()) {
    console.log(`Stopping LineageLens ${mode.toUpperCase()}${opts.volumes ? ' (and removing volumes)' : ''}...\n`);
  }

  const result = runComposeSync(composeFile, envFile, args, { mode });

  if (result.status === 0) {
    // Clear active mode after successful stop
    setActiveMode(null);

    if (isJsonMode()) {
      out({ status: 'stopped', mode });
    }
  } else {
    if (isJsonMode()) {
      err({ error: `Stop command failed with exit code ${result.status}`, mode });
    }
  }

  return finish(result.status ?? 0);
}

module.exports = { stop };
