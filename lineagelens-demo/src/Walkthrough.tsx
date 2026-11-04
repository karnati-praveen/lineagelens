import React from 'react';
import { Series } from 'remotion';

import { Stage } from './Demo';
import {
  CapsuleScene,
  CodeScene,
  ConnectScene,
  InstallScene,
  ReviewScene,
  WalkthroughCloseScene,
  WalkthroughHookScene,
} from './scenes/walkthrough-scenes';

// The product walkthrough (<2 min): what a user actually does, in five
// numbered steps. Fully animated — every screen is a mock built from the real
// product's commands and UI text, no recordings. Durations in frames at 30fps;
// one table so WALKTHROUGH_DURATION can never drift from the Series.
//
//   hook      why this video exists            7.0s
//   install   quickstart-lite.sh + /setup     17.0s
//   connect   /connect pairing → extension    18.0s
//   code      Llama tool-call → record        20.0s
//   review    receipt, review state, gate     18.0s
//   capsule   verifier → VALID / exit 0       15.0s
//   close     the whole loop, one line         8.0s
export const W_SCENE_FRAMES = {
  hook: 210,
  install: 510,
  connect: 540,
  code: 600,
  review: 540,
  capsule: 450,
  close: 240,
} as const;

export const WALKTHROUGH_DURATION = Object.values(W_SCENE_FRAMES).reduce((a, b) => a + b, 0);

export const Walkthrough: React.FC = () => (
  <Stage>
    <Series>
      <Series.Sequence durationInFrames={W_SCENE_FRAMES.hook}>
        <WalkthroughHookScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={W_SCENE_FRAMES.install}>
        <InstallScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={W_SCENE_FRAMES.connect}>
        <ConnectScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={W_SCENE_FRAMES.code}>
        <CodeScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={W_SCENE_FRAMES.review}>
        <ReviewScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={W_SCENE_FRAMES.capsule}>
        <CapsuleScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={W_SCENE_FRAMES.close}>
        <WalkthroughCloseScene />
      </Series.Sequence>
    </Series>
  </Stage>
);
