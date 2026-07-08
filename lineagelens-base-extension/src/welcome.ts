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
  'lineagelens.verifyEvidence',
  'lineagelens.exportCapsule',
  'lineagelens.exportPrSummary',
  'lineagelens.checkBeforeCommit',
  'lineagelens.showFileTimeline',
  'lineagelens.toggleFocusMode',
  'lineagelens.showModeInfo',
  'lineagelens.saveEmail',
]);

let welcomePanel: vscode.WebviewPanel | undefined;

/** Open (or reveal) the welcome panel. */
export function openWelcomePanel(context: vscode.ExtensionContext): void {
  if (welcomePanel) {
    welcomePanel.reveal(vscode.ViewColumn.One);
    return;
  }
  welcomePanel = vscode.window.createWebviewPanel(
    'lineagelens.welcome',
    'Welcome to LineageLens',
    vscode.ViewColumn.One,
    { enableScripts: true, retainContextWhenHidden: true },
  );
  welcomePanel.webview.html = renderHtml(welcomePanel.webview);
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

function renderHtml(webview: vscode.Webview): string {
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
    font-family: var(--vscode-font-family); color: var(--vscode-foreground);
    background:
      radial-gradient(120% 60% at 50% -10%, rgba(124,140,255,.14), transparent 60%),
      var(--vscode-editor-background);
    line-height: 1.5; padding: 0 0 60px;
  }
  .wrap { max-width: 860px; margin: 0 auto; padding: 0 24px; }

  /* Hero */
  .hero { text-align: center; padding: 48px 24px 28px; }
  .logo { font-size: 40px; }
  .hero h1 { font-size: 26px; font-weight: 700; margin: 10px 0 6px; }
  .hero .tag { font-size: 14px; color: var(--vscode-descriptionForeground); max-width: 560px; margin: 0 auto; }
  .hero .sub { font-size: 12.5px; color: var(--vscode-descriptionForeground); margin-top: 10px; }
  .cta { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; margin-top: 20px; }

  .btn {
    display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
    border: 1px solid var(--vscode-button-border, transparent); border-radius: 7px;
    padding: 8px 14px; font-size: 12.5px; font-family: inherit;
    background: var(--vscode-button-background); color: var(--vscode-button-foreground);
  }
  .btn:hover { background: var(--vscode-button-hoverBackground); }
  .btn.sec { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
  .btn.sec:hover { background: var(--vscode-button-secondaryHoverBackground); }
  .btn.sm { padding: 5px 10px; font-size: 11.5px; border-radius: 6px; }

  h2 { font-size: 17px; font-weight: 700; margin: 34px 0 4px; }
  h2 .em { color: var(--accent); }
  .lead { font-size: 12.5px; color: var(--vscode-descriptionForeground); margin-bottom: 14px; }

  /* Feature grid */
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
  .card {
    border: 1px solid var(--vscode-widget-border, rgba(255,255,255,.08)); border-radius: 10px;
    padding: 14px 15px; background: var(--vscode-editorWidget-background, rgba(255,255,255,.025));
  }
  .card .ico { font-size: 18px; }
  .card h3 { font-size: 13.5px; font-weight: 700; margin: 6px 0 5px; }
  .card p { font-size: 12px; color: var(--vscode-descriptionForeground); }
  .card ul { margin: 8px 0 10px 16px; font-size: 12px; color: var(--vscode-descriptionForeground); }
  .card li { margin-bottom: 3px; }
  .card .actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }

  /* Mode section */
  .modes { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  @media (max-width: 620px) { .modes { grid-template-columns: 1fr; } }
  .mode {
    border: 1px solid var(--vscode-widget-border, rgba(255,255,255,.08)); border-radius: 10px; padding: 16px;
  }
  .mode.easy { border-color: rgba(245,166,35,.4); }
  .mode.power { border-color: rgba(124,140,255,.5); background: rgba(124,140,255,.05); }
  .mode .pill { font-size: 11px; font-weight: 700; padding: 2px 9px; border-radius: 20px; }
  .mode.easy .pill { color: #f5a623; border: 1px solid rgba(245,166,35,.4); }
  .mode.power .pill { color: var(--accent); border: 1px solid rgba(124,140,255,.5); }
  .mode h3 { font-size: 14px; margin: 8px 0 6px; }
  .mode p { font-size: 12px; color: var(--vscode-descriptionForeground); }
  .mode ul { margin: 8px 0 0 16px; font-size: 12px; color: var(--vscode-descriptionForeground); }
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
      <button class="btn sec" data-act="settings">Open settings</button>
    </div>
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
      <button class="btn sec" data-cmd="lineagelens.showModeInfo">Check my current mode</button>
      <button class="btn sec" data-act="settings">Open settings</button>
    </div>

    <div class="note">Tip: the AI Captures panel has a one-click actions bar for Verify, Capsule, PR summary, Pre-commit, Timeline, Focus, and exports.</div>
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
