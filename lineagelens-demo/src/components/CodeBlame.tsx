import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';

import { theme } from '../theme';

export type CodeLine = {
  text: string;
  /** When set, this line is AI-attributed and the gutter shows the tool. */
  ai?: { tool: string; date: string };
  /** Author shown by ordinary git blame. */
  author?: string;
};

/**
 * A code pane with two gutters: what `git blame` can tell you (an author) and
 * what it cannot (which AI wrote the line). The second gutter slides in, which
 * is the whole argument of the product made visual.
 */
export const CodeBlame: React.FC<{
  file: string;
  lines: CodeLine[];
  /** Frame at which the AI gutter begins revealing. Omit to never show it. */
  revealAiAt?: number;
  fontSize?: number;
}> = ({ file, lines, revealAiAt, fontSize = 24 }) => {
  const frame = useCurrentFrame();

  const aiProgress = revealAiAt === undefined
    ? 0
    : interpolate(frame - revealAiAt, [0, 14], [0, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });

  const authorWidth = Math.max(...lines.map((l) => (l.author ?? '').length), 6);

  return (
    <div
      style={{
        background: theme.bgTerminal,
        border: `1px solid ${theme.border}`,
        borderRadius: 14,
        overflow: 'hidden',
        fontFamily: theme.fonts.mono,
        boxShadow: '0 30px 80px rgba(0,0,0,0.45)',
      }}
    >
      <div
        style={{
          padding: '14px 22px',
          borderBottom: `1px solid ${theme.border}`,
          fontSize: 17,
          color: theme.colors.inkFaint,
        }}
      >
        {file}
      </div>

      <div style={{ padding: '22px 26px', fontSize, lineHeight: 1.62 }}>
        {lines.map((line, i) => {
          const isAi = Boolean(line.ai);
          return (
            <div key={`${i}-${line.text}`} style={{ display: 'flex', whiteSpace: 'pre' }}>
              <span style={{ color: theme.colors.inkFaint }}>
                {(line.author ?? '').padEnd(authorWidth)}
                {'  '}
              </span>

              <span
                style={{
                  color: isAi ? theme.colors.ai : theme.colors.inkFaint,
                  opacity: aiProgress,
                  width: `${22 * aiProgress}ch`,
                  overflow: 'hidden',
                  display: 'inline-block',
                }}
              >
                {isAi ? `${line.ai!.tool} ${line.ai!.date}` : '—'}
              </span>

              <span style={{ color: theme.colors.inkFaint }}>
                {String(i + 1).padStart(3)}
                {' │ '}
              </span>

              <span
                style={{
                  color: theme.colors.ink,
                  background: isAi
                    ? `rgba(255,181,71,${0.11 * aiProgress})`
                    : 'transparent',
                }}
              >
                {line.text}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
};
