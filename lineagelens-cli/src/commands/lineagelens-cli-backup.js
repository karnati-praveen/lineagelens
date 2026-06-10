'use strict';

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const { lockFilePermissions } = require('../utils/lineagelens-cli-fsperm');

function backup(mode, opts) {
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const outFile = opts.output || `lineagelens-backup-${mode}-${timestamp}.dump`;
  const containerName = `lineagelens-${mode}-postgres`;

  console.log(`Creating backup of ${containerName} → ${outFile}`);

  const result = spawnSync(
    'docker',
    ['exec', containerName, 'pg_dump', '-U', 'postgres', '-d', 'provenance', '--format=custom'],
    { stdio: ['ignore', 'pipe', 'inherit'] }
  );

  if (result.status !== 0) {
    console.error('Backup failed. Is the backend running?');
    process.exit(1);
  }

  fs.writeFileSync(outFile, result.stdout);
  // The dump contains the entire database in cleartext — source code, prompts,
  // password hashes and encrypted fields. Restrict it to the current user.
  lockFilePermissions(outFile);
  console.log(`Backup saved: ${outFile}  (${(result.stdout.length / 1024).toFixed(1)} KB)`);
  console.log(
    'WARNING: this backup is NOT encrypted and contains all workspace data. ' +
    'Store it somewhere safe, and encrypt it before moving it off this machine — e.g.:\n' +
    `  gpg --symmetric --cipher-algo AES256 "${outFile}"   (then delete the plaintext .dump)`
  );
}

module.exports = { backup };
