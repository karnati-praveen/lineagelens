import assert from 'node:assert/strict';
import test from 'node:test';
import {
  calculateLevenshteinSimilarity,
  correlateInsertionWithProxyRequest
} from '../correlation';

test('calculateLevenshteinSimilarity returns 1 for identical snippets', () => {
  const snippet = 'function add(a, b) {\n  return a + b;\n}';
  const score = calculateLevenshteinSimilarity(snippet, snippet);

  assert.equal(score, 1);
});

test('calculateLevenshteinSimilarity stays bounded for unrelated snippets', () => {
  const score = calculateLevenshteinSimilarity(
    'SELECT * FROM users WHERE id = 42;',
    'def fib(n):\n    return n if n < 2 else fib(n - 1) + fib(n - 2)'
  );

  assert.ok(score >= 0 && score <= 1);
  assert.ok(score < 0.5);
});

test('correlateInsertionWithProxyRequest reports proxy unavailable with configured window', async () => {
  const result = await correlateInsertionWithProxyRequest({
    insertionTimestampIso: new Date().toISOString(),
    filePath: 'src/example.ts',
    insertedCode: 'console.log("hello");',
    localProxy: undefined,
    correlationWindowMs: 15_000,
    similarityThreshold: 0.8
  });

  assert.equal(result.promptStatus, 'not-captured');
  assert.equal(result.reason, 'local-proxy-unavailable');
  assert.equal(result.correlationWindowMs, 15_000);
  assert.equal(result.similarityThreshold, 0.8);
});
