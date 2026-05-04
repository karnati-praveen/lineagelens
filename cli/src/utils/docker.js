'use strict';

const { execFileSync, spawnSync, spawn } = require('node:child_process');

function checkDocker() {
  const result = spawnSync('docker', ['info'], { stdio: 'pipe' });
  if (result.status !== 0) {
    console.error('Docker is not running or not installed.');
    console.error('Install Docker Desktop from https://www.docker.com/products/docker-desktop');
    process.exit(1);
  }
}

function composeCmd() {
  // Prefer `docker compose` (v2); fall back to `docker-compose` (v1)
  const v2 = spawnSync('docker', ['compose', 'version'], { stdio: 'pipe' });
  if (v2.status === 0) return ['docker', ['compose']];
  const v1 = spawnSync('docker-compose', ['version'], { stdio: 'pipe' });
  if (v1.status === 0) return ['docker-compose', []];
  console.error('Docker Compose not found. Install Docker Desktop >= 3.0.');
  process.exit(1);
}

function runCompose(composeFile, envFile, args, opts = {}) {
  const [bin, baseArgs] = composeCmd();
  const fullArgs = [
    ...baseArgs,
    '--file', composeFile,
    '--env-file', envFile,
    '--project-name', `lineagelens-${opts.mode || 'plus'}`,
    ...args,
  ];
  const result = spawn(bin, fullArgs, {
    stdio: opts.detach ? 'pipe' : 'inherit',
    ...opts.spawnOpts,
  });
  return result;
}

function runComposeSync(composeFile, envFile, args, opts = {}) {
  const [bin, baseArgs] = composeCmd();
  const fullArgs = [
    ...baseArgs,
    '--file', composeFile,
    '--env-file', envFile,
    '--project-name', `lineagelens-${opts.mode || 'plus'}`,
    ...args,
  ];
  return spawnSync(bin, fullArgs, { stdio: 'inherit' });
}

module.exports = { checkDocker, composeCmd, runCompose, runComposeSync };
