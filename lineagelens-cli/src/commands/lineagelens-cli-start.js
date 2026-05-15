'use strict';

const path = require('node:path');
const { checkDocker, runComposeSync } = require('../utils/lineagelens-cli-docker');
const { ensureEnv } = require('../utils/lineagelens-cli-env');
const { out, err, isJsonMode } = require('../utils/lineagelens-cli-output');
const { setActiveMode } = require('../utils/lineagelens-cli-config');

const COMPOSE_DIR = path.join(__dirname, '..', '..', '..', 'lineagelens-deploy');

async function waitForBackend(timeoutMs = 30000) {
  const start = Date.now();
  const healthUrl = 'http://localhost:8787/health';
  while (Date.now() - start < timeoutMs) {
    try {
      const resp = await fetch(healthUrl, { signal: AbortSignal.timeout(2000) });
      if (resp.ok) return true;
    } catch {
      // not ready yet
    }
    await new Promise(r => setTimeout(r, 2000));
  }
  return false;
}

async function start(mode, opts = {}) {
  checkDocker();

  const composeFile = path.join(COMPOSE_DIR, `docker-compose.${mode}.yml`);
  const envFile = await ensureEnv(mode, { nonInteractive: opts.nonInteractive });

  if (!isJsonMode()) {
    console.log(`Starting LineageLens ${mode.toUpperCase()} backend...`);
    console.log('Pulling latest images (this may take a minute on first run).\n');
  }

  const result = runComposeSync(composeFile, envFile, ['up', '--detach', '--pull', 'always'], { mode });

  if (result.status !== 0) {
    if (isJsonMode()) {
      err({ error: 'Failed to start containers', mode, hint: `Run: docker compose --file "${composeFile}" --env-file "${envFile}" up` });
    } else {
      console.error('\nFailed to start. Run with COMPOSE_FILE for manual debugging:');
      console.error(`  docker compose --file "${composeFile}" --env-file "${envFile}" up`);
    }
    process.exit(result.status ?? 1);
  }

  if (!isJsonMode()) {
    console.log('\nLineageLens is running.\n');
    console.log('  Dashboard    →  http://localhost:8787/dashboard');
    console.log('  Backend API  →  http://localhost:8787');
    console.log('  Proxy        →  http://localhost:8788');
    if (mode === 'max') {
      console.log('  Neo4j UI     →  http://localhost:7474');
    }
    console.log('\nOpen http://localhost:8787/dashboard in your browser to get started.');
    console.log('Run  lineagelens status  to see container health.\n');
    console.log('Waiting for backend to be ready...');
  }

  const ready = await waitForBackend();

  // Persist active mode on successful start
  setActiveMode(mode);

  if (isJsonMode()) {
    out({
      status: 'started',
      mode,
      dashboard: 'http://localhost:8787/dashboard',
      healthy: ready,
    });
  } else {
    if (ready) {
      console.log('Backend is healthy.\n');
    } else {
      console.warn(`Warning: backend did not respond within 30s. Check logs: lineagelens logs --mode ${mode}`);
    }
  }
}

module.exports = { start };
