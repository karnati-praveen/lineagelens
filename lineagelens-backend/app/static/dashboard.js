'use strict';
let _tok = null;
let _ref = null;
let _user = null, _mode = 'plus', _rTimer = null;
let _featureIntegrity = false, _featureAiBom = false;

// ── Pagination state ──────────────────────────────────────────────────────────
window._currentPage = 0;
window._pageSize = 20;
window._totalRecords = 0;
window._activeFilters = {};

// ── API ──────────────────────────────────────────────────────────────────────
async function req(method, path, body, noRefresh) {
  const h = {'Content-Type':'application/json'};
  if (_tok) h['Authorization'] = `Bearer ${_tok}`;
  const res = await fetch(path, {method, headers: h, body: body ? JSON.stringify(body) : undefined});
  if (res.status === 401 && !noRefresh) {
    if (await tryRefresh()) return req(method, path, body, true);
    doLogout(); return null;
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) {
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || `HTTP ${res.status}`);
    return d;
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res;
}

let _refreshPromise = null;
async function tryRefresh() {
  if (!_ref) return false;
  if (_refreshPromise) return _refreshPromise;
  _refreshPromise = (async () => {
    try {
      const d = await req('POST', '/auth/refresh', {refreshToken: _ref}, true);
      if (!d) return false;
      setTok(d.accessToken, d.refreshToken, d.expiresInSeconds);
      return true;
    } catch { return false; }
    finally { _refreshPromise = null; }
  })();
  return _refreshPromise;
}

function setTok(tok, ref, exp) {
  _tok = tok; _ref = ref || _ref;
  clearTimeout(_rTimer);
  if (exp > 60) _rTimer = setTimeout(tryRefresh, (exp - 45) * 1000);
}

function clearTok() {
  _tok = null; _ref = null; _user = null;
  clearTimeout(_rTimer);
}

// ── AUTH VIEWS ────────────────────────────────────────────────────────────────
function showLogin(e) { e?.preventDefault(); show('v-login'); hide('v-reg'); hide('v-app'); }
function showReg(e) { e?.preventDefault(); hide('v-login'); show('v-reg'); hide('v-app'); }
function showApp() { hide('v-login'); hide('v-reg'); show('v-app'); }

function setAl(id, msg, type='err') {
  const el = document.getElementById(id);
  el.innerHTML = msg ? `<div class="al ${type}">${esc(msg)}</div>` : '';
}

async function doLogin() {
  setAl('al-login','');
  const ws=g('l-ws'), un=g('l-user'), pw=g('l-pass');
  // workspace_id is optional — the backend will infer it from the username
  // if not provided. Don't reject the form just because it's blank.
  if (!un||!pw) { setAl('al-login','Username and password required.'); return; }
  try {
    const body = ws ? {username:un, password:pw, workspaceId:ws} : {username:un, password:pw};
    const d = await req('POST', '/auth/login', body, true);
    if (!d.accessToken || !d.refreshToken || !d.user) { setAl('al-login', 'Invalid server response.'); return; }
    setTok(d.accessToken, d.refreshToken, d.expiresInSeconds);
    _user = d.user;
    await initApp();
  } catch(e) { setAl('al-login', e.message); }
}

async function doRegister() {
  setAl('al-reg','');
  const ws=g('r-ws'), un=g('r-user'), pw=g('r-pass');
  if (!ws||!un||!pw) { setAl('al-reg','All fields required.'); return; }
  try {
    const d = await req('POST', '/auth/register', {username:un, password:pw, workspaceId:ws}, true);
    if (!d.accessToken || !d.refreshToken || !d.user) { setAl('al-reg', 'Invalid server response.'); return; }
    setTok(d.accessToken, d.refreshToken, d.expiresInSeconds);
    _user = d.user;
    await initApp();
  } catch(e) { setAl('al-reg', e.message); }
}

async function doLogout() {
  try { await req('POST', '/auth/logout', {}); } catch {}
  if (_mcpTimer) { clearInterval(_mcpTimer); _mcpTimer = null; }
  if (_lastUpdatedTimer) { clearInterval(_lastUpdatedTimer); _lastUpdatedTimer = null; }
  if (_alertPoll) { clearInterval(_alertPoll); _alertPoll = null; }
  if (_asyncPollTimer) { clearInterval(_asyncPollTimer); _asyncPollTimer = null; }
  clearTok(); showLogin();
}

// ── INIT ─────────────────────────────────────────────────────────────────────
async function initApp() {
  if (_mcpTimer) { clearInterval(_mcpTimer); _mcpTimer = null; }
  if (_lastUpdatedTimer) { clearInterval(_lastUpdatedTimer); _lastUpdatedTimer = null; }
  if (_alertPoll) { clearInterval(_alertPoll); _alertPoll = null; }
  if (!_user) {
    try { _user = await req('GET', '/auth/me'); } catch { showLogin(); return; }
  }
  try {
    const h = await fetch('/health').then(r=>r.json());
    _mode = h.productMode || 'plus';
    const _feat = h.features || {};
    _featureIntegrity = _feat.provenanceIntegrity ?? (_mode === 'plus' || _mode === 'max');
    _featureAiBom = _feat.aiBomExport ?? (_mode === 'plus' || _mode === 'max');
  } catch {}

  document.getElementById('uname').textContent = _user.username;
  const rb = document.getElementById('role-bdg');
  rb.textContent = _user.role; rb.className = `role-badge${_user.role==='admin'?' adm':''}`;
  const mb = document.getElementById('mode-bdg');
  mb.textContent = _mode; mb.className = `tier-badge ${_mode}`;

  // Admin-only nav items
  if (_user.role === 'admin') {
    document.querySelectorAll('.adm-nav').forEach(t => t.style.display = 'flex');
    document.getElementById('invite-box').style.display = 'block';
    document.getElementById('policy-config-section').style.display = 'block';
    document.getElementById('alert-config-section').style.display = 'block';
  }

  // Tier-lock nav items that require plus or max
  document.querySelectorAll('.nav-item[data-requires="plus"]').forEach(t => {
    if (!hasTierAccess('plus')) t.classList.add('nav-locked');
  });
  document.querySelectorAll('.nav-item[data-requires="max"]').forEach(t => {
    if (!hasTierAccess('max')) t.classList.add('nav-locked');
  });

  showApp();
  go('dashboard');
  loadPolicies();
  loadAlertConfigs();
  checkMcp();
  _mcpTimer = setInterval(checkMcp, 60000);
  startAlertPoll();
  // Update "last updated" text every 30s
  _lastUpdatedTimer = setInterval(updateLastUpdatedText, 30000);
}

// ── TIER ACCESS ───────────────────────────────────────────────────────────────
function hasTierAccess(required) {
  if (!required) return true;
  if (required === 'admin') return _user?.role === 'admin';
  if (required === 'plus') return _mode === 'plus' || _mode === 'max';
  if (required === 'max') return _mode === 'max';
  return true;
}

const _VIEW_LABELS = {
  dashboard: 'Overview', timeline: 'Timeline', graph: 'Graph', alerts: 'Live Feed',
  search: 'Search', record: 'Record Viewer', reviews: 'Reviews', quality: 'Quality',
  developers: 'Developer Activity', team: 'Team', workspace: 'Workspace',
  github: 'GitHub CI', mcp: 'MCP Server', export: 'Export', 'scheduled-reports': 'Digests',
  sso: 'SSO / OIDC', routing: 'Model Routing'
};

function _setTopbarBreadcrumb(name) {
  const el = document.getElementById('topbar-breadcrumb');
  if (el) el.textContent = _VIEW_LABELS[name] || name;
}

function showUpgradePanel(name, required) {
  document.querySelectorAll('.nav-item').forEach(t => t.classList.toggle('on', t.dataset.t === name));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('on', v.id === 'upgrade-panel'));
  const tierNames = { plus: 'Plus', max: 'Max', admin: 'Admin' };
  const viewLabel = _VIEW_LABELS[name] || name;
  const tierName = tierNames[required] || required;
  const titleEl = document.getElementById('upgrade-title');
  const bodyEl  = document.getElementById('upgrade-body');
  const badgeEl = document.getElementById('upgrade-tier-needed');
  if (titleEl) titleEl.textContent = viewLabel;
  if (bodyEl)  bodyEl.textContent  = `${viewLabel} requires LineageLens ${tierName} or higher. Contact your admin to upgrade.`;
  if (badgeEl) {
    badgeEl.textContent  = tierName;
    badgeEl.className    = `tier-badge ${required}`;
  }
  _setTopbarBreadcrumb(name);
}

// ── TABS / NAV ────────────────────────────────────────────────────────────────
function go(name) {
  document.querySelectorAll('.nav-item').forEach(t => t.classList.toggle('on', t.dataset.t === name));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('on', v.id === `t-${name}`));
  _setTopbarBreadcrumb(name);
  if (name==='dashboard') loadDash();
  if (name==='timeline') loadTimeline();
  if (name==='graph') loadGraph();
  if (name==='developers') loadDeveloperActivity();
  if (name==='reviews') loadReviews();
  if (name==='alerts') { window._currentPage = 0; clearAlertBadge(); loadAlerts(); }
  if (name==='team') loadTeam();
  if (name==='record') loadRec();
  if (name==='quality') { loadQuality(); loadLlmStatus(); }
  if (name==='search') { window._currentPage = 0; document.getElementById('s-kw').focus(); }
  if (name==='github') loadGithubConfig();
  if (name==='mcp') loadMcp();
  if (name==='scheduled-reports') loadDigests();
  if (name==='sso') loadSsoProviders();
  if (name==='workspace') loadWorkspace();
  if (name==='routing') loadRouting();
}

// ── DATA SOURCE BADGE ────────────────────────────────────────────────────────
function setDataSourceBadge(status) {
  const el = document.getElementById('data-source-badge');
  if (!el) return;
  const labels = { backend: 'Backend', cached: 'Cached', unavailable: 'Unavailable' };
  const label = labels[status] || 'Unavailable';
  el.innerHTML = `<span class="ds-badge ${status}">${label}</span>`;
  if (status === 'backend') {
    window._lastUpdated = Date.now();
    updateLastUpdatedText();
  }
}

function updateLastUpdatedText() {
  const el = document.getElementById('ds-last-updated');
  if (!el || !window._lastUpdated) return;
  const secs = Math.round((Date.now() - window._lastUpdated) / 1000);
  if (secs < 60) {
    el.textContent = `Last updated: ${secs} second${secs === 1 ? '' : 's'} ago`;
  } else {
    const mins = Math.round(secs / 60);
    el.textContent = `Last updated: ${mins} minute${mins === 1 ? '' : 's'} ago`;
  }
}

// ── DASHBOARD ─────────────────────────────────────────────────────────────────
let _riskTrendChart = null;
let _modelUsageChart = null;

async function loadDash() {
  const el = document.getElementById('dash-body');
  el.innerHTML = '<div class="loading"><div class="spin" aria-hidden="true"></div> Loading…</div>';
  try {
    const body = {workspaceId: _user?.workspaceId};
    const df = g('d-from'), dt = g('d-to');
    if (df) body.dateFrom = new Date(df).toISOString();
    if (dt) body.dateTo = new Date(dt+'T23:59:59').toISOString();
    const d = await req('POST', '/insights/dashboard', body);
    el.innerHTML = buildDash(d);
    setDataSourceBadge('backend');
    // Load supplemental analytics in parallel
    loadRiskTrend();
    loadModelUsage();
    loadTokenCost();
    loadRoutingSavings();
    if (_featureIntegrity) loadIntegrityCard();
  } catch(e) {
    el.innerHTML = `<div class="al err">Dashboard error: ${esc(e.message)}</div>`;
    setDataSourceBadge('unavailable');
  }
}

async function loadRiskTrend() {
  const section = document.getElementById('risk-trend-section');
  const placeholder = document.getElementById('risk-trend-placeholder');
  const canvas = document.getElementById('riskTrendChart');
  if (!section) return;
  section.style.display = 'block';
  placeholder.style.display = 'none';
  canvas.style.display = 'block';
  try {
    const now = new Date();
    const from = new Date(now); from.setDate(from.getDate() - 30);
    const body = {
      workspaceId: _user?.workspaceId,
      dateFrom: from.toISOString(),
      dateTo: now.toISOString(),
      bucket: 'day'
    };
    const d = await req('POST', '/analytics/risk-trend', body);
    // Backend returns { results: [...], bucket } — each item has { period, critical, high, medium, low }
    const buckets = d.results || d.buckets || d.data || [];
    if (!buckets.length) { canvas.style.display = 'none'; placeholder.style.display = 'block'; return; }
    const labels = buckets.map(b => b.period ? new Date(b.period).toLocaleDateString() : (b.date || b.bucket || b.label || ''));
    const textColor = _dark ? '#94a3b8' : '#4a5568';
    const gridColor = _dark ? '#2d4a6a' : '#cbd5e0';
    const ctx = canvas.getContext('2d');
    if (_riskTrendChart) { _riskTrendChart.destroy(); }
    _riskTrendChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Critical', data: buckets.map(b => b.critical || 0), borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,.1)', borderWidth: 2, pointRadius: 2, tension: 0.3, fill: true },
          { label: 'High',     data: buckets.map(b => b.high || 0),     borderColor: '#f97316', backgroundColor: 'rgba(249,115,22,.1)', borderWidth: 2, pointRadius: 2, tension: 0.3, fill: true },
          { label: 'Medium',   data: buckets.map(b => b.medium || 0),   borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,.1)', borderWidth: 2, pointRadius: 2, tension: 0.3, fill: true },
          { label: 'Low',      data: buckets.map(b => b.low || 0),      borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,.1)',  borderWidth: 2, pointRadius: 2, tension: 0.3, fill: true },
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { color: textColor, font: { size: 12 } } } },
        scales: {
          x: { ticks: { color: textColor, maxRotation: 45 }, grid: { color: gridColor } },
          y: { beginAtZero: true, ticks: { color: textColor }, grid: { color: gridColor } }
        }
      }
    });
  } catch(e) {
    // 404 = not yet deployed; any error → placeholder
    canvas.style.display = 'none';
    placeholder.style.display = 'block';
  }
}

async function loadModelUsage() {
  const section = document.getElementById('model-usage-section');
  const placeholder = document.getElementById('model-usage-placeholder');
  const canvas = document.getElementById('modelUsageChart');
  if (!section) return;
  section.style.display = 'block';
  placeholder.style.display = 'none';
  canvas.style.display = 'block';
  try {
    const now = new Date();
    const from = new Date(now); from.setDate(from.getDate() - 30);
    const body = {
      workspaceId: _user?.workspaceId,
      dateFrom: from.toISOString(),
      dateTo: now.toISOString()
    };
    const d = await req('POST', '/analytics/model-usage', body);
    // Backend returns { results: [...] } — each item has { model_name, record_count, avg_risk_score }
    const rows = d.results || d.models || d.data || [];
    if (!rows.length) { canvas.style.display = 'none'; placeholder.style.display = 'block'; return; }
    const labels = rows.map(r => r.model_name || r.model || '');
    const counts = rows.map(r => r.record_count || r.count || 0);
    const avgRisks = rows.map(r => r.avg_risk_score != null ? Number(r.avg_risk_score).toFixed(3) : '—');
    const textColor = _dark ? '#94a3b8' : '#4a5568';
    const gridColor = _dark ? '#2d4a6a' : '#cbd5e0';
    const ctx = canvas.getContext('2d');
    if (_modelUsageChart) { _modelUsageChart.destroy(); }
    _modelUsageChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Records',
          data: counts,
          backgroundColor: 'rgba(59,130,246,.55)',
          borderColor: '#3b82f6',
          borderWidth: 1
        }]
      },
      options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: textColor, font: { size: 12 } } },
          tooltip: {
            callbacks: {
              afterLabel: (ctx) => {
                const ar = avgRisks[ctx.dataIndex];
                return ar !== '—' ? `Avg risk: ${ar}` : '';
              }
            }
          }
        },
        scales: {
          x: { beginAtZero: true, ticks: { color: textColor }, grid: { color: gridColor } },
          y: { ticks: { color: textColor }, grid: { color: gridColor } }
        }
      }
    });
    // Populate model autocomplete datalist
    const dl = document.getElementById('model-datalist');
    if (dl) {
      dl.innerHTML = labels.map(l => `<option value="${esc(l)}">`).join('');
    }
  } catch(e) {
    canvas.style.display = 'none';
    placeholder.style.display = 'block';
  }
}

async function loadTokenCost() {
  // Token/cost card is injected into dash-body by buildDash; here we POST to analytics endpoint
  // and update the placeholder values if the cards exist.
  try {
    const body = { workspaceId: _user?.workspaceId };
    const d = await req('POST', '/analytics/token-cost', body);
    const tokEl = document.getElementById('totalTokens');
    const costEl = document.getElementById('totalCostUsd');
    if (tokEl && d.total_tokens != null) {
      tokEl.textContent = Number(d.total_tokens).toLocaleString();
    }
    if (costEl && d.total_cost_usd != null) {
      costEl.textContent = `$${Number(d.total_cost_usd).toFixed(2)}`;
    }
  } catch {
    // 404 or error — leave placeholder dashes
  }
}

async function loadRoutingSavings() {
  // Fetch 30-day routing savings estimate from the analytics endpoint.
  try {
    const d = await req('GET', '/analytics/routing-savings');
    const el = document.getElementById('routingSavings');
    const sub = document.getElementById('routingSavingsSub');
    if (el && d.savings_usd_30d != null) {
      el.textContent = `$${Number(d.savings_usd_30d).toFixed(4)}`;
    }
    if (sub && d.routed_requests_30d != null) {
      sub.textContent = `${Number(d.routed_requests_30d).toLocaleString()} routed requests`;
    }
  } catch {
    // 404 or not available — leave placeholder dashes
  }
}

function buildDash(d) {
  // Backend returns d.summary (not d.governanceSummary)
  const gs = d.summary || d.governanceSummary || {};
  const pct = v => {
    if (v == null) return '—';
    const n = typeof v === 'number' && v <= 1 ? v * 100 : Number(v);
    return n.toFixed(1) + '%';
  };
  const num = v => v==null ? '—' : Number(v).toLocaleString();
  const rCls = v => {
    const n = Number.parseFloat(v);
    if (n >= .7) { return 'danger'; }
    if (n >= .4) { return 'warn'; }
    return 'ok';
  };

  // summary fields: totalRecords, promptCaptureRate, avgRiskScore,
  //   highRiskRecords (count), criticalRecords, uniqueFiles, uniqueModels,
  //   totalNetAddedLines, uniqueAgentSessions
  const highRiskCount = gs.highRiskRecords ?? gs.highRiskCount ?? 0;
  const criticalCount = gs.criticalRecords ?? gs.criticalCount ?? 0;

  let h = `<div class="mgrid">
    ${mc('Total Records', num(gs.totalRecords), 'accent')}
    ${mc('Prompt Capture', pct(gs.promptCaptureRate), (gs.promptCaptureRate||0)<.5?'warn':'ok')}
    ${mc('Avg Risk', pct(gs.avgRiskScore), rCls(gs.avgRiskScore))}
    ${mc('High Risk', num(highRiskCount), highRiskCount>0?'danger':'ok')}
    ${mc('Critical', num(criticalCount), criticalCount>0?'danger':'ok')}
    ${mc('Files', num(gs.uniqueFiles), '')}
    ${mc('Models', num(gs.uniqueModels), '')}
    ${mc('AI Lines Added', num(gs.totalNetAddedLines ?? gs.aiNetLinesAdded), 'accent')}
    ${mc('Team Members', num(gs.teamMemberCount ?? (d.memberStats?.length)), '')}
    ${mc('Agent Sessions', num(gs.uniqueAgentSessions ?? gs.agentSessionCount), '')}
    <div class="mcard"><div class="mlabel">Total Tokens</div><div class="mval accent" id="totalTokens">—</div><div class="msublabel" id="totalCostUsd"></div></div>
    <div class="mcard"><div class="mlabel">AI Cost Saved by Routing (30d)</div><div class="mval accent" id="routingSavings">—</div><div class="msublabel" id="routingSavingsSub">model routing savings estimate</div></div>
  </div>`;

  if (d.complianceControls?.length) {
    h += '<div class="cgrid">' + d.complianceControls.map(c => {
      let dc = 'fail';
      if (c.status === 'pass') { dc = 'ok'; }
      // Backend uses "warning" (not "warn")
      else if (c.status === 'warn' || c.status === 'warning') { dc = 'warn'; }
      // Use c.title (service key) with fallback to c.name
      return `<div class="ccard"><div class="cname"><span class="dot ${dc}"></span>${esc(c.title || c.name || '')}</div>
        <div class="cval">${esc(String(c.metric??''))}</div>
        <div class="csub">${esc(c.summary||'')}</div></div>`;
    }).join('') + '</div>';
  }

  if (d.highRiskRecords?.length) {
    h += tw('High-Risk Records', `<table><thead><tr><th>File</th><th>Risk</th><th>Model</th><th>Timestamp</th><th>Tool</th><th></th></tr></thead><tbody>` +
      d.highRiskRecords.map(r => {
        // Preview records from _top_high_risk_previews use riskLevel (not riskAssessment.level)
        const lvl = (r.riskLevel || ((r.riskAssessment||{}).level) || '').toLowerCase();
        const tool = r.toolName || ((r.normalizedEvent||{}).source||{}).toolName || '';
        const model = r.model || r.modelName || '';
        return `<tr><td><code>${esc(r.filePath||'')}</code></td>
          <td>${lvl?`<span class="rsk ${lvl}">${esc(lvl)}</span>`:''}</td>
          <td>${esc(model)}</td><td>${fd(r.timestampIso)}</td>
          <td>${esc(tool)}</td>
          <td><button class="s btn-sm" data-action="open-rec" data-uuid="${esc(r.uuid||'')}">View</button></td></tr>`;
      }).join('') + '</tbody></table>');
  }

  // Backend returns d.hotspots (not d.fileHotspots)
  // hotspot items: { filePath, recordCount, highRiskCount, avgRiskScore, netLinesAdded, latestTimestampIso }
  const hotspots = d.fileHotspots || d.hotspots || [];
  if (hotspots.length) {
    h += tw('File Hotspots', `<table><thead><tr><th>File</th><th>Records</th><th>High Risk</th><th>Avg Risk</th><th>Last AI Change</th></tr></thead><tbody>` +
      hotspots.map(f => `<tr>
        <td><code>${esc(f.filePath || f.fileName || '')}</code></td><td>${num(f.recordCount)}</td>
        <td>${(f.highRiskCount||0)>0?`<span class="rsk high">${num(f.highRiskCount)}</span>`:num(f.highRiskCount||0)}</td>
        <td>${pct(f.avgRiskScore ?? f.avgRisk)}</td><td>${fd(f.latestTimestampIso || f.lastAiChangeDate)}</td></tr>`).join('') + '</tbody></table>');
  }

  // modelAnalytics items: { model, recordCount, promptCaptureRate, avgRiskScore, highRiskCount }
  if (d.modelAnalytics?.length) {
    h += tw('Model Analytics', `<table><thead><tr><th>Model</th><th>Records</th><th>Prompt Capture</th><th>Avg Risk</th><th>High Risk</th></tr></thead><tbody>` +
      d.modelAnalytics.map(m => `<tr>
        <td>${esc(m.model || m.modelName || '')}</td><td>${num(m.recordCount ?? m.insertions)}</td>
        <td>${pct(m.promptCaptureRate)}</td>
        <td>${pct(m.avgRiskScore ?? m.avgRisk)}</td><td>${num(m.highRiskCount)}</td></tr>`).join('') + '</tbody></table>');
  }

  // riskTrends items: { bucketLabel, recordCount, highRiskCount, avgRiskScore, promptCaptureRate }
  if (d.riskTrends?.length) {
    h += tw('Risk Trends', `<table><thead><tr><th>Period</th><th>Records</th><th>High Risk</th><th>Avg Risk</th><th>Prompt Capture</th></tr></thead><tbody>` +
      d.riskTrends.map(t => `<tr>
        <td>${esc(t.bucketLabel||'')}</td><td>${num(t.recordCount)}</td>
        <td>${num(t.highRiskCount)}</td><td>${pct(t.avgRiskScore ?? t.avgRisk)}</td>
        <td>${pct(t.promptCaptureRate)}</td></tr>`).join('') + '</tbody></table>');
  }

  if (_featureIntegrity) {
    h += '<div id="integrity-card-section" class="twrap" style="margin-top:12px"></div>';
  }
  return h || '<div class="empty">No data yet. Start capturing AI insertions and refresh.</div>';
}

function mc(lbl, val, cls) {
  return `<div class="mcard"><div class="mlabel">${esc(lbl)}</div><div class="mval ${cls}">${val}</div></div>`;
}
function tw(title, inner) {
  return `<div class="twrap"><div class="thead-row"><span class="ttitle">${esc(title)}</span></div>${inner}</div>`;
}

// ── PROVENANCE INTEGRITY CARD ─────────────────────────────────────────────────

async function loadIntegrityCard() {
  const ws = _user?.workspaceId;
  if (!ws) return;
  const sec = document.getElementById('integrity-card-section');
  if (!sec) return;
  sec.innerHTML = '<div style="padding:10px 14px;font-size:13px;color:var(--muted)"><div class="spin" aria-hidden="true"></div> Checking chain…</div>';
  try {
    const d = await req('GET', `/integrity/verify?workspace_id=${encodeURIComponent(ws)}`);
    const ok = !!d.ok;
    const checked = Number(d.records_checked ?? 0);
    const breakUuid = d.first_break_uuid ? esc(String(d.first_break_uuid)) : '';
    const statusLabel = ok
      ? `Chain intact · ${checked} record${checked === 1 ? '' : 's'} verified`
      : `Chain break at ${breakUuid}`;
    const exportBtn = _featureAiBom
      ? `<button class="s btn-sm" style="margin-left:auto" data-action="export-aibom">Export AI‑BOM</button>`
      : '';
    sec.innerHTML =
      `<div class="thead-row"><span class="ttitle">Provenance Integrity</span>${exportBtn}</div>` +
      `<div style="padding:10px 14px;display:flex;align-items:center;gap:10px;font-size:13px">` +
      `<span class="dot ${ok ? 'ok' : 'fail'}" style="flex-shrink:0"></span>` +
      `<span>${statusLabel}</span>` +
      `<span style="margin-left:auto;font-size:12px;color:var(--muted)">${checked} checked</span>` +
      `</div>`;
  } catch(e) {
    sec.innerHTML = `<div style="padding:10px 14px;font-size:13px;color:var(--muted)">Integrity check unavailable: ${esc(e.message)}</div>`;
  }
}

async function exportAiBom() {
  const ws = _user?.workspaceId;
  if (!ws) return;
  try {
    const d = await req('POST', `/integrity/aibom?workspace_id=${encodeURIComponent(ws)}`);
    const blob = new Blob([JSON.stringify(d, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `aibom-${esc(ws.slice(0, 8))}-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch(e) {
    alert(`AI-BOM export failed: ${e.message}`);
  }
}

// ── ADVANCED FILTERS ──────────────────────────────────────────────────────────
function applyAdvancedFilters() {
  const risk = g('af-risk');
  const model = g('af-model');
  const from = g('af-from');
  const to = g('af-to');
  const capture = g('af-capture');
  const hasPrompt = document.getElementById('af-has-prompt').checked;
  window._activeFilters = {};
  if (risk) window._activeFilters.risk_level = risk;
  if (model) window._activeFilters.model_name = model;
  if (from) window._activeFilters.date_from = new Date(from).toISOString();
  if (to) window._activeFilters.date_to = new Date(to + 'T23:59:59').toISOString();
  if (capture) window._activeFilters.capture_status = capture;
  if (hasPrompt) window._activeFilters.has_prompt = true;
  window._currentPage = 0;
  doSearch();
}

function clearAdvancedFilters() {
  window._activeFilters = {};
  document.getElementById('af-risk').value = '';
  document.getElementById('af-model').value = '';
  document.getElementById('af-from').value = '';
  document.getElementById('af-to').value = '';
  document.getElementById('af-capture').value = '';
  document.getElementById('af-has-prompt').checked = false;
  window._currentPage = 0;
  doSearch();
}

// ── PAGINATION ────────────────────────────────────────────────────────────────
function changePage(delta) {
  const totalPages = Math.max(1, Math.ceil(window._totalRecords / window._pageSize));
  window._currentPage = Math.max(0, Math.min(window._currentPage + delta, totalPages - 1));
  doSearch();
}

function changePageSize() {
  window._pageSize = Number.parseInt(document.getElementById('pageSize').value, 10) || 20;
  window._currentPage = 0;
  doSearch();
}

function updatePagination(total) {
  window._totalRecords = total || 0;
  const totalPages = Math.max(1, Math.ceil(window._totalRecords / window._pageSize));
  const pg = document.getElementById('search-pagination');
  const pi = document.getElementById('pageInfo');
  const prev = document.getElementById('prevPage');
  const next = document.getElementById('nextPage');
  if (pg) pg.style.display = window._totalRecords > 0 ? 'flex' : 'none';
  if (pi) pi.textContent = `Page ${window._currentPage + 1} of ${totalPages} (${window._totalRecords} records)`;
  if (prev) prev.disabled = window._currentPage === 0;
  if (next) next.disabled = window._currentPage >= totalPages - 1;
}

// ── SEARCH ────────────────────────────────────────────────────────────────────
async function doSearch() {
  const el = document.getElementById('search-body');
  el.innerHTML = '<div class="loading"><div class="spin" aria-hidden="true"></div> Searching…</div>';
  try {
    const body = {workspace_id: _user?.workspaceId};
    const kw=g('s-kw'), mo=g('s-model'), fi=g('s-file'), sf=g('s-from'), st=g('s-to');
    if (kw) body.keywords = kw;
    if (mo) body.model = mo;
    if (fi) body.file_path = fi;
    if (sf) {
      const dFrom = new Date(sf);
      if (!isNaN(dFrom.getTime())) body.date_from = dFrom.toISOString();
    }
    if (st) {
      const d = new Date(st + 'T23:59:59');
      if (!isNaN(d.getTime())) body.date_to = d.toISOString();
    }
    // Merge advanced filters
    Object.assign(body, window._activeFilters);
    // Pagination
    body.limit = window._pageSize;
    body.offset = window._currentPage * window._pageSize;
    const d = await req('POST', '/search', body);
    const rs = d.results || d || [];
    const total = d.total != null ? d.total : rs.length;
    updatePagination(total);
    if (!rs.length) { el.innerHTML = '<div class="empty">No results found.</div>'; return; }
    el.innerHTML = rs.map(r => {
      const src=(r.normalizedEvent||{}).source||{};
      const lvl=((r.riskAssessment||{}).level||'').toLowerCase();
      const score = r.similarityScore==null ? '' : ` · ${Number(r.similarityScore).toFixed(3)}`;
      const tags = r.tags || [];
      const tagHtml = tags.length ? `<div class="tags-row">${tags.map(t => `<span class="tag-pill">${esc(t)}</span>`).join('')}</div>` : '';
      return `<div class="ritem" role="button" tabindex="0" data-action="open-rec" data-uuid="${esc(r.uuid||'')}">
        <div class="rmeta">
          <span>📄 ${esc(r.filePath||'—')}</span>
          <span>🤖 ${esc(r.modelName||src.adapterName||'—')}</span>
          <span>🕐 ${fd(r.timestampIso)}</span>
          ${lvl?`<span class="rsk ${lvl}">${esc(lvl)}</span>`:''}
          <span>${score}</span>
        </div>
        ${r.insertedCode?`<code class="code-snip">${esc(r.insertedCode.slice(0,200))}</code>`:''}
        ${tagHtml}
      </div>`;
    }).join('');
  } catch(e) { el.innerHTML = `<div class="al err">${esc(e.message)}</div>`; }
}

// ── RECORD ────────────────────────────────────────────────────────────────────
function openRec(uuid) {
  openModal(uuid);
}

function openModal(uuid) {
  const mb = document.getElementById('modal-body');
  mb.innerHTML = '<div class="loading"><div class="spin" aria-hidden="true"></div> Loading…</div>';
  document.getElementById('rec-modal').classList.add('open');
  document.body.style.overflow = 'hidden';
  document.querySelector('#rec-modal .modal')?.focus();
  req('GET', `/provenance/${encodeURIComponent(uuid)}`)
    .then(r => { mb.innerHTML = buildRec(r.record || r); })
    .catch(e => { mb.innerHTML = `<div class="al err">${esc(e.message)}</div>`; });
}

function closeModal() {
  document.getElementById('rec-modal').classList.remove('open');
  document.body.style.overflow = '';
  const expResult = document.getElementById('exp-result');
  const expStatus = document.getElementById('exp-status');
  if (expResult) expResult.innerHTML = '';
  if (expStatus) expStatus.textContent = '';
}

function openRecTab(uuid) {
  document.getElementById('rec-uuid').value = uuid;
  closeModal();
  go('record');
  loadRec();
}

async function loadRec() {
  const uuid = g('rec-uuid').trim();
  const el = document.getElementById('rec-body');
  if (!uuid) { el.innerHTML = '<div class="al info">Enter a UUID above to load a record.</div>'; return; }
  el.innerHTML = '<div class="loading"><div class="spin" aria-hidden="true"></div> Loading record…</div>';
  try {
    const r = await req('GET', `/provenance/${encodeURIComponent(uuid)}`);
    el.innerHTML = buildRec(r.record || r);
  } catch(e) { el.innerHTML = `<div class="al err">${esc(e.message)}</div>`; }
}

function buildRec(r) {
  const ev = r.normalizedEvent||{};
  const src = ev.source||{}, diff = ev.diff||{}, model = ev.model||{};
  const risk = r.riskAssessment||{}, snap = r.contextSnapshot||{}, cap = ev.capture||{};
  const lvl = (risk.level||risk.riskLevel||'').toLowerCase();

  let h = rs('Overview', `<div class="kv">
    ${kv('UUID', r.uuid)} ${kv('Timestamp', fd(r.timestampIso))}
    ${kv('File', r.filePath)} ${kv('Model', r.modelName||model.name)}
    ${kv('Tool', src.toolName)} ${kv('Adapter', src.adapterName)}
    ${kv('Net Lines', diff.netAddedLines??r.netAddedLines)} ${kv('Prompt Status', cap.promptStatus)}
    ${lvl?`<div class="kk">Risk</div><div class="kv-v"><span class="rsk ${lvl}">${esc(lvl)}</span></div>`:''}
  </div>`);

  // Confidence
  const confData = (ev.confidence && ev.confidence.value != null) ? ev.confidence
    : (r.confidenceValue != null ? {value: r.confidenceValue, level: r.confidenceLevel} : null);
  const breakdown = r.confidenceBreakdown || (ev.confidence && ev.confidence.evidence) || [];
  if (confData) {
    const cv = Number(confData.value).toFixed(3);
    const cl = confData.level || '';
    const clColor = cl==='very_high'?'var(--emerald)':cl==='high'?'var(--cyan)':cl==='medium'?'var(--amber)':cl==='low'?'var(--rose)':'var(--text2)';
    let bdHtml = '';
    if (breakdown.length) {
      bdHtml = `<table style="width:100%;border-collapse:collapse;margin-top:10px;font-size:12px">
        <thead><tr style="color:var(--text2)">
          <th style="text-align:left;padding:4px 8px;border-bottom:1px solid var(--border)">Signal</th>
          <th style="text-align:left;padding:4px 8px;border-bottom:1px solid var(--border)">Value</th>
          <th style="text-align:right;padding:4px 8px;border-bottom:1px solid var(--border)">Weight</th>
          <th style="text-align:right;padding:4px 8px;border-bottom:1px solid var(--border)">Contribution</th>
          <th style="text-align:left;padding:4px 8px;border-bottom:1px solid var(--border)">Rationale</th>
        </tr></thead><tbody>` +
        breakdown.map(e => `<tr>
          <td style="padding:4px 8px;border-bottom:1px solid var(--border);color:var(--accent2);font-weight:600">${esc(e.signal||'')}</td>
          <td style="padding:4px 8px;border-bottom:1px solid var(--border)">${esc(String(e.value??'—'))}</td>
          <td style="padding:4px 8px;border-bottom:1px solid var(--border);text-align:right">${Number(e.weight||0).toFixed(2)}</td>
          <td style="padding:4px 8px;border-bottom:1px solid var(--border);text-align:right;color:var(--cyan2)">${Number(e.contribution||0).toFixed(4)}</td>
          <td style="padding:4px 8px;border-bottom:1px solid var(--border);color:var(--text2)">${esc(e.rationale||'')}</td>
        </tr>`).join('') +
        `</tbody></table>`;
    }
    h += rs('Confidence', `<div>
      <span style="font-size:20px;font-weight:800;color:${clColor}">${cv}</span>
      <span class="badge badge-alert" style="margin-left:8px;vertical-align:middle">${esc(cl.replace('_',' '))}</span>
      <span style="font-size:12px;color:var(--text2);margin-left:8px">weighted_evidence_v1</span>
      ${bdHtml}
    </div>`);
  }

  // Tags
  const tags = r.tags || [];
  if (tags.length) {
    h += rs('Tags', `<div class="tags-row">${tags.map(t => `<span class="tag-pill">${esc(t)}</span>`).join('')}</div>`);
  }

  if (r.insertedCode) h += rs('Inserted Code', `<pre>${esc(r.insertedCode.slice(0,3000))}</pre>`);

  if (r.promptMessages) {
    let ph = '';
    if (typeof r.promptMessages === 'string') {
      ph = `<pre>${esc(r.promptMessages.slice(0,3000))}</pre>`;
    } else if (Array.isArray(r.promptMessages)) {
      ph = r.promptMessages.slice(0,5).map(m => {
        const c = typeof m.content==='string' ? m.content : JSON.stringify(m.content,null,2);
        return `<div style="margin-bottom:10px"><div style="font-size:11px;color:var(--text2);text-transform:uppercase;margin-bottom:4px">${esc(m.role||'')}</div><pre>${esc(c.slice(0,1500))}</pre></div>`;
      }).join('');
    }
    h += rs('Prompt', ph);
  }

  if (Object.keys(snap).length) h += rs('Context Snapshot', `<pre>${esc(JSON.stringify(snap,null,2))}</pre>`);

  h += `<div style="margin-top:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <button class="p" data-action="explain-rec" data-uuid="${esc(r.uuid||'')}">✨ Explain with AI</button>
    <button class="s" data-action="open-rec-tab" data-uuid="${esc(r.uuid||'')}">Open in Record Viewer</button>
    <span id="exp-status" style="font-size:12px;color:var(--text2)" role="status" aria-live="polite"></span>
  </div><div id="exp-result" style="margin-top:12px"></div>`;
  return h;
}

async function doExplain(uuid) {
  document.getElementById('exp-status').textContent = 'Generating…';
  document.getElementById('exp-result').innerHTML = '';
  try {
    const d = await req('POST', '/explain', {uuid, workspace_id: _user?.workspaceId});
    document.getElementById('exp-status').textContent = '';
    const txt = d.explanation || d.text || JSON.stringify(d);
    let html = rs('AI Explanation', `<pre style="white-space:pre-wrap">${esc(txt)}</pre>`);
    if (d.source === 'fallback') {
      html += `<div style="margin-top:10px;padding:10px 14px;background:rgba(245,158,11,.1);border:1px solid var(--amber);border-radius:8px;font-size:13px">
        <b style="color:var(--amber)">Fallback mode</b> — No LLM API key configured.
        <button class="s btn-sm" style="margin-left:10px" data-action="go-quality-llm">Configure LLM key</button>
      </div>`;
    }
    document.getElementById('exp-result').innerHTML = html;
  } catch(e) {
    document.getElementById('exp-status').textContent = '';
    document.getElementById('exp-result').innerHTML = `<div class="al err">${esc(e.message)}</div>`;
  }
}

async function loadLlmStatus() {
  try {
    const d = await req('GET', '/explain/llm-status');
    const banner = document.getElementById('llm-banner');
    if (banner && !d.configured) banner.style.display = 'block';
  } catch(_) {}
}

async function saveLlmKey() {
  const key = document.getElementById('llm-key-input').value.trim();
  const model = document.getElementById('llm-model-input').value.trim();
  const msg = document.getElementById('llm-key-msg');
  if (!key) { msg.textContent = 'API key is required.'; msg.style.color = 'var(--err)'; return; }
  try {
    await req('POST', '/admin/llm-key', {api_key: key, model: model || undefined});
    msg.textContent = 'Saved.'; msg.style.color = 'var(--ok)';
    document.getElementById('llm-banner').style.display = 'none';
    document.getElementById('llm-setup-card').style.display = 'none';
    document.getElementById('llm-key-input').value = '';
  } catch(e) { msg.textContent = e.message || 'Save failed.'; msg.style.color = 'var(--err)'; }
}

function rs(title, body) {
  return `<div class="rsec"><div class="rsec-h">${esc(title)}</div><div class="rsec-b">${body}</div></div>`;
}
function kv(k, v) {
  if (v==null||v==='') return '';
  return `<div class="kk">${esc(k)}</div><div class="kv-v">${esc(String(v))}</div>`;
}

// ── EXPORT ────────────────────────────────────────────────────────────────────
async function doExport() {
  setAl('al-export','');
  const p = new URLSearchParams();
  const ef=g('e-from'), et=g('e-to'), ed=g('e-dev'), efi=g('e-file');
  if (ef) p.set('dateFrom', new Date(ef).toISOString());
  if (et) p.set('dateTo', new Date(et+'T23:59:59').toISOString());
  if (ed) p.set('developer', ed);
  if (efi) p.set('filePath', efi);
  setAl('al-export','Preparing export…','info');
  try {
    const res = await fetch(`/export/audit?${p}`, {headers:{Authorization:`Bearer ${_tok}`}});
    if (!res.ok) { const d=await res.json().catch(()=>({})); throw new Error(d.detail||`HTTP ${res.status}`); }
    const count = res.headers.get('X-Record-Count')||'?';
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href=url; a.download=`lineagelens-audit-${new Date().toISOString().slice(0,10)}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    setAl('al-export',`Downloaded ${count} records.`,'ok');
  } catch(e) { setAl('al-export', e.message); }
}

// ── AGENT TRACE ───────────────────────────────────────────────────────────────
async function doAgentTraceExport() {
  setAl('al-at-export','');
  const fmt = g('at-format') || 'jsonl';
  const tool = g('at-tool');
  const conf = g('at-conf');
  const p = new URLSearchParams();
  p.set('format', fmt);
  if (tool) p.set('toolName', tool);
  if (conf) p.set('minConfidence', conf);
  setAl('al-at-export','Preparing Agent Trace export…','info');
  try {
    const res = await fetch(`/export/agent-trace?${p}`, {headers:{Authorization:`Bearer ${_tok}`}});
    if (!res.ok) { const d=await res.json().catch(()=>({})); throw new Error(d.detail||`HTTP ${res.status}`); }
    const count = res.headers.get('X-Record-Count')||'?';
    const ext = fmt === 'csv' ? 'csv' : fmt === 'json' ? 'json' : 'jsonl';
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href=url; a.download=`lineagelens-agent-trace-${new Date().toISOString().slice(0,10)}.${ext}`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    setAl('al-at-export',`Downloaded ${count} agent trace records.`,'ok');
  } catch(e) { setAl('al-at-export', e.message); }
}

function onImportFileChange(input) {
  const name = input.files?.[0]?.name || '';
  document.getElementById('at-import-filename').textContent = name || 'No file chosen';
  document.getElementById('at-import-btn').disabled = !name;
}

async function doAgentTraceImport() {
  setAl('al-at-import','');
  const fileInput = document.getElementById('at-import-file');
  const file = fileInput?.files?.[0];
  if (!file) { setAl('al-at-import','No file selected.'); return; }
  setAl('al-at-import','Uploading…','info');
  try {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch('/import/agent-trace', {
      method:'POST',
      headers:{Authorization:`Bearer ${_tok}`},
      body: form,
    });
    const data = await res.json().catch(()=>({}));
    if (!res.ok) throw new Error(data.detail||`HTTP ${res.status}`);
    const errs = data.errors?.length ? ` (${data.errors.length} parse errors)` : '';
    setAl('al-at-import',`Imported ${data.imported}, skipped ${data.skipped} duplicates.${errs}`,'ok');
    fileInput.value='';
    document.getElementById('at-import-filename').textContent='No file chosen';
    document.getElementById('at-import-btn').disabled=true;
  } catch(e) { setAl('al-at-import', e.message); }
}

// ── TEAM ─────────────────────────────────────────────────────────────────────
async function loadTeam() {
  const el = document.getElementById('team-body');
  el.innerHTML = '<div class="loading"><div class="spin" aria-hidden="true"></div> Loading…</div>';
  try {
    const d = await req('GET', '/team/members');
    const members = d.members || d || [];
    if (!members.length) { el.innerHTML = '<div class="empty">No team members yet.</div>'; return; }
    el.innerHTML = tw('Members', `<table><thead><tr><th>Username</th><th>Role</th><th>Records</th><th>Net Lines Added</th><th>Joined</th></tr></thead><tbody>` +
      members.map(m => `<tr>
        <td>${esc(m.username)}</td>
        <td><span class="bdg${m.role==='admin'?' adm':''}">${esc(m.role)}</span></td>
        <td>${m.recordCount??'—'}</td><td>${m.netAddedLines??'—'}</td>
        <td>${fd(m.joinedAtIso)}</td></tr>`).join('') + '</tbody></table>');
  } catch(e) { el.innerHTML = `<div class="al err">${esc(e.message)}</div>`; }
}

async function doInvite() {
  setAl('al-invite','');
  const role = g('inv-r');
  const ttl = parseInt(g('inv-ttl') || '1440', 10);
  const maxUses = parseInt(document.getElementById('inv-uses')?.value || '1', 10);
  const wsId = _user?.workspaceId;
  if (!wsId) { setAl('al-invite','Not logged in.'); return; }
  try {
    const data = await req('POST', '/auth/invite', {workspaceId: wsId, role, ttl_minutes: ttl, max_uses: maxUses});
    const origin = window.location.origin;
    const link = `${origin}/invite-accept?token=${encodeURIComponent(data.token)}`;
    document.getElementById('invite-link-url').value = link;
    document.getElementById('invite-link-result').hidden = false;
    setAl('al-invite', 'Link generated — share it with your engineer.', 'ok');
  } catch(e) { setAl('al-invite', e.message); }
}

function copyInviteLink() {
  const input = document.getElementById('invite-link-url');
  if (!input) return;
  navigator.clipboard?.writeText(input.value).then(() => {
    setAl('al-invite', 'Link copied to clipboard.', 'ok');
  }).catch(() => {
    input.select();
    document.execCommand('copy');
    setAl('al-invite', 'Link copied.', 'ok');
  });
}

// ── UTILS ─────────────────────────────────────────────────────────────────────
function g(id) { return document.getElementById(id)?.value||''; }
function show(id) { document.getElementById(id).style.display='flex'; }
function hide(id) { document.getElementById(id).style.display='none'; }
function esc(s) { return String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'); }
function fd(iso) { if (!iso) { return '—'; } try{return new Date(iso).toLocaleString(undefined,{dateStyle:'short',timeStyle:'short'});}catch{return iso;} }

// ── THEME ─────────────────────────────────────────────────────────────────────
let _dark = true;

function _applyTheme(dark) {
  _dark = dark;
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  const sunIcon  = document.getElementById('theme-icon-sun');
  const moonIcon = document.getElementById('theme-icon-moon');
  if (sunIcon)  sunIcon.style.display  = dark ? 'block' : 'none';
  if (moonIcon) moonIcon.style.display = dark ? 'none'  : 'block';
  try { localStorage.setItem('ll_theme', dark ? 'dark' : 'light'); } catch(_) {}
  if (_tlChart) { _tlChart.destroy(); _tlChart = null; if (_lastDashData) buildTimeline(_lastDashData); }
  if (_riskTrendChart)  { _riskTrendChart.destroy();  _riskTrendChart = null;  loadRiskTrend(); }
  if (_modelUsageChart) { _modelUsageChart.destroy(); _modelUsageChart = null; loadModelUsage(); }
  if (_devChart) { _devChart.destroy(); _devChart = null; }
}

function toggleTheme() { _applyTheme(!_dark); }

// ── MCP / BACKEND STATUS ──────────────────────────────────────────────────────
async function checkMcp() {
  const dot = document.getElementById('mcp-dot');
  if (!dot) return;
  try {
    const d = await fetch('/health').then(r => r.json());
    dot.style.background = 'var(--ok)';
    dot.title = `Backend OK · v${d.version || '?'} · ${d.productMode || ''} mode`;
  } catch {
    dot.style.background = 'var(--danger)';
    dot.title = 'Backend unreachable';
  }
}

// ── MCP SETUP PANEL ──────────────────────────────────────────────────────────
function _mcpBackendUrl() {
  // Use the current page origin so the config works whether the user is on
  // localhost, a LAN IP, or a public hostname.
  return window.location.origin || 'http://localhost:8787';
}

function _mcpScriptPath() {
  // Best-effort guess at where the MCP server script lives relative to the
  // user's repo checkout. The user must edit this in the config anyway.
  return '/absolute/path/to/lineagelens/lineagelens-mcp/lineagelens-mcp.py';
}

function _mcpSnippetClaude(backendUrl, scriptPath) {
  return JSON.stringify({
    mcpServers: {
      lineagelens: {
        command: 'python',
        args: [scriptPath],
        env: {
          LINEAGELENS_BACKEND_URL: backendUrl,
          LINEAGELENS_ACCESS_TOKEN: 'YOUR_API_KEY_HERE'
        }
      }
    }
  }, null, 2);
}

function _mcpSnippetCursor(backendUrl, scriptPath) {
  return JSON.stringify({
    mcpServers: {
      lineagelens: {
        command: 'python',
        args: [scriptPath],
        env: {
          LINEAGELENS_BACKEND_URL: backendUrl,
          LINEAGELENS_ACCESS_TOKEN: 'YOUR_API_KEY_HERE'
        }
      }
    }
  }, null, 2);
}

function _mcpSnippetContinue(backendUrl, scriptPath) {
  return JSON.stringify({
    experimental: {
      modelContextProtocolServers: [
        {
          transport: {
            type: 'stdio',
            command: 'python',
            args: [scriptPath]
          },
          env: {
            LINEAGELENS_BACKEND_URL: backendUrl,
            LINEAGELENS_ACCESS_TOKEN: 'YOUR_API_KEY_HERE'
          }
        }
      ]
    }
  }, null, 2);
}

function _mcpSnippetWindsurf(backendUrl, scriptPath) {
  return JSON.stringify({
    mcpServers: {
      lineagelens: {
        command: 'python',
        args: [scriptPath],
        env: {
          LINEAGELENS_BACKEND_URL: backendUrl,
          LINEAGELENS_ACCESS_TOKEN: 'YOUR_API_KEY_HERE'
        }
      }
    }
  }, null, 2);
}

function loadMcp() {
  const backendUrl = _mcpBackendUrl();
  const scriptPath = _mcpScriptPath();
  const urlField = document.getElementById('mcp-backend-url');
  if (urlField) urlField.value = backendUrl;
  const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  set('mcp-snippet-claude',   _mcpSnippetClaude(backendUrl, scriptPath));
  set('mcp-snippet-cursor',   _mcpSnippetCursor(backendUrl, scriptPath));
  set('mcp-snippet-continue', _mcpSnippetContinue(backendUrl, scriptPath));
  set('mcp-snippet-windsurf', _mcpSnippetWindsurf(backendUrl, scriptPath));
}

function mcpShowConfig(which) {
  document.querySelectorAll('.mcp-config-tab').forEach(b => {
    b.classList.toggle('on', b.dataset.mcp === which);
  });
  document.querySelectorAll('.mcp-config-block').forEach(d => {
    d.style.display = d.id === `mcp-config-${which}` ? 'block' : 'none';
  });
}

function mcpCopyBackendUrl() {
  const field = document.getElementById('mcp-backend-url');
  if (!field || !navigator.clipboard) return;
  navigator.clipboard.writeText(field.value).catch(() => {});
}

function mcpCopyActiveSnippet() {
  const active = document.querySelector('.mcp-config-tab.on');
  const which = active ? active.dataset.mcp : 'claude';
  const pre = document.getElementById(`mcp-snippet-${which}`);
  const msg = document.getElementById('mcp-copy-msg');
  if (!pre || !navigator.clipboard) return;
  navigator.clipboard.writeText(pre.textContent || '').then(() => {
    if (msg) {
      msg.textContent = `Copied ${which} config.`;
      setTimeout(() => { msg.textContent = ''; }, 1800);
    }
  }).catch(() => {
    if (msg) msg.textContent = 'Copy failed.';
  });
}

// ── TIMELINE ─────────────────────────────────────────────────────────────────
let _tlChart = null;
let _lastDashData = null;

async function loadTimeline() {
  const el = document.getElementById('tl-body');
  el.innerHTML = '<div class="loading"><div class="spin" aria-hidden="true"></div> Loading…</div>';
  try {
    const body = { workspaceId: _user?.workspaceId };
    const df = g('d-from'), dt = g('d-to');
    if (df) body.dateFrom = new Date(df).toISOString();
    if (dt) body.dateTo = new Date(dt + 'T23:59:59').toISOString();
    const d = await req('POST', '/insights/dashboard', body);
    _lastDashData = d;
    el.innerHTML = '';
    buildTimeline(d);
  } catch(e) {
    el.innerHTML = `<div class="al err">${esc(e.message)}</div>`;
  }
}

function buildTimeline(d) {
  const el = document.getElementById('tl-body');
  if (!el) return;
  const trends = d.riskTrends || [];
  // Backend returns d.hotspots; fall back to d.fileHotspots for compatibility
  const hotspots = d.hotspots || d.fileHotspots || [];

  el.innerHTML = `<div class="tl-chart-wrap"><canvas id="tl-chart"></canvas></div>`;
  if (hotspots.length) el.innerHTML += buildRiskHeatmap(hotspots);

  if (!trends.length) {
    document.getElementById('tl-chart').parentElement.innerHTML =
      '<div class="empty">No trend data yet — capture more AI insertions and refresh.</div>';
    return;
  }

  const labels = trends.map(t => t.bucketLabel || '');
  const records = trends.map(t => t.recordCount || 0);
  const highRisk = trends.map(t => t.highRiskCount || 0);
  const avgRisk = trends.map(t => {
    // Backend returns avgRiskScore (not avgRisk)
    const v = t.avgRiskScore ?? t.avgRisk;
    if (v == null) return null;
    return (typeof v === 'number' && v <= 1 ? v * 100 : Number(v));
  });

  const textColor = _dark ? '#94a3b8' : '#4a5568';
  const gridColor = _dark ? '#2d4a6a' : '#cbd5e0';
  const ctx = document.getElementById('tl-chart').getContext('2d');
  if (_tlChart) { _tlChart.destroy(); }
  _tlChart = new Chart(ctx, {
    data: {
      labels,
      datasets: [
        { type: 'bar', label: 'AI Insertions', data: records, backgroundColor: 'rgba(59,130,246,.5)', borderColor: '#3b82f6', borderWidth: 1, yAxisID: 'y' },
        { type: 'bar', label: 'High Risk', data: highRisk, backgroundColor: 'rgba(239,68,68,.45)', borderColor: '#ef4444', borderWidth: 1, yAxisID: 'y' },
        { type: 'line', label: 'Avg Risk %', data: avgRisk, borderColor: '#f59e0b', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 3, tension: 0.3, yAxisID: 'y2' },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: textColor, font: { size: 12 } } } },
      scales: {
        x: { ticks: { color: textColor }, grid: { color: gridColor } },
        y: { beginAtZero: true, ticks: { color: textColor }, grid: { color: gridColor }, title: { display: true, text: 'Insertions', color: textColor } },
        y2: { beginAtZero: true, max: 100, position: 'right', grid: { display: false }, ticks: { color: '#f59e0b', callback: v => v + '%' }, title: { display: true, text: 'Avg Risk %', color: '#f59e0b' } },
      },
    },
  });
}

function buildRiskHeatmap(hotspots) {
  const max = Math.max(...hotspots.map(f => f.recordCount || 0), 1);
  const rows = hotspots.slice(0, 20).map(f => {
    const w = Math.max(8, Math.round(f.recordCount / max * 100));
    // Backend hotspot items use avgRiskScore (0-100) and filePath
    const fp = f.filePath || f.fileName || '';
    const risk = f.avgRiskScore ?? f.avgRisk ?? 0;
    const rn = typeof risk === 'number' && risk <= 1 ? risk : risk / 100;
    const hue = Math.round((1 - Math.min(rn, 1)) * 120);
    const alpha = 0.3 + rn * 0.5;
    const name = (fp).split('/').pop() || fp || '?';
    return `<div class="hm-cell" style="width:${w}%;background:hsla(${hue},70%,50%,${alpha})"
      title="${esc(fp)} · ${f.recordCount} insertions · avg risk ${(rn*100).toFixed(1)}%"
      role="button" tabindex="0"
      aria-label="File ${esc(fp)} with ${f.recordCount} insertions and avg risk ${(rn*100).toFixed(1)}%"
      data-action="filter-file" data-file="${esc(fp)}">
      <span class="hm-name">${esc(name)}</span><span class="hm-cnt">${f.recordCount}</span>
    </div>`;
  }).join('');
  return `<div class="twrap"><div class="thead-row"><span class="ttitle">File Risk Heatmap</span>
    <span style="font-size:11px;color:var(--text2)">bar width = relative insertions · color = risk (red→green)</span></div>
    <div style="padding:16px;display:flex;flex-direction:column;gap:5px">${rows}</div></div>`;
}

function filterByFile(fp) {
  document.getElementById('s-file').value = fp;
  go('search'); doSearch();
}

// ── GRAPH ─────────────────────────────────────────────────────────────────────
async function loadGraph() {
  const el = document.getElementById('graph-body');
  el.innerHTML = '<div class="loading"><div class="spin" aria-hidden="true"></div> Building graph…</div>';
  try {
    const d = await req('POST', '/search', { workspaceId: _user?.workspaceId, limit: 100 });
    const results = d.results || [];
    if (!results.length) { el.innerHTML = '<div class="empty">No records yet to graph.</div>'; return; }
    const gd = buildGraphData(results);
    el.innerHTML = `<div class="graph-info">${gd.nodes.length} files · ${gd.links.length} model-shared connections · click a node to search that file</div>
      <canvas id="graph-canvas" aria-label="File lineage graph"></canvas>`;
    drawGraph(gd);
  } catch(e) { el.innerHTML = `<div class="al err">${esc(e.message)}</div>`; }
}

function buildGraphData(records) {
  const fileMap = {};
  const modelFiles = {};
  records.forEach(r => {
    const fp = r.filePath || 'unknown';
    const lvl = ((r.record || {}).riskAssessment || {}).level || '';
    const riskByCritical = lvl === 'critical' ? 0.95 : null;
    const riskByHigh = lvl === 'high' ? 0.7 : null;
    const riskByMedium = lvl === 'medium' ? 0.45 : null;
    const riskVal = riskByCritical ?? riskByHigh ?? riskByMedium ?? 0.15;
    if (!fileMap[fp]) { fileMap[fp] = { id: fp, count: 0, risk: 0, model: r.model || '' }; }
    fileMap[fp].count++;
    fileMap[fp].risk = Math.max(fileMap[fp].risk, riskVal);
    const mdl = r.model || 'unknown';
    if (!modelFiles[mdl]) { modelFiles[mdl] = new Set(); }
    modelFiles[mdl].add(fp);
  });
  const linkSet = new Set();
  const links = [];
  Object.values(modelFiles).forEach(files => {
    const arr = [...files];
    for (let i = 0; i < arr.length; i++) {
      for (let j = i + 1; j < arr.length; j++) {
        const key = arr[i] < arr[j] ? arr[i] + '||' + arr[j] : arr[j] + '||' + arr[i];
        if (!linkSet.has(key)) { linkSet.add(key); links.push({ source: arr[i], target: arr[j] }); }
      }
    }
  });
  return { nodes: Object.values(fileMap), links };
}

function drawGraph(data) {
  const canvas = document.getElementById('graph-canvas');
  if (!canvas) return;
  const W = canvas.clientWidth || 900, H = 500;
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d');
  const nodes = data.nodes.map(n => ({
    ...n, x: W / 2 + (Math.random() - .5) * W * .55, y: H / 2 + (Math.random() - .5) * H * .55,
    vx: 0, vy: 0, r: Math.max(14, Math.min(36, 10 + n.count * 4)),
  }));
  const byId = {};
  nodes.forEach(n => byId[n.id] = n);
  let frame = 0;
  const tick = () => {
    if (frame++ > 200) return;
    nodes.forEach((a, i) => {
      nodes.forEach((b, j) => {
        if (i >= j) return;
        const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
        const f = 2200 / (d * d);
        a.vx -= f*dx/d; a.vy -= f*dy/d; b.vx += f*dx/d; b.vy += f*dy/d;
      });
    });
    data.links.forEach(l => {
      const a = byId[l.source], b = byId[l.target];
      if (!a || !b) return;
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
      const f = (d - 130) * 0.012;
      a.vx += f*dx/d; a.vy += f*dy/d; b.vx -= f*dx/d; b.vy -= f*dy/d;
    });
    nodes.forEach(n => {
      n.vx += (W/2 - n.x) * .003; n.vy += (H/2 - n.y) * .003;
      n.vx *= .82; n.vy *= .82;
      n.x = Math.max(n.r, Math.min(W - n.r, n.x + n.vx));
      n.y = Math.max(n.r, Math.min(H - n.r, n.y + n.vy));
    });
    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = 'rgba(45,74,106,.5)'; ctx.lineWidth = 1;
    data.links.forEach(l => {
      const a = byId[l.source], b = byId[l.target];
      if (!a || !b) return;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    });
    nodes.forEach(n => {
      const hue = Math.round((1 - n.risk) * 120);
      ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${hue},68%,48%,.85)`; ctx.fill();
      ctx.strokeStyle = `hsla(${hue},68%,70%,.9)`; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.fillStyle = _dark ? '#e2e8f0' : '#1a202c';
      ctx.font = `${Math.min(11, n.r * 0.75)}px sans-serif`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(n.id.split('/').pop().slice(0, 14), n.x, n.y);
    });
    requestAnimationFrame(tick);
  };
  tick();
  canvas.onclick = e => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const hit = nodes.find(n => Math.hypot(n.x - mx, n.y - my) <= n.r);
    if (hit) filterByFile(hit.id);
  };
  canvas.onmousemove = e => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const hit = nodes.find(n => Math.hypot(n.x - mx, n.y - my) <= n.r);
    canvas.style.cursor = hit ? 'pointer' : 'default';
    canvas.title = hit ? `${hit.id} · ${hit.count} insertions · risk ${(hit.risk*100).toFixed(0)}%` : '';
  };
}

// ── ALERT FEED ────────────────────────────────────────────────────────────────
let _alertPoll = null;
let _alertSeen = new Set();
let _alertCount = 0;
let _mcpTimer = null;
let _lastUpdatedTimer = null;

function loadAlerts() { pollAlerts(); }

function startAlertPoll() {
  if (_alertPoll) clearInterval(_alertPoll);
  _alertPoll = setInterval(async () => {
    await pollAlerts();
    const isAlertsTab = document.getElementById('t-alerts').classList.contains('on');
    if (isAlertsTab) clearAlertBadge();
  }, 30000);
}

function clearAlertBadge() {
  _alertCount = 0;
  const b = document.getElementById('alert-badge');
  if (b) { b.style.display = 'none'; b.textContent = ''; }
}

async function pollAlerts() {
  const el = document.getElementById('alert-feed');
  if (!el) return;
  try {
    const d = await req('POST', '/search', { workspaceId: _user?.workspaceId, limit: 25 });
    const results = d.results || [];
    const newOnes = results.filter(r => !_alertSeen.has(r.uuid));
    results.forEach(r => _alertSeen.add(r.uuid));

    if (!results.length) {
      el.innerHTML = '<div class="empty">No captures yet. Start coding with AI to see the live feed here.</div>'; return;
    }

    if (newOnes.length > 0) {
      _alertCount += newOnes.length;
      const isAlertsTab = document.getElementById('t-alerts').classList.contains('on');
      if (!isAlertsTab) {
        const b = document.getElementById('alert-badge');
        if (b) { b.textContent = _alertCount; b.style.display = 'inline-block'; }
      }
    }

    el.innerHTML = results.map(r => {
      const isNew = newOnes.includes(r);
      const lvl = ((r.record?.riskAssessment) || {}).level || '';
      const snip = (r.snippet || '').replaceAll(/\s+/g, ' ').slice(0, 110);
      const tags = r.tags || [];
      const tagHtml = tags.length ? `<div class="tags-row" style="grid-column:2;grid-row:3">${tags.map(t => `<span class="tag-pill">${esc(t)}</span>`).join('')}</div>` : '';
      return `<div class="feed-item${isNew ? ' feed-new' : ''}">
        <div class="feed-time">${fd(r.timestampIso)}</div>
        <div class="feed-main">
          <code>${esc(r.filePath || '—')}</code>
          ${lvl ? `<span class="rsk ${esc(lvl.toLowerCase())}">${esc(lvl)}</span>` : ''}
          <span class="feed-model">${esc(r.model || '—')}</span>
        </div>
        ${snip ? `<div class="feed-snip">${esc(snip)}</div>` : ''}
        ${tagHtml}
        <button class="s feed-view" data-action="open-rec" data-uuid="${esc(r.uuid||'')}">View</button>
      </div>`;
    }).join('');

    const st = document.getElementById('feed-status');
    if (st) st.textContent = `Last checked ${new Date().toLocaleTimeString(undefined,{timeStyle:'short'})} · polling every 30s`;
  } catch {}
}

// ── HELPERS ──────────────────────────────────────────────────────────────────
function currentWorkspaceId() { return window._workspaceId || _user?.workspaceId || _user?.workspace_id || ''; }

function riskBadge(score) {
  if (score >= 70) return `<span class="rsk critical">${score}</span>`;
  if (score >= 40) return `<span class="rsk high">${score}</span>`;
  if (score >= 20) return `<span class="rsk medium">${score}</span>`;
  return `<span class="rsk low">${score}</span>`;
}

// ── DEVELOPER ACTIVITY ───────────────────────────────────────────────────────
let _devChart = null;

async function loadDeveloperActivity() {
  const from = document.getElementById('dev-date-from')?.value || null;
  const to = document.getElementById('dev-date-to')?.value || null;
  const body = { workspaceId: currentWorkspaceId(), dateFrom: from, dateTo: to };
  const el = document.getElementById('dev-table-body');
  if (el) el.innerHTML = '<div class="loading"><div class="spin" aria-hidden="true"></div> Loading…</div>';
  try {
    const data = await req('POST', '/analytics/developer-activity', body);
    renderDeveloperTable(data.results || []);
    renderDevChart(data.results || []);
  } catch (e) {
    if (el) el.innerHTML = '<p class="placeholder">Developer activity unavailable.</p>';
  }
}

function renderDeveloperTable(devs) {
  const el = document.getElementById('dev-table-body');
  if (!devs.length) { el.innerHTML = '<p class="placeholder">No developer activity found.</p>'; return; }
  const rows = devs.map(d => `
    <tr>
      <td>${esc(d.username || d.userId || '—')}</td>
      <td>${d.recordCount ?? '—'}</td>
      <td>${riskBadge(Math.round(d.avgRiskScore ?? d.avgRisk ?? 0))}</td>
      <td>${d.modelCount ?? '—'}</td>
      <td>${(d.totalTokens || 0).toLocaleString()}</td>
      <td>$${(d.totalCostUsd || 0).toFixed(4)}</td>
      <td>${d.lastActive ? new Date(d.lastActive).toLocaleDateString() : '—'}</td>
    </tr>
  `).join('');
  el.innerHTML = `<div class="twrap"><table class="data-table"><thead><tr>
    <th>Developer</th><th>Records</th><th>Avg Risk</th><th>Models</th><th>Tokens</th><th>Cost</th><th>Last Active</th>
  </tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderDevChart(devs) {
  const canvas = document.getElementById('devActivityChart');
  if (!canvas || !window.Chart) return;
  if (_devChart) { _devChart.destroy(); _devChart = null; }
  if (!devs.length) return;
  const labels = devs.slice(0, 10).map(d => d.username || d.userId || 'unknown');
  const counts = devs.slice(0, 10).map(d => d.recordCount || 0);
  const textColor = _dark ? '#94a3b8' : '#4a5568';
  const gridColor = _dark ? '#2d4a6a' : '#cbd5e0';
  _devChart = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{ label: 'AI Records', data: counts, backgroundColor: 'rgba(59,130,246,.55)', borderColor: '#3b82f6', borderWidth: 1 }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: textColor }, grid: { color: gridColor } },
        y: { beginAtZero: true, ticks: { color: textColor }, grid: { color: gridColor } }
      }
    }
  });
}

// ── REVIEWER QUEUE ───────────────────────────────────────────────────────────
async function loadReviews() {
  const statusFilter = document.getElementById('review-status-filter')?.value || '';
  const url = statusFilter ? `/reviews?review_status=${encodeURIComponent(statusFilter)}` : '/reviews';
  const el = document.getElementById('reviews-body');
  if (el) el.innerHTML = '<div class="loading"><div class="spin" aria-hidden="true"></div> Loading…</div>';
  try {
    const data = await req('GET', url);
    renderReviews(data.results || []);
  } catch (e) {
    if (el) el.innerHTML = '<p class="placeholder">Review queue unavailable.</p>';
  }
}

function renderReviews(items) {
  const el = document.getElementById('reviews-body');
  if (!items.length) { el.innerHTML = '<p class="placeholder">No reviews found.</p>'; return; }
  const rows = items.map(item => `
    <tr>
      <td><code style="font-size:11px">${esc(item.recordUuid || '')}</code></td>
      <td><span class="badge badge-${esc(item.status || '')}">${esc(item.status || '')}</span></td>
      <td>${esc(item.assignedTo || '—')}</td>
      <td>${esc(item.notes || '—')}</td>
      <td>${item.createdAt ? new Date(item.createdAt).toLocaleDateString() : '—'}</td>
      <td style="white-space:nowrap">
        <button class="btn-sm" data-action="review-approve" data-id="${esc(item.id || '')}">✓ Approve</button>
        <button class="btn-sm btn-danger" data-action="review-reject" data-id="${esc(item.id || '')}">✗ Reject</button>
      </td>
    </tr>
  `).join('');
  el.innerHTML = `<div class="twrap"><table class="data-table"><thead><tr>
    <th>Record UUID</th><th>Status</th><th>Assigned To</th><th>Notes</th><th>Created</th><th>Actions</th>
  </tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function updateReview(reviewId, newStatus) {
  try {
    await req('PATCH', `/reviews/${encodeURIComponent(reviewId)}`, { status: newStatus });
    loadReviews();
  } catch (e) {
    alert('Failed to update review: ' + (e.message || e));
  }
}

// ── POLICIES ─────────────────────────────────────────────────────────────────
async function loadPolicies() {
  try {
    const data = await req('GET', '/policies');
    renderPolicies(data.results || []);
  } catch (e) {
    const el = document.getElementById('policy-list-body');
    const msg = (e.message || '').includes('403') || (e.message || '').toLowerCase().includes('forbidden')
      ? 'Policies unavailable (admin only).'
      : 'Failed to load policies: ' + (e.message || 'unknown error');
    if (el) el.innerHTML = `<p class="placeholder">${esc(msg)}</p>`;
  }
}

function renderPolicies(policies) {
  const el = document.getElementById('policy-list-body');
  if (!el) return;
  if (!policies.length) { el.innerHTML = '<p class="placeholder">No policies configured.</p>'; return; }
  el.innerHTML = policies.map(p => `
    <div class="policy-card">
      <span class="policy-name">${esc(p.name || '')}</span>
      <span class="badge">${esc(p.policyType || '')}</span>
      <span class="badge badge-${esc(p.action || '')}">${esc(p.action || '')}</span>
      <span class="${p.enabled ? 'text-success' : 'text-muted'}">${p.enabled ? 'enabled' : 'disabled'}</span>
      <button class="btn-sm btn-danger" data-action="delete-policy" data-id="${esc(p.id || '')}">Delete</button>
    </div>
  `).join('');
}

function showCreatePolicyForm() {
  document.getElementById('create-policy-form').hidden = false;
}

async function createPolicy() {
  const name = document.getElementById('new-policy-name').value.trim();
  const policyType = document.getElementById('new-policy-type').value;
  const action = document.getElementById('new-policy-action').value;
  const configText = document.getElementById('new-policy-config').value.trim();
  let config = {};
  try { config = configText ? JSON.parse(configText) : {}; } catch { alert('Invalid JSON in config.'); return; }
  try {
    await req('POST', '/policies', { workspaceId: currentWorkspaceId(), name, policyType, action, config });
    document.getElementById('create-policy-form').hidden = true;
    loadPolicies();
  } catch (e) { alert('Failed to create policy: ' + (e.message || e)); }
}

async function deletePolicy(policyId) {
  if (!confirm('Delete this policy?')) return;
  try {
    await req('DELETE', `/policies/${encodeURIComponent(policyId)}`);
    loadPolicies();
  } catch (e) { alert('Failed: ' + (e.message || e)); }
}

// ── ALERT CONFIGS ─────────────────────────────────────────────────────────────
async function loadAlertConfigs() {
  try {
    const data = await req('GET', '/alert-configs');
    renderAlertConfigs(data.results || []);
  } catch (e) {
    const el = document.getElementById('alert-config-list');
    const msg = (e.message || '').includes('403') || (e.message || '').toLowerCase().includes('forbidden')
      ? 'Alert configs unavailable (admin only).'
      : 'Failed to load alert configs: ' + (e.message || 'unknown error');
    if (el) el.innerHTML = `<p class="placeholder">${esc(msg)}</p>`;
  }
}

function renderAlertConfigs(configs) {
  const el = document.getElementById('alert-config-list');
  if (!el) return;
  if (!configs.length) { el.innerHTML = '<p class="placeholder">No alert channels configured.</p>'; return; }
  el.innerHTML = configs.map(c => `
    <div class="policy-card">
      <span class="policy-name">${esc(c.name || '')}</span>
      <span class="badge">${esc(c.channel || '')}</span>
      <span class="text-muted">${(c.triggerOn || []).map(t => esc(t)).join(', ')}</span>
      <span class="${c.enabled ? 'text-success' : 'text-muted'}">${c.enabled ? 'enabled' : 'disabled'}</span>
      <button class="btn-sm btn-danger" data-action="delete-alert-config" data-id="${esc(c.id || '')}">Delete</button>
    </div>
  `).join('');
}

function showCreateAlertForm() {
  document.getElementById('create-alert-form').hidden = false;
}

function updateAlertChannelHint() {
  const ch = document.getElementById('new-alert-channel')?.value;
  const hints = {
    slack: 'Paste your Slack incoming webhook URL: {"webhook_url": "https://hooks.slack.com/..."}',
    teams: 'Paste your Teams webhook URL: {"webhook_url": "https://outlook.office.com/webhook/..."}',
    email: 'Provide SMTP config: {"smtp_host": "smtp.gmail.com", "smtp_port": 587, "from_addr": "...", "recipients": ["..."]}',
    webhook: 'Any HTTPS URL accepting POST: {"webhook_url": "https://..."}',
  };
  const el = document.getElementById('alert-channel-hint');
  if (el) el.textContent = hints[ch] || '';
}

async function createAlertConfig() {
  const name = document.getElementById('new-alert-name').value.trim();
  const channel = document.getElementById('new-alert-channel').value;
  const configText = document.getElementById('new-alert-config').value.trim();
  const triggersRaw = document.getElementById('new-alert-triggers').value;
  let config = {};
  try { config = configText ? JSON.parse(configText) : {}; } catch { alert('Invalid JSON.'); return; }
  const triggerOn = triggersRaw.split(',').map(t => t.trim()).filter(Boolean);
  try {
    await req('POST', '/alert-configs', { workspaceId: currentWorkspaceId(), name, channel, config, triggerOn });
    document.getElementById('create-alert-form').hidden = true;
    loadAlertConfigs();
  } catch (e) { alert('Failed: ' + (e.message || e)); }
}

async function deleteAlertConfig(configId) {
  if (!confirm('Delete this alert channel?')) return;
  try {
    await req('DELETE', `/alert-configs/${encodeURIComponent(configId)}`);
    loadAlertConfigs();
  } catch (e) { alert('Failed: ' + (e.message || e)); }
}

// ── ASYNC EXPORT ──────────────────────────────────────────────────────────────
let _asyncJobId = null;
let _asyncPollTimer = null;

async function startAsyncExport() {
  const format = document.getElementById('async-export-format')?.value || 'json';
  const limit = Number.parseInt(document.getElementById('async-export-limit')?.value || '1000', 10);
  try {
    const data = await req('POST', '/export/async', { workspaceId: currentWorkspaceId(), format, limit });
    if (!data.jobId) { alert('Export started but no job ID returned.'); return; }
    _asyncJobId = data.jobId;
    document.getElementById('async-export-status').hidden = false;
    document.getElementById('async-job-id').textContent = _asyncJobId || '—';
    document.getElementById('async-job-status').textContent = data.status || 'queued';
    document.getElementById('async-download-btn').hidden = true;
    _pollAsyncExport();
  } catch (e) {
    alert('Failed to start export: ' + (e.message || e));
  }
}

function _pollAsyncExport() {
  if (_asyncPollTimer) clearInterval(_asyncPollTimer);
  _asyncPollTimer = setInterval(async () => {
    if (!_asyncJobId) { clearInterval(_asyncPollTimer); return; }
    try {
      const data = await req('GET', `/export/jobs/${encodeURIComponent(_asyncJobId)}`);
      document.getElementById('async-job-status').textContent = data.status || '—';
      if (data.status === 'done') {
        clearInterval(_asyncPollTimer);
        document.getElementById('async-download-btn').hidden = false;
      } else if (data.status === 'failed') {
        clearInterval(_asyncPollTimer);
        document.getElementById('async-job-status').textContent = 'Failed: ' + (data.error || 'unknown error');
      }
    } catch(e) { clearInterval(_asyncPollTimer); document.getElementById('async-job-status').textContent = 'Error: ' + (e.message || 'network error'); }
  }, 2000);
}

async function downloadAsyncExport() {
  if (!_asyncJobId) return;
  try {
    const res = await fetch(`/export/jobs/${encodeURIComponent(_asyncJobId)}/download`, {
      headers: { Authorization: `Bearer ${_tok}` }
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      alert('Download failed: ' + (d.detail || `HTTP ${res.status}`));
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const disposition = res.headers.get('Content-Disposition') || '';
    const fnMatch = disposition.match(/filename="?([^"]+)"?/);
    a.download = fnMatch ? fnMatch[1] : 'export';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert('Download failed: ' + (e.message || e));
  }
}

// ── GITHUB CI ────────────────────────────────────────────────────────────────
async function loadGithubConfig() {
  try {
    const d = await req('GET', '/github/config');
    document.getElementById('gh-threshold').value = d.risk_threshold ?? 70;
    document.getElementById('gh-block').checked = d.block_on_high_risk ?? true;
    document.getElementById('gh-repos').value = (d.allowed_repos || []).join('\n');
  } catch(e) {
    if (!(e.message || '').includes('404')) {
      const msg = document.getElementById('gh-msg');
      if (msg) { msg.textContent = 'Failed to load config: ' + (e.message || 'unknown error'); msg.style.color = 'var(--err)'; }
    }
  }
}
async function saveGithubConfig() {
  const threshold = Number.parseInt(document.getElementById('gh-threshold').value, 10);
  const block_on_high_risk = document.getElementById('gh-block').checked;
  const repos = document.getElementById('gh-repos').value.split('\n').map(s=>s.trim()).filter(Boolean);
  const msg = document.getElementById('gh-msg');
  try {
    await req('PUT', '/github/config', {risk_threshold: threshold, block_on_high_risk, allowed_repos: repos});
    msg.textContent = 'Configuration saved.'; msg.style.color = 'var(--ok)';
  } catch(e) { msg.textContent = e.message || 'Save failed.'; msg.style.color = 'var(--err)'; }
}

// ── QUALITY METRICS ───────────────────────────────────────────────────────────
async function loadQuality() {
  const uuid = document.getElementById('q-uuid').value.trim();
  if (!uuid) return;
  const out = document.getElementById('q-result');
  out.innerHTML = '<div class="loading"><div class="spin"></div> Analyzing…</div>';
  try {
    const d = await req('GET', `/quality/${encodeURIComponent(uuid)}`);
    const m = d.metrics || d;
    out.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin-top:6px">
      ${metricCard('Lines', m.totalLines)}
      ${metricCard('Code Lines', m.codeLines)}
      ${metricCard('Comment %', ((m.commentRatio || 0) * 100).toFixed(1) + '%')}
      ${metricCard('Functions', m.functionCount)}
      ${metricCard('Classes', m.classCount)}
      ${metricCard('Cyclomatic', m.cyclomaticComplexity)}
      ${metricCard('Maintainability', m.maintainabilityScore + '/100')}
      ${metricCard('Language', m.language)}
    </div>`;
  } catch(e) { out.innerHTML = `<span style="color:var(--err)">${esc(e.message || 'Error')}</span>`; }
}
function metricCard(label, value) {
  return `<div class="card" style="text-align:center;padding:12px 8px"><div style="font-size:11px;color:var(--muted);margin-bottom:4px">${label}</div><div style="font-size:18px;font-weight:700">${value}</div></div>`;
}
async function loadQualityBatch() {
  const uuids = document.getElementById('q-batch').value.split('\n').map(s=>s.trim()).filter(Boolean);
  if (!uuids.length) return;
  const out = document.getElementById('q-batch-result');
  out.innerHTML = '<div class="loading"><div class="spin"></div> Analyzing…</div>';
  try {
    const d = await req('POST', '/quality/batch', {record_ids: uuids});
    const s = d.summary;
    out.innerHTML = `<div class="card"><b>Summary</b><br>
      Avg Cyclomatic: ${s.avgCyclomaticComplexity} &nbsp;|&nbsp;
      Avg Maintainability: ${s.avgMaintainabilityScore} &nbsp;|&nbsp;
      Total Code Lines: ${s.totalCodeLines} &nbsp;|&nbsp;
      High Complexity: ${s.highComplexityCount} &nbsp;|&nbsp;
      Low Maintainability: ${s.lowMaintainabilityCount}
    </div>
    <table class="tbl" style="margin-top:10px"><thead><tr><th>UUID</th><th>Lang</th><th>Lines</th><th>Cyclomatic</th><th>Maintain.</th></tr></thead>
    <tbody>${d.results.map(r=>`<tr><td style="font-family:monospace;font-size:11px">${r.record_id.slice(0,8)}…</td><td>${r.metrics?.language||'-'}</td><td>${r.metrics?.codeLines??'-'}</td><td>${r.metrics?.cyclomaticComplexity??'-'}</td><td>${r.metrics?.maintainabilityScore??'-'}</td></tr>`).join('')}</tbody></table>`;
  } catch(e) { out.innerHTML = `<span style="color:var(--err)">${esc(e.message || 'Error')}</span>`; }
}

// ── SCHEDULED DIGESTS ─────────────────────────────────────────────────────────
async function loadDigests() {
  const out = document.getElementById('digests-body');
  try {
    const d = await req('GET', '/scheduled-reports');
    const items = d.results || (Array.isArray(d) ? d : []);
    if (!items.length) { out.innerHTML = '<p style="color:var(--muted)">No digests configured.</p>'; return; }
    out.innerHTML = `<table class="tbl"><thead><tr><th>Name</th><th>Type</th><th>Frequency</th><th>Recipients</th><th>Enabled</th><th></th></tr></thead>
    <tbody>${items.map(r=>`<tr>
      <td>${esc(r.name)}</td>
      <td>${esc(r.report_type)}</td>
      <td>${esc(r.frequency)}</td>
      <td>${(r.recipients||[]).map(e=>esc(e)).join(', ')}</td>
      <td>${r.enabled?'Yes':'No'}</td>
      <td><button class="btn-sm" data-action="run-digest" data-id="${esc(r.id)}">Run now</button></td>
    </tr>`).join('')}</tbody></table>`;
  } catch(e) { out.innerHTML = `<span style="color:var(--err)">${esc(e.message||'Error')}</span>`; }
}
function showCreateDigest() {
  const el = document.getElementById('digests-create');
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}
async function createDigest() {
  const name = document.getElementById('dig-name').value.trim();
  const report_type = document.getElementById('dig-type').value;
  const frequency = document.getElementById('dig-freq').value;
  const recipients = document.getElementById('dig-recipients').value.split(',').map(s=>s.trim()).filter(Boolean);
  const msg = document.getElementById('dig-msg');
  if (!name) { msg.textContent = 'Name is required.'; msg.style.color = 'var(--err)'; return; }
  try {
    await req('POST', '/scheduled-reports', {name, report_type, frequency, recipients, enabled: true});
    msg.textContent = 'Digest created.'; msg.style.color = 'var(--ok)';
    document.getElementById('digests-create').style.display = 'none';
    loadDigests();
  } catch(e) { msg.textContent = e.message || 'Create failed.'; msg.style.color = 'var(--err)'; }
}
async function runDigest(id) {
  try {
    await req('POST', `/scheduled-reports/${id}/run`);
    alert('Digest dispatched.');
  } catch(e) { alert(e.message || 'Failed to run digest.'); }
}

// ── SSO PROVIDERS ─────────────────────────────────────────────────────────────
async function loadSsoProviders() {
  const out = document.getElementById('sso-body');
  try {
    const d = await req('GET', '/auth/sso/providers');
    if (!d.length) { out.innerHTML = '<p style="color:var(--muted)">No SSO providers configured.</p>'; return; }
    out.innerHTML = `<table class="tbl"><thead><tr><th>Name</th><th>Issuer</th><th>Client ID</th><th>Enabled</th><th></th></tr></thead>
    <tbody>${d.map(p=>`<tr>
      <td>${esc(p.name)}</td>
      <td>${esc(p.issuer)}</td>
      <td><code style="font-size:11px">${esc(p.client_id)}</code></td>
      <td>${p.enabled?'Yes':'No'}</td>
      <td><button class="btn-sm btn-danger" data-action="delete-sso" data-id="${esc(p.id)}">Delete</button></td>
    </tr>`).join('')}</tbody></table>`;
  } catch(e) { out.innerHTML = `<span style="color:var(--err)">${esc(e.message||'Error')}</span>`; }
}
function showCreateSso() {
  const el = document.getElementById('sso-create');
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}
async function createSsoProvider() {
  const name = document.getElementById('sso-name').value.trim();
  const issuer = document.getElementById('sso-issuer').value.trim();
  const client_id = document.getElementById('sso-client-id').value.trim();
  const client_secret = document.getElementById('sso-client-secret').value.trim();
  const msg = document.getElementById('sso-msg');
  if (!name || !issuer || !client_id || !client_secret) { msg.textContent = 'All fields are required.'; msg.style.color = 'var(--err)'; return; }
  try {
    await req('POST', '/auth/sso/providers', {name, issuer, client_id, client_secret, scopes: ['openid','email','profile'], enabled: true});
    msg.textContent = 'Provider added.'; msg.style.color = 'var(--ok)';
    document.getElementById('sso-create').style.display = 'none';
    loadSsoProviders();
  } catch(e) { msg.textContent = e.message || 'Failed to add.'; msg.style.color = 'var(--err)'; }
}
async function deleteSsoProvider(id) {
  if (!confirm('Delete this SSO provider?')) return;
  try {
    await req('DELETE', `/auth/sso/providers/${id}`);
    loadSsoProviders();
  } catch(e) { alert(e.message || 'Delete failed.'); }
}

// ── MODEL ROUTING ─────────────────────────────────────────────────────────────

const _PROVIDER_LABELS = {anthropic: 'Anthropic (Claude Code)', openai: 'OpenAI (Codex CLI)', gemini: 'Google (Gemini CLI)'};
const _PROVIDER_ICONS  = {anthropic: '🟣', openai: '🟢', gemini: '🔵'};

async function loadRouting() {
  const out = document.getElementById('routing-body');
  try {
    // Fetch current policies and built-in defaults in parallel.
    const [polRes, defRes] = await Promise.all([
      req('GET', '/policies/routing').catch(() => ({results: [], count: 0})),
      req('GET', '/policies/routing/defaults').catch(() => ({defaults: {}})),
    ]);
    const existing = {};
    (polRes.results || []).forEach(p => { existing[p.provider] = p; });
    const defaults = defRes.defaults || {};

    const workspaceId = _user?.workspace_id || '';
    const providers = ['anthropic', 'openai', 'gemini'];

    let html = '<div style="display:flex;flex-direction:column;gap:16px;max-width:560px">';
    providers.forEach(prov => {
      const pol = existing[prov];
      const def = defaults[prov] || {};
      const enabled = pol?.enabled ?? false;
      const mappings = pol?.mappings || {};
      const simple   = mappings.simple   || def.simple   || '';
      const standard = mappings.standard || def.standard || '';
      const complex  = mappings.complex  || def.complex  || '';
      const statusColor = enabled ? 'var(--ok)' : 'var(--text2)';
      const statusLabel = enabled ? 'Enabled' : 'Disabled';

      html += `<div class="card" id="rc-${prov}" style="padding:18px 20px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
          <div style="font-size:14px;font-weight:600">${_PROVIDER_ICONS[prov]} ${esc(_PROVIDER_LABELS[prov] || prov)}</div>
          <span id="rs-badge-${prov}" style="font-size:12px;font-weight:600;color:${statusColor}">${statusLabel}</span>
        </div>
        <div style="display:grid;grid-template-columns:80px 1fr;gap:6px 12px;align-items:center;margin-bottom:14px;font-size:13px">
          <label style="color:var(--text2)">Simple</label>
          <input id="rm-simple-${prov}" type="text" value="${esc(simple)}" placeholder="${esc(def.simple||'')}" style="padding:5px 8px;font-size:13px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
          <label style="color:var(--text2)">Standard</label>
          <input id="rm-standard-${prov}" type="text" value="${esc(standard)}" placeholder="${esc(def.standard||'')}" style="padding:5px 8px;font-size:13px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
          <label style="color:var(--text2)">Complex</label>
          <input id="rm-complex-${prov}" type="text" value="${esc(complex)}" placeholder="${esc(def.complex||'')}" style="padding:5px 8px;font-size:13px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="p" data-action="routing-enable" data-provider="${prov}" data-workspace="${esc(workspaceId)}">Enable</button>
          <button class="s" data-action="routing-disable" data-provider="${prov}" data-workspace="${esc(workspaceId)}">Disable</button>
          <span id="rs-msg-${prov}" style="font-size:12px;margin-left:4px"></span>
        </div>
      </div>`;
    });
    html += '</div>';
    out.innerHTML = html;
  } catch(e) {
    out.innerHTML = `<div class="al err">Failed to load routing policies: ${esc(e.message || 'Server error')}</div>`;
  }
}

async function saveRoutingPolicy(provider, workspaceId, enable) {
  const msg = document.getElementById(`rs-msg-${provider}`);
  const badge = document.getElementById(`rs-badge-${provider}`);
  const simple   = document.getElementById(`rm-simple-${provider}`)?.value.trim();
  const standard = document.getElementById(`rm-standard-${provider}`)?.value.trim();
  const complex  = document.getElementById(`rm-complex-${provider}`)?.value.trim();
  // Build mappings; empty strings → omit so server fills defaults.
  const mappings = {};
  if (simple)   mappings.simple   = simple;
  if (standard) mappings.standard = standard;
  if (complex)  mappings.complex  = complex;
  try {
    msg.textContent = 'Saving…'; msg.style.color = 'var(--text2)';
    await req('PUT', '/policies/routing', {
      workspaceId, provider, mappings, enabled: enable,
    });
    msg.textContent = enable ? 'Enabled ✓' : 'Disabled';
    msg.style.color = enable ? 'var(--ok)' : 'var(--text2)';
    if (badge) {
      badge.textContent = enable ? 'Enabled' : 'Disabled';
      badge.style.color = enable ? 'var(--ok)' : 'var(--text2)';
    }
  } catch(e) {
    msg.textContent = e.message || 'Save failed';
    msg.style.color = 'var(--err)';
  }
}

// ── WORKSPACE ─────────────────────────────────────────────────────────────────
async function loadWorkspace() {
  const out = document.getElementById('workspace-body');
  try {
    const d = await req('GET', '/workspaces');
    out.innerHTML = `<div class="card" style="max-width:480px">
      <h4 style="margin:0 0 14px">Current Workspace</h4>
      <div style="margin-bottom:8px;font-size:13px"><b>ID:</b> <code>${esc(d.id || _user?.workspace_id || '—')}</code></div>
      <div style="margin-bottom:14px;font-size:13px"><b>Name:</b> ${esc(d.name || '—')}</div>
      <label style="display:block;margin-bottom:8px;font-size:13px">Workspace Name
        <input id="ws-name" type="text" value="${esc(d.name || '')}" style="display:block;margin-top:4px;width:100%;padding:7px 10px;font-size:13px;box-sizing:border-box">
      </label>
      <button class="p" data-action="save-workspace-name" data-ws-id="${esc(d.id || '')}">Update Name</button>
      <div id="ws-msg" style="margin-top:10px;font-size:13px"></div>
    </div>`;
  } catch(e) {
    if (e.message && e.message.includes('404')) {
      const defaultId = _user?.workspace_id || '';
      out.innerHTML = `<div class="card" style="max-width:480px">
        <h4 style="margin:0 0 14px">Create Workspace</h4>
        <label style="display:block;margin-bottom:8px;font-size:13px">Workspace ID (slug)
          <input id="ws-new-id" type="text" placeholder="my-org" value="${esc(defaultId)}" style="display:block;margin-top:4px;width:100%;padding:7px 10px;font-size:13px;box-sizing:border-box">
        </label>
        <label style="display:block;margin-bottom:14px;font-size:13px">Display Name
          <input id="ws-new-name" type="text" placeholder="My Organisation" style="display:block;margin-top:4px;width:100%;padding:7px 10px;font-size:13px;box-sizing:border-box">
        </label>
        <button class="p" data-action="create-workspace">Create Workspace</button>
        <div id="ws-msg" style="margin-top:10px;font-size:13px"></div>
      </div>`;
    } else {
      out.innerHTML = `<div class="al err">Failed to load workspace: ${esc(e.message || 'Server error')}</div>`;
    }
  }
}
async function saveWorkspaceName(id) {
  const name = document.getElementById('ws-name').value.trim();
  const msg = document.getElementById('ws-msg');
  try {
    await req('PATCH', `/workspaces/${encodeURIComponent(id)}`, {name});
    msg.textContent = 'Name updated.'; msg.style.color = 'var(--ok)';
  } catch(e) { msg.textContent = e.message || 'Update failed.'; msg.style.color = 'var(--err)'; }
}
async function createWorkspace() {
  const id = document.getElementById('ws-new-id').value.trim();
  const name = document.getElementById('ws-new-name').value.trim();
  const msg = document.getElementById('ws-msg');
  if (!id || !name) { msg.textContent = 'Workspace ID and name are required.'; msg.style.color = 'var(--err)'; return; }
  try {
    await req('POST', '/workspaces', {id, name});
    msg.textContent = 'Workspace created.'; msg.style.color = 'var(--ok)';
    loadWorkspace();
  } catch(e) { msg.textContent = e.message || 'Create failed.'; msg.style.color = 'var(--err)'; }
}

// ── EVENT DELEGATION — dynamic content ──────────────────────────────────────
// All onclick/onkeydown that live in JS-generated innerHTML are routed here.
// Static button wiring is in _wireStaticHandlers() at the bottom.
document.addEventListener('click', function(e) {
  // Handle [role="button"] keyboard-activated elements (click fires from Enter/Space via keydown)
  const target = e.target.closest('[data-action]');
  if (!target) return;
  const action = target.dataset.action;
  switch (action) {
    case 'open-rec':
      openRec(target.dataset.uuid);
      break;
    case 'open-rec-tab':
      openRecTab(target.dataset.uuid);
      break;
    case 'explain-rec':
      doExplain(target.dataset.uuid);
      break;
    case 'export-aibom':
      exportAiBom();
      break;
    case 'filter-file':
      filterByFile(target.dataset.file);
      break;
    case 'review-approve':
      updateReview(target.dataset.id, 'approved');
      break;
    case 'review-reject':
      updateReview(target.dataset.id, 'rejected');
      break;
    case 'delete-policy':
      deletePolicy(target.dataset.id);
      break;
    case 'delete-alert-config':
      deleteAlertConfig(target.dataset.id);
      break;
    case 'run-digest':
      runDigest(target.dataset.id);
      break;
    case 'delete-sso':
      deleteSsoProvider(target.dataset.id);
      break;
    case 'routing-enable':
      saveRoutingPolicy(target.dataset.provider, target.dataset.workspace, true);
      break;
    case 'routing-disable':
      saveRoutingPolicy(target.dataset.provider, target.dataset.workspace, false);
      break;
    case 'save-workspace-name':
      saveWorkspaceName(target.dataset.wsId);
      break;
    case 'create-workspace':
      createWorkspace();
      break;
    case 'go-quality-llm':
      go('quality');
      setTimeout(() => {
        const b = document.getElementById('llm-banner');
        const c = document.getElementById('llm-setup-card');
        if (b) b.style.display = 'block';
        if (c) c.style.display = 'block';
      }, 100);
      break;
  }
});

// Keyboard activation for [role="button"] elements with data-action
document.addEventListener('keydown', function(e) {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const target = e.target.closest('[data-action]');
  if (!target) return;
  // Only trigger for non-button elements (buttons fire click on Enter natively)
  if (target.tagName === 'BUTTON') return;
  e.preventDefault();
  target.click();
});

// Close modal when clicking the overlay background
document.addEventListener('click', function(e) {
  const overlay = document.getElementById('rec-modal');
  if (e.target === overlay) closeModal();
});

// ── STATIC HANDLER WIRING ────────────────────────────────────────────────────
function _wireStaticHandlers() {
  // ── Auth ──
  const _on = (id, ev, fn) => { const el = document.getElementById(id); if (el) el.addEventListener(ev, fn); };

  _on('btn-login',     'click', () => doLogin());
  _on('l-pass',        'keydown', e => { if (e.key === 'Enter') doLogin(); });
  _on('btn-register',  'click', () => doRegister());
  _on('r-pass',        'keydown', e => { if (e.key === 'Enter') doRegister(); });
  _on('btn-show-reg',  'click', e => showReg(e));
  _on('btn-show-login','click', e => showLogin(e));
  _on('logout-btn',    'click', () => doLogout());

  // ── Theme ──
  _on('theme-btn', 'click', () => toggleTheme());

  // ── Modal ──
  _on('btn-modal-close', 'click', () => closeModal());
  const modal = document.querySelector('#rec-modal .modal');
  if (modal) modal.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

  // ── Sidebar toggle (desktop collapse) ──
  _on('sidebar-toggle', 'click', () => {
    document.getElementById('v-app').classList.toggle('sidebar-collapsed');
  });

  // ── Mobile menu button ──
  _on('menu-btn', 'click', () => {
    const sidebar  = document.getElementById('sidebar');
    const overlay  = document.getElementById('drawer-overlay');
    const menuBtn  = document.getElementById('menu-btn');
    const isOpen   = sidebar.classList.toggle('drawer-open');
    if (overlay) overlay.classList.toggle('visible', isOpen);
    menuBtn.setAttribute('aria-expanded', String(isOpen));
  });
  _on('drawer-overlay', 'click', () => {
    const sidebar  = document.getElementById('sidebar');
    const overlay  = document.getElementById('drawer-overlay');
    const menuBtn  = document.getElementById('menu-btn');
    sidebar.classList.remove('drawer-open');
    if (overlay) overlay.classList.remove('visible');
    menuBtn.setAttribute('aria-expanded', 'false');
  });

  // ── Sidebar nav items ──
  document.querySelectorAll('.nav-item[data-t]').forEach(btn => {
    btn.addEventListener('click', () => {
      const name = btn.dataset.t;
      // Check tier / admin gate
      const required = btn.dataset.requires;
      if (required && !hasTierAccess(required)) {
        showUpgradePanel(name, required);
        return;
      }
      go(name);
      // Close mobile drawer if open
      const sidebar = document.getElementById('sidebar');
      const overlay = document.getElementById('drawer-overlay');
      if (sidebar && sidebar.classList.contains('drawer-open')) {
        sidebar.classList.remove('drawer-open');
        if (overlay) overlay.classList.remove('visible');
        const menuBtn = document.getElementById('menu-btn');
        if (menuBtn) menuBtn.setAttribute('aria-expanded', 'false');
      }
    });
  });

  // ── Dashboard ──
  _on('btn-dash-refresh', 'click', () => loadDash());

  // ── Search ──
  _on('btn-search',   'click', () => doSearch());
  _on('s-kw',         'keydown', e => { if (e.key === 'Enter') doSearch(); });
  _on('btn-adv-apply','click', () => applyAdvancedFilters());
  _on('btn-adv-clear','click', () => clearAdvancedFilters());
  _on('prevPage',     'click', () => changePage(-1));
  _on('nextPage',     'click', () => changePage(1));
  _on('pageSize',     'change', () => changePageSize());

  // ── Record viewer ──
  _on('btn-load-rec', 'click', () => loadRec());
  _on('rec-uuid',     'keydown', e => { if (e.key === 'Enter') loadRec(); });

  // ── Timeline ──
  _on('btn-tl-refresh', 'click', () => loadTimeline());

  // ── Graph ──
  _on('btn-graph-refresh', 'click', () => loadGraph());

  // ── Live feed ──
  _on('btn-alerts-refresh', 'click', () => pollAlerts());

  // ── Export ──
  _on('btn-export-csv',          'click', () => doExport());
  _on('btn-start-async-export',  'click', () => startAsyncExport());
  _on('async-download-btn',      'click', () => downloadAsyncExport());
  _on('btn-at-export',           'click', () => doAgentTraceExport());
  _on('at-import-file',          'change', function() { onImportFileChange(this); });
  _on('at-import-btn',           'click', () => doAgentTraceImport());

  // ── Team / invite ──
  _on('btn-invite',          'click', () => doInvite());
  _on('btn-copy-invite',     'click', () => copyInviteLink());
  _on('btn-new-policy',      'click', () => showCreatePolicyForm());
  _on('btn-create-policy',   'click', () => createPolicy());
  _on('btn-cancel-policy',   'click', () => { document.getElementById('create-policy-form').hidden = true; });
  _on('btn-new-alert-channel','click', () => showCreateAlertForm());
  _on('btn-create-alert',    'click', () => createAlertConfig());
  _on('btn-cancel-alert',    'click', () => { document.getElementById('create-alert-form').hidden = true; });
  _on('new-alert-channel',   'change', () => updateAlertChannelHint());

  // ── Developer activity ──
  _on('btn-dev-refresh', 'click', () => loadDeveloperActivity());

  // ── Reviews ──
  _on('review-status-filter', 'change', () => loadReviews());
  _on('btn-reviews-refresh',  'click',  () => loadReviews());

  // ── GitHub CI ──
  _on('btn-save-github', 'click', () => saveGithubConfig());

  // ── MCP ──
  _on('btn-mcp-copy-url',     'click', () => mcpCopyBackendUrl());
  _on('btn-mcp-copy-snippet', 'click', () => mcpCopyActiveSnippet());
  document.querySelectorAll('.mcp-config-tab').forEach(btn => {
    btn.addEventListener('click', () => mcpShowConfig(btn.dataset.mcp));
  });

  // ── Quality ──
  _on('btn-show-llm-setup', 'click', () => {
    const card = document.getElementById('llm-setup-card');
    const banner = document.getElementById('llm-banner');
    if (card) card.style.display = 'block';
    if (banner) banner.style.display = 'none';
  });
  _on('btn-save-llm',    'click', () => saveLlmKey());
  _on('btn-cancel-llm',  'click', () => { document.getElementById('llm-setup-card').style.display = 'none'; });
  _on('btn-quality-analyze', 'click', () => loadQuality());
  _on('btn-quality-batch',   'click', () => loadQualityBatch());

  // ── Digests ──
  _on('btn-new-digest',    'click', () => showCreateDigest());
  _on('btn-create-digest', 'click', () => createDigest());
  _on('btn-cancel-digest', 'click', () => { document.getElementById('digests-create').style.display = 'none'; });

  // ── SSO ──
  _on('btn-add-sso',    'click', () => showCreateSso());
  _on('btn-create-sso', 'click', () => createSsoProvider());
  _on('btn-cancel-sso', 'click', () => { document.getElementById('sso-create').style.display = 'none'; });
}

// ── DOM READY — wire handlers + apply saved theme ────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Restore theme from localStorage; fall back to prefers-color-scheme
  try {
    const saved = localStorage.getItem('ll_theme');
    if (saved) {
      _applyTheme(saved === 'dark');
    } else {
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      _applyTheme(prefersDark);
    }
  } catch(_) { _applyTheme(true); }

  _wireStaticHandlers();
});

// ── BOOT ─────────────────────────────────────────────────────────────────────
// Auto-login when arriving from the /setup wizard
(async function boot() {
  // Pre-fill workspace ID from URL hash (#ws=...) if present. Lets the user
  // log in manually if auto-login fails after setup. URL hash is never sent
  // to the server, only available to JS.
  try {
    const hash = window.location.hash || '';
    const m = hash.match(/[#&]ws=([^&]+)/);
    if (m && m[1]) {
      const wsField = document.getElementById('l-ws');
      if (wsField && !wsField.value) { wsField.value = decodeURIComponent(m[1]); }
    }
  } catch(_) { /* ignore malformed hash */ }

  const access = sessionStorage.getItem('ll_access');
  const refresh = sessionStorage.getItem('ll_refresh');
  if (access && refresh) {
    try {
      // Validate the token by fetching /auth/me
      const res = await fetch('/auth/me', {headers: {'Authorization': `Bearer ${access}`}});
      if (res.ok) {
        // Tokens validated — safe to remove from sessionStorage now (one-time use).
        sessionStorage.removeItem('ll_access');
        sessionStorage.removeItem('ll_refresh');
        sessionStorage.removeItem('ll_workspace');
        const user = await res.json();
        _tok = access; _ref = refresh;
        _user = {username: user.username, role: user.role, workspace_id: user.workspaceId};
        const h = await fetch('/health').then(r => r.json()).catch(() => ({}));
        _mode = h.productMode || 'plus';
        const _feat2 = h.features || {};
        _featureIntegrity = _feat2.provenanceIntegrity ?? (_mode === 'plus' || _mode === 'max');
        _featureAiBom = _feat2.aiBomExport ?? (_mode === 'plus' || _mode === 'max');
        document.getElementById('uname').textContent = _user.username;
        const rb = document.getElementById('role-bdg');
        rb.textContent = _user.role; rb.className = `role-badge${_user.role === 'admin' ? ' adm' : ''}`;
        const mb = document.getElementById('mode-bdg');
        mb.textContent = _mode; mb.className = `tier-badge ${_mode}`;
        if (_user.role === 'admin') {
          document.querySelectorAll('.adm-nav').forEach(t => t.style.display = 'flex');
          document.getElementById('invite-box').style.display = 'block';
          document.getElementById('policy-config-section').style.display = 'block';
          document.getElementById('alert-config-section').style.display = 'block';
        }
        document.querySelectorAll('.nav-item[data-requires="plus"]').forEach(t => {
          if (!hasTierAccess('plus')) t.classList.add('nav-locked');
        });
        document.querySelectorAll('.nav-item[data-requires="max"]').forEach(t => {
          if (!hasTierAccess('max')) t.classList.add('nav-locked');
        });
        showApp();
        go('dashboard');
        loadPolicies(); loadAlertConfigs(); checkMcp();
        if (_mcpTimer) { clearInterval(_mcpTimer); _mcpTimer = null; }
        if (_lastUpdatedTimer) { clearInterval(_lastUpdatedTimer); _lastUpdatedTimer = null; }
        _mcpTimer = setInterval(checkMcp, 60000);
        startAlertPoll();
        _lastUpdatedTimer = setInterval(updateLastUpdatedText, 30000);
        return;
      } else if (res.status === 401 || res.status === 403) {
        // Tokens are definitively invalid — clean up so refreshing the page
        // doesn't loop on the same bad tokens. Transient errors (5xx, network)
        // leave the tokens in place so the next page load can retry.
        sessionStorage.removeItem('ll_access');
        sessionStorage.removeItem('ll_refresh');
        sessionStorage.removeItem('ll_workspace');
      }
    } catch(e) { showLogin(); return; }
  }
  showLogin();
})();
