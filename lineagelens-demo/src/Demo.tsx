import React from 'react';
import { AbsoluteFill, Series, useVideoConfig } from 'remotion';

import {
  CloseScene,
  HookScene,
  ProblemScene,
  RiskScene,
  ScanScene,
  UpgradeScene,
} from './scenes/scenes';
import { theme } from './theme';

// Scene durations in frames at 30fps. Kept in one table so the total runtime is
// readable at a glance and `DEMO_DURATION` can never drift from the Series.
export const SCENE_FRAMES = {
  hook: 165,
  problem: 135,
  scan: 300,
  risk: 270,
  upgrade: 165,
  close: 195,
} as const;

// Scenes are opaque full-frame fills, so overlapping them would hide frames
// rather than blend them. Cuts are hard, and each scene opens with its own
// staggered reveal, which is what carries the transition.
export const DEMO_DURATION = Object.values(SCENE_FRAMES).reduce((a, b) => a + b, 0);

// The scenes are laid out once, for a 1920x1080 surface, in absolute px. Rather
// than making every font size responsive, the whole surface is scaled to fit
// the composition — so the square social cut is the same frame, letterboxed,
// and there is only one layout that can ever go wrong.
const STAGE_WIDTH = 1920;
const STAGE_HEIGHT = 1080;

// Also used by the Hackathon cut (src/Hackathon.tsx) — one stage, two stories.
export const Stage: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { width, height } = useVideoConfig();
  const scale = Math.min(width / STAGE_WIDTH, height / STAGE_HEIGHT);

  return (
    <AbsoluteFill style={{ background: theme.bg, justifyContent: 'center', alignItems: 'center' }}>
      <div
        style={{
          width: STAGE_WIDTH,
          height: STAGE_HEIGHT,
          transform: `scale(${scale})`,
          position: 'relative',
          overflow: 'hidden',
          flexShrink: 0,
        }}
      >
        {children}
      </div>
    </AbsoluteFill>
  );
};

export const Demo: React.FC = () => (
  <Stage>
    <Series>
      <Series.Sequence durationInFrames={SCENE_FRAMES.hook}>
        <HookScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENE_FRAMES.problem}>
        <ProblemScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENE_FRAMES.scan}>
        <ScanScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENE_FRAMES.risk}>
        <RiskScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENE_FRAMES.upgrade}>
        <UpgradeScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENE_FRAMES.close}>
        <CloseScene />
      </Series.Sequence>
    </Series>
  </Stage>
);
