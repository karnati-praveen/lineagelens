'use strict';

const fs = require('fs');
const path = require('path');

const {
  filterRecordsForFile,
  blameLines,
  computeStats,
} = require('../blame/lineagelens-cli-blame-engine');
const { loadRecords } = require('../blame/lineagelens-cli-record-source');
const { isJsonMode, out, err } = require('../utils/lineagelens-cli-output');
const { makePalette } = require('../utils/lineagelens-cli-render');

// ── Rendering ─────────────────────────────────────────────────────────────────

function shortModel(model) {
  const m = model || 'unknown-ai';
  return m.length > 22 ? m.slice(0, 21) + '…' : m;
}

function shortDate(ts) {
  return ts ? String(ts).slice(0, 10) : '          ';
}

function renderBlame(filePath, blame, stats, palette, statsOnly) {
  const lines = [];

  if (!statsOnly) {
    const gutterWidth = Math.max(
      4,
      ...blame.filter((l) => l.attribution).map((l) => shortModel(l.attribution.model).length),
    );
    const lineNoWidth = String(blame.length).length;

    for (const line of blame) {
      const no = String(line.lineNo).padStart(lineNoWidth);
      if (line.attribution) {
        const a = line.attribution;
        const marker = a.matchType === 'exact' ? palette.green('AI ') : palette.yellow('AI?');
        const model = palette.cyan(shortModel(a.model).padEnd(gutterWidth));
        const date = palette.dim(shortDate(a.timestamp));
        lines.push(`${marker} ${model} ${date} ${palette.dim(no)} │ ${line.text}`);
      } else {
        const pad = ' '.repeat(3 + 1 + gutterWidth + 1 + 10);
        lines.push(`${pad} ${palette.dim(no)} │ ${palette.dim(line.text)}`);
      }
    }
    lines.push('');
  }

  lines.push(palette.bold(`── lineagelens blame ─ ${filePath}`));
  lines.push(
    `   ${stats.aiLines}/${stats.totalLines} lines AI-attributed (${stats.percent}%)` +
    (stats.partialLines > 0 ? ` — ${stats.exactLines} exact, ${stats.partialLines} fuzzy (AI?)` : ''),
  );
  for (const entry of stats.byModel) {
    lines.push(`     ${palette.cyan(entry.model)}: ${entry.lines} line${entry.lines !== 1 ? 's' : ''}`);
  }
  if (stats.aiLines === 0) {
    lines.push(palette.dim('   No provenance records matched this file’s current contents.'));
  }
  return lines.join('\n');
}

// ── Command ───────────────────────────────────────────────────────────────────

/**
 * lineagelens blame <file> — per-line AI attribution.
 *
 * Record sources (first match wins):
 *   --input <file>   extension captures.json export, agent-trace .jsonl,
 *                    or a saved backend /search response
 *   --url/--token    query a LineageLens backend directly
 *                    (env: LINEAGELENS_URL, LINEAGELENS_TOKEN, LINEAGELENS_WORKSPACE)
 */
async function blame(file, opts) {
  const targetPath = path.resolve(file);
  if (!fs.existsSync(targetPath)) {
    err(`File not found: ${targetPath}`);
    process.exit(1);
  }
  const fileText = fs.readFileSync(targetPath, 'utf-8');

  let loaded;
  try {
    loaded = await loadRecords(opts, targetPath);
  } catch (e) {
    err(e.message);
    process.exit(1);
  }

  const fileRecords = filterRecordsForFile(loaded.records, targetPath);
  const minConfidence = typeof opts.minConfidence === 'number' && !Number.isNaN(opts.minConfidence)
    ? opts.minConfidence
    : null;
  const result = blameLines(fileText, fileRecords, { minConfidence });
  const stats = computeStats(result);

  if (isJsonMode()) {
    out({
      file: targetPath,
      recordSource: loaded.format,
      recordsConsidered: fileRecords.length,
      warnings: loaded.warnings,
      stats,
      lines: result
        .filter((l) => l.attribution)
        .map((l) => ({
          line: l.lineNo,
          matchType: l.attribution.matchType,
          model: l.attribution.model,
          recordId: l.attribution.recordId,
          timestamp: l.attribution.timestamp,
          confidence: l.attribution.confidence,
        })),
    });
    return;
  }

  const useColor = opts.color !== false && process.stdout.isTTY;
  const palette = makePalette(useColor);
  for (const warning of loaded.warnings) {
    err(palette.dim(`note: ${warning}`));
  }
  const rel = path.relative(process.cwd(), targetPath);
  const displayPath = rel && !rel.startsWith('..') ? rel : targetPath;
  out(renderBlame(displayPath, result, stats, palette, !!opts.stats));
}

module.exports = { blame };
