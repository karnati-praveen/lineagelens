import * as vscode from 'vscode';
import { CaptureRecord, ReviewState } from './store';
import { checklistFor } from './review/checklist';
import { InstructionFile } from './risk/instructionScan';

function sourceLabel(source: string | undefined): string {
  if (source === 'ai') { return 'AI generated'; }
  if (source === 'paste') { return 'Pasted'; }
  return 'Unknown origin';
}

function reviewLabel(state: ReviewState | undefined): string {
  switch (state) {
    case 'reviewed': return 'Reviewed';
    case 'needs_changes': return 'Needs changes';
    case 'rejected': return 'Rejected';
    case 'accepted': return 'Accepted';
    default: return 'Unreviewed';
  }
}

function esc(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function jsonString(value: unknown): string {
  return (JSON.stringify(value ?? '') as string).replace(/</g, '\\u003c');
}

function nonce(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let out = '';
  for (let i = 0; i < 24; i++) { out += chars.charAt(Math.floor(Math.random() * chars.length)); }
  return out;
}

function codeRows(code: string): string {
  const lines = String(code ?? '').split('\n');
  return lines.map((line, i) => `
    <tr>
      <td class="line-no">${i + 1}</td>
      <td class="line-code">${esc(line) || '&nbsp;'}</td>
    </tr>`).join('');
}

export function buildDetailPanel(
  panel: vscode.WebviewPanel,
  record: CaptureRecord,
  instructionFiles: InstructionFile[] = [],
): void {
  const n = nonce();
  const date = new Date(record.timestamp).toLocaleString();
  const reviewState = record.reviewState ?? 'unreviewed';
  const risks = record.riskSignals ?? [];
  const checklistCategory = risks[0]?.category ?? 'generic';
  const pct = Math.round((record.confidence ?? 0.5) * 100);
  const lang = record.language ?? 'text';
  const linesAdded = Number(record.linesAdded) || 0;
  const source = record.source ?? 'unknown';
  const highRisk = risks.some(r => r.severity === 'high');
  const riskTone = highRisk ? 'danger' : risks.length ? 'warn' : 'quiet';

  panel.webview.html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${n}'; style-src ${panel.webview.cspSource} 'unsafe-inline';">
<title>LineageLens Capture</title>
<style>
  :root {
    --bg: var(--vscode-editor-background, #151515);
    --panel: color-mix(in srgb, var(--vscode-editor-background, #151515) 86%, white 7%);
    --panel2: color-mix(in srgb, var(--vscode-editor-background, #151515) 78%, white 10%);
    --line: color-mix(in srgb, var(--vscode-foreground, #d4d4d4) 14%, transparent);
    --line2: color-mix(in srgb, var(--vscode-foreground, #d4d4d4) 9%, transparent);
    --text: var(--vscode-foreground, #e8e8e8);
    --muted: var(--vscode-descriptionForeground, #9a9a9a);
    --accent: #8b95ff;
    --green: #39d98a;
    --amber: #f5b342;
    --red: #ff6b6b;
    --code: var(--vscode-textCodeBlock-background, #101010);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    min-height: 100vh;
    font-family: var(--vscode-font-family);
    color: var(--text);
    background:
      radial-gradient(900px 360px at 4% -8%, rgba(139,149,255,.16), transparent 58%),
      radial-gradient(650px 320px at 96% 0%, rgba(57,217,138,.08), transparent 56%),
      var(--bg);
    line-height: 1.5;
  }
  button, textarea { font: inherit; }
  .shell { max-width: 1160px; margin: 0 auto; padding: 26px 28px 34px; }
  .hero {
    display: grid; grid-template-columns: minmax(0,1fr) auto; gap: 20px; align-items: start;
    padding: 24px; border: 1px solid var(--line); border-radius: 18px;
    background: linear-gradient(135deg, rgba(255,255,255,.07), rgba(255,255,255,.025));
    box-shadow: 0 22px 70px rgba(0,0,0,.22);
  }
  .eyebrow { color: var(--muted); font-size: 11px; font-weight: 750; letter-spacing: .11em; text-transform: uppercase; margin-bottom: 8px; }
  h1 { font-size: 24px; line-height: 1.16; font-weight: 760; letter-spacing: -.025em; overflow-wrap: anywhere; }
  .sub { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 13px; color: var(--muted); font-size: 12px; }
  .pill {
    display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line);
    border-radius: 999px; padding: 4px 9px; background: rgba(255,255,255,.035); color: var(--muted);
  }
  .pill.good { color: var(--green); border-color: rgba(57,217,138,.35); background: rgba(57,217,138,.08); }
  .pill.warn { color: var(--amber); border-color: rgba(245,179,66,.4); background: rgba(245,179,66,.09); }
  .pill.danger { color: var(--red); border-color: rgba(255,107,107,.42); background: rgba(255,107,107,.09); }
  .confidence { text-align: right; min-width: 130px; }
  .confidence .num { font-size: 34px; font-weight: 760; letter-spacing: -.04em; }
  .confidence .lbl { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }
  .bar { height: 5px; margin-top: 11px; border-radius: 999px; background: rgba(255,255,255,.08); overflow: hidden; }
  .bar span { display: block; height: 100%; width: ${pct}%; background: linear-gradient(90deg, var(--accent), var(--green)); }

  .toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 20px; align-items: center; }
  .btn {
    border: 1px solid var(--line); border-radius: 10px; padding: 8px 12px;
    color: var(--text); background: rgba(255,255,255,.045); cursor: pointer;
  }
  .btn.primary { border-color: rgba(139,149,255,.55); background: rgba(139,149,255,.18); color: #fff; }
  .btn.danger { color: var(--red); border-color: rgba(255,107,107,.4); background: rgba(255,107,107,.055); }
  .btn:hover { border-color: rgba(139,149,255,.62); background: rgba(139,149,255,.13); }
  .spacer { flex: 1; }
  .seg { display: inline-flex; overflow: hidden; border: 1px solid var(--line); border-radius: 11px; background: rgba(255,255,255,.035); }
  .seg button { border: 0; border-right: 1px solid var(--line); color: var(--muted); background: transparent; padding: 8px 11px; cursor: pointer; }
  .seg button:last-child { border-right: 0; }
  .seg button.active { color: #fff; background: rgba(139,149,255,.22); }
  .seg-label { align-self: center; color: var(--muted); font-size: 12px; margin-right: 2px; }

  .layout { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(320px, .7fr); gap: 18px; align-items: start; }
  .panel {
    border: 1px solid var(--line); border-radius: 16px; background: rgba(255,255,255,.035);
    overflow: hidden;
  }
  .panel-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 15px 16px; border-bottom: 1px solid var(--line2); }
  .panel-title { font-size: 12px; font-weight: 760; letter-spacing: .09em; text-transform: uppercase; color: var(--muted); }
  .panel-body { padding: 16px; }
  .facts { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; }
  .fact { padding: 13px; border: 1px solid var(--line2); border-radius: 13px; background: rgba(255,255,255,.027); }
  .fact.wide { grid-column: 1 / -1; }
  .label { color: var(--muted); font-size: 10.5px; font-weight: 730; letter-spacing: .08em; text-transform: uppercase; margin-bottom: 6px; }
  .value { font-size: 13px; font-weight: 650; overflow-wrap: anywhere; }
  .mono { font-family: var(--vscode-editor-font-family, monospace); font-size: 12px; color: color-mix(in srgb, var(--text) 82%, var(--muted)); }

  .risk-list { list-style: none; display: grid; gap: 9px; }
  .risk-item { border: 1px solid var(--line2); border-radius: 12px; padding: 11px; background: rgba(255,255,255,.025); }
  .risk-item.high { border-color: rgba(255,107,107,.35); background: rgba(255,107,107,.07); }
  .risk-item.medium { border-color: rgba(245,179,66,.35); background: rgba(245,179,66,.07); }
  .risk-top { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
  .risk-label { font-weight: 730; }
  .sev { margin-left: auto; font-size: 10px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
  .high .sev { color: var(--red); }
  .medium .sev { color: var(--amber); }
  .risk-msg { color: color-mix(in srgb, var(--text) 82%, var(--muted)); font-size: 12px; }

  .review-status { margin-left: auto; }
  .review-actions { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 12px; }
  .rbtn { border: 1px solid var(--line); border-radius: 999px; padding: 7px 10px; background: rgba(255,255,255,.035); color: var(--muted); cursor: pointer; }
  .rbtn.active { color: #fff; border-color: rgba(139,149,255,.55); background: rgba(139,149,255,.2); }
  .note { width: 100%; min-height: 86px; resize: vertical; border: 1px solid var(--line); border-radius: 12px; padding: 10px 11px; background: rgba(0,0,0,.16); color: var(--text); outline: none; }
  .note:focus { border-color: rgba(139,149,255,.6); }
  details { margin-top: 13px; color: var(--muted); }
  summary { cursor: pointer; }
  details ul { margin: 10px 0 0 18px; color: var(--text); }
  details li { margin: 6px 0; }

  .instr-list { list-style: none; display: grid; gap: 7px; }
  .instr-list li { display: flex; gap: 8px; align-items: baseline; }
  .tool { flex: none; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; color: var(--muted); font-size: 11px; }

  .code-panel { margin-top: 18px; }
  .code-wrap { overflow: auto; max-height: 58vh; background: var(--code); }
  table { width: 100%; border-collapse: collapse; font-family: var(--vscode-editor-font-family, monospace); font-size: 12.5px; line-height: 1.65; }
  td { vertical-align: top; }
  .line-no { width: 1%; min-width: 44px; padding: 0 12px; text-align: right; color: color-mix(in srgb, var(--muted) 65%, transparent); user-select: none; border-right: 1px solid var(--line2); background: rgba(255,255,255,.025); }
  .line-code { padding: 0 16px; white-space: pre; color: color-mix(in srgb, var(--text) 94%, white); }
  .id { margin-top: 10px; color: var(--muted); font-size: 11px; }

  @media (max-width: 820px) {
    .shell { padding: 18px 14px 26px; }
    .hero { grid-template-columns: 1fr; }
    .confidence { text-align: left; }
    .layout { grid-template-columns: 1fr; }
    .facts { grid-template-columns: 1fr; }
    .spacer { display: none; }
  }
</style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div>
        <div class="eyebrow">LineageLens capture</div>
        <h1>${esc(record.fileName)}</h1>
        <div class="sub">
          <span class="pill">${esc(date)}</span>
          <span class="pill ${reviewState === 'reviewed' || reviewState === 'accepted' ? 'good' : reviewState === 'needs_changes' || reviewState === 'rejected' ? 'danger' : ''}">${esc(reviewLabel(reviewState))}</span>
          <span class="pill ${riskTone}">${risks.length ? `${risks.length} risk signal${risks.length === 1 ? '' : 's'}` : 'No local risk signals'}</span>
        </div>
      </div>
      <div class="confidence" title="Heuristic confidence that this came from an AI-assisted insertion">
        <div class="num">${pct}%</div>
        <div class="lbl">confidence</div>
        <div class="bar"><span></span></div>
      </div>
    </section>

    <div class="toolbar">
      <button class="btn primary" id="insertBtn" type="button">Insert at cursor</button>
      <button class="btn" id="copyBtn" type="button">Copy code</button>
      <button class="btn" id="revealBtn" type="button">Reveal file</button>
      <button class="btn" id="recallBtn" type="button">Recall similar</button>
      <button class="btn danger" id="deleteBtn" type="button">Delete</button>
      <span class="spacer"></span>
      <span class="seg-label">Origin</span>
      <span class="seg" aria-label="Reclassify origin">
        <button data-src="ai" class="${source === 'ai' ? 'active' : ''}" type="button">AI</button>
        <button data-src="paste" class="${source === 'paste' ? 'active' : ''}" type="button">Paste</button>
        <button data-src="unknown" class="${source === 'unknown' ? 'active' : ''}" type="button">Unknown</button>
      </span>
    </div>

    <section class="layout">
      <div>
        <section class="panel">
          <div class="panel-head"><div class="panel-title">Evidence summary</div></div>
          <div class="panel-body facts">
            <div class="fact"><div class="label">Source</div><div class="value">${esc(sourceLabel(source))}</div></div>
            <div class="fact"><div class="label">Language</div><div class="value">${esc(lang)}</div></div>
            <div class="fact"><div class="label">Lines added</div><div class="value">+${linesAdded}</div></div>
            <div class="fact"><div class="label">Workspace</div><div class="value">${esc(record.workspaceFolder ?? 'Local workspace')}</div></div>
            <div class="fact wide"><div class="label">File path</div><div class="value mono">${esc(record.filePath)}</div></div>
          </div>
        </section>

        <section class="panel code-panel">
          <div class="panel-head"><div class="panel-title">Inserted code</div><span class="pill">${linesAdded} lines</span></div>
          <div class="code-wrap"><table><tbody>${codeRows(record.insertedCode ?? '')}</tbody></table></div>
        </section>
        <div class="id mono">ID: ${esc(record.id)}</div>
      </div>

      <aside>
        <section class="panel">
          <div class="panel-head"><div class="panel-title">Review</div><span class="pill review-status">${esc(reviewLabel(reviewState))}</span></div>
          <div class="panel-body">
            <div class="review-actions">
              <button class="rbtn ${reviewState === 'reviewed' ? 'active' : ''}" data-review="reviewed" type="button">Reviewed</button>
              <button class="rbtn ${reviewState === 'needs_changes' ? 'active' : ''}" data-review="needs_changes" type="button">Needs changes</button>
              <button class="rbtn ${reviewState === 'rejected' ? 'active' : ''}" data-review="rejected" type="button">Rejected</button>
              <button class="rbtn ${reviewState === 'unreviewed' ? 'active' : ''}" data-review="unreviewed" type="button">Reopen</button>
            </div>
            <textarea id="reviewNote" class="note" placeholder="Add a short review note...">${esc(record.reviewNote ?? '')}</textarea>
            <div style="margin-top:10px"><button class="btn" id="saveNote" type="button">Save note</button></div>
            <details>
              <summary>Review checklist${checklistCategory !== 'generic' ? ` - ${esc(checklistCategory)}` : ''}</summary>
              <ul>${checklistFor(checklistCategory).map(item => `<li>${esc(item)}</li>`).join('')}</ul>
            </details>
          </div>
        </section>

        <section class="panel" style="margin-top:18px">
          <div class="panel-head"><div class="panel-title">Local risk signals</div><span class="pill ${riskTone}">${risks.length || 0}</span></div>
          <div class="panel-body">
            ${risks.length ? `<ul class="risk-list">${risks.map(r => `<li class="risk-item ${esc(r.severity)}"><div class="risk-top"><span class="risk-label">${esc(r.label)}</span><span class="sev">${esc(r.severity)}</span></div><div class="risk-msg">${esc(r.message)}</div></li>`).join('')}</ul>` : '<div class="mono">No local heuristic risk signals were detected for this capture.</div>'}
          </div>
        </section>

        ${instructionFiles.length ? `<section class="panel" style="margin-top:18px"><div class="panel-head"><div class="panel-title">AI instructions</div></div><div class="panel-body"><ul class="instr-list">${instructionFiles.map(f => `<li><span class="tool">${esc(f.tool)}</span><span class="mono">${esc(f.path)}</span></li>`).join('')}</ul></div></section>` : ''}
      </aside>
    </section>
  </main>

  <script nonce="${n}">
    (function() {
      var vscode = acquireVsCodeApi();
      var CODE = ${jsonString(record.insertedCode)};
      function noteValue() {
        var note = document.getElementById('reviewNote');
        return note ? note.value : '';
      }
      function on(id, fn) {
        var node = document.getElementById(id);
        if (node) node.addEventListener('click', fn);
      }
      on('insertBtn', function() { vscode.postMessage({ type: 'insert' }); });
      on('revealBtn', function() { vscode.postMessage({ type: 'reveal' }); });
      on('recallBtn', function() { vscode.postMessage({ type: 'recall' }); });
      on('deleteBtn', function() { vscode.postMessage({ type: 'delete' }); });
      on('saveNote', function() { vscode.postMessage({ type: 'review', note: noteValue() }); });
      on('copyBtn', function() {
        var btn = document.getElementById('copyBtn');
        if (navigator.clipboard) {
          navigator.clipboard.writeText(CODE).then(function() {
            if (btn) btn.textContent = 'Copied';
            setTimeout(function() { if (btn) btn.textContent = 'Copy code'; }, 1400);
          }).catch(function() { vscode.postMessage({ type: 'copy' }); });
        } else {
          vscode.postMessage({ type: 'copy' });
        }
      });
      Array.prototype.forEach.call(document.querySelectorAll('.seg button'), function(btn) {
        btn.addEventListener('click', function() {
          vscode.postMessage({ type: 'reclassify', source: btn.getAttribute('data-src'), note: noteValue() });
        });
      });
      Array.prototype.forEach.call(document.querySelectorAll('.rbtn'), function(btn) {
        btn.addEventListener('click', function() {
          vscode.postMessage({ type: 'review', state: btn.getAttribute('data-review'), note: noteValue() });
        });
      });
    })();
  </script>
</body>
</html>`;
}
