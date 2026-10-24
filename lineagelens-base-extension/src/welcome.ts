/**
 * Welcome / getting-started webview.
 *
 * A single, polished panel that showcases every Base feature and explains how to
 * move from Easy Mode (local file-level capture) to Power Mode (full prompt +
 * model lineage via the LineageLens proxy). Opened on first run and via the
 * `LineageLens: Open Welcome` command.
 */

import * as vscode from 'vscode';

const PROXY_DOCS_URL = 'https://github.com/karnati-praveen/lineagelens#proxy';

// Commands the welcome page may invoke (allowlisted — never an arbitrary id).
const WELCOME_COMMANDS = new Set<string>([
  'lineagelens.captures.focus',
  'lineagelens.searchCaptures',
  'lineagelens.verifyEvidence',
  'lineagelens.exportCapsule',
  'lineagelens.exportPrSummary',
  'lineagelens.checkBeforeCommit',
  'lineagelens.showFileTimeline',
  'lineagelens.toggleFocusMode',
  'lineagelens.showModeInfo',
  'lineagelens.saveEmail',
  'lineagelensBase.connectToServer',
  'lineagelensBase.disconnectServer',
]);

let welcomePanel: vscode.WebviewPanel | undefined;

/** Live tour state, derived from the store + a persisted flag — no extra tracking. */
export interface WelcomeTour {
  captured: boolean;
  reviewed: boolean;
  verified: boolean;
}

/** Open (or reveal) the welcome panel. Pass the store so the tour reflects reality. */
export function openWelcomePanel(context: vscode.ExtensionContext, store?: { getAll(): { reviewState?: string }[] }): void {
  if (welcomePanel) {
    welcomePanel.reveal(vscode.ViewColumn.One);
    return;
  }
  const records = store?.getAll() ?? [];
  const tour: WelcomeTour = {
    captured: records.length > 0,
    reviewed: records.some(r => (r.reviewState ?? 'unreviewed') !== 'unreviewed'),
    verified: context.globalState.get<boolean>('lineagelens.tour.verified') ?? false,
  };
  welcomePanel = vscode.window.createWebviewPanel(
    'lineagelens.welcome',
    'Welcome to LineageLens',
    vscode.ViewColumn.One,
    { enableScripts: true, retainContextWhenHidden: true },
  );
  welcomePanel.webview.html = renderHtml(welcomePanel.webview, tour);
  welcomePanel.onDidDispose(() => { welcomePanel = undefined; });

  welcomePanel.webview.onDidReceiveMessage((msg: { type: string; command?: string }) => {
    switch (msg.type) {
      case 'cmd':
        if (msg.command && WELCOME_COMMANDS.has(msg.command)) {
          vscode.commands.executeCommand(msg.command);
        }
        break;
      case 'settings':
        vscode.commands.executeCommand('workbench.action.openSettings', 'lineagelensBase');
        break;
      case 'docs':
        vscode.env.openExternal(vscode.Uri.parse(PROXY_DOCS_URL));
        break;
    }
  }, undefined, context.subscriptions);
}

function getNonce(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let out = '';
  for (let i = 0; i < 24; i++) { out += chars.charAt(Math.floor(Math.random() * chars.length)); }
  return out;
}

function renderHtml(webview: vscode.Webview, tour: WelcomeTour = { captured: false, reviewed: false, verified: false }): string {
  const nonce = getNonce();
  const csp = [
    `default-src 'none'`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `script-src 'nonce-${nonce}'`,
  ].join('; ');

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<title>Welcome to LineageLens</title>
<style>
  :root { --accent: #7c8cff; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--vscode-font-family); color: #FFFFFF;
    background: #0A0A0A;
    line-height: 1.5; padding: 0 0 60px;
    min-height: 100vh;
  }
  .wrap { max-width: 860px; margin: 0 auto; padding: 0 32px; }

  /* Hero */
  .hero { text-align: center; padding: 64px 24px 32px; }
  .logo { font-size: 40px; }
  .hero h1 { font-size: 34px; font-weight: 700; letter-spacing: -.5px; margin: 12px 0 8px; color: #FFFFFF; }
  .hero .tag { font-size: 14px; color: #A1A1A1; max-width: 560px; margin: 0 auto; }
  .hero .sub { font-size: 12.5px; color: #A1A1A1; margin-top: 10px; }
  .cta { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; margin-top: 22px; }

  .btn {
    display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
    border: none; border-radius: 8px;
    padding: 10px 18px; font-size: 13px; font-weight: 600; font-family: inherit;
    color: #000000; background: #FFFFFF;
    transition: background .15s ease;
  }
  .btn:hover { background: #E8E8E8; }
  .btn.sec {
    background: transparent; color: #FFFFFF;
    border: 1px solid rgba(255,255,255,.16);
  }
  .btn.sec:hover { background: transparent; border-color: rgba(255,255,255,.32); }
  .btn.sm { padding: 5px 10px; font-size: 11.5px; border-radius: 6px; }

  /* 60-second tour — one quiet checklist, checks reflect live state. */
  .tour {
    max-width: 560px; margin: 26px auto 0; text-align: left;
    border: 1px solid rgba(255,255,255,.08); border-radius: 12px;
    background: rgba(255,255,255,.03); padding: 16px 18px;
  }
  .tour h2 { font-size: 13px; margin: 0 0 10px; }
  .tour ol { list-style: none; }
  .tour li { display: flex; align-items: center; gap: 10px; font-size: 12.5px; color: #A1A1A1; padding: 7px 0; border-top: 1px solid rgba(255,255,255,.06); }
  .tour li:first-child { border-top: none; }
  .tour .box {
    flex: none; width: 20px; height: 20px; border-radius: 50%;
    border: 1px solid rgba(255,255,255,.24);
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; color: #000000;
  }
  .tour li.done .box { background: #FFFFFF; border-color: #FFFFFF; }
  .tour li.done span.t { color: #FFFFFF; text-decoration: line-through; text-decoration-color: rgba(255,255,255,.4); }
  .tour li .sp { flex: 1; }
  .tour.done-all { border-color: rgba(255,255,255,.2); }

  h2 { font-size: 18px; font-weight: 700; margin: 36px 0 4px; color: #FFFFFF; }
  h2 .em { color: #FFFFFF; }
  .lead { font-size: 12.5px; color: #A1A1A1; margin-bottom: 14px; }

  /* Feature grid */
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
  .card {
    border: 1px solid rgba(255,255,255,.08); border-radius: 12px;
    padding: 16px; background: rgba(255,255,255,.03);
    transition: border-color .15s ease;
  }
  .card:hover { border-color: rgba(255,255,255,.16); }
  .card .ico { font-size: 18px; }
  .card h3 { font-size: 13.5px; font-weight: 700; margin: 8px 0 6px; color: #FFFFFF; }
  .card p { font-size: 12px; color: #A1A1A1; }
  .card ul { margin: 8px 0 10px 16px; font-size: 12px; color: #A1A1A1; }
  .card li { margin-bottom: 3px; }
  .card .actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }

  /* Mode section */
  .modes { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  @media (max-width: 620px) { .modes { grid-template-columns: 1fr; } }
  .mode {
    border: 1px solid rgba(255,255,255,.08); border-radius: 12px; padding: 18px;
    background: rgba(255,255,255,.03);
    transition: border-color .15s ease;
  }
  .mode:hover { border-color: rgba(255,255,255,.16); }
  .mode.easy { border-color: rgba(255,255,255,.08); }
  .mode.power { border-color: rgba(255,255,255,.14); background: rgba(255,255,255,.04); }
  .mode .pill { font-size: 11px; font-weight: 700; padding: 2px 9px; border-radius: 20px; color: #FFFFFF; border: 1px solid rgba(255,255,255,.16); }
  .mode.easy .pill { color: #FFFFFF; border: 1px solid rgba(255,255,255,.16); }
  .mode.power .pill { color: #FFFFFF; border: 1px solid rgba(255,255,255,.24); }
  .mode h3 { font-size: 14px; margin: 8px 0 6px; color: #FFFFFF; }
  .mode p { font-size: 12px; color: #A1A1A1; }
  .mode ul { margin: 8px 0 0 16px; font-size: 12px; color: #A1A1A1; }
  .mode li { margin-bottom: 4px; }

  .steps { counter-reset: step; margin: 14px 0 0; list-style: none; }
  .steps li {
    position: relative; padding: 6px 0 6px 34px; font-size: 12.5px;
    border-top: 1px solid var(--vscode-panel-border);
  }
  .steps li:first-child { border-top: none; }
  .steps li::before {
    counter-increment: step; content: counter(step);
    position: absolute; left: 0; top: 6px; width: 22px; height: 22px; border-radius: 50%;
    background: var(--accent); color: #fff; font-size: 11px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
  }
  code {
    font-family: var(--vscode-editor-font-family, monospace); font-size: 11.5px;
    background: var(--vscode-textCodeBlock-background, rgba(0,0,0,.2)); padding: 1px 6px; border-radius: 4px;
  }
  .note { font-size: 11.5px; color: var(--vscode-descriptionForeground); margin-top: 16px; text-align: center; }
  .divider { height: 1px; background: var(--vscode-panel-border); margin: 34px 0 0; }
</style>
</head>
<body>
  <div class="hero">
    <div class="logo">⚡</div>
    <h1>LineageLens Base</h1>
    <div class="tag">The IDE-native trust layer for AI-written code. Use any AI coding tool — LineageLens keeps the receipt.</div>
    <div class="sub">Everything works locally. No server, no account, no internet required.</div>
    <div class="cta">
      <button class="btn" data-cmd="lineagelens.captures.focus">Open AI Captures</button>
      <button class="btn sec" data-cmd="lineagelens.searchCaptures">Find a capture…</button>
      <button class="btn sec" data-act="settings">Open settings</button>
    </div>
  </div>

  <div class="tour${tour.captured && tour.reviewed && tour.verified ? ' done-all' : ''}">
    <h2>Get started in a minute</h2>
    <ol>
      <li class="${tour.captured ? 'done' : ''}">
        <span class="box">${tour.captured ? '✓' : ''}</span>
        <span class="sp"><span class="t">Capture your first AI block</span> — accept or paste 4+ lines of code</span>
        <span class="actions"><button class="btn sec sm" data-cmd="lineagelens.captures.focus">Open captures</button></span>
      </li>
      <li class="${tour.reviewed ? 'done' : ''}">
        <span class="box">${tour.reviewed ? '✓' : ''}</span>
        <span class="sp"><span class="t">Review one capture</span> — hover a card and press ✓, or open it</span>
        <span class="actions"><button class="btn sec sm" data-cmd="lineagelens.searchCaptures">Find a capture…</button></span>
      </li>
      <li class="${tour.verified ? 'done' : ''}">
        <span class="box">${tour.verified ? '✓' : ''}</span>
        <span class="sp"><span class="t">Verify your evidence chain</span> — one click, no server needed</span>
        <span class="actions"><button class="btn sec sm" data-cmd="lineagelens.verifyEvidence">Verify now</button></span>
      </li>
    </ol>
  </div>

  <div class="wrap">
    <h2><span class="em">See</span> AI code in your editor</h2>
    <div class="lead">As soon as you accept a multi-line AI insertion (4+ lines), it’s captured and shown where you code.</div>
    <div class="grid">
      <div class="card">
        <div class="ico">🔎</div>
        <h3>CodeLens, hover &amp; gutter</h3>
        <p>A label above each AI block, a hover receipt, and a subtle line tint — all follow the code as it moves (original / modified / moved).</p>
        <div class="actions"><button class="btn sec sm" data-cmd="lineagelens.toggleFocusMode">Toggle focus mode</button></div>
      </div>
      <div class="card">
        <div class="ico">🕓</div>
        <h3>Per-file AI timeline</h3>
        <p>See how AI code entered a file and what happened to it since — chronologically.</p>
        <div class="actions"><button class="btn sec sm" data-cmd="lineagelens.showFileTimeline">Show timeline</button></div>
      </div>
    </div>

    <h2><span class="em">Review</span> &amp; gate before commit</h2>
    <div class="lead">Teams buy workflow, not raw capture. Mark what you’ve checked and stop risky AI code before it ships.</div>
    <div class="grid">
      <div class="card">
        <div class="ico">✅</div>
        <h3>Review workflow</h3>
        <ul>
          <li>Mark reviewed / needs-changes / rejected + notes</li>
          <li>Risk-tailored review checklist</li>
        </ul>
        <p>Open any capture from the sidebar to review it.</p>
      </div>
      <div class="card">
        <div class="ico">⎇</div>
        <h3>Pre-commit &amp; PR summary</h3>
        <ul>
          <li>Warn on unreviewed / risky / drifted AI code in staged changes</li>
          <li>Deterministic Markdown PR summary</li>
        </ul>
        <div class="actions">
          <button class="btn sec sm" data-cmd="lineagelens.checkBeforeCommit">Check before commit</button>
          <button class="btn sec sm" data-cmd="lineagelens.exportPrSummary">PR summary</button>
        </div>
      </div>
    </div>

    <h2><span class="em">Prove</span> it — evidence you can verify</h2>
    <div class="lead">A tamper-evident record of what was captured, verifiable with no server.</div>
    <div class="grid">
      <div class="card">
        <div class="ico">🔐</div>
        <h3>Hash chain &amp; verifier</h3>
        <p>Each capture is sealed into a tamper-evident chain (with git branch/commit when available). Re-check it anytime.</p>
        <div class="actions"><button class="btn sec sm" data-cmd="lineagelens.verifyEvidence">Verify evidence</button></div>
      </div>
      <div class="card">
        <div class="ico">📦</div>
        <h3>Evidence capsule</h3>
        <p>Export a verifiable <code>.llcapsule</code> bundle — records, hashes, review state, and the verification verdict.</p>
        <div class="actions"><button class="btn sec sm" data-cmd="lineagelens.exportCapsule">Export capsule</button></div>
      </div>
    </div>

    <h2><span class="em">Catch</span> risk &amp; recall blast radius</h2>
    <div class="lead">Heuristic local signals (not a security scanner) and a way to find everywhere a suspect AI block landed.</div>
    <div class="grid">
      <div class="card">
        <div class="ico">⚠️</div>
        <h3>Local risk signals</h3>
        <p>Flags AI-generated auth, crypto, SQL, shell, eval, dependency, CI, infra, secret, and untested-logic code — shown in CodeLens, the receipt, and the gate.</p>
      </div>
      <div class="card">
        <div class="ico">⟲</div>
        <h3>Recall &amp; instruction influence</h3>
        <p>Find similar AI blocks across your captures, and see which project rules files (<code>.cursorrules</code>, <code>copilot-instructions.md</code>…) may have shaped a change.</p>
      </div>
    </div>

    <div class="divider"></div>

    <h2>Easy Mode → <span class="em">Power Mode</span></h2>
    <div class="lead">Base runs in Easy Mode out of the box. Start the LineageLens proxy to upgrade to full prompt + model lineage — no restart needed.</div>
    <div class="modes">
      <div class="mode easy">
        <span class="pill">⚡ Easy Mode · default</span>
        <h3>Local file-level capture</h3>
        <p>Zero setup, fully offline. LineageLens infers which code is AI-generated from editor activity.</p>
        <ul>
          <li>Which code is AI, when, and where</li>
          <li>Review state, risk signals, evidence chain</li>
          <li>Confidence is <em>inferred</em> (no prompt/model)</li>
        </ul>
      </div>
      <div class="mode power">
        <span class="pill">🛡 Power Mode</span>
        <h3>Full prompt + model lineage</h3>
        <p>Route your AI tool’s API traffic through the local LineageLens proxy. Captures gain the actual prompt, model, and response.</p>
        <ul>
          <li>Everything in Easy Mode, plus</li>
          <li>Prompt, model id, and response <em>observed</em></li>
          <li>Higher-confidence provenance</li>
        </ul>
      </div>
    </div>

    <h2>How to switch to Power Mode</h2>
    <ol class="steps">
      <li>Start the LineageLens proxy (it listens on <code>http://localhost:8788</code> by default).</li>
      <li>Point your AI tool / SDK at the proxy so its requests flow through it.</li>
      <li>LineageLens polls <code>proxyHealthUrl</code> every 30s and the status bar flips to <strong>🛡 LL: Power</strong> automatically — no reload.</li>
      <li>Optional: set <code>lineagelensBase.backendUrl</code> to sync captures to a LineageLens backend.</li>
    </ol>
    <div class="cta" style="justify-content:flex-start;margin-top:16px">
      <button class="btn" data-act="docs">How to start the proxy ↗</button>
      <button class="btn sec" data-cmd="lineagelensBase.connectToServer">Connect to Server</button>
      <button class="btn sec" data-cmd="lineagelens.showModeInfo">Check my current mode</button>
      <button class="btn sec" data-act="settings">Open settings</button>
    </div>

    <div class="note">Tip: press <code>Find a capture…</code> above (or run “LineageLens: Find a Capture…” from the command palette). In the sidebar search, <code>risk:high</code>, <code>review:unreviewed</code> and <code>source:ai</code> filter that field.</div>
  </div>

  <script nonce="${nonce}">
    (function () {
      const vscode = acquireVsCodeApi();
      document.querySelectorAll('[data-cmd]').forEach(function (b) {
        b.addEventListener('click', function () {
          vscode.postMessage({ type: 'cmd', command: b.getAttribute('data-cmd') });
        });
      });
      document.querySelectorAll('[data-act]').forEach(function (b) {
        b.addEventListener('click', function () {
          vscode.postMessage({ type: b.getAttribute('data-act') });
        });
      });
    })();
  </script>
</body>
</html>`;
}
