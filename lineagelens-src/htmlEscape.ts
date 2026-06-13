/**
 * Escape HTML special characters so that backend-derived values can be safely
 * interpolated into innerHTML strings inside VS Code webviews.
 *
 * This is the canonical implementation.  The inline copy embedded in each
 * webview <script> block must be kept identical to this function.
 *
 * Characters escaped: & < > " '
 */
export function escapeHtml(text: unknown): string {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
