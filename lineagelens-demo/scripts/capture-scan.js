#!/usr/bin/env node
'use strict';

// Regenerates src/data/scan-result.json from a real `lineagelens scan` run.
//
// The demo must never show numbers nobody can reproduce. Every figure on screen
// comes from this file, and this file only ever comes from the real CLI. Point
// it at any repo:
//
//   node scripts/capture-scan.js                     # scan this repo
//   node scripts/capture-scan.js ../some-other-repo
//
// If the target repo has no AI-declared commits the script says so and leaves
// the existing snapshot alone, rather than writing an empty demo.

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..', '..');
const cli = path.join(repoRoot, 'lineagelens-cli', 'bin', 'lineagelens-cli.js');
const outFile = path.join(__dirname, '..', 'src', 'data', 'scan-result.json');

const target = path.resolve(process.argv[2] || repoRoot);

console.log(`Scanning ${target} …`);

let raw;
try {
  raw = execFileSync(process.execPath, [cli, '--json', 'scan', target], {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
} catch (e) {
  console.error(`scan failed: ${e.message}`);
  process.exit(1);
}

let result;
try {
  result = JSON.parse(raw);
} catch {
  console.error('scan did not return JSON. Did the CLI print a warning to stdout?');
  process.exit(1);
}

if (result.totals.aiAttributedLines === 0) {
  console.error(
    'This repo has no AI-declared commits, so there is nothing to show.\n' +
    'Pick a repo whose history contains AI commit trailers, or keep the existing snapshot.',
  );
  process.exit(1);
}

// Keep only what the compositions render. A trimmed snapshot keeps the bundle
// small and makes it obvious which fields the video actually depends on.
const trimmed = {
  capturedAt: new Date().toISOString(),
  capturedFrom: path.basename(target),
  scan: result.scan,
  totals: result.totals,
  byTool: result.byTool,
  byCategory: result.byCategory,
  files: result.files.slice(0, 8),
  coverage: {
    status: result.coverage.status,
    reasons: result.coverage.reasons,
    configuredTools: result.coverage.configuredTools,
    declarationRate: result.coverage.declarationRate,
  },
  assurance: result.assurance,
};

fs.writeFileSync(outFile, `${JSON.stringify(trimmed, null, 2)}\n`);
console.log(`Wrote ${path.relative(process.cwd(), outFile)}`);
console.log(
  `  ${trimmed.totals.aiAttributedPercent}% AI · ` +
  `${trimmed.totals.aiAttributedLines} of ${trimmed.totals.trackedLines} lines · ` +
  `${Object.keys(trimmed.byTool).length} tool(s) · coverage ${trimmed.coverage.status}`,
);
