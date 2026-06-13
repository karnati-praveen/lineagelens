'use strict';

/**
 * Tests for --review-status and --category CLI flags on blame and report.
 *
 * Strategy: mock global.fetch so no real network call is made; assert the
 * POST /search body contains the expected parameters.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { loadRecords, loadRecordsFromBackend } = require('../src/blame/lineagelens-cli-record-source');

// ── helpers ──────────────────────────────────────────────────────────────────

/** Capture the body of the first fetch call and return empty results. */
function mockFetch(capturedBodies) {
  return async (url, init) => {
    capturedBodies.push(JSON.parse(init.body));
    return {
      ok: true,
      json: async () => ({ results: [], count: 0, has_more: false }),
    };
  };
}

function withFetchMock(fn) {
  return async () => {
    const origFetch = global.fetch;
    const bodies = [];
    global.fetch = mockFetch(bodies);
    try {
      await fn(bodies);
    } finally {
      global.fetch = origFetch;
    }
  };
}

// ── backend-mode: params reach the search body ───────────────────────────────

test(
  'loadRecordsFromBackend includes reviewStatus in search body',
  withFetchMock(async (bodies) => {
    await loadRecordsFromBackend(
      'http://backend', 'tok', 'ws1', null,
      { reviewStatus: 'unreviewed' },
    );
    assert.equal(bodies.length >= 1, true);
    assert.equal(bodies[0].reviewStatus, 'unreviewed');
  }),
);

test(
  'loadRecordsFromBackend includes category in search body',
  withFetchMock(async (bodies) => {
    await loadRecordsFromBackend(
      'http://backend', 'tok', 'ws1', null,
      { category: 'auth' },
    );
    assert.equal(bodies[0].category, 'auth');
  }),
);

test(
  'loadRecordsFromBackend includes both reviewStatus and category',
  withFetchMock(async (bodies) => {
    await loadRecordsFromBackend(
      'http://backend', 'tok', 'ws1', null,
      { reviewStatus: 'unreviewed', category: 'secrets' },
    );
    assert.equal(bodies[0].reviewStatus, 'unreviewed');
    assert.equal(bodies[0].category, 'secrets');
  }),
);

test(
  'loadRecords backend-mode passes reviewStatus through opts',
  withFetchMock(async (bodies) => {
    await loadRecords(
      { url: 'http://backend', token: 'tok', workspace: 'ws1', reviewStatus: 'pending' },
      null,
    );
    assert.equal(bodies[0].reviewStatus, 'pending');
  }),
);

test(
  'loadRecords backend-mode passes category through opts',
  withFetchMock(async (bodies) => {
    await loadRecords(
      { url: 'http://backend', token: 'tok', workspace: 'ws1', category: 'sql' },
      null,
    );
    assert.equal(bodies[0].category, 'sql');
  }),
);

// ── --input mode with server-side filters errors out ─────────────────────────

test('loadRecords --input + --review-status throws a clear error', async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'll-filter-test-'));
  const inputFile = path.join(tmp, 'captures.json');
  fs.writeFileSync(inputFile, '[]');
  try {
    await assert.rejects(
      () => loadRecords({ input: inputFile, reviewStatus: 'unreviewed' }, null),
      (err) => {
        assert.match(err.message, /backend mode/i);
        return true;
      },
    );
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('loadRecords --input + --category throws a clear error', async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'll-filter-test-'));
  const inputFile = path.join(tmp, 'captures.json');
  fs.writeFileSync(inputFile, '[]');
  try {
    await assert.rejects(
      () => loadRecords({ input: inputFile, category: 'auth' }, null),
      (err) => {
        assert.match(err.message, /backend mode/i);
        return true;
      },
    );
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

// ── no filters: backwards-compatible (no extra fields in body) ───────────────

test(
  'loadRecords without filters does not inject reviewStatus or category',
  withFetchMock(async (bodies) => {
    await loadRecords(
      { url: 'http://backend', token: 'tok', workspace: 'ws1' },
      null,
    );
    assert.equal('reviewStatus' in bodies[0], false);
    assert.equal('category' in bodies[0], false);
  }),
);
