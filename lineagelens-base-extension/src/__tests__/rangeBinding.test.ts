import { locateCapture } from '../evidence/rangeBinding';

const FOO = 'function foo() {\n  return 1;\n}';

test('original: intact block at the recorded start line', () => {
  const doc = 'line0\nfunction foo() {\n  return 1;\n}\nline4';
  const r = locateCapture(doc, { insertedCode: FOO, startLine: 1, endLine: 3 });
  expect(r).toEqual({ startLine: 1, endLine: 3, lineageState: 'original' });
});

test('moved: intact block found at a different line', () => {
  const doc = 'line0\nline1\nfunction foo() {\n  return 1;\n}';
  const r = locateCapture(doc, { insertedCode: FOO, startLine: 1, endLine: 3 });
  expect(r).toEqual({ startLine: 2, endLine: 4, lineageState: 'moved' });
});

test('modified: block partially present after an edit', () => {
  const doc = 'line0\nfunction foo() {\n  return 2;\n}\nline4';
  const r = locateCapture(doc, { insertedCode: FOO, startLine: 1, endLine: 3 });
  expect(r.lineageState).toBe('modified');
  // Falls back to the recorded range as the best pointer.
  expect(r.startLine).toBe(1);
  expect(r.endLine).toBe(3);
});

test('deleted: none of the block lines remain', () => {
  const doc = 'something\nelse entirely\n';
  const r = locateCapture(doc, { insertedCode: 'alpha()\nbeta()\ngamma()', startLine: 0, endLine: 2 });
  expect(r.lineageState).toBe('deleted');
});

test('whitespace-tolerant: trailing whitespace differences still match as original', () => {
  const doc = 'line0\nfunction foo() {   \n  return 1;\t\n}  ';
  const r = locateCapture(doc, { insertedCode: FOO, startLine: 1, endLine: 3 });
  expect(r.lineageState).toBe('original');
});

test('leading blank lines in the captured text are mapped to the recorded start', () => {
  // insertedCode begins with a newline; the non-blank block starts one line later.
  const doc = 'line0\n\nfunction foo() {\n  return 1;\n}';
  const r = locateCapture(doc, { insertedCode: '\n' + FOO, startLine: 1, endLine: 4 });
  expect(r).toEqual({ startLine: 2, endLine: 4, lineageState: 'original' });
});

test('legacy capture without a recorded start reports an intact block as original', () => {
  const doc = 'a\nfunction foo() {\n  return 1;\n}\nb';
  const r = locateCapture(doc, { insertedCode: FOO });
  expect(r.startLine).toBe(1);
  expect(r.endLine).toBe(3);
  expect(r.lineageState).toBe('original');
});

test('empty captured block is unknown', () => {
  const r = locateCapture('whatever\ncontent', { insertedCode: '   \n\n', startLine: 0, endLine: 1 });
  expect(r.lineageState).toBe('unknown');
});

test('out-of-range recorded start is clamped to document bounds', () => {
  const doc = 'x\ny';
  const r = locateCapture(doc, { insertedCode: 'alpha()\nbeta()', startLine: 999, endLine: 1000 });
  expect(r.lineageState).toBe('deleted');
  expect(r.startLine).toBeLessThanOrEqual(1);
  expect(r.endLine).toBeLessThanOrEqual(1);
});
