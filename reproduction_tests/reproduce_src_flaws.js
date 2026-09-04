const { spawnSync } = require('child_process');
const path = require('path');

const flaws = [
  {
    id: 1,
    name: 'Gzip Proxy Payload Corruption',
    script: 'flaw1_gzip_corruption.ts',
    description: 'Proxy stores raw binary compressed gzipped bytes in rawBodyUtf8 instead of decompressing.'
  },
  {
    id: 2,
    name: 'Document Change Event Race Condition',
    script: 'flaw2_doc_change_race.ts',
    description: 'previousDocumentTexts updates late in finally block after async tasks, causing state desynchronization.'
  },
  {
    id: 3,
    name: 'LocalStorage Concurrent Write Overwrite',
    script: 'flaw3_localstorage_overwrite.ts',
    description: 'updateLineageFromLatestCommit calls writeStore without writeLock, overwriting concurrent ingest records.'
  },
  {
    id: 4,
    name: 'Reviewer Custom API URL Fallback',
    script: 'flaw4_reviewer_url_fallback.ts',
    description: 'openai-compatible provider settings fall back to OpenAI endpoint when legacy config is missing.'
  },
  {
    id: 5,
    name: 'Line Threshold Enter Keystroke Flaw',
    script: 'flaw5_line_threshold_enter.ts',
    description: 'countApproximateLines("\\n") returns 1, triggering insertion detection on single Enter key press when threshold is 1.'
  }
];

console.log('================================================================');
console.log(' LineageLens lineagelens-src Flaw Reproduction Test Suite');
console.log('================================================================\n');

let totalRun = 0;
let flawsReproduced = 0;
const results = [];

const rootDir = path.join(__dirname, '..');
const tsconfigPath = path.join(rootDir, 'tsconfig.test.json');

for (const flaw of flaws) {
  totalRun++;
  const targetScript = path.join(__dirname, flaw.script);
  console.log(`[TEST ${flaw.id}/5] ${flaw.name} (${flaw.script})...`);

  const res = spawnSync('npx', ['tsx', '--tsconfig', tsconfigPath, targetScript], {
    cwd: rootDir,
    encoding: 'utf8',
    shell: true
  });

  const failedAsExpected = res.status !== 0;
  const output = (res.stdout || '') + (res.stderr || '');

  if (failedAsExpected) {
    flawsReproduced++;
    results.push({
      flaw: flaw,
      status: 'REPRODUCED (FAILED AS EXPECTED)',
      output: output.trim()
    });
    console.log(`  -> RESULT: CONFIRMED FAILED (Flaw ${flaw.id} successfully reproduced!)`);
  } else {
    results.push({
      flaw: flaw,
      status: 'PASSED (UNEXPECTED - Flaw not observed)',
      output: output.trim()
    });
    console.log(`  -> RESULT: PASSED (UNEXPECTED - Flaw ${flaw.id} was not triggered!)`);
  }
  console.log('');
}

console.log('================================================================');
console.log(` REPRODUCTION SUMMARY: ${flawsReproduced}/${totalRun} FLAWS REPRODUCED`);
console.log('================================================================\n');

results.forEach((r) => {
  console.log(`Flaw ${r.flaw.id}: ${r.flaw.name}`);
  console.log(`Status: ${r.status}`);
  console.log(`Output Details:\n${r.output}\n`);
  console.log('----------------------------------------------------------------');
});

if (flawsReproduced === totalRun) {
  console.error(`\n[REPRODUCTION COMPLETE] All ${flawsReproduced} lineagelens-src flaws were successfully reproduced with failing test cases.`);
  process.exit(1); // Exit code non-zero as required to demonstrate flaw failure
} else {
  console.error(`\n[WARNING] Only ${flawsReproduced}/${totalRun} flaws failed.`);
  process.exit(2);
}
