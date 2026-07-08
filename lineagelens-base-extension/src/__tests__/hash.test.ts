import {
  sha256Hex,
  canonicalJson,
  normalizeForHash,
  rangeContentHash,
} from '../evidence/hash';

// ── sha256Hex ─────────────────────────────────────────────────────────────────

test('sha256Hex returns a 64-char lowercase hex digest', () => {
  const h = sha256Hex('hello');
  expect(h).toMatch(/^[0-9a-f]{64}$/);
  // Known SHA-256 of "hello".
  expect(h).toBe('2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824');
});

test('sha256Hex is deterministic and order-sensitive', () => {
  expect(sha256Hex('ab')).toBe(sha256Hex('ab'));
  expect(sha256Hex('ab')).not.toBe(sha256Hex('ba'));
});

// ── canonicalJson ─────────────────────────────────────────────────────────────

test('canonicalJson is independent of object key insertion order', () => {
  expect(canonicalJson({ a: 1, b: 2 })).toBe(canonicalJson({ b: 2, a: 1 }));
});

test('canonicalJson sorts keys at every nesting level', () => {
  const out = canonicalJson({ z: { y: 1, x: 2 }, a: 3 });
  expect(out).toBe('{"a":3,"z":{"x":2,"y":1}}');
});

test('canonicalJson preserves array order', () => {
  expect(canonicalJson([3, 1, 2])).toBe('[3,1,2]');
});

// ── normalizeForHash ──────────────────────────────────────────────────────────

test('normalizeForHash strips trailing whitespace per line and trims blank ends', () => {
  // Trailing WS removed per line; leading/trailing blank lines trimmed (and the
  // overall trim() also removes leading indentation on the first surviving line).
  expect(normalizeForHash('\n  a  \nb\t\n\n')).toBe('a\nb');
  // Interior indentation is preserved.
  expect(normalizeForHash('a\n    b   \nc')).toBe('a\n    b\nc');
});

// ── rangeContentHash ──────────────────────────────────────────────────────────

test('rangeContentHash ignores trailing whitespace and surrounding blank lines', () => {
  expect(rangeContentHash('const x = 1;\nconst y = 2;'))
    .toBe(rangeContentHash('\nconst x = 1;   \nconst y = 2;\t\n'));
});

test('rangeContentHash distinguishes different code', () => {
  expect(rangeContentHash('a()')).not.toBe(rangeContentHash('b()'));
});

test('rangeContentHash handles empty input', () => {
  expect(rangeContentHash('')).toBe(sha256Hex(''));
});
