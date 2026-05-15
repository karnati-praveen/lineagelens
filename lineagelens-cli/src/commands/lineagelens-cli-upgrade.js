'use strict';

const path = require('node:path');
const fs = require('node:fs');
const readline = require('node:readline');
const { spawnSync } = require('node:child_process');
const { checkDocker, runComposeSync } = require('../utils/lineagelens-cli-docker');
const { envFilePath } = require('../utils/lineagelens-cli-env');
const { out, err, isJsonMode } = require('../utils/lineagelens-cli-output');

const COMPOSE_DIR = path.join(__dirname, '..', '..', '..', 'lineagelens-deploy');

function promptConfirm(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => rl.question(question, ans => { rl.close(); resolve(ans.trim().toLowerCase()); }));
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

async function upgrade(mode, opts = {}) {
  checkDocker();

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

  // Step 1: Confirm unless --yes
  if (!opts.nonInteractive && !isJsonMode()) {
    const ans = await promptConfirm('This will restart containers. Data is preserved. Continue? [y/N] ');
    if (ans !== 'y' && ans !== 'yes') {
      console.log('Upgrade cancelled.');
      process.exit(0);
    }
  }

  // Step 2: Backup database
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const backupFile = `lineagelens-backup-${mode}-${timestamp}.dump`;
  const containerName = `lineagelens-${mode}-postgres`;

  if (!isJsonMode()) console.log(`\nBacking up database to ${backupFile}...`);

  const pgResult = spawnSync(
    'docker',
    ['exec', containerName, 'pg_dump', '-U', 'postgres', '-d', 'provenance', '--format=custom'],
    { stdio: ['ignore', 'pipe', 'inherit'] }
  );

  if (pgResult.status !== 0) {
    if (!isJsonMode()) {
      console.warn('Warning: backup failed (container may not be running). Continuing with upgrade anyway.');
    }
  } else {
    fs.writeFileSync(backupFile, pgResult.stdout);
    if (!isJsonMode()) {
      console.log(`Backup saved: ${backupFile}  (${(pgResult.stdout.length / 1024).toFixed(1)} KB)`);
    }
  }

  // Step 3: Pull latest images
  if (!isJsonMode()) console.log('\nPulling latest images...');
  const pullResult = runComposeSync(composeFile, envFile, ['pull'], { mode });
  if (pullResult.status !== 0) {
    if (isJsonMode()) {
      err({ error: 'Failed to pull images', mode });
    } else {
      console.error('Failed to pull latest images.');
    }
    process.exit(pullResult.status ?? 1);
  }

  // Step 4: Force-recreate containers
  if (!isJsonMode()) console.log('\nRecreating containers with new images (data preserved)...');
  const upResult = runComposeSync(composeFile, envFile, ['up', '--detach', '--force-recreate'], { mode });
  if (upResult.status !== 0) {
    if (isJsonMode()) {
      err({ error: 'Failed to recreate containers', mode });
    } else {
      console.error('Failed to recreate containers.');
    }
    process.exit(upResult.status ?? 1);
  }

  // Step 5: Health check
  if (!isJsonMode()) console.log('\nWaiting for backend to be ready...');
  const ready = await waitForBackend();

  // Step 6: Summary
  if (isJsonMode()) {
    out({
      status: 'upgraded',
      mode,
      backup: pgResult.status === 0 ? backupFile : null,
      healthy: ready,
      dashboard: 'http://localhost:8787/dashboard',
    });
  } else {
    if (ready) {
      console.log('Backend is healthy.\n');
    } else {
      console.warn(`Warning: backend did not respond within 30s. Check logs: lineagelens logs --mode ${mode}`);
    }
    console.log('\nUpgrade complete.');
    if (pgResult.status === 0) console.log(`Backup file: ${backupFile}`);
    console.log(`Dashboard: http://localhost:8787/dashboard`);
  }
}

module.exports = { upgrade };
