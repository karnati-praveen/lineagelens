import { redactSecrets, containsSecret, redactRecord, redactRecords } from '../secrets';

describe('redactSecrets', () => {
  test('leaves clean code untouched', () => {
    const code = 'function add(a, b) {\n  return a + b;\n}';
    const { text, count } = redactSecrets(code);
    expect(text).toBe(code);
    expect(count).toBe(0);
  });

  test('redacts an OpenAI-style key', () => {
    const { text, count } = redactSecrets('const key = "sk-abcdefghijklmnopqrstuvwx";');
    expect(text).toContain('[REDACTED]');
    expect(text).not.toContain('sk-abcdefghijklmnopqrstuvwx');
    expect(count).toBe(1);
  });

  test('redacts an Anthropic key', () => {
    const { text, count } = redactSecrets('ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnop1234');
    expect(text).not.toContain('sk-ant-api03-abcdefghijklmnop1234');
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test('redacts AWS access key id', () => {
    const { count } = redactSecrets('aws_key = AKIAIOSFODNN7EXAMPLE');
    expect(count).toBe(1);
  });

  test('redacts a GitHub token', () => {
    const { text } = redactSecrets('token: ghp_1234567890abcdefghijABCDEFGHIJ1234');
    expect(text).not.toContain('ghp_1234567890abcdefghijABCDEFGHIJ1234');
  });

  test('redacts a Bearer token', () => {
    const { text } = redactSecrets('Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456');
    expect(text).toContain('[REDACTED]');
    expect(text).not.toContain('abcdefghijklmnopqrstuvwxyz123456');
  });

  test('redacts an entire private key block', () => {
    const pem = '-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\nlinesoftext\n-----END RSA PRIVATE KEY-----';
    const { text } = redactSecrets(`key:\n${pem}\nafter`);
    expect(text).not.toContain('MIIEpAIBAAKCAQEA');
    expect(text).toContain('[REDACTED]');
    expect(text).toContain('after');
  });

  test('counts multiple secrets', () => {
    const { count } = redactSecrets('a sk-abcdefghijklmnopqrstuvwx and AKIAIOSFODNN7EXAMPLE');
    expect(count).toBe(2);
  });

  test('handles empty input', () => {
    expect(redactSecrets('')).toEqual({ text: '', count: 0 });
  });
});

describe('containsSecret', () => {
  test('true when a secret is present', () => {
    expect(containsSecret('sk-abcdefghijklmnopqrstuvwx')).toBe(true);
  });
  test('false for clean text', () => {
    expect(containsSecret('const x = 1;')).toBe(false);
  });
});

describe('redactRecord / redactRecords', () => {
  test('returns the same reference when no secret present', () => {
    const rec = { insertedCode: 'clean code', id: '1' };
    const result = redactRecord(rec);
    expect(result.count).toBe(0);
    expect(result.record).toBe(rec);
  });

  test('returns a scrubbed copy without mutating the original', () => {
    const rec = { insertedCode: 'key sk-abcdefghijklmnopqrstuvwx', id: '1' };
    const { record, count } = redactRecord(rec);
    expect(count).toBe(1);
    expect(record).not.toBe(rec);
    expect(rec.insertedCode).toContain('sk-abcdefghijklmnopqrstuvwx'); // original intact
    expect(record.insertedCode).not.toContain('sk-abcdefghijklmnopqrstuvwx');
  });

  test('aggregates totals across records', () => {
    const recs = [
      { insertedCode: 'sk-abcdefghijklmnopqrstuvwx', id: '1' },
      { insertedCode: 'clean', id: '2' },
      { insertedCode: 'AKIAIOSFODNN7EXAMPLE', id: '3' },
    ];
    const { records, total } = redactRecords(recs);
    expect(total).toBe(2);
    expect(records[1]).toBe(recs[1]); // clean record unchanged
  });
});
