// Axiolytics — frontend.

// ---------- page tabs ----------
document.querySelectorAll('.page-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.page-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('page-' + btn.dataset.page).classList.add('active');
  });
});

const md = (text) => {
  if (typeof marked === 'undefined') return text;
  marked.setOptions({ breaks: true, gfm: true });
  return marked.parse(text || '');
};

// ============================================================
// PAGE 1 — Knowledge Agent
// ============================================================

const agentInput = document.getElementById('agent-question');
const agentBtn = document.getElementById('agent-ask-btn');
const agentLoading = document.getElementById('agent-loading');
const agentAnswer = document.getElementById('agent-answer');
const agentEcho = document.getElementById('agent-question-echo');
const agentBadges = document.getElementById('agent-source-badges');
const agentKgCtx = document.getElementById('agent-kg-context');
const agentBody = document.getElementById('agent-answer-body');
const agentUsage = document.getElementById('agent-usage');

document.querySelectorAll('.chip[data-q]').forEach(c => {
  c.addEventListener('click', () => {
    agentInput.value = c.dataset.q;
    askAgent();
  });
});
agentBtn.addEventListener('click', askAgent);
agentInput.addEventListener('keydown', e => { if (e.key === 'Enter') askAgent(); });

async function askAgent() {
  const q = (agentInput.value || '').trim();
  if (!q) return;
  agentBtn.disabled = true;
  agentLoading.hidden = false;
  agentAnswer.hidden = true;
  try {
    const r = await fetch('/api/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ question: q }),
    });
    const data = await r.json();
    if (r.status === 401) { window.location.href = '/login'; return; }
    if (!r.ok) throw new Error(data.error || 'request failed');
    renderAgentAnswer(q, data);
  } catch (err) {
    agentAnswer.hidden = false;
    agentEcho.textContent = q;
    agentBadges.innerHTML = '';
    agentKgCtx.hidden = true;
    agentBody.innerHTML = `<p style="color:var(--bad)">Error: ${err.message}</p>`;
    agentUsage.textContent = '';
  } finally {
    agentBtn.disabled = false;
    agentLoading.hidden = true;
  }
}

function renderAgentAnswer(question, data) {
  agentAnswer.hidden = false;
  agentEcho.textContent = question;

  // badges
  const kgUsed = data.answer && data.answer.kg_used;
  agentBadges.innerHTML =
    (kgUsed ? '<span class="badge kg">KG-grounded</span>' : '<span class="badge gap">no KG match</span>') +
    '<span class="badge llm">LLM-extended</span>';

  // KG context strip
  if (data.kg_context) {
    const c = data.kg_context.company || {};
    agentKgCtx.hidden = false;
    agentKgCtx.innerHTML = `<strong>KG match:</strong> ${escapeHtml(c.company_name || '')} — `
      + `industry <em>${escapeHtml(data.kg_context.industry || '')}</em>. `
      + `Reported metric families on file: ${(data.kg_context.available_metrics || []).slice(0, 5).map(escapeHtml).join(', ') || '—'}`;
  } else {
    agentKgCtx.hidden = true;
  }

  agentBody.innerHTML = md(data.answer.answer_markdown);
  const u = data.answer.usage || {};
  agentUsage.textContent =
    `Tokens — input: ${u.input || 0}, output: ${u.output || 0}, ` +
    `cache read: ${u.cache_read || 0}, cache write: ${u.cache_write || 0}`;
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

// ============================================================
// PAGE 2 — Analytics for Reporting
// ============================================================

const SEARCH = document.getElementById('company-search');
const SEARCH_RESULTS = document.getElementById('company-results');
const ANALYTICS_BODY = document.getElementById('analytics-body');
const ANALYTICS_LOADING = document.getElementById('analytics-loading');
const ANALYTICS_LOAD_TEXT = document.getElementById('analytics-loading-text');

let currentCompany = null;
let currentSnapshot = null;
let currentSections = {};   // section_id -> { markdown, included }
let trendChart = null;

const SECTION_DEFS = [
  { id: 'executive_summary', title: 'Executive summary' },
  { id: 'industry_materiality', title: 'Industry materiality (SASB)' },
  { id: 'disclosure_coverage', title: 'Disclosure coverage' },
  { id: 'trend_analysis', title: 'Trend analysis' },
  { id: 'gaps_and_recommendations', title: 'Gaps & recommendations' },
];

let searchTimer = null;
SEARCH.addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(searchCompanies, 250);
});

async function searchCompanies() {
  const q = (SEARCH.value || '').trim();
  if (q.length < 2) { SEARCH_RESULTS.innerHTML = ''; return; }
  const r = await fetch('/api/company/search?q=' + encodeURIComponent(q));
  const data = await r.json();
  SEARCH_RESULTS.innerHTML = (data.results || []).map(c => `
    <div class="company-result" data-pid="${c.perm_id}">
      <strong>${escapeHtml(c.company_name)}</strong>
      <span>${escapeHtml(c.sasb_industry)}${c.ticker ? ' · ' + escapeHtml(c.ticker) : ''}</span>
    </div>
  `).join('');
  SEARCH_RESULTS.querySelectorAll('.company-result').forEach(el => {
    el.addEventListener('click', () => loadCompany(el.dataset.pid));
  });
}

async function loadCompany(permId) {
  ANALYTICS_BODY.hidden = true;
  ANALYTICS_LOADING.hidden = false;
  ANALYTICS_LOAD_TEXT.textContent = 'Loading company snapshot…';
  SEARCH_RESULTS.innerHTML = '';
  try {
    const r = await fetch(`/api/company/${permId}/snapshot`);
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || 'failed');
    currentCompany = data.company;
    currentSnapshot = data;
    renderSnapshot(data);
    renderMateriality(data.coverage);
    renderTrendControls(data.metric_summaries);
    initReportCards();
    ANALYTICS_BODY.hidden = false;
  } catch (err) {
    alert('Failed to load company: ' + err.message);
  } finally {
    ANALYTICS_LOADING.hidden = true;
  }
}

function renderSnapshot(data) {
  const c = data.company;
  document.getElementById('snap-company-name').textContent = c.company_name;
  document.getElementById('snap-industry').textContent = c.sasb_industry;
  document.getElementById('snap-ticker').textContent = c.ticker || '—';
  const cov = data.coverage || {};
  document.getElementById('snap-topics').textContent = cov.topics_total || 0;
  document.getElementById('snap-coverage').textContent =
    (cov.topics_covered || 0) + ' / ' + (cov.topics_total || 0);
  document.getElementById('snap-metrics').textContent = (data.metric_summaries || []).length;
}

function renderMateriality(coverage) {
  const grid = document.getElementById('materiality-grid');
  if (!coverage || !coverage.topics) { grid.innerHTML = '<em>No materiality data.</em>'; return; }
  grid.innerHTML = coverage.topics.map(t => `
    <div class="mat-tile ${t.covered ? 'covered' : 'gap'}" title="${escapeHtml(t.summary || '')}">
      <div class="mat-name">${escapeHtml(t.topic)}</div>
      <div class="mat-meta">
        ${t.covered
          ? `${t.match_count} metric${t.match_count === 1 ? '' : 's'} on file`
          : 'No data — disclosure gap'}
      </div>
    </div>
  `).join('');
}

function renderTrendControls(metricSummaries) {
  const sel = document.getElementById('trend-metric');
  if (!metricSummaries || !metricSummaries.length) {
    sel.innerHTML = '<option>(no multi-year metrics on file)</option>';
    if (trendChart) { trendChart.destroy(); trendChart = null; }
    return;
  }
  sel.innerHTML = metricSummaries.map(m =>
    `<option value="${escapeHtml(m.metric_name)}" data-unit="${escapeHtml(m.unit || '')}">
       ${escapeHtml(m.metric_name)} (${m.n_years} yrs)
     </option>`
  ).join('');
  sel.onchange = () => loadTrend(sel.value, sel.options[sel.selectedIndex].dataset.unit);
  loadTrend(sel.value, sel.options[sel.selectedIndex].dataset.unit);
}

async function loadTrend(metricName, unit) {
  if (!currentCompany) return;
  document.getElementById('trend-unit-label').textContent = unit ? `Unit: ${unit}` : '';
  const r = await fetch(`/api/company/${currentCompany.perm_id}/trend?metric=${encodeURIComponent(metricName)}`);
  const data = await r.json();
  drawTrendChart(data);
}

function drawTrendChart(data) {
  const ctx = document.getElementById('trend-chart');
  const labels = (data.points || []).map(p => p.metric_year);
  const values = (data.points || []).map(p => p.metric_value);
  if (trendChart) trendChart.destroy();
  trendChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: data.metric_name,
        data: values,
        borderColor: '#0072CE',
        backgroundColor: 'rgba(0,114,206,0.15)',
        fill: true,
        tension: 0.25,
        pointRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false } },
        y: { beginAtZero: false, ticks: { callback: v => formatNumber(v) } },
      }
    }
  });
}

function formatNumber(v) {
  if (v == null) return '';
  if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(1) + 'B';
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(1) + 'k';
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

// ----- Report cards -----

function initReportCards() {
  currentSections = {};
  const wrap = document.getElementById('report-sections');
  wrap.innerHTML = SECTION_DEFS.map(s => `
    <div class="report-card included" data-id="${s.id}">
      <div class="report-card-header">
        <label>
          <input type="checkbox" class="include-cb" checked />
          ${escapeHtml(s.title)}
        </label>
        <span class="section-status">not generated yet</span>
        <button class="regen" data-action="regen">Generate</button>
      </div>
      <textarea placeholder="Click 'Generate' to draft this section, then edit freely."></textarea>
      <div class="preview" hidden></div>
    </div>
  `).join('');
  // Wire up interactions
  wrap.querySelectorAll('.report-card').forEach(card => {
    const cb = card.querySelector('.include-cb');
    const regen = card.querySelector('button.regen');
    const ta = card.querySelector('textarea');
    const preview = card.querySelector('.preview');
    cb.addEventListener('change', () => {
      card.classList.toggle('included', cb.checked);
    });
    regen.addEventListener('click', () => regenerateSection(card));
    ta.addEventListener('input', () => {
      preview.hidden = !ta.value.trim();
      preview.innerHTML = md(ta.value);
    });
  });
  // also wire generate-all
  document.getElementById('generate-all-btn').onclick = generateAllSections;
  document.getElementById('export-pdf-btn').onclick = exportPdf;
}

async function regenerateSection(card) {
  const sectionId = card.dataset.id;
  const status = card.querySelector('.section-status');
  const btn = card.querySelector('button.regen');
  const ta = card.querySelector('textarea');
  const preview = card.querySelector('.preview');
  btn.disabled = true; btn.textContent = '…';
  status.textContent = 'generating';
  try {
    const r = await fetch(`/api/company/${currentCompany.perm_id}/report-section`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ section_id: sectionId }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || 'failed');
    ta.value = data.markdown;
    preview.hidden = false;
    preview.innerHTML = md(data.markdown);
    const u = data.usage || {};
    status.textContent = `generated · ${u.input || 0} in / ${u.output || 0} out · cache read ${u.cache_read || 0}`;
  } catch (err) {
    status.textContent = 'error: ' + err.message;
  } finally {
    btn.disabled = false; btn.textContent = 'Regenerate';
  }
}

async function generateAllSections() {
  const cards = document.querySelectorAll('.report-card');
  for (const card of cards) {
    await regenerateSection(card);
  }
}

async function exportPdf() {
  if (!currentCompany) return;
  const cards = document.querySelectorAll('.report-card');
  const sections = [];
  cards.forEach(card => {
    const cb = card.querySelector('.include-cb');
    if (!cb.checked) return;
    const ta = card.querySelector('textarea');
    if (!ta.value.trim()) return;
    sections.push({
      section_id: card.dataset.id,
      title: card.querySelector('label').innerText.trim(),
      markdown: ta.value,
    });
  });
  if (!sections.length) {
    alert('Select at least one section with content first.');
    return;
  }
  // Get the trend chart as PNG and attach to the trend section if included.
  const trendIdx = sections.findIndex(s => s.section_id === 'trend_analysis');
  if (trendIdx >= 0 && trendChart) {
    try {
      const dataUrl = trendChart.toBase64Image();
      sections[trendIdx].chart_png_b64 = dataUrl;
    } catch (e) { /* ignore */ }
  }
  const btn = document.getElementById('export-pdf-btn');
  btn.disabled = true; btn.textContent = 'Building PDF…';
  try {
    const r = await fetch('/api/generate-pdf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: `${currentCompany.company_name} — ESG Analytics`,
        subtitle: `SASB Industry: ${currentCompany.sasb_industry}`,
        filename: `${currentCompany.company_name.replace(/[^a-z0-9]+/gi, '_')}_ESG_report.pdf`,
        sections,
      }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.error || 'PDF build failed');
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${currentCompany.company_name.replace(/[^a-z0-9]+/gi, '_')}_ESG_report.pdf`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert('Export failed: ' + err.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Export selected as PDF';
  }
}
