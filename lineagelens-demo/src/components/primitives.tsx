import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

import { theme } from '../theme';

/**
 * Fade + rise entrance. Every element in the demo uses this one easing so the
 * whole video feels like a single object rather than a pile of animations.
 */
export const Reveal: React.FC<{
  delay?: number;
  children: React.ReactNode;
  distance?: number;
  style?: React.CSSProperties;
}> = ({ delay = 0, distance = 18, children, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const progress = spring({
    frame: frame - delay,
    fps,
    config: { damping: 200, mass: 0.6 },
  });

  return (
    <div
      style={{
        opacity: progress,
        transform: `translateY(${(1 - progress) * distance}px)`,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

/** Small uppercase label above a headline. */
export const Kicker: React.FC<{ children: React.ReactNode; color?: string }> = ({
  children,
  color = theme.colors.accent,
}) => (
  <div
    style={{
      fontFamily: theme.fonts.mono,
      fontSize: 22,
      letterSpacing: 4,
      textTransform: 'uppercase',
      color,
    }}
  >
    {children}
  </div>
);

export const Headline: React.FC<{
  children: React.ReactNode;
  size?: number;
  color?: string;
}> = ({ children, size = 68, color = theme.colors.ink }) => (
  <div
    style={{
      fontFamily: theme.fonts.sans,
      fontSize: size,
      fontWeight: 700,
      lineHeight: 1.12,
      letterSpacing: -1.5,
      color,
    }}
  >
    {children}
  </div>
);

export const Subhead: React.FC<{ children: React.ReactNode; size?: number }> = ({
  children,
  size = 30,
}) => (
  <div
    style={{
      fontFamily: theme.fonts.sans,
      fontSize: size,
      lineHeight: 1.45,
      color: theme.colors.inkMuted,
      maxWidth: 1100,
    }}
  >
    {children}
  </div>
);

/**
 * A number that counts up to its value. Used only for figures that came from a
 * real scan — the count-up is presentation, the target is measured.
 */
export const CountUp: React.FC<{
  to: number;
  delay?: number;
  duration?: number;
  decimals?: number;
  suffix?: string;
  prefix?: string;
}> = ({ to, delay = 0, duration = 30, decimals = 0, suffix = '', prefix = '' }) => {
  const frame = useCurrentFrame();
  const value = interpolate(frame - delay, [0, duration], [0, to], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const shown = decimals > 0
    ? value.toFixed(decimals)
    : Math.round(value).toLocaleString('en-US');
  return <>{prefix}{shown}{suffix}</>;
};

/** Horizontal proportion bar. Fills to `percent` of its track. */
export const Bar: React.FC<{
  percent: number;
  delay?: number;
  color?: string;
  height?: number;
  duration?: number;
}> = ({ percent, delay = 0, color = theme.colors.ai, height = 14, duration = 34 }) => {
  const frame = useCurrentFrame();
  const width = interpolate(frame - delay, [0, duration], [0, percent], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <div
      style={{
        width: '100%',
        height,
        borderRadius: height,
        background: theme.colors.line,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          width: `${Math.max(width, 0.4)}%`,
          height: '100%',
          borderRadius: height,
          background: color,
        }}
      />
    </div>
  );
};

/** Bordered panel used for cards and callouts. */
export const Panel: React.FC<{
  children: React.ReactNode;
  accent?: string;
  style?: React.CSSProperties;
}> = ({ children, accent, style }) => (
  <div
    style={{
      background: theme.bgElevated,
      border: `1px solid ${accent ?? theme.border}`,
      borderLeft: accent ? `4px solid ${accent}` : `1px solid ${theme.border}`,
      borderRadius: 14,
      padding: '28px 34px',
      ...style,
    }}
  >
    {children}
  </div>
);

/** Full-frame scene background with consistent padding. */
export const Scene: React.FC<{
  children: React.ReactNode;
  justify?: React.CSSProperties['justifyContent'];
}> = ({ children, justify = 'center' }) => (
  <div
    style={{
      position: 'absolute',
      inset: 0,
      background: theme.bg,
      display: 'flex',
      flexDirection: 'column',
      justifyContent: justify,
      gap: 30,
      padding: '90px 110px',
    }}
  >
    {children}
  </div>
);
