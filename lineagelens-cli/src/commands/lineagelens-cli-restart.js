'use strict';

const { stop } = require('./lineagelens-cli-stop');
const { start } = require('./lineagelens-cli-start');

async function restart(mode, opts) {
  console.log(`Restarting LineageLens ${mode.toUpperCase()}...\n`);
  stop(mode, { volumes: false });  // stop without removing volumes
  await start(mode);
}

module.exports = { restart };
