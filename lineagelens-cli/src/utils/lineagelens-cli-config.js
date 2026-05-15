'use strict';

const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const CONFIG_DIR = path.join(os.homedir(), '.lineagelens');
const CONFIG_FILE = path.join(CONFIG_DIR, 'config.json');

function readConfig() {
  try {
    if (!fs.existsSync(CONFIG_FILE)) return {};
    return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
  } catch {
    return {};
  }
}

function writeConfig(data) {
  fs.mkdirSync(CONFIG_DIR, { recursive: true });
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(data, null, 2));
  try { fs.chmodSync(CONFIG_FILE, 0o600); } catch {}
}

function getActiveMode() { return readConfig().activeMode ?? null; }
function setActiveMode(mode) { writeConfig({ ...readConfig(), activeMode: mode }); }
function getConfig(key) { return readConfig()[key]; }
function setConfig(key, value) { writeConfig({ ...readConfig(), [key]: value }); }

module.exports = { readConfig, writeConfig, getActiveMode, setActiveMode, getConfig, setConfig };
