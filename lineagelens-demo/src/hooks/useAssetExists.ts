import React from 'react';
import { continueRender, delayRender, staticFile } from 'remotion';

/**
 * True when a file in public/ actually exists. Resolved before the frame renders
 * (delayRender), so a scene can swap a real recording for a styled placeholder
 * without a config flag: drop the file into public/, re-render, done.
 *
 * Any failure (404, fetch error, timeout) resolves false — the placeholder is
 * always the safe fallback, never a render crash.
 */
export const useAssetExists = (relativePath: string): boolean => {
  const [exists, setExists] = React.useState(false);
  const [handle] = React.useState(() =>
    delayRender(`checking asset ${relativePath}`, { timeoutInMilliseconds: 10_000 }),
  );

  React.useEffect(() => {
    let cancelled = false;
    fetch(staticFile(relativePath), { method: 'HEAD' })
      .then((res) => {
        if (!cancelled) {
          setExists(res.ok);
          continueRender(handle);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setExists(false);
          continueRender(handle);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [relativePath, handle]);

  return exists;
};
