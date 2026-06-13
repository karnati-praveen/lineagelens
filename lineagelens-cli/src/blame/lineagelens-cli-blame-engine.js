'use strict';

/**
 * LineageLens blame engine — pure functions, zero dependencies.
 *
 * Maps AI provenance records onto the current contents of a file, producing
 * per-line attribution: which lines were AI-inserted, by which model/tool,
 * and when. "git blame tells you who; lineagelens blame tells you which AI."
 *
 * Accepts three record sources (auto-detected by normalizeRecords):
 *   1. Base extension export      — CaptureRecord[] from "Export JSON"
 *   2. Backend search response    — POST /search payload ({ results: [...] })
 *   3. Agent Trace JSONL          — cursor/agent-trace 0.1.0 (preview-only;
 *                                   matched from the insertedCodePreview field)
 */

// ── Line normalization ────────────────────────────────────────────────────────

/** Collapse internal whitespace and trim, so formatting drift doesn't break matches. */
function normLine(line) {
  return String(line).replace(/\s+/g, ' ').trim();
}

/**
 * A line is "significant" enough to attribute on its own (without surrounding
 * context). Short or punctuation-only lines like `}` or `});` appear all over
 * a file and would cause false attribution.
 */
function isSignificantLine(norm) {
  if (norm.length < 8) return false;
  if (!/[a-zA-Z0-9]/.test(norm)) return false;
  return true;
}

// ── Record normalization ──────────────────────────────────────────────────────

/**
 * Parse a record source into normalized records:
 *   { id, filePath, insertedCode, model, timestamp, confidence, source, contentTruncated }
 *
 * @param {string} text - raw file contents (JSON or JSONL)
 * @returns {{ records: Array<object>, format: string, warnings: string[] }}
 */
function normalizeRecords(text) {
  const trimmed = String(text).trim();
  if (!trimmed) return { records: [], format: 'empty', warnings: [] };

  // Try whole-document JSON first (extension export or backend search response).
  let parsed = null;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    parsed = null;
  }

  if (Array.isArray(parsed)) {
    return { records: parsed.map(fromCaptureRecord).filter(Boolean), format: 'extension-export', warnings: [] };
  }
  if (parsed && typeof parsed === 'object' && Array.isArray(parsed.results)) {
    return { records: parsed.results.map(fromSearchResult).filter(Boolean), format: 'backend-search', warnings: [] };
  }

  // Fall back to JSONL (agent-trace docs, or NDJSON of backend records).
  const records = [];
  const warnings = [];
  let agentTraceLines = 0;
  for (const line of trimmed.split(/\r?\n/)) {
    const l = line.trim();
    if (!l) continue;
    let doc;
    try {
      doc = JSON.parse(l);
    } catch {
      warnings.push('Skipped a line that is not valid JSON.');
      continue;
    }
    if (doc && Array.isArray(doc.files)) {
      agentTraceLines++;
      for (const rec of fromAgentTraceDoc(doc)) records.push(rec);
    } else if (doc && typeof doc === 'object' && (doc.insertedCode || doc.insertedText)) {
      const rec = fromCaptureRecord(doc);
      if (rec) records.push(rec);
    }
  }
  if (agentTraceLines > 0) {
    warnings.push(
      'Agent Trace input carries only a 120-char code preview per record; ' +
      'attribution is best-effort. Use the extension JSON export or backend API for full matching.',
    );
  }
  return { records, format: agentTraceLines > 0 ? 'agent-trace' : 'jsonl', warnings };
}

/** Base extension CaptureRecord (or backend-record-shaped object) → normalized. */
function fromCaptureRecord(r) {
  if (!r || typeof r !== 'object') return null;
  const insertedCode = r.insertedCode || r.insertedText || '';
  if (!insertedCode) return null;
  return {
    id: r.id || r.uuid || null,
    filePath: r.filePath || '',
    insertedCode,
    model: r.modelName || r.model || (r.provenance && r.provenance.modelName) || null,
    timestamp: r.timestamp || r.timestampIso || null,
    confidence: typeof r.confidence === 'number' ? r.confidence : null,
    source: r.source || 'unknown',
    contentTruncated: false,
  };
}

/** Backend POST /search result item → normalized. */
function fromSearchResult(item) {
  if (!item || typeof item !== 'object') return null;
  const rec = item.record || {};
  const insertedCode = rec.insertedCode || item.snippet || '';
  if (!insertedCode) return null;
  return {
    id: item.uuid || rec.uuid || null,
    filePath: item.filePath || rec.filePath || '',
    insertedCode,
    model: item.model || rec.modelName || null,
    timestamp: item.timestampIso || rec.timestampIso || null,
    confidence: null,
    source: 'backend',
    // snippet is capped at 700 chars by the search route; full record is not
    contentTruncated: !rec.insertedCode && insertedCode.length >= 700,
  };
}

/** One agent-trace document → zero or more normalized records (one per file entry). */
function fromAgentTraceDoc(doc) {
  const out = [];
  const meta = doc.metadata || {};
  const preview = meta['lineagelens.insertedCodePreview'];
  if (typeof preview !== 'string' || !preview) return out;

  // Preview encodes newlines as '↵' and is truncated at 120 chars. If it hit
  // the cap, the final segment may be a partial line — drop it.
  let lines = preview.split('↵');
  const truncated = preview.length >= 120;
  if (truncated && lines.length > 1) lines = lines.slice(0, -1);
  const insertedCode = lines.join('\n');
  if (!insertedCode.trim()) return out;

  const conf = meta['lineagelens.confidence'];
  for (const file of doc.files) {
    if (!file || !file.path) continue;
    const conv = Array.isArray(file.conversations) ? file.conversations[0] : null;
    const contributor = (conv && conv.contributor) || {};
    out.push({
      id: doc.id || null,
      filePath: file.path,
      insertedCode,
      model: contributor.model_id || null,
      timestamp: doc.timestamp || null,
      confidence: conf && typeof conf.score === 'number' ? conf.score : null,
      source: contributor.type || 'unknown',
      contentTruncated: truncated,
    });
  }
  return out;
}

// ── File-path matching ────────────────────────────────────────────────────────

/** Normalize a path for cross-platform comparison. */
function normPath(p) {
  return String(p || '').replace(/\\/g, '/').toLowerCase();
}

/**
 * Keep records that plausibly belong to targetPath: exact match, suffix match
 * (records often store absolute paths from another machine), or basename match.
 */
function filterRecordsForFile(records, targetPath) {
  const target = normPath(targetPath);
  const base = target.split('/').pop();
  return records.filter((r) => {
    const rp = normPath(r.filePath);
    if (!rp) return false;
    if (rp === target) return true;
    if (rp.endsWith('/' + base) || target.endsWith('/' + rp.split('/').pop())) {
      // Same basename — require the shorter path to be a suffix of the longer
      // one, OR fall back to basename equality (cheap, predictable).
      return rp.split('/').pop() === base;
    }
    return rp.split('/').pop() === base;
  });
}

// ── Blame core ────────────────────────────────────────────────────────────────

/**
 * Attribute file lines to provenance records.
 *
 * Strategy per record (applied oldest → newest, so the newest record wins
 * overlapping lines, like git blame):
 *   1. Exact contiguous match: the record's inserted lines appear in order in
 *      the file (whitespace-normalized). All occurrences are attributed.
 *   2. Partial fallback: if no contiguous match survives (the code was edited
 *      since insertion), individually attribute file lines that exactly match
 *      a *significant* record line. Marked matchType: 'partial'.
 *
 * @param {string} fileText
 * @param {Array<object>} records - normalized records (already path-filtered)
 * @param {{ minConfidence?: number }} [options]
 * @returns {Array<{ lineNo: number, text: string, attribution: object|null }>}
 */
function blameLines(fileText, records, options = {}) {
  const minConfidence = typeof options.minConfidence === 'number' ? options.minConfidence : null;
  const fileLines = String(fileText).split(/\r?\n/);
  const fileNorm = fileLines.map(normLine);
  const attribution = new Array(fileLines.length).fill(null);

  const usable = records
    .filter((r) => r.insertedCode && (minConfidence === null || r.confidence === null || r.confidence >= minConfidence))
    .slice()
    .sort((a, b) => String(a.timestamp || '').localeCompare(String(b.timestamp || '')));

  for (const record of usable) {
    let recLines = record.insertedCode.split(/\r?\n/).map(normLine);
    // Trim leading/trailing empty lines from the inserted block.
    while (recLines.length && !recLines[0]) recLines.shift();
    while (recLines.length && !recLines[recLines.length - 1]) recLines.pop();
    if (recLines.length === 0) continue;

    // Single-line records must be significant on their own to avoid attributing
    // every `}` in the file.
    if (recLines.length === 1 && !isSignificantLine(recLines[0])) continue;

    const matchedExact = matchContiguous(fileNorm, recLines, attribution, record);
    if (!matchedExact) {
      matchPartial(fileNorm, recLines, attribution, record);
    }
  }

  return fileLines.map((text, i) => ({ lineNo: i + 1, text, attribution: attribution[i] }));
}

/** Find every contiguous occurrence of recLines in fileNorm; attribute them. */
function matchContiguous(fileNorm, recLines, attribution, record) {
  let found = false;
  const n = fileNorm.length;
  const m = recLines.length;
  for (let i = 0; i + m <= n; i++) {
    let ok = true;
    for (let j = 0; j < m; j++) {
      if (fileNorm[i + j] !== recLines[j]) { ok = false; break; }
    }
    if (ok) {
      found = true;
      for (let j = 0; j < m; j++) {
        attribution[i + j] = makeAttribution(record, 'exact');
      }
      i += m - 1;
    }
  }
  return found;
}

/** Attribute individual file lines that exactly match significant record lines. */
function matchPartial(fileNorm, recLines, attribution, record) {
  const significant = new Set(recLines.filter(isSignificantLine));
  if (significant.size === 0) return;
  for (let i = 0; i < fileNorm.length; i++) {
    if (significant.has(fileNorm[i])) {
      attribution[i] = makeAttribution(record, 'partial');
    }
  }
}

function makeAttribution(record, matchType) {
  return {
    recordId: record.id,
    model: record.model,
    timestamp: record.timestamp,
    confidence: record.confidence,
    source: record.source,
    matchType,
  };
}

// ── Stats ─────────────────────────────────────────────────────────────────────

/**
 * Summarize a blame result.
 * @returns {{ totalLines, aiLines, exactLines, partialLines, percent, byModel: Array<{model, lines}> }}
 */
function computeStats(blame) {
  // A trailing empty line (from a final newline) shouldn't count toward totals.
  let total = blame.length;
  if (total > 0 && blame[total - 1].text === '' && !blame[total - 1].attribution) total -= 1;

  let ai = 0;
  let exact = 0;
  let partial = 0;
  const byModel = new Map();
  for (const line of blame) {
    const a = line.attribution;
    if (!a) continue;
    ai++;
    if (a.matchType === 'exact') exact++;
    else partial++;
    const key = a.model || 'unknown-ai';
    byModel.set(key, (byModel.get(key) || 0) + 1);
  }
  return {
    totalLines: total,
    aiLines: ai,
    exactLines: exact,
    partialLines: partial,
    percent: total > 0 ? Math.round((ai / total) * 1000) / 10 : 0,
    byModel: [...byModel.entries()]
      .map(([model, lines]) => ({ model, lines }))
      .sort((a, b) => b.lines - a.lines),
  };
}

module.exports = {
  normalizeRecords,
  filterRecordsForFile,
  blameLines,
  computeStats,
  // exported for tests
  normLine,
  isSignificantLine,
};
