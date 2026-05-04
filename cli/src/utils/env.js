'use strict';

const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const readline = require('node:readline');

function dataDir(mode) {
  const dir = path.join(os.homedir(), '.lineagelens', mode);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function envFilePath(mode) {
  return path.join(dataDir(mode), '.env');
}

function randomSecret(bytes = 32) {
  return crypto.randomBytes(bytes).toString('hex');
}

function parseEnv(filePath) {
  if (!fs.existsSync(filePath)) return {};
  const lines = fs.readFileSync(filePath, 'utf8').split('\n');
  const result = {};
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    result[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim();
  }
  return result;
}

function writeEnv(filePath, vars) {
  const lines = Object.entries(vars).map(([k, v]) => `${k}=${v}`);
  fs.writeFileSync(filePath, lines.join('\n') + '\n', 'utf8');
}

function prompt(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => rl.question(question, ans => { rl.close(); resolve(ans.trim()); }));
}

async function ensureEnv(mode) {
  const file = envFilePath(mode);
  if (fs.existsSync(file)) return file;

  console.log(`\nNo config found for ${mode} mode. Let's set it up (secrets are auto-generated).\n`);

  const vars = {
    POSTGRES_PASSWORD: randomSecret(16),
    JWT_SECRET_KEY: randomSecret(32),
    JWT_REFRESH_SECRET_KEY: randomSecret(32),
    EXPLAIN_LLM_API_KEY: '',
    PROXY_INGEST_TOKEN: '',
    PROXY_UPSTREAM_URL: 'https://api.anthropic.com',
    PROXY_WORKSPACE_ID: 'proxy-capture',
  };

  if (mode === 'max') {
    vars.NEO4J_USERNAME = 'neo4j';
    vars.NEO4J_PASSWORD = randomSecret(16);
  }

  const llmKey = await prompt('OpenAI API key for AI explanations (optional, press Enter to skip): ');
  if (llmKey) vars.EXPLAIN_LLM_API_KEY = llmKey;

  writeEnv(file, vars);
  console.log(`\nConfig saved to ${file}`);
  console.log('Secrets were auto-generated. Edit that file any time to change them.\n');
  return file;
}

module.exports = { dataDir, envFilePath, ensureEnv, parseEnv };
