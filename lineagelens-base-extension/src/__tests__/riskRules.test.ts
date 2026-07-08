import { evaluateRisk, hasHighRisk, RiskInput } from '../risk/rules';

function input(over: Partial<RiskInput>): RiskInput {
  return { filePath: '/repo/src/app.ts', language: 'typescript', insertedCode: 'const x = 1;', ...over };
}

/** Ids fired for a given input. */
function ids(over: Partial<RiskInput>): string[] {
  return evaluateRisk(input(over)).map((s) => s.id);
}

// ── individual rules: positive fires, negative stays silent ───────────────────

test('hardcoded-secret fires on an embedded key and not on clean code', () => {
  expect(ids({ insertedCode: 'const k = "sk-ant-abcdefghijklmnop1234567890";' })).toContain('hardcoded-secret');
  expect(ids({ insertedCode: 'const total = a + b;' })).not.toContain('hardcoded-secret');
});

test('generated-auth-code fires on an auth path and on auth content', () => {
  expect(ids({ filePath: '/repo/src/auth/login.ts', insertedCode: 'function check() {}' })).toContain('generated-auth-code');
  expect(ids({ insertedCode: 'const t = jwt.sign(payload, secret);' })).toContain('generated-auth-code');
  expect(ids({ insertedCode: 'const sum = 1 + 2;' })).not.toContain('generated-auth-code');
});

test('generated-sql fires on a query and not on prose', () => {
  expect(ids({ insertedCode: 'db.run("SELECT id FROM users WHERE name = " + name)' })).toContain('generated-sql');
  expect(ids({ insertedCode: 'const list = [1,2,3];' })).not.toContain('generated-sql');
});

test('generated-shell-exec fires on process execution', () => {
  expect(ids({ insertedCode: 'const { execSync } = require("child_process"); execSync(cmd);' })).toContain('generated-shell-exec');
  expect(ids({ insertedCode: 'array.map(x => x * 2);' })).not.toContain('generated-shell-exec');
});

test('dynamic-eval fires on eval/new Function', () => {
  expect(ids({ insertedCode: 'const r = eval(userInput);' })).toContain('dynamic-eval');
  expect(ids({ insertedCode: 'const fn = new Function("a", "return a");' })).toContain('dynamic-eval');
  expect(ids({ insertedCode: 'const r = evaluate(x);' })).not.toContain('dynamic-eval');
});

test('dependency-change fires on manifest files', () => {
  expect(ids({ filePath: '/repo/package.json', insertedCode: '{"dependencies":{}}' })).toContain('dependency-change');
  expect(ids({ filePath: '/repo/requirements.txt', insertedCode: 'flask==3.0' })).toContain('dependency-change');
  expect(ids({ filePath: '/repo/src/app.ts' })).not.toContain('dependency-change');
});

test('ci-change and infra-change fire on their paths', () => {
  expect(ids({ filePath: '/repo/.github/workflows/ci.yml', insertedCode: 'on: push' })).toContain('ci-change');
  expect(ids({ filePath: '/repo/main.tf', insertedCode: 'resource "aws_s3_bucket" "b" {}' })).toContain('infra-change');
});

test('security-bypass fires on suppression directives', () => {
  expect(ids({ insertedCode: 'fetch(url, { rejectUnauthorized: false });' })).toContain('security-bypass');
  expect(ids({ insertedCode: '// eslint-disable-next-line\nconst x = 1;' })).toContain('security-bypass');
});

test('untested-generated-logic fires in source but not in a test file', () => {
  expect(ids({ filePath: '/repo/src/calc.ts', insertedCode: 'function add(a, b) { return a + b; }' })).toContain('untested-generated-logic');
  expect(ids({ filePath: '/repo/src/calc.test.ts', insertedCode: 'function add(a, b) { return a + b; }' })).not.toContain('untested-generated-logic');
});

// ── aggregate behaviour ───────────────────────────────────────────────────────

test('signals are sorted high→low severity', () => {
  // auth content (high) + untested logic (low) in one block.
  const signals = evaluateRisk(input({ insertedCode: 'function authenticate(user) { return jwt.sign(user); }' }));
  expect(signals.length).toBeGreaterThanOrEqual(2);
  const ranks = signals.map((s) => s.severity);
  expect(ranks[0]).toBe('high');
  expect(ranks[ranks.length - 1]).toBe('low');
});

test('clean code in a test file yields no signals', () => {
  expect(evaluateRisk(input({ filePath: '/repo/src/x.test.ts', insertedCode: 'expect(1).toBe(1);' }))).toEqual([]);
});

test('hasHighRisk reflects the presence of a high-severity signal', () => {
  expect(hasHighRisk(evaluateRisk(input({ insertedCode: 'eval(x)' })))).toBe(true);
  expect(hasHighRisk(evaluateRisk(input({ filePath: '/repo/package.json', insertedCode: '{}' })))).toBe(false);
  expect(hasHighRisk(undefined)).toBe(false);
});

test('every signal carries an id, label, category, severity, and message', () => {
  for (const s of evaluateRisk(input({ filePath: '/repo/src/auth/jwt.ts', insertedCode: 'eval(jwt.sign(x))' }))) {
    expect(s.id).toBeTruthy();
    expect(s.label).toBeTruthy();
    expect(s.category).toBeTruthy();
    expect(['low', 'medium', 'high']).toContain(s.severity);
    expect(s.message.length).toBeGreaterThan(0);
  }
});
