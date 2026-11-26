let qaData = {unanswered: [], form_answers: {}, custom_rules: [], verified_skills: [], builtin_rules: []};
let dirtyUnanswered = new Set();
let llmCacheEntries = [];

async function loadQuestions() {
  const statusEl = document.getElementById('qa-save-status');
  try {
    const res = await fetch(typeof apiUrl === 'function' ? apiUrl('/api/questions') : '/api/questions');
    if (!res.ok) {
      if (statusEl) statusEl.textContent = `Error ${res.status}: Restart StageBot UI server so /api/questions is active.`;
      return;
    }
    qaData = await res.json();
    dirtyUnanswered.clear();
    if (statusEl) statusEl.textContent = '';
    document.getElementById('fa-save-status').textContent = '';
    document.getElementById('cr-save-status').textContent = '';
  } catch (e) {
    if (statusEl) statusEl.textContent = 'Failed to reach /api/questions — restart StageBot UI server.';
    return;
  }
  renderUnanswered();
  renderCustomRules();
  await loadLlmCache();

  const grid = document.getElementById('qa-form-answers-grid');
  grid.innerHTML = '';
  for (const [k, v] of Object.entries(qaData.form_answers || {})) {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex; flex-direction:column; gap:4px;';
    const lbl = document.createElement('label');
    lbl.textContent = k;
    lbl.style.cssText = 'font-size:12px; color:var(--muted);';
    const inp = document.createElement('input');
    inp.value = v || '';
    inp.dataset.faKey = k;
    inp.oninput = () => {
      inp.classList.add('dirty-input');
      document.getElementById('fa-save-status').textContent = 'Unsaved profile changes.';
    };
    wrap.appendChild(lbl);
    wrap.appendChild(inp);
    grid.appendChild(wrap);
  }

  const rulesBody = document.getElementById('qa-rules-tbody');
  rulesBody.innerHTML = '';
  for (const r of (qaData.builtin_rules || [])) {
    const tr = document.createElement('tr');
    const td1 = document.createElement('td');
    td1.innerHTML = `<strong>${escapeHtml(r.category)}</strong>`;
    const td2 = document.createElement('td');
    td2.textContent = r.rule;
    tr.appendChild(td1);
    tr.appendChild(td2);
    rulesBody.appendChild(tr);
  }

  const skillsEl = document.getElementById('qa-skills-list');
  skillsEl.innerHTML = '';
  for (const sk of (qaData.verified_skills || [])) {
    const span = document.createElement('span');
    span.textContent = sk;
    span.style.cssText = 'font-size:11px; padding:2px 8px; border-radius:999px; border:1px solid var(--border); color:var(--muted);';
    skillsEl.appendChild(span);
  }
}

function renderUnanswered() {
  const filterEl = document.getElementById('qa-filter');
  const tbody = document.getElementById('qa-unanswered-tbody');
  if (!filterEl || !tbody) return;
  const filter = filterEl.value;
  tbody.innerHTML = '';
  const items = qaData.unanswered || [];
  let shown = 0;
  items.forEach((item, idx) => {
    const hasAns = Boolean((item.answer || '').trim());
    if (filter === 'missing' && hasAns) return;
    if (filter === 'answered' && !hasAns) return;
    shown++;
    const tr = document.createElement('tr');
    if (dirtyUnanswered.has(idx)) tr.classList.add('dirty-row');

    const tdType = document.createElement('td');
    tdType.textContent = item.field_type || '';
    tdType.style.color = 'var(--muted)';

    const tdQ = document.createElement('td');
    tdQ.textContent = item.question || '';

    const tdOpts = document.createElement('td');
    (item.options || []).forEach(opt => {
      const chip = document.createElement('button');
      chip.className = 'secondary';
      chip.textContent = opt;
      chip.style.cssText = 'font-size:11px; padding:2px 7px; margin:2px;';
      chip.onclick = () => {
        qaData.unanswered[idx].answer = opt;
        dirtyUnanswered.add(idx);
        document.getElementById('qa-save-status').textContent = 'Unsaved answer changes.';
        renderUnanswered();
      };
      tdOpts.appendChild(chip);
    });

    const tdComp = document.createElement('td');
    tdComp.textContent = item.company || '';
    tdComp.style.color = 'var(--muted)';

    const tdAns = document.createElement('td');
    tdAns.style.cssText = 'display:flex; gap:6px; align-items:center;';
    const inp = document.createElement('input');
    inp.value = item.answer || '';
    inp.placeholder = 'Enter answer...';
    inp.style.flex = '1';
    if (dirtyUnanswered.has(idx)) inp.classList.add('dirty-input');
    inp.oninput = (e) => {
      qaData.unanswered[idx].answer = e.target.value;
      dirtyUnanswered.add(idx);
      tr.classList.add('dirty-row');
      inp.classList.add('dirty-input');
      document.getElementById('qa-save-status').textContent = 'Unsaved answer changes.';
    };
    const ruleBtn = document.createElement('button');
    ruleBtn.className = 'secondary';
    ruleBtn.textContent = '+ Rule';
    ruleBtn.title = 'Promote to reusable custom rule';
    ruleBtn.style.padding = '4px 8px';
    ruleBtn.onclick = () => promoteToRule(idx);

    const delBtn = document.createElement('button');
    delBtn.className = 'secondary';
    delBtn.textContent = '✕';
    delBtn.title = 'Remove';
    delBtn.style.padding = '4px 8px';
    delBtn.onclick = () => {
      qaData.unanswered.splice(idx, 1);
      dirtyUnanswered.add(-1);
      document.getElementById('qa-save-status').textContent = 'Unsaved answer changes.';
      renderUnanswered();
    };
    tdAns.appendChild(inp);
    tdAns.appendChild(ruleBtn);
    tdAns.appendChild(delBtn);

    tr.appendChild(tdType);
    tr.appendChild(tdQ);
    tr.appendChild(tdOpts);
    tr.appendChild(tdComp);
    tr.appendChild(tdAns);
    tbody.appendChild(tr);
  });
  if (shown === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="5" style="color:var(--muted); text-align:center;">No questions logged yet.</td>';
    tbody.appendChild(tr);
  }
}

async function saveUnansweredQuestions() {
  const isDbg = typeof debugMode !== 'undefined' && debugMode;
  const res = await fetch('/api/questions', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({type: 'unanswered', items: qaData.unanswered || [], debug: isDbg})
  });
  const data = await res.json();
  if (data.ok) {
    dirtyUnanswered.clear();
    renderUnanswered();
  }
  document.getElementById('qa-save-status').textContent = data.ok ? (isDbg ? 'Saved to debug sandbox!' : 'Saved to data/unanswered_questions.json!') : 'Save failed.';
}

async function saveFormAnswers() {
  const isDbg = typeof debugMode !== 'undefined' && debugMode;
  const updated = {};
  document.querySelectorAll('#qa-form-answers-grid input[data-fa-key]').forEach(inp => {
    updated[inp.dataset.faKey] = inp.value;
    inp.classList.remove('dirty-input');
  });
  const res = await fetch('/api/questions', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({type: 'form_answers', form_answers: updated, debug: isDbg})
  });
  const data = await res.json();
  document.getElementById('fa-save-status').textContent = data.ok ? (isDbg ? 'Saved to debug sandbox!' : 'Saved to config/profile.json!') : 'Save failed.';
}

function renderCustomRules() {
  const tbody = document.getElementById('qa-custom-rules-tbody');
  tbody.innerHTML = '';
  const rules = qaData.custom_rules || [];
  rules.forEach((rule, idx) => {
    const tr = document.createElement('tr');
    const tdKw = document.createElement('td');
    const inpKw = document.createElement('input');
    inpKw.value = rule.keyword || '';
    inpKw.placeholder = 'e.g. permis b';
    inpKw.style.width = '100%';
    inpKw.oninput = (e) => {
      qaData.custom_rules[idx].keyword = e.target.value;
      inpKw.classList.add('dirty-input');
      document.getElementById('cr-save-status').textContent = 'Unsaved rule changes.';
    };
    tdKw.appendChild(inpKw);

    const tdAns = document.createElement('td');
    const inpAns = document.createElement('input');
    inpAns.value = rule.answer || '';
    inpAns.placeholder = 'e.g. Non';
    inpAns.style.width = '100%';
    inpAns.oninput = (e) => {
      qaData.custom_rules[idx].answer = e.target.value;
      inpAns.classList.add('dirty-input');
      document.getElementById('cr-save-status').textContent = 'Unsaved rule changes.';
    };
    tdAns.appendChild(inpAns);

    const tdDel = document.createElement('td');
    const delBtn = document.createElement('button');
    delBtn.className = 'secondary';
    delBtn.textContent = '✕';
    delBtn.style.padding = '4px 8px';
    delBtn.onclick = () => {
      qaData.custom_rules.splice(idx, 1);
      document.getElementById('cr-save-status').textContent = 'Unsaved rule changes.';
      renderCustomRules();
    };
    tdDel.appendChild(delBtn);

    tr.appendChild(tdKw);
    tr.appendChild(tdAns);
    tr.appendChild(tdDel);
    tbody.appendChild(tr);
  });
  if (rules.length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="3" style="color:var(--muted); text-align:center;">No custom rules yet. Click + Add Rule or promote an answered question.</td>';
    tbody.appendChild(tr);
  }
}

function addCustomRule(keyword = '', answer = '') {
  qaData.custom_rules = qaData.custom_rules || [];
  qaData.custom_rules.push({keyword, answer});
  document.getElementById('cr-save-status').textContent = 'Unsaved rule changes.';
  renderCustomRules();
}

async function promoteToRule(idx) {
  const item = qaData.unanswered[idx];
  const ans = (item.answer || '').trim();
  if (!ans) {
    document.getElementById('qa-save-status').textContent = 'Enter an answer first before promoting to a rule.';
    return;
  }
  const kw = prompt('Keyword to match in future questions:', (item.question || '').toLowerCase());
  if (!kw || !kw.trim()) return;
  addCustomRule(kw.trim(), ans);
  await saveCustomRules();
}

async function saveCustomRules() {
  const isDbg = typeof debugMode !== 'undefined' && debugMode;
  const res = await fetch('/api/questions', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({type: 'custom_rules', custom_rules: qaData.custom_rules || [], debug: isDbg})
  });
  const data = await res.json();
  if (data.ok) {
    qaData.custom_rules = data.custom_rules || qaData.custom_rules;
    renderCustomRules();
  }
  document.getElementById('cr-save-status').textContent = data.ok ? (isDbg ? 'Saved rules to debug sandbox!' : 'Saved rules to config/profile.json!') : 'Save failed.';
}

async function loadLlmCache() {
  const statusEl = document.getElementById('llm-cache-status');
  try {
    const res = await fetch(typeof apiUrl === 'function' ? apiUrl('/api/llm_cache') : '/api/llm_cache');
    if (!res.ok) return;
    const data = await res.json();
    llmCacheEntries = data.entries || [];
    renderLlmCache();
  } catch (e) {
    if (statusEl) statusEl.textContent = 'Could not load LLM cache.';
  }
}

function renderLlmCache() {
  const tbody = document.getElementById('qa-llm-cache-tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  if (!llmCacheEntries.length) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="4" style="color:var(--muted); text-align:center;">No cached LLM answers yet.</td>';
    tbody.appendChild(tr);
    return;
  }
  llmCacheEntries.forEach((entry) => {
    const tr = document.createElement('tr');
    const tdType = document.createElement('td');
    tdType.textContent = entry.field_type || '';
    tdType.style.color = 'var(--muted)';

    const tdQ = document.createElement('td');
    tdQ.textContent = entry.question || '';

    const tdAns = document.createElement('td');
    const inp = document.createElement('input');
    inp.value = entry.answer || '';
    inp.style.width = '100%';
    tdAns.appendChild(inp);

    const tdAct = document.createElement('td');
    tdAct.style.cssText = 'display:flex; gap:6px;';
    const saveBtn = document.createElement('button');
    saveBtn.className = 'secondary';
    saveBtn.textContent = 'Save';
    saveBtn.style.padding = '4px 8px';
    saveBtn.onclick = async () => {
      const res = await fetch('/api/llm_cache', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'update', key: entry.key, answer: inp.value, debug: typeof debugMode !== 'undefined' && debugMode}),
      });
      const d = await res.json();
      document.getElementById('llm-cache-status').textContent = d.ok ? 'Updated cached answer.' : (d.error || 'Update failed.');
    };

    const ruleBtn = document.createElement('button');
    ruleBtn.className = 'secondary';
    ruleBtn.textContent = '+ Rule';
    ruleBtn.style.padding = '4px 8px';
    ruleBtn.onclick = async () => {
      const ans = inp.value.trim();
      if (!ans) return;
      const kw = prompt('Keyword to match in future questions:', (entry.question || '').toLowerCase());
      if (!kw || !kw.trim()) return;
      addCustomRule(kw.trim(), ans);
      await saveCustomRules();
    };

    const delBtn = document.createElement('button');
    delBtn.className = 'secondary';
    delBtn.textContent = '✕';
    delBtn.style.padding = '4px 8px';
    delBtn.onclick = async () => {
      const res = await fetch('/api/llm_cache', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'delete', key: entry.key, debug: typeof debugMode !== 'undefined' && debugMode}),
      });
      const d = await res.json();
      if (d.ok) {
        document.getElementById('llm-cache-status').textContent = 'Deleted cached answer.';
        await loadLlmCache();
      }
    };

    tdAct.appendChild(saveBtn);
    tdAct.appendChild(ruleBtn);
    tdAct.appendChild(delBtn);
    tr.appendChild(tdType);
    tr.appendChild(tdQ);
    tr.appendChild(tdAns);
    tr.appendChild(tdAct);
    tbody.appendChild(tr);
  });
}

setLang(currentLang);
setLogView(logViewMode);
loadApps();
loadProfile();
loadQuestions();
