import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';

import { theme } from '../theme';

/** A line of terminal output, revealed as a whole once its frame arrives. */
export type OutputLine = {
  text: string;
  /** Colour key; defaults to muted body text. */
  tone?: 'ink' | 'muted' | 'faint' | 'ai' | 'risk' | 'ok' | 'accent';
  bold?: boolean;
};

const toneColor: Record<NonNullable<OutputLine['tone']>, string> = {
  ink: theme.colors.ink,
  muted: theme.colors.inkMuted,
  faint: theme.colors.inkFaint,
  ai: theme.colors.ai,
  risk: theme.colors.risk,
  ok: theme.colors.ok,
  accent: theme.colors.accent,
};

/**
 * Terminal window that types a command, then reveals output lines on a stagger.
 *
 * Timings are in frames relative to the enclosing sequence so scenes stay
 * composable — a scene decides when its terminal starts, the terminal decides
 * nothing about the scene.
 */
export const Terminal: React.FC<{
  command: string;
  output: OutputLine[];
  /** Frames before the first character appears. */
  typeDelay?: number;
  /** Frames per typed character. */
  charsPerFrame?: number;
  /** Frames between the command finishing and output starting. */
  outputDelay?: number;
  /** Frames between successive output lines. */
  lineStagger?: number;
  fontSize?: number;
  title?: string;
}> = ({
  command,
  output,
  typeDelay = 8,
  charsPerFrame = 1.6,
  outputDelay = 12,
  lineStagger = 2.2,
  fontSize = 25,
  title = 'bash',
}) => {
  const frame = useCurrentFrame();

  const typedChars = Math.floor(Math.max(0, frame - typeDelay) * charsPerFrame);
  const typed = command.slice(0, typedChars);
  const doneTyping = typedChars >= command.length;
  const typingEndFrame = typeDelay + command.length / charsPerFrame;
  const outputStart = typingEndFrame + outputDelay;

  // Cursor blinks while typing, then disappears once output starts.
  const cursorVisible = !doneTyping || (frame < outputStart && Math.floor(frame / 8) % 2 === 0);

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
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '14px 20px',
          borderBottom: `1px solid ${theme.border}`,
          background: 'rgba(255,255,255,0.02)',
        }}
      >
        {['#ff5f57', '#febc2e', '#28c840'].map((c) => (
          <div key={c} style={{ width: 12, height: 12, borderRadius: 6, background: c }} />
        ))}
        <div style={{ marginLeft: 12, fontSize: 16, color: theme.colors.inkFaint }}>{title}</div>
      </div>

      <div style={{ padding: '26px 30px', fontSize, lineHeight: 1.55 }}>
        <div style={{ color: theme.colors.ink, whiteSpace: 'pre' }}>
          <span style={{ color: theme.colors.ok }}>$ </span>
          {typed}
          {cursorVisible ? (
            <span style={{ background: theme.colors.ink, color: theme.bgTerminal }}>&nbsp;</span>
          ) : null}
        </div>

        <div style={{ marginTop: 14 }}>
          {output.map((line, i) => {
            const appearAt = outputStart + i * lineStagger;
            const opacity = interpolate(frame - appearAt, [0, 3], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            });
            return (
              <div
                key={`${i}-${line.text}`}
                style={{
                  opacity,
                  whiteSpace: 'pre',
                  color: toneColor[line.tone ?? 'muted'],
                  fontWeight: line.bold ? 700 : 400,
                  minHeight: fontSize * 0.6,
                }}
              >
                {line.text}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
