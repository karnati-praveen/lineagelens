'use strict';

const path = require('node:path');
const fs = require('node:fs');
const { spawnSync } = require('node:child_process');
const { checkDocker, runComposeSync } = require('../utils/lineagelens-cli-docker');
const { envFilePath } = require('../utils/lineagelens-cli-env');
const { out, err, isJsonMode } = require('../utils/lineagelens-cli-output');

const COMPOSE_DIR = path.join(__dirname, '..', '..', '..', 'lineagelens-deploy');
const MODES = ['base', 'plus', 'max'];

/**
 * Parse docker ps tab-delimited output: Names\tStatus\tPorts
 * Returns array of { name, status, ports, healthy }
 */
function parseDockerPs(output) {
  if (!output) return [];
  const containers = [];
  for (const line of output.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const parts = trimmed.split('\t');
    const name = (parts[0] || '').trim();
    const rawStatus = (parts[1] || '').trim();
    const ports = (parts[2] || '').trim();
    if (!name) continue;

    let healthy = 'UNKNOWN';
    if (/healthy/i.test(rawStatus) && !/unhealthy/i.test(rawStatus)) {
      healthy = 'HEALTHY';
    } else if (/unhealthy/i.test(rawStatus)) {
      healthy = 'UNHEALTHY';
    } else if (/starting/i.test(rawStatus)) {
      healthy = 'STARTING';
    } else if (/up/i.test(rawStatus)) {
      healthy = 'UP';
    } else if (/exit/i.test(rawStatus)) {
      healthy = 'EXITED';
    }

    containers.push({ name, status: rawStatus, ports, healthy });
  }
  return containers;
}

function getContainersForMode(mode) {
  const result = spawnSync(
    'docker',
    ['ps', '--filter', `name=lineagelens-${mode}`, '--format', '{{.Names}}\t{{.Status}}\t{{.Ports}}'],
    { stdio: 'pipe', encoding: 'utf8' }
  );
  if (result.status !== 0) return [];
  return parseDockerPs(result.stdout || '');
}

function formatTable(containers) {
  if (!containers.length) return '  (no containers found)';

  const nameW = Math.max(4, ...containers.map(c => c.name.length));
  const statusW = Math.max(6, ...containers.map(c => c.healthy.length));
  const portsW = Math.max(5, ...containers.map(c => c.ports.length));

  const header = `  ${'NAME'.padEnd(nameW)}  ${'HEALTH'.padEnd(statusW)}  ${'PORTS'.padEnd(portsW)}`;
  const sep = `  ${'-'.repeat(nameW)}  ${'-'.repeat(statusW)}  ${'-'.repeat(portsW)}`;
  const rows = containers.map(c =>
    `  ${c.name.padEnd(nameW)}  ${c.healthy.padEnd(statusW)}  ${c.ports.padEnd(portsW)}`
  );
  return [header, sep, ...rows].join('\n');
}

function status(opts) {
  checkDocker();

  const modes = opts.mode ? [opts.mode] : MODES;
  let any = false;
  const allContainers = [];

  for (const mode of modes) {
    const envFile = envFilePath(mode);
    if (!fs.existsSync(envFile)) continue;

    any = true;
    const containers = getContainersForMode(mode);
    allContainers.push(...containers);

    if (!isJsonMode()) {
      console.log(`\n--- LineageLens ${mode.toUpperCase()} ---`);
      if (containers.length) {
        console.log(formatTable(containers));
      } else {
        // Fallback: run compose ps for raw output
        const composeFile = path.join(COMPOSE_DIR, `docker-compose.${mode}.yml`);
        runComposeSync(composeFile, envFile, ['ps'], { mode });
      }
    }
  }

  if (!any) {
    if (isJsonMode()) {
      out({ containers: [] });
    } else {
      console.log('No LineageLens backends are configured yet.');
      console.log('Run  lineagelens start --mode plus  to get started.');
    }
    return;
  }

  if (isJsonMode()) {
    out({ containers: allContainers });
  }
}

module.exports = { status };
