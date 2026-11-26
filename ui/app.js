let currentLang = localStorage.getItem('stagebot_lang') || 'fr';
let currentTab = 'guide';
let runMode = 'auto';
let debugMode = false;
let allRows = [];
let countsCache = {drafted: 0, manual_portal: 0};
let visibleUrlsCache = [];
const selectedRemake = new Set();

function escapeHtml(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function apiUrl(path) {
  if (!debugMode) return path;
  return path + (path.includes('?') ? '&' : '?') + 'debug=1';
}

function updateDebugUI() {
  const t = I18N[currentLang];
  const btn = document.getElementById('btn-debug-mode');
  if (btn) {
    btn.classList.toggle('active', debugMode);
    btn.textContent = debugMode ? t.debugOn : t.debugOff;
    btn.title = t.debugTitle;
  }
  const banner = document.getElementById('debug-banner');
  if (banner) {
    banner.classList.toggle('hidden', !debugMode);
    banner.textContent = t.debugBanner || '';
  }
  const fetchDesc = document.getElementById('fetch-desc');
  if (fetchDesc) {
    fetchDesc.textContent = debugMode ? t.debugFetchDesc : t.fetchDesc;
    fetchDesc.style.color = debugMode ? 'var(--amber)' : 'var(--muted)';
  }
  const hint = document.getElementById('apply-hint');
  if (hint) {
    if (debugMode) {
      hint.textContent = t.debugHint;
      hint.style.color = 'var(--amber)';
    } else {
      hint.textContent = t.applyHint;
      hint.style.color = 'var(--muted)';
    }
  }
  const remakeBtn = document.getElementById('btn-remake');
  if (remakeBtn) {
    remakeBtn.disabled = debugMode;
    remakeBtn.style.opacity = debugMode ? '0.45' : '1';
  }
  const syncBtn = document.getElementById('btn-sync');
  if (syncBtn) {
    syncBtn.disabled = debugMode;
    syncBtn.style.opacity = debugMode ? '0.45' : '1';
  }
  const gSyncBtn = document.getElementById('g-s2-btn');
  if (gSyncBtn) {
    gSyncBtn.disabled = debugMode;
    gSyncBtn.style.opacity = debugMode ? '0.45' : '1';
  }
  for (const id of ['link-report-tracker', 'link-report-guide']) {
    const a = document.getElementById(id);
    if (a) a.href = apiUrl('/api/report');
  }
  const nextBtn = document.getElementById('mc-btn-next');
  if (nextBtn) nextBtn.textContent = t.debugNext;
}

function toggleDebugMode() {
  debugMode = !debugMode;
  selectedRemake.clear();
  const cvFrame = document.getElementById('pdf-cv');
  const lmFrame = document.getElementById('pdf-lm');
  const prevLbl = document.getElementById('preview-label');
  if (cvFrame) cvFrame.src = '';
  if (lmFrame) lmFrame.src = '';
  if (prevLbl) prevLbl.textContent = I18N[currentLang].previewEmpty;
  updateDebugUI();
  loadApps();
  loadProfile();
  if (typeof loadQuestions === 'function') loadQuestions();
}

function setRunMode(mode) {
  runMode = mode;
  ['auto', 'apply'].forEach(m => {
    document.getElementById('mode-' + m).classList.toggle('active', m === mode);
  });
  updateDebugUI();
}

function setLang(lang) {
  currentLang = I18N[lang] ? lang : 'fr';
  localStorage.setItem('stagebot_lang', currentLang);
  document.querySelectorAll('.lang-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('lang-' + currentLang).classList.add('active');
  const t = I18N[currentLang];
  document.getElementById('nav-guide').textContent = t.navGuide;
  document.getElementById('nav-fetch').textContent = t.navFetch;
  document.getElementById('nav-apply').textContent = t.navApply;
  document.getElementById('nav-tracker').textContent = t.navTracker;
  document.getElementById('nav-preview').textContent = t.navPreview;
  document.getElementById('nav-profile').textContent = t.navProfile;
  document.getElementById('nav-questions').textContent = t.titles.questions;
  document.getElementById('lbl-total').textContent = t.total;
  document.getElementById('lbl-applied').textContent = t.applied;
  document.getElementById('lbl-drafted').textContent = t.drafted;
  document.getElementById('lbl-manual').textContent = t.manual;
  document.getElementById('lbl-login').textContent = t.needsLogin;
  document.getElementById('lbl-other').textContent = t.other;
  document.getElementById('btn-report').textContent = t.report;
  document.getElementById('btn-refresh').textContent = t.refresh;
  document.getElementById('filter-q').placeholder = t.filterPlaceholder;
  document.getElementById('opt-all').textContent = t.allStatuses;
  document.getElementById('opt-all-src').textContent = t.allSources;
  document.getElementById('btn-skip-filtered').textContent = t.skipFiltered;
  updateRemakeButton();
  document.getElementById('th-date').textContent = t.thDate;
  document.getElementById('th-source').textContent = t.thSource;
  document.getElementById('th-company').textContent = t.thCompany;
  document.getElementById('th-role').textContent = t.thRole;
  document.getElementById('th-status').textContent = t.thStatus;
  document.getElementById('th-notes').textContent = t.thNotes;
  document.getElementById('th-docs').textContent = t.thDocs;
  document.getElementById('th-actions').textContent = t.thActions;
  document.getElementById('fetch-heading').textContent = t.fetchHeading;
  document.getElementById('fetch-desc').textContent = t.fetchDesc;
  document.getElementById('btn-fetch').textContent = t.btnFetch;
  document.getElementById('apply-mode-title').textContent = t.applyModeTitle;
  document.getElementById('lbl-skip-ext').textContent = t.skipExternal;
  document.getElementById('lbl-m-auto').textContent = t.mAuto;
  document.getElementById('lbl-m-apply').textContent = t.mApply;
  document.getElementById('btn-mail').textContent = t.btnMail;
  document.getElementById('lbl-apply-platforms').textContent = t.platforms;
  updateApplyButtonCounts();
  updateDebugUI();
  document.getElementById('lbl-logs-fetch').textContent = t.logs;
  document.getElementById('lbl-logs-apply').textContent = t.logs;
  document.getElementById('run-state-fetch').textContent = t.idle;
  document.getElementById('run-state-apply').textContent = t.idle;
  if (!document.getElementById('pdf-cv').src) {
    document.getElementById('preview-label').textContent = t.previewEmpty;
  }
  document.getElementById('lbl-master').textContent = t.masterTitle;
  document.getElementById('btn-sync').textContent = t.syncBtn;
  document.getElementById('g-title').textContent = t.gTitle;
  document.getElementById('g-intro').textContent = t.gIntro;
  document.getElementById('g-s1-t').textContent = t.gS1T;
  document.getElementById('g-s1-d').textContent = t.gS1D;
  document.getElementById('g-s1-btn').textContent = t.gS1Btn;
  document.getElementById('g-s2-t').textContent = t.gS2T;
  document.getElementById('g-s2-d').textContent = t.gS2D;
  document.getElementById('g-s2-btn').textContent = t.gS2Btn;
  document.getElementById('g-s3-t').textContent = t.gS3T;
  document.getElementById('g-s3-d').textContent = t.gS3D;
  document.getElementById('g-s3-btn').textContent = t.gS3Btn;
  document.getElementById('g-s4-t').textContent = t.gS4T;
  document.getElementById('g-s4-d').textContent = t.gS4D;
  document.getElementById('g-s4-btn').textContent = t.gS4Btn;
  document.getElementById('g-s5-t').textContent = t.gS5T;
  document.getElementById('g-s5-d').textContent = t.gS5D;
  document.getElementById('g-s5-btn').textContent = t.gS5Btn;
  document.getElementById('page-title').textContent = t.titles[currentTab] || 'StageBot';
  renderTable();
}

function showTab(name, btn) {
  currentTab = name;
  document.querySelectorAll('section').forEach(s => s.classList.add('hidden'));
  document.getElementById('tab-' + name).classList.remove('hidden');
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  document.getElementById('page-title').textContent = I18N[currentLang].titles[name] || 'StageBot';
  if (name === 'questions' && typeof loadQuestions === 'function') loadQuestions();
}

async function loadApps() {
  const res = await fetch(apiUrl('/api/applications'));
  const data = await res.json();
  allRows = data.rows || [];
  document.getElementById('st-total').textContent = data.counts.total;
  document.getElementById('st-applied').textContent = data.counts.applied;
  document.getElementById('st-drafted').textContent = data.counts.drafted;
  document.getElementById('st-manual').textContent = data.counts.manual_portal;
  document.getElementById('st-login').textContent = data.counts.needs_login || 0;
  document.getElementById('st-other').textContent = (data.counts.expired || 0) + (data.counts.skipped || 0);
  countsCache = data.counts;
  const t = I18N[currentLang];
  document.getElementById('lbl-apply-platforms').textContent = t.platforms;
  updateApplyButtonCounts();
  renderTable();
}

function detectSource(url) {
  const u = (url || '').toLowerCase();
  if (u.startsWith('spontaneous://')) return 'Spontaneous';
  if (u.includes('linkedin.com')) return 'LinkedIn';
  if (u.includes('hellowork.com')) return 'HelloWork';
  if (u.includes('welcometothejungle.com')) return 'WTTJ';
  if (u.includes('jobteaser.com')) return 'JobTeaser';
  if (u.includes('indeed.com')) return 'Indeed';
  if (u.includes('apec.fr')) return 'APEC';
  if (u.includes('meteojob.com')) return 'Meteojob';
  return 'Other';
}

function renderTable() {
  const t = I18N[currentLang];
  const q = document.getElementById('filter-q').value.toLowerCase();
  const st = document.getElementById('filter-status').value;
  const srcFilter = document.getElementById('filter-source').value;
  const qFilter = document.getElementById('filter-quality').value;
  const tbody = document.getElementById('apps-tbody');
  tbody.innerHTML = '';
  visibleUrlsCache = [];
  for (const r of allRows) {
    if (st && r.Status !== st) continue;
    const src = detectSource(r.URL);
    if (srcFilter && src !== srcFilter) continue;
    const qual = (r.Quality || 'medium').toLowerCase();
    if (qFilter && qual !== qFilter) continue;
    const blob = `${src} ${qual} ${r.Company} ${r.Role} ${r.Matched_Keywords} ${r.Notes || ''}`.toLowerCase();
    if (q && !blob.includes(q)) continue;
    if (r.URL) visibleUrlsCache.push(r.URL);
    const qColor = qual === 'high' ? 'var(--green)' : (qual === 'medium' ? 'var(--amber)' : 'var(--muted)');
    const qLabel = qual === 'high' ? 'High' : (qual === 'medium' ? 'Medium' : 'Low');
    const noteText = (r.Notes || '').trim();
    const noteCell = noteText
      ? `<span title="${escapeHtml(noteText)}" style="font-size:11.5px;color:var(--amber);display:inline-block;max-width:280px;word-break:break-word;">${escapeHtml(noteText)}</span>`
      : `<span style="color:var(--muted);">—</span>`;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input type="checkbox" ${selectedRemake.has(r.URL) ? 'checked' : ''} onchange="toggleRemake('${encodeURIComponent(r.URL || '')}', this.checked)"></td>
      <td style="color:var(--muted);white-space:nowrap;">${escapeHtml(r.Date || '')}</td>
      <td><span style="font-size:11px;padding:2px 7px;border-radius:999px;border:1px solid var(--border);color:var(--muted);">${src}</span></td>
      <td><span title="Score: ${r.Score ?? ''}" style="font-size:11px;font-weight:600;padding:2px 7px;border-radius:999px;border:1px solid ${qColor};color:${qColor};">${qLabel}</span></td>
      <td><strong>${escapeHtml(r.Company || '')}</strong></td>
      <td><a href="${escapeHtml(r.URL)}" target="_blank" style="color:var(--accent);text-decoration:none;">${escapeHtml(r.Role || '')}</a></td>
      <td>
        <select onchange="changeStatus('${encodeURIComponent(r.URL)}', this.value)">
          ${['drafted','applied','manual_portal','needs_login','expired','skipped'].map(s => `<option value="${s}" ${r.Status===s?'selected':''}>${s}</option>`).join('')}
        </select>
      </td>
      <td>${noteCell}</td>
      <td><button class="secondary" onclick="previewDocs('${encodeURIComponent(r.Company || '')}','${encodeURIComponent(r.CV_PDF || '')}','${encodeURIComponent(r.LM_PDF || '')}')">${t.viewPdf}</button></td>
      <td><button class="secondary" onclick="openInChrome('${encodeURIComponent(r.CV_PDF || '')}','${encodeURIComponent(r.URL || '')}')">${t.applyBtn}</button></td>
    `;
    tbody.appendChild(tr);
  }
  updateRemakeButton();
}

function updateRemakeButton() {
  const t = I18N[currentLang];
  const btn = document.getElementById('btn-remake');
  if (btn) btn.textContent = `${t.remakeSel} (${selectedRemake.size})`;
  const all = document.getElementById('chk-all');
  if (all) all.checked = visibleUrlsCache.length > 0 && visibleUrlsCache.every(u => selectedRemake.has(u));
}

function toggleRemake(urlEnc, on) {
  const url = decodeURIComponent(urlEnc);
  if (on) selectedRemake.add(url); else selectedRemake.delete(url);
  updateRemakeButton();
}

function toggleAllRemake(on) {
  for (const u of visibleUrlsCache) { if (on) selectedRemake.add(u); else selectedRemake.delete(u); }
  renderTable();
}

async function remakeSelected() {
  const t = I18N[currentLang];
  if (!selectedRemake.size) { alert(t.remakeNone); return; }
  if (!confirm(t.remakeConfirm.replace('{n}', selectedRemake.size))) return;
  const res = await fetch('/api/applications/remake', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({urls: Array.from(selectedRemake), debug: debugMode})
  });
  const data = await res.json();
  if (!data.ok) { alert(data.error || 'Another job is already running.'); return; }
  selectedRemake.clear();
  showTab('apply', document.getElementById('nav-apply'));
  pollLogs();
}

async function changeStatus(encodedUrl, status) {
  await fetch('/api/applications/status', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({url: decodeURIComponent(encodedUrl), status, debug: debugMode})
  });
  loadApps();
}

function previewDocs(companyEnc, cvEnc, lmEnc) {
  const company = decodeURIComponent(companyEnc);
  const cv = decodeURIComponent(cvEnc);
  const lm = decodeURIComponent(lmEnc);
  document.getElementById('preview-label').textContent = `${company}`;
  document.getElementById('pdf-cv').src = cv ? '/' + cv : '';
  document.getElementById('pdf-lm').src = lm ? '/' + lm : '';
  showTab('preview', document.getElementById('nav-preview'));
}

async function openInChrome(cvEnc, urlEnc) {
  const cv_pdf = decodeURIComponent(cvEnc);
  const url = decodeURIComponent(urlEnc);
  const matchRow = allRows.find(r => (r.URL || '') === url) || {};
  await fetch('/api/run', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      action:'open',
      cv_pdf,
      url,
      company: matchRow.Company || '',
      role: matchRow.Role || '',
      debug: debugMode,
    })
  });
  showTab('apply', document.getElementById('nav-apply'));
  pollLogs();
}

async function launchFetch() {
  const queries = document.getElementById('q-input').value.split(',').map(s=>s.trim()).filter(Boolean);
  const limit = parseInt(document.getElementById('q-limit').value || '10', 10);
  const sources = [];
  const liOn = document.getElementById('pill-li').classList.contains('active');
  if (liOn) sources.push('linkedin');
  if (document.getElementById('pill-hw').classList.contains('active')) sources.push('hellowork');
  if (document.getElementById('pill-wttj').classList.contains('active')) sources.push('wttj');
  if (document.getElementById('pill-jt').classList.contains('active')) sources.push('jobteaser');
  if (document.getElementById('pill-indeed').classList.contains('active')) sources.push('indeed');
  if (document.getElementById('pill-apec').classList.contains('active')) sources.push('apec');
  if (document.getElementById('pill-mj').classList.contains('active')) sources.push('meteojob');
  if (!queries.length) { alert('Enter at least one search query.'); return; }
  if (!sources.length) { alert('Select at least one platform to fetch from.'); return; }
  const quality = Array.from(document.querySelectorAll('#fetch-quality-group .pill.active')).map(p => p.dataset.q);
  if (!quality.length) { alert('Select at least one quality tier (High, Medium, Low).'); return; }
  await fetch('/api/run', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action:'search', limit, queries, sources, quality, easy_apply: liOn, debug: debugMode})
  });
  pollLogs();
}

const SOURCE_KEY_MAP = {
  LinkedIn: 'linkedin',
  HelloWork: 'hellowork',
  WTTJ: 'wttj',
  JobTeaser: 'jobteaser',
  Indeed: 'indeed',
  APEC: 'apec',
  Meteojob: 'meteojob',
};

function updateApplyButtonCounts() {
  const t = I18N[currentLang];
  const selected = new Set(Array.from(document.querySelectorAll('#apply-sources-group .pill.active')).map(p => p.dataset.src));
  const selectedQ = new Set(Array.from(document.querySelectorAll('#apply-quality-group .pill.active')).map(p => p.dataset.q));
  let drafted = 0, manual = 0, login = 0;
  for (const r of allRows) {
    const key = SOURCE_KEY_MAP[detectSource(r.URL)];
    if (!key || !selected.has(key)) continue;
    const qual = (r.Quality || 'medium').toLowerCase();
    if (!selectedQ.has(qual)) continue;
    const st = (r.Status || '').trim().toLowerCase();
    if (st === 'drafted') drafted++;
    else if (st === 'manual_portal') manual++;
    else if (st === 'needs_login') login++;
  }
  document.getElementById('btn-retry-drafted').textContent = `${t.retryDrafted} (${drafted})`;
  document.getElementById('btn-retry-manual').textContent = `${t.retryManual} (${manual})`;
  document.getElementById('btn-retry-login').textContent = `${t.retryLogin} (${login})`;
}

function toggleApplySource(btn) { btn.classList.toggle('active'); updateApplyButtonCounts(); }
function toggleApplyQuality(btn) { btn.classList.toggle('active'); updateApplyButtonCounts(); }
function toggleAllApplySources(on) {
  document.querySelectorAll('#apply-sources-group .pill').forEach(p => p.classList.toggle('active', on));
  updateApplyButtonCounts();
}

async function skipFilteredRows() {
  if (!visibleUrlsCache.length) { alert('No matching offers to skip.'); return; }
  if (!confirm(`Mark ${visibleUrlsCache.length} filtered offer(s) as 'skipped'?`)) return;
  await fetch('/api/applications/bulk_status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({urls: visibleUrlsCache, status: 'skipped', debug: debugMode})
  });
  await loadApps();
}

async function runRetry(retry_status) {
  const sources = Array.from(document.querySelectorAll('#apply-sources-group .pill.active')).map(p => p.dataset.src);
  if (!sources.length) { alert('Select at least one platform.'); return; }
  const quality = Array.from(document.querySelectorAll('#apply-quality-group .pill.active')).map(p => p.dataset.q);
  if (!quality.length) { alert('Select at least one quality tier (High, Medium, Low).'); return; }
  const selectedSet = new Set(sources);
  const qualitySet = new Set(quality);
  const matchingCount = allRows.filter(r => {
    const key = SOURCE_KEY_MAP[detectSource(r.URL)];
    const qual = (r.Quality || 'medium').toLowerCase();
    return key && selectedSet.has(key) && qualitySet.has(qual) && (r.Status || '').trim().toLowerCase() === retry_status;
  }).length;
  if (!matchingCount) { alert(`No '${retry_status}' offers match the selected platforms and quality tiers.`); return; }
  const skip_external = document.getElementById('pill-skip').classList.contains('active');
  const isDebug = debugMode;
  await fetch('/api/run', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action:'retry', retry_status, sources, quality, limit: 200, auto: runMode === 'auto', debug: isDebug, skip_external})
  });
  pollLogs();
}

let manualCurrentCache = null;

async function refreshManualCurrent() {
  try {
    const res = await fetch('/api/manual_current');
    const mc = await res.json();
    const card = document.getElementById('manual-current-card');
    if (mc && mc.active) {
      manualCurrentCache = mc;
      const isDebug = mc.mode === 'debug';
      const t = I18N[currentLang];
      const progEl = document.getElementById('mc-progress');
      const stEl = document.getElementById('mc-status');
      card.style.borderColor = isDebug ? 'var(--amber)' : 'var(--accent)';
      progEl.style.color = isDebug ? 'var(--amber)' : 'var(--accent)';
      progEl.textContent = isDebug
        ? `${t.debugCardLabel} (${mc.index}/${mc.total})`
        : `Semi-Automatic (${mc.index}/${mc.total})`;
      document.getElementById('mc-title').textContent = `${mc.company} — ${mc.role}`;
      document.getElementById('mc-btn-finder')?.classList.toggle('hidden', isDebug);
      document.getElementById('mc-btn-applied')?.classList.toggle('hidden', isDebug);
      document.getElementById('mc-btn-skip')?.classList.toggle('hidden', isDebug);
      document.getElementById('mc-btn-next')?.classList.toggle('hidden', !isDebug);
      if (isDebug && stEl) {
        stEl.style.color = 'var(--amber)';
        if (mc.debug_state === 'running') {
          stEl.textContent = 'Running auto-fill in visible Chrome...';
        } else if (mc.debug_result === 'debug_ready_submit') {
          stEl.textContent = 'Paused: Reached Submit button (NOT sent — dry-run, CSV untouched).';
        } else if (mc.debug_result) {
          stEl.textContent = `Paused: Stopped with '${mc.debug_result}' (CSV untouched). Inspect Chrome or click Next Offer.`;
        }
      } else if (stEl) {
        stEl.style.color = 'var(--accent)';
      }
      card.classList.remove('hidden');
    } else {
      manualCurrentCache = null;
      document.getElementById('mc-status').textContent = '';
      card.classList.add('hidden');
    }
  } catch (e) {}
}

function openManualPreview() {
  if (!manualCurrentCache) return;
  previewDocs(
    encodeURIComponent(manualCurrentCache.company || ''),
    encodeURIComponent(manualCurrentCache.cv_pdf || ''),
    encodeURIComponent(manualCurrentCache.lm_pdf || '')
  );
}

async function manualAction(action) {
  const stEl = document.getElementById('mc-status');
  if (action === 'fill' && stEl) stEl.textContent = 'Auto-filling active page...';
  const res = await fetch('/api/manual_action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action})
  });
  const data = await res.json();
  if (action === 'fill' && stEl) {
    stEl.textContent = data.ok ? 'Triggered auto-fill on current browser tab — check Live Logs.' : 'Fill failed.';
  }
  if (action === 'skip' || action === 'applied' || action === 'next') {
    setTimeout(() => { loadApps(); refreshManualCurrent(); }, 600);
  }
}

async function stopTask() { await fetch('/api/stop', {method:'POST'}); pollLogs(); }

async function copyLogs(btn) {
  const txt = document.getElementById('live-log-apply').textContent || document.getElementById('live-log-fetch').textContent || '';
  await navigator.clipboard.writeText(txt);
  const old = btn.textContent;
  btn.textContent = 'Copied!';
  setTimeout(() => { btn.textContent = old; }, 1200);
}

async function runLogin(stayOnGuide = false) {
  await fetch('/api/run', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:'login'})});
  if (!stayOnGuide) showTab('guide', document.getElementById('nav-guide'));
  document.getElementById('g-login-status').textContent = I18N[currentLang].gLoginMsg;
}

async function runSync() {
  if (debugMode) {
    alert('GitHub sync is disabled in Debug Sandbox mode (uses fake Droit profile).');
    return;
  }
  await fetch('/api/run', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:'sync'})});
  showTab('fetch', document.getElementById('nav-fetch'));
  pollLogs();
}

let logViewMode = localStorage.getItem('stagebot_log_view') || 'clean';
function setLogView(mode) {
  logViewMode = mode === 'raw' ? 'raw' : 'clean';
  localStorage.setItem('stagebot_log_view', logViewMode);
  for (const sfx of ['fetch', 'apply']) {
    document.getElementById('lv-clean-' + sfx)?.classList.toggle('active', logViewMode === 'clean');
    document.getElementById('lv-raw-' + sfx)?.classList.toggle('active', logViewMode === 'raw');
    document.getElementById('clean-log-' + sfx)?.classList.toggle('hidden', logViewMode !== 'clean');
    document.getElementById('log-summary-' + sfx)?.classList.toggle('hidden', logViewMode !== 'clean');
    document.getElementById('live-log-' + sfx)?.classList.toggle('hidden', logViewMode !== 'raw');
  }
}

function formatBailoutMessage(company, detail) {
  const cleanDetail = detail.split('Locator.click:')[0].trim();
  let action = '';
  if (/Login wall detected/i.test(cleanDetail)) {
    action = 'Action: Click "Open Login Chrome" in Guide (or run ./stage login), sign in, then use Retry Needs Login.';
  } else if (/Skipped external ATS/i.test(cleanDetail)) {
    action = 'Action: External portal — switch to Semi-Automatic (with Skip External OFF) or Skip it in Tracker.';
  } else if (/validation errors/i.test(cleanDetail)) {
    action = 'Action: Missing/invalid fields shown above — add answers in Q&A & Rules or finish in Semi-Automatic.';
  } else if (/unanswered required/i.test(cleanDetail)) {
    action = 'Action: Add these question answers in Tab 7 (Q&A & Rules), then retry.';
  } else if (/No Next\/Submit button found/i.test(cleanDetail)) {
    action = 'Action: Form layout not recognized automatically — open in Semi-Automatic to submit.';
  } else if (/confirmation was not detected/i.test(cleanDetail)) {
    action = 'Action: Submit was clicked; check debug screenshot or portal — if sent, mark Applied in Tracker.';
  } else if (/Stuck on step/i.test(cleanDetail)) {
    action = 'Action: Next button did not advance — check debug screenshot or finish in Semi-Automatic.';
  } else if (/Tab closed/i.test(cleanDetail)) {
    action = 'Action: Tab closed during redirect — retry in Semi-Automatic.';
  }
  const shortDetail = cleanDetail.replace(/\s*\|\s*Visible buttons:.*$/, '').slice(0, 180);
  const hintHtml = action ? `<div style="font-size:11.5px; color:var(--muted); margin-top:2px;">${escapeHtml(action)}</div>` : '';
  return `<strong>${escapeHtml(company)}</strong> — ${escapeHtml(shortDetail)}${hintHtml}`;
}

function renderCleanLogs(lines) {
  const counts = { applied: 0, manual: 0, tailored: 0, llm: 0, searched: 0 };
  const events = [];
  for (const raw of lines) {
    const line = (raw || '').trim();
    if (!line || line.startsWith('---') || line.startsWith('Call log:') || line.startsWith('- waiting for') || line.startsWith('- attempting click') || line.startsWith('× ') || line.startsWith('- element is') || line.startsWith('- scrolling') || line.startsWith('- done scrolling') || line.startsWith('- <div') || line.startsWith('- retrying')) continue;
    let m;
    if ((m = line.match(/^===\s*(?:Manual Review|Semi-Automatic Apply)\s*\[([^\]]+)\]:\s*(.+?)\s*===$/))) {
      events.push({ cls: 'info', badge: `Step ${m[1]}`, text: `<strong>${escapeHtml(m[2])}</strong> — Opening browser &amp; Finder...` });
    } else if ((m = line.match(/^===\s*Debug Auto-Apply\s*\[([^\]]+)\]:\s*(.+?)\s*===$/))) {
      events.push({ cls: 'warn', badge: `Debug ${m[1]}`, text: `<strong>${escapeHtml(m[2])}</strong> — Opening visible Chrome (dry-run, no submit)...` });
    } else if ((m = line.match(/^\[DEBUG\]\s+\[([^\]]+)\]\s+(.+)$/))) {
      events.push({ cls: 'warn', badge: 'Debug', text: `<strong>${escapeHtml(m[1])}</strong> — ${escapeHtml(m[2])}` });
    } else if (line.startsWith('===')) {
      continue;
    } else if ((m = line.match(/^\[\+\]\s+\[([^\]]+)\]\s+(?:Confirmed|Submitted)\s+->\s+'applied'/)) || (m = line.match(/^\[\+\]\s+Confirmed submission for\s+([^!]+)!/))) {
      counts.applied++;
      events.push({ cls: 'ok', badge: 'Applied', text: `<strong>${escapeHtml(m[1])}</strong> — Confirmed &amp; saved as applied.` });
    } else if ((m = line.match(/^\[\+\]\s+\[([^\]]+)\]\s+Re-filled current page:\s*(.+)$/))) {
      events.push({ cls: 'ok', badge: 'Filled', text: `<strong>${escapeHtml(m[1])}</strong> — ${escapeHtml(m[2])}` });
    } else if ((m = line.match(/^\[!\]\s+Bailout\s+\[([^\]]+)\]:\s*(.+)$/))) {
      counts.manual++;
      events.push({ cls: 'warn', badge: 'Needs Action', text: formatBailoutMessage(m[1], m[2]) });
    } else if ((m = line.match(/^\[\*\]\s+\[([^\]]+)\]\s+Ready — (.+)$/))) {
      events.push({ cls: 'ok', badge: 'Your Turn', text: `<strong>${escapeHtml(m[1])}</strong> — Ready. Review, click Fill Current Page if needed, submit, then close tab.` });
    } else if ((m = line.match(/^\[\*\]\s+\[([^\]]+)\]\s+Marked '([^']+)' from UI/))) {
      events.push({ cls: 'info', badge: m[2] === 'applied' ? 'Applied' : 'Skipped', text: `<strong>${escapeHtml(m[1])}</strong> — Marked '${escapeHtml(m[2])}', moving to next offer.` });
    } else if ((m = line.match(/^\[LLM:([^\]]+)\]\s+(.+)$/))) {
      counts.llm++;
      events.push({ cls: 'ai', badge: 'AI', text: escapeHtml(m[2]) });
    } else if ((m = line.match(/^\[\*\]\s+\[(\d+\/\d+)\]\s+Tailoring\s+\(([^)]+)\):\s*(.+)$/))) {
      counts.tailored++;
      events.push({ cls: 'ok', badge: 'Tailored', text: `[${m[1]}] ${escapeHtml(m[2])}: ${escapeHtml(m[3])}` });
    } else if ((m = line.match(/^\[\*\]\s+Searching\s+([^:]+):\s*(.+)$/))) {
      counts.searched++;
      events.push({ cls: 'info', badge: 'Search', text: `${escapeHtml(m[1])}: ${escapeHtml(m[2].replace(/\s*\.\.\.$/, ''))}` });
    } else if ((m = line.match(/^\[\*\]\s+\[([^\]]+)\]\s+Apply clicked:\s*(\w+)\s*\|\s*Target:\s*(.+)$/))) {
      events.push({ cls: 'info', badge: 'Form', text: `<strong>${escapeHtml(m[1])}</strong> — Target: ${escapeHtml(m[3])}` });
    } else if ((m = line.match(/^\[\*\]\s+\[([^\]]+)\]\s+Opening\s+(https?:\/\/\S+)/))) {
      events.push({ cls: 'info', badge: 'Open', text: `<strong>${escapeHtml(m[1])}</strong> <span style="color:var(--muted);font-size:11px;">(${escapeHtml(m[2].replace(/^https?:\/\/(www\.)?/, '').slice(0, 45))})</span>` });
    } else if (line.startsWith('[+] Done!') || line.includes('Starting:') || line.includes('Finished (exit code') || line.includes('Selected top ') || line.includes('Found ') || line.includes('Retrying ')) {
      events.push({ cls: 'info', badge: 'Info', text: escapeHtml(line.replace(/^\[\*\]\s*|^\[\+\]\s*/, '')) });
    } else if (line.startsWith('[!]')) {
      events.push({ cls: 'err', badge: 'Warn', text: escapeHtml(line.replace(/^\[!\]\s*/, '').slice(0, 160)) });
    }
  }
  const chips = [];
  if (counts.applied) chips.push(`<span class="log-chip green">Applied: ${counts.applied}</span>`);
  if (counts.manual) chips.push(`<span class="log-chip amber">Manual: ${counts.manual}</span>`);
  if (counts.tailored) chips.push(`<span class="log-chip green">Tailored: ${counts.tailored}</span>`);
  if (counts.llm) chips.push(`<span class="log-chip purple">AI Answered: ${counts.llm}</span>`);
  if (counts.searched) chips.push(`<span class="log-chip blue">Queries: ${counts.searched}</span>`);

  const html = events.length
    ? events.map(e => `<div class="log-row"><span class="log-badge ${e.cls}">${e.badge}</span><span>${e.text}</span></div>`).join('')
    : `<div style="color:var(--muted);">${escapeHtml(I18N[currentLang].ready)}</div>`;
  for (const sfx of ['fetch', 'apply']) {
    const sumEl = document.getElementById('log-summary-' + sfx);
    const cleanEl = document.getElementById('clean-log-' + sfx);
    if (sumEl) sumEl.innerHTML = chips.join('');
    if (cleanEl) { cleanEl.innerHTML = html; cleanEl.scrollTop = cleanEl.scrollHeight; }
  }
}

let pollTimer = null;
async function pollLogs() {
  if (pollTimer) clearInterval(pollTimer);
  const t = I18N[currentLang];
  const tick = async () => {
    const res = await fetch('/api/logs');
    const data = await res.json();
    const lines = data.lines || [];
    const txt = lines.join('\n') || t.ready;
    for (const id of ['live-log-fetch', 'live-log-apply']) {
      const el = document.getElementById(id);
      if (el) { el.textContent = txt; el.scrollTop = el.scrollHeight; }
    }
    renderCleanLogs(lines);
    refreshManualCurrent();
    for (const id of ['run-state-fetch', 'run-state-apply']) {
      const st = document.getElementById(id);
      if (st) st.textContent = data.running ? t.running : t.done;
    }
    if (!data.running) {
      clearInterval(pollTimer);
      loadApps();
      if (typeof loadQuestions === 'function') loadQuestions();
    }
  };
  await tick();
  pollTimer = setInterval(tick, 1200);
}

async function loadProfile() {
  const res = await fetch(apiUrl('/api/profile'));
  const data = await res.json();
  document.getElementById('master-md').textContent = data.master_md || '';
  const p = data.profile || {};
  if (p.personal && p.personal.full_name) {
    document.title = debugMode ? `StageBot [DEBUG] — ${p.personal.full_name}` : `StageBot — ${p.personal.full_name}`;
  }
  if (p.education && p.internship) {
    document.getElementById('sidebar-sub').textContent = `${p.education.level} · ${p.internship.start_date} → ${p.internship.end_date}`;
  }
  if (p.search && Array.isArray(p.search.queries)) {
    document.getElementById('q-input').value = p.search.queries.join(', ');
    document.getElementById('pref-queries').value = p.search.queries.join(', ');
  }
  const pr = p.preferences || {};
  document.getElementById('pref-target-roles').value = (pr.target_roles || []).join(', ');
  document.getElementById('pref-avoid-roles').value = (pr.avoid_roles || []).join(', ');
  document.getElementById('pref-pref-locs').value = (pr.preferred_locations || []).join(', ');
  document.getElementById('pref-idf-locs').value = (pr.idf_locations || []).join(', ');
  document.getElementById('pref-work-types').value = (pr.work_types || []).join(', ');
  document.getElementById('pref-avoid-locs').value = (pr.avoid_locations || []).join(', ');
}

async function importResumePdf(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const stEl = document.getElementById('pref-save-status');
  const gEl = document.getElementById('g-resume-status');
  if (stEl) stEl.textContent = 'Parsing resume...';
  if (gEl) gEl.textContent = 'Parsing resume...';
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const res = await fetch('/api/import_resume', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({filename: file.name, pdf_base64: reader.result, debug: debugMode}),
      });
      const data = await res.json();
      if (data.ok) {
        const msg = `Imported ${data.updated_fields.length} field(s)${data.used_gemini ? ' via Gemini' : ' via regex'}!`;
        if (stEl) stEl.textContent = msg;
        if (gEl) gEl.textContent = msg;
        await loadProfile();
        await loadApps();
      } else {
        const err = data.error || 'Import failed';
        if (stEl) stEl.textContent = err;
        if (gEl) gEl.textContent = err;
      }
    } catch (e) {
      if (stEl) stEl.textContent = 'Import error';
    }
    input.value = '';
  };
  reader.readAsDataURL(file);
}

async function savePreferences() {
  const splitList = (id) => document.getElementById(id).value.split(',').map(s => s.trim()).filter(Boolean);
  const preferences = {
    target_roles: splitList('pref-target-roles'),
    avoid_roles: splitList('pref-avoid-roles'),
    preferred_locations: splitList('pref-pref-locs'),
    idf_locations: splitList('pref-idf-locs'),
    work_types: splitList('pref-work-types'),
    avoid_locations: splitList('pref-avoid-locs'),
  };
  const queries = splitList('pref-queries');
  await fetch('/api/preferences', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({preferences, queries, debug: debugMode}),
  });
  document.getElementById('pref-save-status').textContent = 'Saved & reclassified!';
  await loadProfile();
  await loadApps();
}
