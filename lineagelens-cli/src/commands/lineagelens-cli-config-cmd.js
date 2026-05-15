'use strict';

const { readConfig } = require('../utils/lineagelens-cli-config');
const { out } = require('../utils/lineagelens-cli-output');

function configCmd() {
  const cfg = readConfig();
  out(cfg);
}

module.exports = { configCmd };
