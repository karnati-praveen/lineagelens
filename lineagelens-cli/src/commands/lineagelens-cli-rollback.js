'use strict';

const path = require('node:path');
const fs = require('node:fs');
const { spawnSync } = require('node:child_process');
const { checkDocker, runComposeSync } = require('../utils/lineagelens-cli-docker');
const { envFilePath } = require('../utils/lineagelens-cli-env');
const { out, err, isJsonMode } = require('../utils/lineagelens-cli-output');

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

async function rollback(mode, opts = {}) {
  checkDocker();

  const dumpFile = opts.file;
  if (!dumpFile) {
    if (isJsonMode()) {
      err({ error: 'A dump file is required. Use --file <path>' });
    } else {
      console.error('Error: A dump file is required. Use  --file <path>');
    }
    process.exit(1);
  }

  if (!fs.existsSync(dumpFile)) {
    if (isJsonMode()) {
      err({ error: `Dump file not found: ${dumpFile}` });
    } else {
      console.error(`Error: Dump file not found: ${dumpFile}`);
    }
    process.exit(1);
  }

  const composeFile = path.join(COMPOSE_DIR, `docker-compose.${mode}.yml`);
  const envFile = envFilePath(mode);

  if (!fs.existsSync(envFile)) {
    if (isJsonMode()) {
      err({ error: `No config found for ${mode} mode. Run lineagelens start --mode ${mode} first.` });
    } else {
      console.error(`No config found for ${mode} mode. Run  lineagelens start --mode ${mode}  first.`);
    }
    process.exit(1);
  }

  const postgresContainer = `lineagelens-${mode}-postgres`;
  const backendService = 'backend';

  // Step 1: Stop backend service (not postgres)
  if (!isJsonMode()) console.log(`Stopping backend service for ${mode.toUpperCase()}...`);
  const stopResult = runComposeSync(composeFile, envFile, ['stop', backendService], { mode });
  if (stopResult.status !== 0) {
    if (isJsonMode()) {
      err({ error: 'Failed to stop backend service', mode });
    } else {
      console.error('Failed to stop backend service.');
    }
    process.exit(stopResult.status ?? 1);
  }

  // Step 2: Run pg_restore inside the postgres container
  if (!isJsonMode()) console.log(`\nRestoring database from ${dumpFile}...`);

  // Read dump file and pipe to docker exec
  const dumpData = fs.readFileSync(dumpFile);

  // Drop and recreate the database, then restore
  const dropResult = spawnSync(
    'docker',
    ['exec', '-i', postgresContainer, 'psql', '-U', 'postgres', '-c', 'DROP DATABASE IF EXISTS provenance; CREATE DATABASE provenance;'],
    { stdio: ['ignore', 'pipe', 'pipe'] }
  );
  if (dropResult.status !== 0) {
    const errMsg = (dropResult.stderr || '').toString().slice(0, 300);
    if (isJsonMode()) {
      err({ error: 'Failed to recreate database', detail: errMsg });
    } else {
      console.error('Failed to recreate database:', errMsg);
    }
    process.exit(1);
  }

  const restoreResult = spawnSync(
    'docker',
    ['exec', '-i', postgresContainer, 'pg_restore', '-U', 'postgres', '-d', 'provenance', '--no-owner', '--exit-on-error'],
    { input: dumpData, stdio: ['pipe', 'pipe', 'pipe'] }
  );
  if (restoreResult.status !== 0) {
    const errMsg = (restoreResult.stderr || '').toString().slice(0, 300);
    if (isJsonMode()) {
      err({ error: 'pg_restore failed', detail: errMsg });
    } else {
      console.error('pg_restore failed:', errMsg);
    }
    process.exit(1);
  }

  if (!isJsonMode()) console.log('Database restored successfully.');

  // Step 3: Restart backend service
  if (!isJsonMode()) console.log('\nRestarting backend service...');
  const startResult = runComposeSync(composeFile, envFile, ['start', backendService], { mode });
  if (startResult.status !== 0) {
    if (isJsonMode()) {
      err({ error: 'Failed to restart backend service', mode });
    } else {
      console.error('Failed to restart backend service.');
    }
    process.exit(startResult.status ?? 1);
  }

  // Step 4: Health check
  if (!isJsonMode()) console.log('\nWaiting for backend to be ready...');
  const ready = await waitForBackend();

  if (isJsonMode()) {
    out({
      status: 'rolled_back',
      mode,
      file: dumpFile,
      healthy: ready,
    });
  } else {
    if (ready) {
      console.log('Backend is healthy.\n');
    } else {
      console.warn(`Warning: backend did not respond within 30s. Check logs: lineagelens logs --mode ${mode}`);
    }
    console.log(`\nRollback complete from: ${dumpFile}`);
  }
}

module.exports = { rollback };
