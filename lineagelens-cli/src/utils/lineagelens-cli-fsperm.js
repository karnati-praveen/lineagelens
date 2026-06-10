'use strict';

const fs = require('node:fs');
const os = require('node:os');
const { execFileSync } = require('node:child_process');

/**
 * Restrict a secret-bearing file to the current user only.
 *
 * POSIX: chmod 0600.
 * Windows: chmod is a no-op, so we use icacls to strip inherited ACEs and
 * grant Full control to the current user only. Without this, ~/.lineagelens
 * secret files (JWT keys, DB passwords, ingest tokens) inherit broad profile
 * ACLs and are readable by any process — the local info-stealer threat.
 *
 * Best-effort: never throws, so config writes never fail on a locked-down host.
 */
function lockFilePermissions(filePath) {
  if (process.platform === 'win32') {
    try {
      const user = process.env.USERNAME
        ? `${process.env.USERDOMAIN || os.hostname()}\\${process.env.USERNAME}`
        : os.userInfo().username;
      // /inheritance:r removes inherited permissions; /grant:r replaces with an
      // explicit grant of Full control to just this user.
      execFileSync('icacls', [filePath, '/inheritance:r', '/grant:r', `${user}:F`], {
        stdio: 'ignore',
      });
    } catch {
      // icacls unavailable or failed — fall through to chmod best-effort below.
    }
  }
  try {
    fs.chmodSync(filePath, 0o600);
  } catch {
    // No-op on platforms/filesystems that don't support it.
  }
}

module.exports = { lockFilePermissions };
