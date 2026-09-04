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

/** Report an error (JSON or text, matching the active output mode) and exit. */
function failAndExit(jsonPayload, textMessage, exitCode = 1) {
  if (isJsonMode()) {
    err(jsonPayload);
  } else {
    console.error(textMessage);
  }
  process.exit(exitCode);
}

function validateRollbackInputs(mode, dumpFile) {
  if (!dumpFile) {
    failAndExit({ error: 'A dump file is required. Use --file <path>' }, 'Error: A dump file is required. Use  --file <path>');
  }
  if (!fs.existsSync(dumpFile)) {
    failAndExit({ error: `Dump file not found: ${dumpFile}` }, `Error: Dump file not found: ${dumpFile}`);
  }
  const composeFile = path.join(COMPOSE_DIR, `lineagelens-cli-docker-compose.${mode}.yml`);
  const envFile = envFilePath(mode);
  if (!fs.existsSync(envFile)) {
    failAndExit(
      { error: `No config found for ${mode} mode. Run lineagelens start --mode ${mode} first.` },
      `No config found for ${mode} mode. Run  lineagelens start --mode ${mode}  first.`
    );
  }
  return { composeFile, envFile };
}

async function confirmRollback(opts) {
  if (opts.nonInteractive || isJsonMode()) return;
  const ans = await promptConfirm('This will overwrite the current database. Continue? [y/N] ');
  if (ans !== 'y' && ans !== 'yes') {
    console.log('Rollback cancelled.');
    process.exit(0);
  }
}

function createSafetyBackup(mode, postgresContainer) {
  const safetyBackupDir = path.join(os.tmpdir(), 'lineagelens');
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const safetyBackupFile = path.join(safetyBackupDir, `lineagelens-rollback-safety-${mode}-${timestamp}.dump`);

  if (!fs.existsSync(safetyBackupDir)) {
    fs.mkdirSync(safetyBackupDir, { recursive: true });
  }

  if (!isJsonMode()) console.log(`Creating safety backup → ${safetyBackupFile}`);
  const safetyBackupResult = dumpDatabase(postgresContainer);
  if (safetyBackupResult.status !== 0) {
    failAndExit(
      { error: 'Failed to create safety backup', mode },
      'Failed to create safety backup. Aborting rollback.',
      safetyBackupResult.status ?? 1
    );
  }

  fs.writeFileSync(safetyBackupFile, safetyBackupResult.stdout);
  return safetyBackupFile;
}

/** Restore `dumpData`; on failure, roll back to the pre-restore safety backup. */
function restoreWithSafetyFallback(postgresContainer, dumpFile, dumpData, safetyBackupFile) {
  const dropResult = recreateDatabase(postgresContainer);
  if (dropResult.status !== 0) {
    const errMsg = (dropResult.stderr || '').toString().slice(0, 300);
    failAndExit({ error: 'Failed to recreate database', detail: errMsg }, `Failed to recreate database: ${errMsg}`);
  }

  const restoreResult = restoreDatabase(postgresContainer, dumpData);
  if (restoreResult.status === 0) {
    return;
  }

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
    failAndExit({ error: 'Safety restore failed', detail: safetyErr }, `Safety restore failed: ${safetyErr}`);
  }

  if (!isJsonMode()) {
    console.warn('Original database restored from safety backup after rollback failure.');
  }
}

async function rollback(mode, opts = {}) {
  checkDocker();

  const dumpFile = opts.file;
  const { composeFile, envFile } = validateRollbackInputs(mode, dumpFile);
  await confirmRollback(opts);

  const postgresContainer = `lineagelens-${mode}-postgres`;
  const backendService = 'backend';

  const safetyBackupFile = createSafetyBackup(mode, postgresContainer);

  // Step 1: Stop backend service (not postgres)
  if (!isJsonMode()) console.log(`Stopping backend service for ${mode.toUpperCase()}...`);
  const stopResult = runComposeSync(composeFile, envFile, ['stop', backendService], { mode });
  if (stopResult.status !== 0) {
    failAndExit({ error: 'Failed to stop backend service', mode }, 'Failed to stop backend service.', stopResult.status ?? 1);
  }

  // Step 2: Run pg_restore inside the postgres container
  if (!isJsonMode()) console.log(`\nRestoring database from ${dumpFile}...`);
  const dumpData = fs.readFileSync(dumpFile);
  restoreWithSafetyFallback(postgresContainer, dumpFile, dumpData, safetyBackupFile);

  if (!isJsonMode()) console.log('Database restored successfully.');

  // Step 3: Restart backend service
  if (!isJsonMode()) console.log('\nRestarting backend service...');
  const startResult = runComposeSync(composeFile, envFile, ['start', backendService], { mode });
  if (startResult.status !== 0) {
    failAndExit({ error: 'Failed to restart backend service', mode }, 'Failed to restart backend service.', startResult.status ?? 1);
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
