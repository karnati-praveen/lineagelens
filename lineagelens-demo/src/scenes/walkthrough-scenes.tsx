import React from 'react';
import { useCurrentFrame } from 'remotion';

import { CodeBlame } from '../components/CodeBlame';
import { Terminal, OutputLine } from '../components/Terminal';
import { Headline, Kicker, Panel, Reveal, Scene, Subhead } from '../components/primitives';
import { theme } from '../theme';

// The walkthrough's argument — "what a user actually does", in five numbered
// steps, every pixel animated (no screen recordings). Each UI mock quotes the
// real product: quickstart-lite.sh output, /setup fields, the /connect page's
// Generate Pairing Code flow, the extension's Connect to Server prompts, the
// dashboard record shape, and the verifier's verdict.

// The pairing code shown on screen. Valid Crockford-base32 format (the same
// alphabet the backend generates — no I/L/O/U).
const PAIRING_CODE = 'LL-4KQ7M-2XZ9P';
const MODEL = 'llama3.2';

/** Numbered chip shown on every step scene — the walkthrough's spine. */
export const StepBadge: React.FC<{ n: number; total: number; label: string }> = ({
  n,
  total,
  label,
}) => (
  <div
    style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 16,
      fontFamily: theme.fonts.mono,
      fontSize: 20,
      letterSpacing: 3,
      textTransform: 'uppercase',
      color: theme.colors.accent,
      border: `1px solid ${theme.colors.accent}`,
      borderRadius: 999,
      padding: '10px 22px',
    }}
  >
    <span style={{ color: theme.colors.ink }}>
      STEP {n} / {total}
    </span>
    <span style={{ opacity: 0.5 }}>·</span>
    <span>{label}</span>
  </div>
);

/** Text that types itself in. Used inside the UI mocks. */
const TypeValue: React.FC<{ text: string; start: number; speed?: number; color?: string }> = ({
  text,
  start,
  speed = 1.4,
  color = theme.colors.ink,
}) => {
  const frame = useCurrentFrame();
  const chars = Math.floor(Math.max(0, frame - start) * speed);
  const typing = chars < text.length;
  return (
    <span style={{ color }}>
      {text.slice(0, chars)}
      {typing && Math.floor(frame / 5) % 2 === 0 ? (
        <span style={{ opacity: 0.7 }}>▌</span>
      ) : null}
    </span>
  );
};

/** Card shell shared by the UI mocks. */
const Mock: React.FC<{
  title: string;
  children: React.ReactNode;
  accent?: string;
  style?: React.CSSProperties;
}> = ({ title, children, accent, style }) => (
  <div
    style={{
      background: theme.bgElevated,
      border: `1px solid ${accent ?? theme.border}`,
      borderRadius: 14,
      overflow: 'hidden',
      fontFamily: theme.fonts.mono,
      boxShadow: '0 30px 80px rgba(0,0,0,0.45)',
      ...style,
    }}
  >
    <div
      style={{
        padding: '12px 20px',
        borderBottom: `1px solid ${theme.border}`,
        fontSize: 16,
        letterSpacing: 2,
        color: theme.colors.inkFaint,
        background: 'rgba(255,255,255,0.02)',
      }}
    >
      {title}
    </div>
    <div style={{ padding: '24px 28px' }}>{children}</div>
  </div>
);

/** 0. The frame: what this video is. */
export const WalkthroughHookScene: React.FC = () => (
  <Scene>
    <Reveal>
      <Kicker color={theme.colors.accent}>product walkthrough</Kicker>
    </Reveal>
    <Reveal delay={6}>
      <Headline>Your team ships AI-written code every day.</Headline>
    </Reveal>
    <Reveal delay={16}>
      <Headline color={theme.colors.ai}>Here is what a user does about it.</Headline>
    </Reveal>
    <Reveal delay={30}>
      <Subhead size={28}>
        Five steps. Real commands, real screens — animated from the actual product.
      </Subhead>
    </Reveal>
  </Scene>
);

/** The first-run /setup form, fields typing themselves in. */
const SetupFormMock: React.FC = () => (
  <Mock title="http://localhost:8787/setup" accent={theme.colors.accent}>
    <div style={{ fontFamily: theme.fonts.sans, fontSize: 30, fontWeight: 700, color: theme.colors.ink }}>
      Welcome to LineageLens
    </div>
    <div style={{ fontFamily: theme.fonts.sans, fontSize: 19, color: theme.colors.inkMuted, marginTop: 6 }}>
      Create the first admin account — no curl commands needed.
    </div>
    <div style={{ marginTop: 22, display: 'flex', flexDirection: 'column', gap: 14, fontSize: 22 }}>
      <div>
        <span style={{ color: theme.colors.inkFaint }}>username&ensp;</span>
        <TypeValue text="praveen" start={100} />
      </div>
      <div>
        <span style={{ color: theme.colors.inkFaint }}>password&ensp;</span>
        <TypeValue text="••••••••••••" start={125} color={theme.colors.ink} />
      </div>
      <div>
        <span style={{ color: theme.colors.inkFaint }}>workspace&ensp;</span>
        <TypeValue text="My Workspace" start={150} />
      </div>
    </div>
    <div
      style={{
        marginTop: 24,
        display: 'inline-block',
        background: theme.colors.accent,
        color: '#04121f',
        fontFamily: theme.fonts.sans,
        fontSize: 22,
        fontWeight: 700,
        borderRadius: 10,
        padding: '12px 26px',
      }}
    >
      Create Admin Account
    </div>
    <div style={{ marginTop: 18, fontSize: 20, color: theme.colors.ok }}>
      <TypeValue text="✓ Admin account created — dashboard ready" start={195} speed={2.2} />
    </div>
  </Mock>
);

/** 1. Install: the real quickstart script, then the real setup wizard. */
export const InstallScene: React.FC = () => (
  <Scene>
    <StepBadge n={1} total={5} label="Install the server" />
    <Terminal
      command="bash lineagelens-scripts/quickstart-lite.sh"
      title="bash — single container · SQLite · zero deps"
      typeDelay={6}
      charsPerFrame={1.7}
      outputDelay={10}
      lineStagger={9}
      fontSize={23}
      output={[
        { text: '✓  Docker is ready.', tone: 'ok' },
        { text: '✓  JWT secrets generated', tone: 'ok' },
        { text: '✓  LineageLens Lite is ready!', tone: 'ok', bold: true },
        { text: '→  Open this URL in your browser:  http://localhost:8787/setup', tone: 'accent' },
      ]}
    />
    <Reveal delay={90} style={{ maxWidth: 860 }}>
      <SetupFormMock />
    </Reveal>
  </Scene>
);

/** 2. Connect: the /connect page's Generate Pairing Code flow, then the
 *  extension's Connect to Server wizard — the actual product UI text. */
export const ConnectScene: React.FC = () => (
  <Scene>
    <StepBadge n={2} total={5} label="Connect the editor" />
    <div style={{ display: 'flex', gap: 34, alignItems: 'stretch' }}>
      <Reveal delay={8} style={{ flex: 1 }}>
        <Mock title="localhost:8787/connect — admin" accent={theme.colors.accent}>
          <div style={{ fontFamily: theme.fonts.sans, fontSize: 30, fontWeight: 700, color: theme.colors.ink }}>
            Lineage<span style={{ color: theme.colors.accent }}>Lens</span> Connect
          </div>
          <div style={{ fontFamily: theme.fonts.sans, fontSize: 18, color: theme.colors.inkMuted, marginTop: 8, lineHeight: 1.4 }}>
            Generate a short-lived pairing code. The developer pastes this code into their
            VS Code extension to bind to this server automatically.
          </div>
          <div
            style={{
              marginTop: 20,
              display: 'inline-block',
              background: theme.colors.accent,
              color: '#04121f',
              fontFamily: theme.fonts.sans,
              fontSize: 21,
              fontWeight: 700,
              borderRadius: 10,
              padding: '11px 24px',
            }}
          >
            Generate Pairing Code
          </div>
          <div style={{ marginTop: 22 }}>
            <div style={{ fontSize: 44, fontWeight: 700, letterSpacing: 2, color: theme.colors.ai }}>
              <TypeValue text={PAIRING_CODE} start={70} speed={1.8} color={theme.colors.ai} />
            </div>
            <div style={{ fontSize: 17, color: theme.colors.inkFaint, marginTop: 8 }}>
              expires in 10:00 · single use
            </div>
          </div>
        </Mock>
      </Reveal>

      <Reveal delay={40} style={{ flex: 1 }}>
        <Mock title="vs code — command palette">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 21 }}>
            <div
              style={{
                background: 'rgba(94,184,255,0.12)',
                border: `1px solid ${theme.colors.accent}`,
                borderRadius: 8,
                padding: '10px 16px',
                color: theme.colors.ink,
              }}
            >
              &gt; LineageLens Base: Connect to Server
            </div>
            <div style={{ color: theme.colors.inkFaint, padding: '2px 16px' }}>
              Enter your LineageLens server URL
            </div>
            <div style={{ padding: '2px 16px' }}>
              <TypeValue text="http://localhost:8787" start={105} />
            </div>
            <div style={{ color: theme.colors.inkFaint, padding: '2px 16px', marginTop: 6 }}>
              Enter the Connect Code (format: LL-XXXXX-XXXXX)
            </div>
            <div style={{ padding: '2px 16px' }}>
              <TypeValue text={PAIRING_CODE} start={150} />
            </div>
            <div style={{ color: theme.colors.ok, padding: '8px 16px 0', fontSize: 20 }}>
              <TypeValue
                text={`✓ Connected to localhost:8787 — workspace "My Workspace"`}
                start={200}
                speed={2.2}
              />
            </div>
          </div>
        </Mock>
      </Reveal>
    </div>
  </Scene>
);

/** The dashboard record card a user sees after the capture lands. */
const RecordCardMock: React.FC = () => (
  <Mock title="localhost:8787/dashboard — provenance records" accent={theme.colors.ai}>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, fontSize: 22 }}>
      <div>
        <span style={{ color: theme.colors.inkFaint }}>file&emsp;&emsp;&emsp;</span>
        <span style={{ color: theme.colors.ink }}>utils/health.py</span>
      </div>
      <div>
        <span style={{ color: theme.colors.inkFaint }}>model&emsp;&emsp;</span>
        <span style={{ color: theme.colors.ai, fontWeight: 700 }}>{MODEL}</span>
      </div>
      <div>
        <span style={{ color: theme.colors.inkFaint }}>prompt&emsp;&ensp;</span>
        <span style={{ color: theme.colors.ink }}>
          "add a health() endpoint"
        </span>
      </div>
      <div>
        <span style={{ color: theme.colors.inkFaint }}>edit&emsp;&emsp;&ensp;</span>
        <span style={{ color: theme.colors.ok }}>applied ✓</span>
      </div>
      <div>
        <span style={{ color: theme.colors.inkFaint }}>confidence&ensp;</span>
        <span style={{ color: theme.colors.ink }}>0.80</span>
      </div>
    </div>
  </Mock>
);

/** 3. Code with AI: one env var, a Llama tool-call, the captured record. */
export const CodeScene: React.FC = () => (
  <Scene>
    <StepBadge n={3} total={5} label="Code with AI" />
    <div style={{ display: 'flex', gap: 34, alignItems: 'flex-start' }}>
      <div style={{ flex: 1.15 }}>
        <Terminal
          command={`export OPENAI_BASE_URL=http://localhost:8788/v1`}
          title="bash — any OpenAI-compatible Llama server"
          typeDelay={6}
          charsPerFrame={2.2}
          outputDelay={12}
          lineStagger={8}
          fontSize={21}
          output={[
            { text: `POST /v1/chat/completions  { "model": "${MODEL}" }`, tone: 'muted' },
            { text: 'response:', tone: 'faint' },
            { text: `  "tool_calls": [{ "function": {`, tone: 'muted' },
            { text: `    "name": "write_file",`, tone: 'ink' },
            { text: `    "arguments": { "path": "utils/health.py", ... } } }]`, tone: 'ink' },
            { text: `→ captured by the proxy — prompt, model, edit bound together`, tone: 'ai', bold: true },
          ]}
        />
      </div>
      <Reveal delay={110} style={{ flex: 1 }}>
        <RecordCardMock />
      </Reveal>
    </div>
  </Scene>
);

/** 4. Review: the receipt, the review state, the pre-commit gate. */
export const ReviewScene: React.FC = () => {
  const frame = useCurrentFrame();
  const reviewed = frame > 210;

  return (
    <Scene>
      <StepBadge n={4} total={5} label="Review what landed" />
      <CodeBlame
        file="utils/health.py"
        fontSize={23}
        revealAiAt={24}
        lines={[
          { text: 'def health() -> dict:', ai: { tool: MODEL, date: '2026-11-04' }, author: 'you' },
          { text: `    return {"status": "ok"}`, ai: { tool: MODEL, date: '2026-11-04' }, author: 'you' },
          { text: '', author: '' },
          { text: 'def log_health(result: dict) -> None:', author: 'you' },
          { text: '    logger.info("health: %s", result)', author: 'you' },
        ]}
      />
      <div style={{ display: 'flex', gap: 34, alignItems: 'stretch' }}>
        <Reveal delay={70} style={{ flex: 1 }}>
          <Mock title="AI receipt — hover the captured block">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 20 }}>
              <div>
                <span style={{ color: theme.colors.inkFaint }}>prompt&emsp;</span>
                <span style={{ color: theme.colors.ink }}>Create utils/health.py…</span>
              </div>
              <div>
                <span style={{ color: theme.colors.inkFaint }}>model&emsp;&ensp;</span>
                <span style={{ color: theme.colors.ai }}>{MODEL}</span>
              </div>
              <div>
                <span style={{ color: theme.colors.inkFaint }}>review&emsp;&ensp;</span>
                {reviewed ? (
                  <span style={{ color: theme.colors.ok, fontWeight: 700 }}>✓ reviewed</span>
                ) : (
                  <span style={{ color: theme.colors.risk }}>● needs changes</span>
                )}
              </div>
            </div>
          </Mock>
        </Reveal>
        <Reveal delay={280} style={{ flex: 1 }}>
          <Panel accent={theme.colors.risk} style={{ height: '100%' }}>
            <div style={{ fontFamily: theme.fonts.mono, fontSize: 17, color: theme.colors.inkFaint, letterSpacing: 2 }}>
              PRE-COMMIT GATE
            </div>
            <div style={{ fontFamily: theme.fonts.mono, fontSize: 21, marginTop: 14 }}>
              <div style={{ color: theme.colors.ok }}>✓ utils/health.py — reviewed · risk: low</div>
              <div style={{ color: theme.colors.risk, marginTop: 10 }}>
                ⚠ utils/billing.py — AI change unreviewed — COMMIT BLOCKED
              </div>
            </div>
          </Panel>
        </Reveal>
      </div>
    </Scene>
  );
};

/** 5. Export the proof: the capsule and the offline verifier. */
export const CapsuleScene: React.FC = () => (
  <Scene>
    <StepBadge n={5} total={5} label="Export the proof" />
    <Terminal
      command="lineagelens-verifier capsule.lllpkg"
      title="clean machine — offline"
      typeDelay={6}
      charsPerFrame={2.4}
      outputDelay={14}
      lineStagger={10}
      fontSize={25}
      output={[
        { text: 'manifest: 14 members — sha256 match', tone: 'muted' },
        { text: 'signature: Ed25519 — valid at generation time', tone: 'muted' },
        { text: 'key registry: embedded snapshot — key valid', tone: 'muted' },
        { text: 'record chain: 214 records — 3 validly redacted', tone: 'muted' },
        { text: '', tone: 'muted' },
        { text: 'capsule: VALID', tone: 'ok', bold: true },
        { text: 'exit 0', tone: 'ok' },
      ] as OutputLine[]}
    />
    <Reveal delay={140}>
      <Subhead size={27}>
        Signed, self-contained, verifiable with no server, no database, and no network — the evidence
        outlives the company.
      </Subhead>
    </Reveal>
  </Scene>
);

/** 6. Close. */
export const WalkthroughCloseScene: React.FC = () => (
  <Scene>
    <Reveal>
      <Headline size={72}>That's the whole loop.</Headline>
    </Reveal>
    <Reveal delay={12}>
      <Subhead size={32}>
        Install, connect, code with Llama, review what landed, export the proof.
      </Subhead>
    </Reveal>
    <Reveal delay={26} style={{ marginTop: 20 }}>
      <Headline size={56}>LineageLens</Headline>
    </Reveal>
    <Reveal delay={34}>
      <Subhead size={26}>
        Provenance for AI-generated code — self-hosted, MIT, works with Llama out of the box.
      </Subhead>
    </Reveal>
    <Reveal delay={48} style={{ marginTop: 16 }}>
      <div
        style={{
          fontFamily: theme.fonts.mono,
          fontSize: 27,
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
