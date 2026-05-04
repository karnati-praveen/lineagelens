'use strict';

const path = require('node:path');
const { checkDocker, runComposeSync } = require('../utils/docker');
const { ensureEnv } = require('../utils/env');

const COMPOSE_DIR = path.join(__dirname, '..', '..', 'deploy');

async function start(mode) {
  checkDocker();

  const composeFile = path.join(COMPOSE_DIR, `docker-compose.${mode}.yml`);
  const envFile = await ensureEnv(mode);

  console.log(`Starting LineageLens ${mode.toUpperCase()} backend...`);
  console.log('Pulling latest images (this may take a minute on first run).\n');

  const result = runComposeSync(composeFile, envFile, ['up', '--detach', '--pull', 'always'], { mode });

  if (result.status !== 0) {
    console.error('\nFailed to start. Run with COMPOSE_FILE for manual debugging:');
    console.error(`  docker compose --file "${composeFile}" --env-file "${envFile}" up`);
    process.exit(result.status ?? 1);
  }

  console.log('\nLineageLens is running.\n');
  console.log('  Dashboard    →  http://localhost:8787/dashboard');
  console.log('  Backend API  →  http://localhost:8787');
  console.log('  Proxy        →  http://localhost:8788');
  if (mode === 'max') {
    console.log('  Neo4j UI     →  http://localhost:7474');
  }
  console.log('\nOpen http://localhost:8787/dashboard in your browser to get started.');
  console.log('Run  lineagelens status  to see container health.\n');
}

module.exports = { start };
