'use strict';

/**
 * Shared record loading for `lineagelens blame` and `lineagelens report`.
 *
 * Sources, in the order commands try them:
 *   --input <file>          extension captures.json, agent-trace .jsonl,
 *                           or a saved backend /search response
 *   --url/--token/--workspace (or LINEAGELENS_URL / LINEAGELENS_TOKEN /
 *                           LINEAGELENS_WORKSPACE) — query the backend
 */

const fs = require('fs');
const path = require('path');

const { normalizeRecords } = require('./lineagelens-cli-blame-engine');

function loadRecordsFromFile(inputPath) {
  const raw = fs.readFileSync(inputPath, 'utf-8');
  return normalizeRecords(raw);
}

/**
 * Query the backend /search endpoint. When filePathFilter is given, retries
 * with the basename (captures often store absolute paths from other machines);
 * without it, pages through all workspace records via offset.
 *
 * @param {string} backendUrl
 * @param {string} token
 * @param {string} workspaceId
 * @param {string|null} filePathFilter
 * @param {object} extraSearchParams - e.g. { reviewStatus, category }
 */
async function loadRecordsFromBackend(backendUrl, token, workspaceId, filePathFilter, extraSearchParams = {}) {
  const url = backendUrl.replace(/\/$/, '') + '/search';

  async function search(body) {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({ workspaceId, limit: 500, ...extraSearchParams, ...body }),
    });
    if (!resp.ok) {
      throw new Error(`Backend search failed: HTTP ${resp.status} ${await safeText(resp)}`);
    }
    return resp.json();
  }

  if (filePathFilter) {
    const attempts = [{ filePath: filePathFilter }, { filePath: path.basename(filePathFilter) }];
    for (const filter of attempts) {
      const data = await search(filter);
      const normalized = normalizeRecords(JSON.stringify(data));
      if (normalized.records.length > 0) return normalized;
    }
    return { records: [], format: 'backend-search', warnings: [] };
  }

  // Workspace-wide: page through everything (capped to avoid runaway pulls).
  const all = [];
  const warnings = [];
  let offset = 0;
  const MAX_RECORDS = 5000;
  for (;;) {
    const data = await search({ offset });
    const normalized = normalizeRecords(JSON.stringify(data));
    all.push(...normalized.records);
    const got = (data.results || []).length;
    if (got === 0 || all.length >= MAX_RECORDS || !data.has_more) break;
    offset += got;
  }
  if (all.length >= MAX_RECORDS) {
    warnings.push(`Record pull capped at ${MAX_RECORDS}; report may be incomplete.`);
  }
  return { records: all, format: 'backend-search', warnings };
}

async function safeText(resp) {
  try {
    return (await resp.text()).slice(0, 200);
  } catch {
    return '';
  }
}

/**
 * Resolve a record source from CLI options + env. Returns
 * { records, format, warnings } or throws with a user-facing message.
 *
 * @param {object} opts - { input, url, token, workspace, reviewStatus, category }
 * @param {string|null} filePathFilter - narrow backend queries to one file
 */
async function loadRecords(opts, filePathFilter = null) {
  const backendUrl = opts.url || process.env.LINEAGELENS_URL || '';
  const token = opts.token || process.env.LINEAGELENS_TOKEN || '';
  const workspace = opts.workspace || process.env.LINEAGELENS_WORKSPACE || '';

  const hasServerFilters = !!(opts.reviewStatus || opts.category);

  if (opts.input) {
    if (hasServerFilters) {
      throw new Error(
        '--review-status and --category require backend mode: ' +
        'pass --url/--token/--workspace or set LINEAGELENS_URL / LINEAGELENS_TOKEN / LINEAGELENS_WORKSPACE. ' +
        '--input mode cannot evaluate server-side filters.',
      );
    }
    if (!fs.existsSync(opts.input)) {
      throw new Error(`Records file not found: ${opts.input}`);
    }
    return loadRecordsFromFile(opts.input);
  }

  const extraSearchParams = {};
  if (opts.reviewStatus) extraSearchParams.reviewStatus = opts.reviewStatus;
  if (opts.category) extraSearchParams.category = opts.category;

  if (backendUrl && token) {
    if (!workspace) {
      throw new Error('Backend mode requires a workspace id: pass --workspace or set LINEAGELENS_WORKSPACE.');
    }
    return loadRecordsFromBackend(backendUrl, token, workspace, filePathFilter, extraSearchParams);
  }
  throw new Error(
    'No record source. Either:\n' +
    '  --input <captures.json>     (VS Code extension: "LineageLens: Export JSON")\n' +
    '  --input <trace.jsonl>       (Agent Trace export)\n' +
    '  --url <backend> --token <jwt> --workspace <id>   (or LINEAGELENS_URL / LINEAGELENS_TOKEN / LINEAGELENS_WORKSPACE)',
  );
}

module.exports = { loadRecords, loadRecordsFromFile, loadRecordsFromBackend };
