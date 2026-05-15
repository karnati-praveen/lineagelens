'use strict';

const path = require('node:path');
const { checkDocker, runComposeSync } = require('../utils/lineagelens-cli-docker');
const { envFilePath } = require('../utils/lineagelens-cli-env');

const COMPOSE_DIR = path.join(__dirname, '..', '..', '..', 'lineagelens-deploy');

async function update(mode) {
  checkDocker();

  const composeFile = path.join(COMPOSE_DIR, `docker-compose.${mode}.yml`);
  const envFile = envFilePath(mode);

  console.log(`Pulling latest images for LineageLens ${mode.toUpperCase()}...`);
  runComposeSync(composeFile, envFile, ['pull'], { mode });

  console.log('Recreating containers with new images (data preserved)...');
  runComposeSync(composeFile, envFile, ['up', '--detach', '--force-recreate'], { mode });

  console.log('\nUpdate complete. Run  lineagelens status  to verify.');
}

module.exports = { update };
