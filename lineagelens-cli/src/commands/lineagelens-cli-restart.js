'use strict';

const { stop } = require('./lineagelens-cli-stop');
const { start } = require('./lineagelens-cli-start');

async function restart(mode, opts) {
  console.log(`Restarting LineageLens ${mode.toUpperCase()}...\n`);
  const stopCode = stop(mode, { volumes: false }, { exitProcess: false });
  if (stopCode !== 0) {
    process.exit(stopCode);
  }
  await start(mode, opts);
}

module.exports = { restart };
