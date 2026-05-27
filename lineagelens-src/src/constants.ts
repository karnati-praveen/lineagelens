/**
 * Shared constants used across the LineageLens VS Code extension.
 *
 * H1: Backend URL default extracted from extension.ts, backend.ts, backendAuth.ts
 * H2: Request timeout extracted from backend.ts and backendAuth.ts
 */

/** Default base URL for the LineageLens backend. */
export const DEFAULT_BACKEND_BASE_URL = 'http://127.0.0.1:8787';

/** HTTP request timeout in milliseconds. */
export const REQUEST_TIMEOUT_MS = 12_000;
