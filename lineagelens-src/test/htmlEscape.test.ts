/**
 * Tests for L4: escapeHtml — ensures every backend-derived value written via
 * innerHTML in the three VS Code webview panels (provenanceSidebar,
 * insightsDashboard, provenanceSearchSidebar) is safe against XSS injection.
 *
 * The inline `escapeHtml` definitions embedded inside each webview <script>
 * block must remain identical to the exported function in htmlEscape.ts so
 * these unit tests also cover the webview behaviour.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { escapeHtml } from '../htmlEscape';

// ── basic character escaping ──────────────────────────────────────────────────

test('escapes < and >', () => {
  assert.equal(escapeHtml('<b>bold</b>'), '&lt;b&gt;bold&lt;/b&gt;');
});

test('escapes &', () => {
  assert.equal(escapeHtml('cats & dogs'), 'cats &amp; dogs');
});

test('escapes double-quote', () => {
  assert.equal(escapeHtml('"quoted"'), '&quot;quoted&quot;');
});

test('escapes single-quote', () => {
  assert.equal(escapeHtml("it's"), 'it&#39;s');
});

test('leaves plain text unchanged', () => {
  assert.equal(escapeHtml('hello world'), 'hello world');
});

// ── XSS payloads that simulate backend-derived values ────────────────────────

test('escapes img onerror XSS payload', () => {
  const payload = '<img src=x onerror=alert(1)>';
  const result = escapeHtml(payload);
  assert.ok(!result.includes('<'), 'result must not contain raw <');
  assert.ok(!result.includes('>'), 'result must not contain raw >');
  assert.equal(result, '&lt;img src=x onerror=alert(1)&gt;');
});

test('escapes script tag XSS payload', () => {
  const payload = '<script>alert("xss")</script>';
  const result = escapeHtml(payload);
  assert.ok(!result.includes('<script>'));
  assert.ok(!result.includes('</script>'));
});

test('escapes event-handler attribute XSS payload', () => {
  const payload = '" onmouseover="alert(1)';
  const result = escapeHtml(payload);
  assert.ok(!result.includes('"'), 'result must not contain raw double-quote');
  assert.equal(result, '&quot; onmouseover=&quot;alert(1)');
});

test('escapes javascript: URL payload', () => {
  const payload = "javascript:alert('xss')";
  const result = escapeHtml(payload);
  // ' must be escaped
  assert.ok(!result.includes("'"));
  assert.equal(result, 'javascript:alert(&#39;xss&#39;)');
});

// ── type coercion (matches webview behaviour of String(text)) ─────────────────

test('converts null to string "null"', () => {
  assert.equal(escapeHtml(null), 'null');
});

test('converts undefined to string "undefined"', () => {
  assert.equal(escapeHtml(undefined), 'undefined');
});

test('converts numbers', () => {
  assert.equal(escapeHtml(42), '42');
});

test('converts booleans', () => {
  assert.equal(escapeHtml(true), 'true');
});

// ── simulates the exact innerHTML rendering pattern used in the webviews ──────

test('provenance sidebar overview row escapes backend uuid with XSS', () => {
  // Simulates: '<dt>' + escapeHtml(row[0]) + '</dt><dd>' + escapeHtml(row[1]) + '</dd>'
  const label = 'UUID';
  const value = '<img src=x onerror=alert(1)>';
  const html = `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`;
  assert.ok(!html.includes('<img'), 'HTML must not contain unescaped <img>');
  assert.ok(html.includes('&lt;img'), 'HTML must contain escaped &lt;img');
});

test('search sidebar result item escapes snippet with XSS', () => {
  // Simulates: '<pre class="snippet">' + escapeHtml(snippet) + '</pre>'
  const snippet = '<script>alert("pwned")</script>';
  const html = `<pre class="snippet">${escapeHtml(snippet)}</pre>`;
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
});

test('dashboard high-risk row escapes file path with XSS', () => {
  // Simulates: '<div class="title">' + escapeHtml(item.filePath) + '</div>'
  const filePath = '"><img src=x onerror=fetch("/steal?c="+document.cookie)>';
  const html = `<div class="title">${escapeHtml(filePath)}</div>`;
  // No unescaped opening tag — "<img" cannot form a real element
  assert.ok(!html.includes('<img'));
  // The literal word "onerror" is safe as text content (not inside an element
  // attribute context) once < and " are escaped.
  assert.ok(html.includes('&lt;img'));
  assert.ok(html.includes('&quot;'));
});

test('diff header escapes version labels with XSS', () => {
  // Simulates the diff-header: escapeHtml(previousLabel + ' -> ' + currentLabel)
  const previousLabel = '<evil>';
  const currentLabel = '</evil>';
  const combined = previousLabel + ' -> ' + currentLabel;
  const html = `<div class="diff-header">${escapeHtml(combined)}</div>`;
  assert.ok(!html.includes('<evil>'));
  assert.ok(html.includes('&lt;evil&gt;'));
});
