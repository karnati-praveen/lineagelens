/**
 * Backend capability detection — decides whether to send the richer evidence-v2
 * payload or fall back to the legacy shape. Degrades to `legacy` on any error or
 * unknown response, so an older backend (no capabilities endpoint) keeps working.
 *
 * The fetch is injectable so detection is unit-testable without a live backend.
 */

import { IngestCapability } from './payload';

/** Pure: map a capabilities response to a capability level. */
export function parseCapability(status: number, body: unknown): IngestCapability {
  if (status === 200 && body && typeof body === 'object') {
    const b = body as Record<string, unknown>;
    const caps = b.capabilities;
    if (b.evidenceV2 === true || (Array.isArray(caps) && caps.includes('evidence-v2'))) {
      return 'evidence-v2';
    }
  }
  return 'legacy';
}

export type FetchLike = (
  url: string,
  init?: { method?: string },
) => Promise<{ status: number; json: () => Promise<unknown> }>;

/** Probe `${backendUrl}/ingest/capabilities`; resolve the capability level. */
export async function detectCapability(
  backendUrl: string,
  fetchImpl?: FetchLike,
): Promise<IngestCapability> {
  const doFetch = fetchImpl ?? (globalThis.fetch as unknown as FetchLike);
  try {
    const resp = await doFetch(`${backendUrl}/ingest/capabilities`, { method: 'GET' });
    let body: unknown = null;
    try {
      body = await resp.json();
    } catch {
      // non-JSON response — treat as legacy
    }
    return parseCapability(resp.status, body);
  } catch {
    return 'legacy';
  }
}
