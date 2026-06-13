import assert from 'node:assert/strict';
import test from 'node:test';
import { DEFAULT_BACKEND_BASE_URL, REQUEST_TIMEOUT_MS } from '../src/constants';

test('shared constants have the expected canonical values', () => {
  assert.equal(DEFAULT_BACKEND_BASE_URL, 'http://127.0.0.1:8787');
  assert.equal(REQUEST_TIMEOUT_MS, 12_000);
});

test('constants are importable (consumers resolve one definition)', () => {
  assert.equal(typeof DEFAULT_BACKEND_BASE_URL, 'string');
  assert.equal(typeof REQUEST_TIMEOUT_MS, 'number');
});
