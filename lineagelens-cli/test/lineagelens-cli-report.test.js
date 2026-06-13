'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { walkFiles, recordsByBasename } = require('../src/commands/lineagelens-cli-report');

function mktree(spec) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'll-report-'));
  for (const [rel, content] of Object.entries(spec)) {
    const full = path.join(root, rel);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, content);
  }
  return root;
}

test('walkFiles skips node_modules, .git, dist and hidden dirs', () => {
  const root = mktree({
    'src/app.js': 'code',
    'src/util/helpers.js': 'code',
    'node_modules/dep/index.js': 'dep',
    'dist/bundle.js': 'built',
    '.git/HEAD': 'ref',
    '.cache/x.js': 'cache',
    'README.md': 'docs',
  });
  try {
    const files = walkFiles(root).map((f) => path.relative(root, f).replace(/\\/g, '/')).sort();
    assert.deepEqual(files, ['README.md', 'src/app.js', 'src/util/helpers.js']);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('recordsByBasename groups cross-platform paths case-insensitively', () => {
  const map = recordsByBasename([
    { filePath: '/home/dev/project/Users.py', insertedCode: 'x' },
    { filePath: 'C:\\work\\repo\\users.py', insertedCode: 'y' },
    { filePath: 'other/billing.py', insertedCode: 'z' },
    { filePath: '', insertedCode: 'ignored' },
  ]);
  assert.equal(map.get('users.py').length, 2);
  assert.equal(map.get('billing.py').length, 1);
  assert.equal(map.size, 2);
});
