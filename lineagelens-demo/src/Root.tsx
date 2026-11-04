import React from 'react';
import { Composition } from 'remotion';

import { DEMO_DURATION, Demo } from './Demo';
import { HACKATHON_DURATION, Hackathon } from './Hackathon';
import { WALKTHROUGH_DURATION, Walkthrough } from './Walkthrough';
import { FPS } from './theme';

/**
 * Four compositions, three stories:
 *   LineageLensDemo        16:9 41s   — README, docs, Product Hunt
 *   LineageLensDemoSquare  1:1  41s   — X, LinkedIn feeds
 *   HackathonDemo          16:9 ~2min — the pitch cut: Llama, live capture,
 *                                      capsule verification
 *   WalkthroughDemo        16:9 <2min — the product walkthrough: five numbered
 *                                      steps, fully animated, no recordings
 *
 * The square variant reuses the same components; the flex layouts reflow, so
 * there is only ever one story to keep correct.
 */
export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id="LineageLensDemo"
      component={Demo}
      durationInFrames={DEMO_DURATION}
      fps={FPS}
      width={1920}
      height={1080}
    />
    <Composition
      id="LineageLensDemoSquare"
      component={Demo}
      durationInFrames={DEMO_DURATION}
      fps={FPS}
      width={1080}
      height={1080}
    />
    <Composition
      id="HackathonDemo"
      component={Hackathon}
      durationInFrames={HACKATHON_DURATION}
      fps={FPS}
      width={1920}
      height={1080}
    />
    <Composition
      id="WalkthroughDemo"
      component={Walkthrough}
      durationInFrames={WALKTHROUGH_DURATION}
      fps={FPS}
      width={1920}
      height={1080}
    />
  </>
);
