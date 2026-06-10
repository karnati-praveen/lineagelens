/**
 * Secret scrubbing for data that LEAVES the machine.
 *
 * The local capture store keeps full-fidelity code (it is encrypted at rest and
 * is the developer's own data). But the moment a capture is shared — exported as
 * JSON / Agent Trace, or POSTed to a backend — any secret a developer pasted into
 * AI-generated code would leak in cleartext. This module scrubs those secrets at
 * every egress point.
 *
 * Patterns mirror the proxy's default redaction set
 * (lineagelens-proxy/proxy.py: _DEFAULT_REDACT_PATTERN_STRINGS) so proxy-captured
 * and extension-captured data are scrubbed consistently.
 */

const REDACTED = '[REDACTED]';

// Order matters only for readability; all patterns are applied. Each uses the
// global flag so every occurrence is replaced and counted.
const SECRET_PATTERNS: RegExp[] = [
  // Private key blocks — redact the whole PEM body, not just the header.
  /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----/g,
  /sk-ant-[A-Za-z0-9_-]{16,}/g, // Anthropic
  /sk-[A-Za-z0-9_-]{16,}/g, // OpenAI / generic sk- keys
  /AIza[0-9A-Za-z_-]{20,}/g, // Google API keys
  /ya29\.[0-9A-Za-z_-]+/g, // Google OAuth access tokens
  /gh[pousr]_[A-Za-z0-9]{20,}/g, // GitHub tokens
  /github_pat_[A-Za-z0-9_]{20,}/g, // GitHub fine-grained PATs
  /xox[baprs]-[A-Za-z0-9-]{10,}/g, // Slack tokens
  /AKIA[0-9A-Z]{16}/g, // AWS access key IDs
  /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g, // JWTs
  /\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*/gi, // Bearer tokens
];

/**
 * Replace any secrets found in `text` with `[REDACTED]`.
 * Returns the scrubbed text and the number of secrets removed.
 */
export function redactSecrets(text: string): { text: string; count: number } {
  if (!text) {
    return { text, count: 0 };
  }
  let count = 0;
  let out = text;
  for (const pattern of SECRET_PATTERNS) {
    out = out.replace(pattern, () => {
      count += 1;
      return REDACTED;
    });
  }
  return { text: out, count };
}

/** Return true if `text` contains at least one detectable secret. */
export function containsSecret(text: string): boolean {
  return redactSecrets(text).count > 0;
}

/**
 * Return a shallow copy of `record` with its `insertedCode` scrubbed.
 * Records with no secrets are returned unchanged (same reference).
 */
export function redactRecord<T extends { insertedCode: string }>(
  record: T,
): { record: T; count: number } {
  const { text, count } = redactSecrets(record.insertedCode);
  if (count === 0) {
    return { record, count: 0 };
  }
  return { record: { ...record, insertedCode: text }, count };
}

/**
 * Scrub an array of records. Returns the scrubbed records and the total number
 * of secrets removed across all of them.
 */
export function redactRecords<T extends { insertedCode: string }>(
  records: T[],
): { records: T[]; total: number } {
  let total = 0;
  const scrubbed = records.map((r) => {
    const { record, count } = redactRecord(r);
    total += count;
    return record;
  });
  return { records: scrubbed, total };
}
