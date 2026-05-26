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
  try {
    fs.chmodSync(filePath, 0o600);
  } catch {
    // On Windows, chmod is a no-op but shouldn't throw
  }
}

function prompt(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => rl.question(question, ans => { rl.close(); resolve(ans.trim()); }));
}

/**
 * Ensure env file exists and is populated for the given mode.
 * @param {string} mode - 'plus' | 'max'
 * @param {{ nonInteractive?: boolean }} opts
 */
async function ensureEnv(mode, opts = {}) {
  const nonInteractive = !!(opts.nonInteractive);
  const file = envFilePath(mode);
  const isNew = !fs.existsSync(file);

  if (isNew && !nonInteractive) {
    console.log(`\nNo config found for ${mode} mode. Let's set it up (secrets are auto-generated).\n`);
  }

  // Load existing values (empty object if file doesn't exist)
  const existing = parseEnv(file);

  // Helper: return existing value or generate/use fallback
  function keep(key, fallback) {
    return existing[key] !== undefined ? existing[key] : fallback;
  }

  function legacyNeo4jPassword() {
    const legacyAuth = existing.NEO4J_AUTH || '';
    const slashIndex = legacyAuth.indexOf('/');
    if (slashIndex === -1) {
      return '';
    }
    return legacyAuth.slice(slashIndex + 1).trim();
  }

  const vars = {
    POSTGRES_PASSWORD: keep('POSTGRES_PASSWORD', randomSecret(16)),
    JWT_SECRET_KEY: keep('JWT_SECRET_KEY', randomSecret(32)),
    JWT_REFRESH_SECRET_KEY: keep('JWT_REFRESH_SECRET_KEY', randomSecret(32)),
    EXPLAIN_LLM_API_KEY: keep('EXPLAIN_LLM_API_KEY', ''),
    PROXY_INGEST_TOKEN: keep('PROXY_INGEST_TOKEN', ''),
    PROXY_UPSTREAM_URL: keep('PROXY_UPSTREAM_URL', 'https://api.anthropic.com'),
    PROXY_WORKSPACE_ID: keep('PROXY_WORKSPACE_ID', 'proxy-capture'),
    BACKEND_LOG_LEVEL: keep('BACKEND_LOG_LEVEL', ''),
    REDIS_URL: keep('REDIS_URL', ''),
  };

  if (mode === 'max') {
    vars.NEO4J_PASSWORD = keep('NEO4J_PASSWORD', legacyNeo4jPassword() || randomSecret(16));
  }

  if (nonInteractive) {
    // Auto-fill all empty optional fields with defaults
    if (!vars.BACKEND_LOG_LEVEL) vars.BACKEND_LOG_LEVEL = 'INFO';
    if (mode === 'max') {
      if (!vars.NEO4J_PASSWORD) vars.NEO4J_PASSWORD = randomSecret(16);
    }
  } else {
    // Only prompt for values that are not already set
    if (!vars.EXPLAIN_LLM_API_KEY) {
      const llmKey = await prompt('OpenAI API key for AI explanations (optional, press Enter to skip): ');
      if (llmKey) vars.EXPLAIN_LLM_API_KEY = llmKey;
    }

    if (!vars.REDIS_URL) {
      const redisUrl = await prompt('Redis URL for shared rate limiting (optional — press Enter to skip): ');
      if (redisUrl) vars.REDIS_URL = redisUrl;
    }

    if (mode === 'max') {
      if (!vars.NEO4J_PASSWORD) {
        const neo4jPassword = await prompt('Neo4j password for Max mode (press Enter to auto-generate): ');
        vars.NEO4J_PASSWORD = neo4jPassword || randomSecret(16);
      }
    }

    if (!vars.BACKEND_LOG_LEVEL) {
      const logLevel = await prompt('Backend log level (default: INFO): ');
      vars.BACKEND_LOG_LEVEL = logLevel || 'INFO';
    }
  }

  writeEnv(file, vars);

  if (isNew && !nonInteractive) {
    console.log(`\nConfig saved to ${file}`);
    console.log('Secrets were auto-generated. Edit that file any time to change them.\n');
  }

  return file;
}

module.exports = { dataDir, envFilePath, ensureEnv, parseEnv };
