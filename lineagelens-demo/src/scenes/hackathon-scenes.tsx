import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { Video } from '@remotion/media';

import { Terminal } from '../components/Terminal';
import { Kicker, Headline, Panel, Reveal, Scene, Subhead } from '../components/primitives';
import { theme } from '../theme';
import { useAssetExists } from '../hooks/useAssetExists';

// The hackathon cut's argument, in order:
//   1. git blame answers the wrong question (reused from the marketing cut)
//   2. Llama works out of the box — one env var, every OpenAI-compatible server
//   3. proof: a real Llama session captured with prompt, model, applied status
//   4. every AI edit lands with a receipt in the editor, and gates your commits
//   5. evidence leaves the building as an offline-verifiable capsule
//   6. close: what it is, in one line

/** A real screen recording embedded in the timeline — or a styled placeholder
 *  until the file lands in public/recordings/. Same timing either way. */
export const RecordingClip: React.FC<{
  /** File name inside public/recordings/. */
  file: string;
  /** What this clip shows, printed on the placeholder card. */
  label: string;
  /** Shot-list reference (RECORDINGS.md) printed on the placeholder card. */
  shot: string;
}> = ({ file, label, shot }) => {
  const url = `recordings/${file}`;
  const exists = useAssetExists(url);

  if (exists) {
    return (
      <div
        style={{
          borderRadius: 16,
          overflow: 'hidden',
          border: `1px solid ${theme.border}`,
          boxShadow: '0 40px 120px rgba(0,0,0,0.55)',
          background: theme.bgTerminal,
        }}
      >
        <Video src={url} style={{ display: 'block', width: '100%' }} muted />
      </div>
    );
  }

  return (
    <div
      style={{
        borderRadius: 16,
        border: `2px dashed ${theme.colors.line}`,
        background: theme.bgElevated,
        padding: '110px 70px',
        textAlign: 'center',
      }}
    >
      <div style={{ fontFamily: theme.fonts.mono, fontSize: 20, color: theme.colors.inkFaint, letterSpacing: 3 }}>
        REAL RECORDING — PENDING
      </div>
      <div style={{ fontFamily: theme.fonts.sans, fontSize: 40, fontWeight: 700, color: theme.colors.ink, marginTop: 22 }}>
        {label}
      </div>
      <div style={{ fontFamily: theme.fonts.mono, fontSize: 20, color: theme.colors.inkMuted, marginTop: 18 }}>
        drop <span style={{ color: theme.colors.accent }}>public/recordings/{file}</span> ({shot}) and re-render
      </div>
    </div>
  );
};

/** 2. The Llama claim, drawn as the traffic path it actually is. */
export const LlamaScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const servers = ['Llama API', 'vLLM', 'llama.cpp', 'Ollama'];
  const pulse = spring({ frame: frame - 40, fps, config: { damping: 200 } });

  return (
    <Scene>
      <Reveal>
        <Kicker color={theme.colors.ai}>works with llama out of the box</Kicker>
      </Reveal>
      <Reveal delay={6}>
        <Headline>
          One environment variable. Every Llama server.
        </Headline>
      </Reveal>

      <div style={{ display: 'flex', alignItems: 'center', gap: 34, marginTop: 26 }}>
        <Reveal delay={16}>
          <Panel accent={theme.colors.ai}>
            <div style={{ fontFamily: theme.fonts.mono, fontSize: 17, color: theme.colors.inkFaint, letterSpacing: 2 }}>
              ANY OPENAI-COMPATIBLE LLAMA ENDPOINT
            </div>
            {servers.map((s) => (
              <div key={s} style={{ fontFamily: theme.fonts.mono, fontSize: 26, color: theme.colors.ink, marginTop: 12 }}>
                <span style={{ color: theme.colors.ai }}>◈</span> {s}
              </div>
            ))}
          </Panel>
        </Reveal>

        <Reveal delay={26} style={{ flex: 1 }}>
          <FlowArrow label="chat/completions" />
        </Reveal>

        <Reveal delay={34}>
          <Panel accent={theme.colors.accent}>
            <div style={{ fontFamily: theme.fonts.mono, fontSize: 17, color: theme.colors.inkFaint, letterSpacing: 2 }}>
              LINEAGELENS PROXY :8788
            </div>
            {['parse tool_calls', 'bind prompt + model', 'track applied / rejected'].map((s) => (
              <div key={s} style={{ fontFamily: theme.fonts.mono, fontSize: 26, color: theme.colors.ink, marginTop: 12 }}>
                <span style={{ color: theme.colors.accent }}>→</span> {s}
              </div>
            ))}
          </Panel>
        </Reveal>

        <Reveal delay={42} style={{ flex: 1 }}>
          <FlowArrow label="provenance record" />
        </Reveal>

        <Reveal delay={50}>
          <Panel accent={theme.colors.ok} style={{ opacity: 0.35 + pulse * 0.65 }}>
            <div style={{ fontFamily: theme.fonts.mono, fontSize: 17, color: theme.colors.inkFaint, letterSpacing: 2 }}>
              CAPTURED
            </div>
            <div style={{ fontFamily: theme.fonts.mono, fontSize: 24, color: theme.colors.ink, marginTop: 14 }}>
              prompt ✓
            </div>
            <div style={{ fontFamily: theme.fonts.mono, fontSize: 24, color: theme.colors.ok, marginTop: 8 }}>
              model: meta-llama/Llama-3.3-70B ✓
            </div>
            <div style={{ fontFamily: theme.fonts.mono, fontSize: 24, color: theme.colors.ink, marginTop: 8 }}>
              edit applied ✓
            </div>
          </Panel>
        </Reveal>
      </div>

      <Reveal delay={62}>
        <div
          style={{
            fontFamily: theme.fonts.mono,
            fontSize: 28,
            color: theme.colors.ok,
            background: theme.bgTerminal,
            border: `1px solid ${theme.border}`,
            borderRadius: 10,
            padding: '18px 26px',
            display: 'inline-block',
          }}
        >
          export OPENAI_BASE_URL=http://localhost:8788/v1
        </div>
      </Reveal>
      <Reveal delay={72}>
        <Subhead size={26}>
          That's the whole setup. The proxy speaks the protocol Llama already serves — and nothing, no code
          and no prompts, leaves your network.
        </Subhead>
      </Reveal>
    </Scene>
  );
};

/** Animated connector between flow panels. */
const FlowArrow: React.FC<{ label: string }> = ({ label }) => {
  const frame = useCurrentFrame();
  const draw = interpolate(frame, [0, 14], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <div style={{ textAlign: 'center' }}>
      <div
        style={{
          height: 3,
          background: `linear-gradient(90deg, ${theme.colors.line}, ${theme.colors.accent})`,
          width: `${draw * 100}%`,
          margin: '0 auto 10px',
          borderRadius: 2,
        }}
      />
      <div style={{ fontFamily: theme.fonts.mono, fontSize: 17, color: theme.colors.inkFaint }}>{label}</div>
      <div style={{ fontSize: 22, color: theme.colors.accent, marginTop: 2 }}>▶</div>
    </div>
  );
};

/** 3. Proof scene — the real Llama capture recording, captioned. */
export const CaptureProofScene: React.FC<{ title: string; caption: string; file: string; shot: string }> = ({
  title,
  caption,
  file,
  shot,
}) => (
  <Scene justify="flex-start">
    <Reveal>
      <Kicker color={theme.colors.ai}>proof, not a mockup</Kicker>
    </Reveal>
    <Reveal delay={5}>
      <Headline size={52}>{title}</Headline>
    </Reveal>
    <Reveal delay={12} style={{ flex: 1, display: 'flex', alignItems: 'center' }}>
      <RecordingClip file={file} label={title} shot={shot} />
    </Reveal>
    <Reveal delay={20}>
      <Subhead size={27}>{caption}</Subhead>
    </Reveal>
  </Scene>
);

/** 5. The capsule scene: the recording, then the verifier verdict. */
export const CapsuleScene: React.FC = () => {
  const frame = useCurrentFrame();
  const clipPhase = frame < 330;

  return (
    <Scene justify="flex-start">
      <Reveal>
        <Kicker color={theme.colors.ok}>evidence that outlives the company</Kicker>
      </Reveal>
      <Reveal delay={5}>
        <Headline size={52}>Export the evidence. Verify it on a clean machine.</Headline>
      </Reveal>
      {clipPhase ? (
        <Reveal delay={12} style={{ flex: 1, display: 'flex', alignItems: 'center' }}>
          <RecordingClip
            file="capsule-verify.mp4"
            label="Capsule export → offline verification"
            shot="R3 · RECORDINGS.md"
          />
        </Reveal>
      ) : (
        <Reveal delay={0} style={{ flex: 1, display: 'flex', alignItems: 'center' }}>
          <Terminal
            command="lineagelens-verifier capsule.lllpkg"
            title="clean machine — offline"
            typeDelay={4}
            charsPerFrame={2.4}
            outputDelay={16}
            lineStagger={9}
            fontSize={27}
            output={[
              { text: 'manifest: 14 members — sha256 match', tone: 'muted' },
              { text: 'signature: Ed25519 — valid at generation time', tone: 'muted' },
              { text: 'key registry: embedded snapshot — key valid', tone: 'muted' },
              { text: 'record chain: 214 records — 3 validly redacted', tone: 'muted' },
              { text: '', tone: 'muted' },
              { text: 'capsule: VALID', tone: 'ok', bold: true },
              { text: 'exit 0', tone: 'ok' },
            ]}
          />
        </Reveal>
      )}
      <Reveal delay={20}>
        <Subhead size={27}>
          The capsule is signed, self-contained, and verifies with no server, no database, and no network.
        </Subhead>
      </Reveal>
    </Scene>
  );
};

/** 6. Close. */
export const HackathonCloseScene: React.FC = () => (
  <Scene>
    <Reveal>
      <Headline size={84}>LineageLens</Headline>
    </Reveal>
    <Reveal delay={10}>
      <Subhead size={34}>
        Provenance for AI-generated code. Self-hosted, MIT licensed — and it works with Llama out of the box.
      </Subhead>
    </Reveal>
    <Reveal delay={24} style={{ marginTop: 26 }}>
      <div
        style={{
          fontFamily: theme.fonts.mono,
          fontSize: 28,
          color: theme.colors.ok,
          background: theme.bgTerminal,
          border: `1px solid ${theme.border}`,
          borderRadius: 10,
          padding: '18px 26px',
          display: 'inline-block',
        }}
      >
        npx lineagelens scan .
      </div>
    </Reveal>
  </Scene>
);

/** Ambient wash behind every scene — a faint radial vignette so embedded
 *  screen recordings sit inside the same visual world as the animated panels. */
export const HackathonBackdrop: React.FC = () => (
  <AbsoluteFill
    style={{
      background: `radial-gradient(1200px 700px at 30% 20%, rgba(94,184,255,0.05), transparent 70%), radial-gradient(1000px 600px at 75% 80%, rgba(255,181,71,0.04), transparent 70%)`,
    }}
  />
);
