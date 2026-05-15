'use strict';

const { spawnSync, spawn } = require('node:child_process');
const { isJsonMode, err } = require('./lineagelens-cli-output');

const KNOWN_ERRORS = [
  {
    pattern: /port is already allocated|address already in use/i,
    hint: 'Port conflict. Find what is using the port: lsof -i :8787 (macOS/Linux) or netstat -ano | findstr :8787 (Windows)',
  },
  {
    pattern: /pull access denied|not found/i,
    hint: 'Image not found. Check your internet connection and try: docker pull ghcr.io/lineagelens/backend:latest',
  },
  {
    pattern: /no space left on device/i,
    hint: 'Disk full. Free up space: docker system prune -f',
  },
  {
    pattern: /cannot connect to the docker daemon/i,
    hint: 'Docker is not running. Start Docker Desktop and try again.',
  },
];

function _printComposeError(stderr) {
  if (!stderr) return;
  const text = typeof stderr === 'string' ? stderr : stderr.toString('utf8');
  for (const { pattern, hint } of KNOWN_ERRORS) {
    if (pattern.test(text)) {
      if (isJsonMode()) {
        err({ error: 'Docker error', hint });
      } else {
        console.error(`\nHint: ${hint}`);
      }
      return;
    }
  }
}

function checkDocker() {
  const result = spawnSync('docker', ['info'], { stdio: 'pipe' });
  if (result.status !== 0) {
    if (isJsonMode()) {
      err({ error: 'Docker is not running or not installed.', hint: 'Install Docker Desktop from https://www.docker.com/products/docker-desktop' });
    } else {
      console.error('Docker is not running or not installed.');
      console.error('Install Docker Desktop from https://www.docker.com/products/docker-desktop');
    }
    process.exit(1);
  }
}

function composeCmd() {
  // Prefer `docker compose` (v2); fall back to `docker-compose` (v1)
  const v2 = spawnSync('docker', ['compose', 'version'], { stdio: 'pipe' });
  if (v2.status === 0) return ['docker', ['compose']];
  const v1 = spawnSync('docker-compose', ['version'], { stdio: 'pipe' });
  if (v1.status === 0) return ['docker-compose', []];
  if (isJsonMode()) {
    err({ error: 'Docker Compose not found. Install Docker Desktop >= 3.0.' });
  } else {
    console.error('Docker Compose not found. Install Docker Desktop >= 3.0.');
  }
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
  const result = spawnSync(bin, fullArgs, { stdio: isJsonMode() ? 'pipe' : 'inherit' });

  if (result.status !== 0) {
    const stderr = result.stderr || result.output?.[2] || '';
    _printComposeError(stderr);
  }

  return result;
}

module.exports = { checkDocker, composeCmd, runCompose, runComposeSync };
