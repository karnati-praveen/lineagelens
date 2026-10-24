// One source of truth for the demo's visual language.
//
// Colour carries meaning here and nothing else: amber means "AI wrote this",
// slate means "no AI signal" (never "human"), and red is reserved for risk
// surfaces. That mapping matches the CLI output, so a viewer who runs the tool
// after watching sees the same colours mean the same things.

export const theme = {
  bg: '#08131f',
  bgElevated: '#0e1f31',
  bgTerminal: '#040c15',
  border: '#1c3a52',
  colors: {
    ink: '#f2f7fb',
    inkMuted: '#8ba3ba',
    inkFaint: '#4d6478',
    ai: '#ffb547',
    aiDim: '#7a5a24',
    risk: '#ff5c5c',
    ok: '#4ade80',
    accent: '#5eb8ff',
    line: '#1b3448',
  },
  fonts: {
    mono: '"JetBrains Mono", "Cascadia Code", "SF Mono", Menlo, Consolas, monospace',
    sans: '"Inter", "SF Pro Display", "Segoe UI", system-ui, sans-serif',
  },
} as const;

export const FPS = 30;
