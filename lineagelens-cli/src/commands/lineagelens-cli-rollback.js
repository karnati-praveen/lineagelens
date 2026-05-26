'use strict';

const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { spawnSync } = require('node:child_process');
const { checkDocker, runComposeSync } = require('../utils/lineagelens-cli-docker');
const { envFilePath } = require('../utils/lineagelens-cli-env');
const { out, err, isJsonMode } = require('../utils/lineagelens-cli-output');

const COMPOSE_DIR = path.join(__dirname, '..', '..', 'deploy');

function promptConfirm(question) {
  const readline = require('node:readline');
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => rl.question(question, ans => { rl.close(); resolve(ans.trim().toLowerCase()); }));
}

function dumpDatabase(containerName) {
  return spawnSync(
    'docker',
    ['exec', containerName, 'pg_dump', '-U', 'postgres', '-d', 'provenance', '--format=custom'],
    { stdio: ['ignore', 'pipe', 'inherit'] }
  );
}

function restoreDatabase(containerName, dumpData) {
  return spawnSync(
    'docker',
    ['exec', '-i', containerName, 'pg_restore', '-U', 'postgres', '-d', 'provenance', '--no-owner', '--exit-on-error'],
    { input: dumpData, stdio: ['pipe', 'pipe', 'pipe'] }
  );
}

function recreateDatabase(containerName) {
  return spawnSync(
    'docker',
    ['exec', '-i', containerName, 'psql', '-U', 'postgres', '-c', 'DROP DATABASE IF EXISTS provenance; CREATE DATABASE provenance;'],
    { stdio: ['ignore', 'pipe', 'pipe'] }
  );
}

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

  const composeFile = path.join(COMPOSE_DIR, `lineagelens-cli-docker-compose.${mode}.yml`);
  const envFile = envFilePath(mode);

  if (!fs.existsSync(envFile)) {
    if (isJsonMode()) {
      err({ error: `No config found for ${mode} mode. Run lineagelens start --mode ${mode} first.` });
    } else {
      console.error(`No config found for ${mode} mode. Run  lineagelens start --mode ${mode}  first.`);
    }
    process.exit(1);
  }

  if (!opts.nonInteractive && !isJsonMode()) {
    const ans = await promptConfirm('This will overwrite the current database. Continue? [y/N] ');
    if (ans !== 'y' && ans !== 'yes') {
      console.log('Rollback cancelled.');
      process.exit(0);
    }
  }

  const postgresContainer = `lineagelens-${mode}-postgres`;
  const backendService = 'backend';
  const safetyBackupDir = path.join(os.tmpdir(), 'lineagelens');
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const safetyBackupFile = path.join(safetyBackupDir, `lineagelens-rollback-safety-${mode}-${timestamp}.dump`);

  if (!fs.existsSync(safetyBackupDir)) {
    fs.mkdirSync(safetyBackupDir, { recursive: true });
  }

  if (!isJsonMode()) console.log(`Creating safety backup → ${safetyBackupFile}`);
  const safetyBackupResult = dumpDatabase(postgresContainer);
  if (safetyBackupResult.status !== 0) {
    if (isJsonMode()) {
      err({ error: 'Failed to create safety backup', mode });
    } else {
      console.error('Failed to create safety backup. Aborting rollback.');
    }
    process.exit(safetyBackupResult.status ?? 1);
  }

  fs.writeFileSync(safetyBackupFile, safetyBackupResult.stdout);

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
  const dropResult = recreateDatabase(postgresContainer);
  if (dropResult.status !== 0) {
    const errMsg = (dropResult.stderr || '').toString().slice(0, 300);
    if (isJsonMode()) {
      err({ error: 'Failed to recreate database', detail: errMsg });
    } else {
      console.error('Failed to recreate database:', errMsg);
    }
    process.exit(1);
  }

  const restoreResult = restoreDatabase(postgresContainer, dumpData);
  if (restoreResult.status !== 0) {
    const errMsg = (restoreResult.stderr || '').toString().slice(0, 300);
    if (isJsonMode()) {
      err({ error: 'pg_restore failed', detail: errMsg });
    } else {
      console.error('pg_restore failed:', errMsg);
    }

    const safetyDump = fs.readFileSync(safetyBackupFile);
    recreateDatabase(postgresContainer);
    const safetyRestoreResult = restoreDatabase(postgresContainer, safetyDump);
    if (safetyRestoreResult.status !== 0) {
      const safetyErr = (safetyRestoreResult.stderr || '').toString().slice(0, 300);
      if (isJsonMode()) {
        err({ error: 'Safety restore failed', detail: safetyErr });
      } else {
        console.error('Safety restore failed:', safetyErr);
      }
      process.exit(1);
    }

    if (!isJsonMode()) {
      console.warn('Original database restored from safety backup after rollback failure.');
    }
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
      safetyBackup: safetyBackupFile,
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
