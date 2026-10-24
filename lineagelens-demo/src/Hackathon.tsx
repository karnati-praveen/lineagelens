import React from 'react';
import { Series } from 'remotion';

import { Stage } from './Demo';
import { HookScene } from './scenes/scenes';
import {
  CapsuleScene,
  CaptureProofScene,
  HackathonBackdrop,
  HackathonCloseScene,
  LlamaScene,
} from './scenes/hackathon-scenes';

// The hackathon (~2 min) cut. Scene durations in frames at 30fps, one table so
// the runtime is readable and HACKATHON_DURATION can never drift from the Series.
//
//   hook      reused from the marketing cut — the argument's entry point
//   llama     the claim, drawn as the traffic path (animated)
//   capture   R1 recording — a real Llama session being captured
//   review    R2 recording — receipts in the editor + the pre-commit gate
//   capsule   R3 recording + the verifier verdict
//   close     what it is, in one line
export const H_SCENE_FRAMES = {
  hook: 255, // 8.5s — trimmed before the marketing cut's subhead beats end
  llama: 285, // 9.5s — panels + env var + honest-boundary subhead
  capture: 1065, // 35.5s — real recording R1
  review: 915, // 30.5s — real recording R2
  capsule: 735, // 24.5s — recording R3 (11s) then verifier terminal (13.5s)
  close: 315, // 10.5s
} as const;

export const HACKATHON_DURATION = Object.values(H_SCENE_FRAMES).reduce((a, b) => a + b, 0);

export const Hackathon: React.FC = () => (
  <Stage>
    <HackathonBackdrop />
    <Series>
      <Series.Sequence durationInFrames={H_SCENE_FRAMES.hook}>
        <HookScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={H_SCENE_FRAMES.llama}>
        <LlamaScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={H_SCENE_FRAMES.capture}>
        <CaptureProofScene
          title="Llama writes the code. LineageLens writes the receipt."
          caption="Prompt, model, and applied status — captured as it happens, not reconstructed later."
          file="llama-capture.mp4"
          shot="R1 · RECORDINGS.md"
        />
      </Series.Sequence>
      <Series.Sequence durationInFrames={H_SCENE_FRAMES.review}>
        <CaptureProofScene
          title="Every AI edit carries its receipt into the editor."
          caption="Hover for the origin, mark the review state — and let the pre-commit gate flag what never got reviewed."
          file="ide-receipt.mp4"
          shot="R2 · RECORDINGS.md"
        />
      </Series.Sequence>
      <Series.Sequence durationInFrames={H_SCENE_FRAMES.capsule}>
        <CapsuleScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={H_SCENE_FRAMES.close}>
        <HackathonCloseScene />
      </Series.Sequence>
    </Series>
  </Stage>
);
