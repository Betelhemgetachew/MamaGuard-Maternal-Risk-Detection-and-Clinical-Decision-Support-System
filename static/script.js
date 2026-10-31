/* ============================================================================
   MamaGuard frontend — vanilla JS, no build step, no dependencies.

   Talks to the FastAPI backend in main.py. Every page that Streamlit used to
   render server-side is rebuilt here as a DOM fragment refreshed on navigation.
   Charts are hand-rolled SVG so the app stays dependency-free and works offline.
   ========================================================================== */

'use strict';

/* ─────────────────────────── constants ─────────────────────────── */

const RISK_COLOUR = { 'high risk': '#ef4444', 'mid risk': '#f59e0b', 'low risk': '#22c55e' };
const RISK_BG     = { 'high risk': '#fef2f2', 'mid risk': '#fffbeb', 'low risk': '#f0fdf4' };
const RISK_EMOJI  = { 'high risk': '🔴',      'mid risk': '🟡',      'low risk': '🟢' };
const ALERT_CLASS = { 'high risk': 'alert-high', 'mid risk': 'alert-mid', 'low risk': 'alert-low' };
const STATUS_ICON = { sent: '✅', mock_success: '🔵', failed: '❌', queued: '⏳' };

const NOT_FOUND = 'WARNING: Protocol not found in verified database.';

/* ─────────────────────────── tiny helpers ─────────────────────────── */

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** Escape untrusted text before it goes anywhere near innerHTML. */
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function colourOf(risk) { return RISK_COLOUR[risk] || '#6b7280'; }

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body ? { 'Content-Type': 'application/json' } : {},
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty or non-JSON body */ }

  if (!res.ok) {
    const detail = data?.detail;
    // FastAPI validation errors arrive as a list of {loc, msg} objects.
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg).join('; ')
      : detail || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });

function toast(message, kind = 'ok', ttl = 4200) {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.innerHTML = esc(message);
  $('#toast-host').appendChild(el);
  setTimeout(() => el.remove(), ttl);
}

function busy(button, on, label) {
  if (on) {
    button.dataset.label = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span>${esc(label || 'Working …')}`;
  } else {
    button.disabled = false;
    button.innerHTML = button.dataset.label || button.innerHTML;
  }
}

function daysUntil(isoDate) {
  const then = new Date(`${isoDate}T00:00:00`);
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((then - now) / 86400000);
}

/* ─────────────────────────── shared fragments ─────────────────────────── */

function metricCard(label, value, sub, colour) {
  return `
    <div class="metric-card" style="border-left-color:${colour}">
      <div class="metric-label">${esc(label)}</div>
      <div class="metric-value">${esc(value)}</div>
      <div class="metric-sub">${esc(sub)}</div>
    </div>`;
}

function riskBadge(risk) {
  const colour = colourOf(risk);
  return `<span class="risk-badge" style="background:${RISK_BG[risk] || '#f9fafb'};
          color:${colour};border:2px solid ${colour}">
            ${RISK_EMOJI[risk] || '⚪'} ${esc(String(risk).toUpperCase())}
          </span>`;
}

function probabilityMeters(probabilities) {
  return Object.entries(probabilities).map(([risk, pct]) => `
    <div class="prob-row">
      <span class="prob-label">${RISK_EMOJI[risk] || ''} ${esc(titleCase(risk))}</span>
      <div class="prob-track"><div class="prob-fill" style="width:${pct}%;background:${colourOf(risk)}"></div></div>
      <span class="prob-val">${esc(pct)}%</span>
    </div>`).join('');
}

function titleCase(text) {
  return String(text).replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Horizontal diverging bar chart of SHAP contributions — the DOM equivalent of
 * the Plotly figure the Streamlit app drew. Bars grow from a centre line:
 * right/red pushes risk up, left/green pulls it down.
 */
function shapChart(reasons) {
  const entries = Object.entries(reasons || {});
  if (!entries.length) return '';
  const max = Math.max(...entries.map(([, v]) => Math.abs(v))) || 1;

  const rows = entries.map(([name, value]) => {
    const half = (Math.abs(value) / max) * 50;
    const positive = value > 0;
    const style = positive
      ? `left:50%;width:${half}%;background:${RISK_COLOUR['high risk']}`
      : `right:50%;width:${half}%;background:${RISK_COLOUR['low risk']}`;
    return `
      <div class="shap-row">
        <span class="shap-name" title="${esc(name)}">${esc(name)}</span>
        <span class="shap-track"><span class="shap-bar" style="${style}"></span></span>
        <span class="shap-val">${value > 0 ? '+' : ''}${Number(value).toFixed(3)}</span>
      </div>`;
  }).join('');

  return `
    <div class="chart-box">
      <div class="chart-title">🔍 AI Reasoning — Feature Contributions</div>
      ${rows}
      <div class="legend">
        <span class="key"><span class="swatch" style="background:${RISK_COLOUR['high risk']}"></span>Increases risk</span>
        <span class="key"><span class="swatch" style="background:${RISK_COLOUR['low risk']}"></span>Decreases risk</span>
      </div>
    </div>`;
}

/* ─────────────────────────── SVG charts ─────────────────────────── */

/** Donut chart of the risk-tier split. */
function donutChart(distribution) {
  const entries = Object.entries(distribution || {}).filter(([, n]) => n > 0);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (!total) return '<div class="empty">No predictions yet. Run a new visit first.</div>';

  const CX = 110, CY = 110, R_OUT = 92, R_IN = 52;
  let angle = -Math.PI / 2;   // start at 12 o'clock

  const arcs = entries.map(([risk, count]) => {
    const sweep = (count / total) * Math.PI * 2;
    const end = angle + sweep;
    const large = sweep > Math.PI ? 1 : 0;
    const pt = (r, a) => `${(CX + r * Math.cos(a)).toFixed(2)} ${(CY + r * Math.sin(a)).toFixed(2)}`;

    // A full circle can't be drawn as a single arc — nudge the end point back.
    const safeEnd = sweep >= Math.PI * 2 - 1e-6 ? end - 1e-4 : end;
    const d = [
      `M ${pt(R_OUT, angle)}`,
      `A ${R_OUT} ${R_OUT} 0 ${large} 1 ${pt(R_OUT, safeEnd)}`,
      `L ${pt(R_IN, safeEnd)}`,
      `A ${R_IN} ${R_IN} 0 ${large} 0 ${pt(R_IN, angle)}`,
      'Z',
    ].join(' ');

    const mid = angle + sweep / 2;
    const labelR = R_OUT + 16;
    const lx = CX + labelR * Math.cos(mid);
    const ly = CY + labelR * Math.sin(mid);
    const anchor = Math.cos(mid) >= 0 ? 'start' : 'end';
    angle = end;

    return `
      <path d="${d}" fill="${colourOf(risk)}" opacity="0.9"><title>${esc(risk)}: ${count}</title></path>
      <text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="${anchor}"
            dominant-baseline="middle" font-size="11">${Math.round((count / total) * 100)}%</text>`;
  }).join('');

  const legend = entries.map(([risk, count]) => `
    <span class="key"><span class="swatch" style="background:${colourOf(risk)}"></span>${esc(titleCase(risk))} (${count})</span>`).join('');

  return `
    <svg viewBox="0 0 220 220" role="img" aria-label="Risk distribution">
      ${arcs}
      <text x="${CX}" y="${CY - 6}" text-anchor="middle" font-size="26" font-weight="700" fill="#111827">${total}</text>
      <text x="${CX}" y="${CY + 14}" text-anchor="middle" font-size="11">patients</text>
    </svg>
    <div class="legend">${legend}</div>`;
}

/** Dual-axis line chart: BP on the left axis, blood sugar on the right. */
function trendChart(visits, patientName) {
  if (!visits || visits.length < 2) {
    return '<div class="alert-info">Need at least 2 visits to show trends.</div>';
  }
  const rows = [...visits].sort((a, b) => String(a.visit_date).localeCompare(String(b.visit_date)));

  const W = 720, H = 320;
  const PAD = { top: 24, right: 56, bottom: 52, left: 52 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  const bpValues = rows.flatMap((r) => [r.systolic_bp, r.diastolic_bp]).filter((v) => v != null);
  const bpMin = Math.min(60, ...bpValues) - 5;
  const bpMax = Math.max(150, ...bpValues) + 5;

  const bsValues = rows.map((r) => r.blood_sugar).filter((v) => v != null);
  const bsMin = Math.max(0, Math.min(...bsValues, 4) - 1);
  const bsMax = Math.max(...bsValues, 12) + 1;

  const x = (i) => PAD.left + (rows.length === 1 ? plotW / 2 : (i / (rows.length - 1)) * plotW);
  const yBp = (v) => PAD.top + plotH - ((v - bpMin) / (bpMax - bpMin)) * plotH;
  const yBs = (v) => PAD.top + plotH - ((v - bsMin) / (bsMax - bsMin)) * plotH;

  const series = (key, scale, colour) => {
    const points = rows
      .map((r, i) => (r[key] == null ? null : [x(i), scale(r[key])]))
      .filter(Boolean);
    if (!points.length) return '';
    const path = points.map(([px, py], i) => `${i ? 'L' : 'M'} ${px.toFixed(1)} ${py.toFixed(1)}`).join(' ');
    const dots = points.map(([px, py], i) => `
      <circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="3.5" fill="${colour}">
        <title>${esc(rows[i].visit_date)} — ${esc(rows[i][key])}</title>
      </circle>`).join('');
    return `<path d="${path}" fill="none" stroke="${colour}" stroke-width="2"
              stroke-linejoin="round" stroke-linecap="round" />${dots}`;
  };

  // Horizontal gridlines + left axis ticks (BP).
  const bpTicks = 5;
  const grid = Array.from({ length: bpTicks + 1 }, (_, i) => {
    const value = bpMin + ((bpMax - bpMin) * i) / bpTicks;
    const gy = yBp(value);
    return `
      <line x1="${PAD.left}" y1="${gy.toFixed(1)}" x2="${W - PAD.right}" y2="${gy.toFixed(1)}"
            stroke="#eef0f4" stroke-width="1" />
      <text x="${PAD.left - 8}" y="${gy.toFixed(1)}" text-anchor="end" dominant-baseline="middle"
            font-size="10">${Math.round(value)}</text>`;
  }).join('');

  // Right axis ticks (blood sugar).
  const bsAxis = Array.from({ length: 4 }, (_, i) => {
    const value = bsMin + ((bsMax - bsMin) * i) / 3;
    return `<text x="${W - PAD.right + 8}" y="${yBs(value).toFixed(1)}" dominant-baseline="middle"
              font-size="10" fill="#6366f1">${value.toFixed(1)}</text>`;
  }).join('');

  const xLabels = rows.map((r, i) => {
    // Thin the labels out so they never collide on a long history.
    const step = Math.ceil(rows.length / 8);
    if (i % step !== 0 && i !== rows.length - 1) return '';
    return `<text x="${x(i).toFixed(1)}" y="${H - PAD.bottom + 18}" text-anchor="middle"
              font-size="10">${esc(String(r.visit_date).slice(5))}</text>`;
  }).join('');

  const safeTop = yBp(Math.min(135, bpMax));
  const safeBottom = yBp(Math.max(90, bpMin));

  return `
    <div class="chart-box">
      <div class="chart-title">📈 ${esc(patientName)}'s Health Trend</div>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Vitals trend">
        <rect x="${PAD.left}" y="${safeTop.toFixed(1)}" width="${plotW}"
              height="${Math.max(0, safeBottom - safeTop).toFixed(1)}"
              fill="#22c55e" opacity="0.06" />
        ${grid}
        ${bsAxis}
        <line x1="${PAD.left}" y1="${PAD.top}" x2="${PAD.left}" y2="${PAD.top + plotH}" stroke="#d1d5db" />
        <line x1="${PAD.left}" y1="${PAD.top + plotH}" x2="${W - PAD.right}" y2="${PAD.top + plotH}" stroke="#d1d5db" />
        ${series('systolic_bp', yBp, '#ef4444')}
        ${series('diastolic_bp', yBp, '#f59e0b')}
        ${series('blood_sugar', yBs, '#6366f1')}
        ${xLabels}
        <text x="14" y="${PAD.top + plotH / 2}" font-size="10" text-anchor="middle"
              transform="rotate(-90 14 ${PAD.top + plotH / 2})">Blood Pressure (mmHg)</text>
        <text x="${W - 12}" y="${PAD.top + plotH / 2}" font-size="10" text-anchor="middle" fill="#6366f1"
              transform="rotate(90 ${W - 12} ${PAD.top + plotH / 2})">Blood Sugar (mmol/L)</text>
      </svg>
      <div class="legend">
        <span class="key"><span class="swatch" style="background:#ef4444"></span>Systolic BP</span>
        <span class="key"><span class="swatch" style="background:#f59e0b"></span>Diastolic BP</span>
        <span class="key"><span class="swatch" style="background:#6366f1"></span>Blood Sugar</span>
        <span class="key"><span class="swatch" style="background:#22c55e;opacity:.35"></span>Safe BP zone</span>
      </div>
    </div>`;
}

/* ─────────────────────────── navigation & tabs ─────────────────────────── */

const LOADERS = {
  dashboard:    loadDashboard,
  'new-visit':  loadNewVisit,
  vitals:       loadVitalsPage,
  records:      loadRecords,
  appointments: loadAppointments,
  assistant:    loadAssistant,
  emails:       loadEmails,
  settings:     loadSettings,
};

function showPage(name) {
  $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.page === name));
  $$('.page').forEach((p) => p.classList.toggle('active', p.id === `page-${name}`));
  window.location.hash = name;
  LOADERS[name]?.();
}

function initNav() {
  $$('.nav-item').forEach((btn) => btn.addEventListener('click', () => showPage(btn.dataset.page)));

  $$('.tabs').forEach((group) => {
    const scope = group.parentElement;
    group.addEventListener('click', (event) => {
      const tab = event.target.closest('.tab');
      if (!tab) return;
      $$('.tab', group).forEach((t) => t.classList.toggle('active', t === tab));
      $$(':scope > .tab-panel', scope).forEach((p) => {
        p.classList.toggle('active', p.dataset.panel === tab.dataset.tab);
      });
    });
  });
}

/* ─────────────────────────── page: dashboard ─────────────────────────── */

async function loadDashboard() {
  const box = $('#dash-metrics');
  box.innerHTML = '<div class="empty"><span class="spinner"></span>Loading …</div>';

  let stats;
  try {
    stats = await api('/api/stats');
  } catch (err) {
    box.innerHTML = `<div class="alert-high">${esc(err.message)}</div>`;
    return;
  }

  setModelStatus(stats.model_ready);

  box.innerHTML =
    metricCard('👩 Registered Patients', stats.patients, 'Total in system', '#6366f1') +
    metricCard('🔴 High Risk', stats.high_risk, 'Require urgent attention', '#ef4444') +
    metricCard("🏥 Today's Visits", stats.today_visits, `Date: ${stats.today}`, '#0ea5e9') +
    metricCard('📅 Pending Appointments', stats.pending_appointments, 'Upcoming', '#22c55e');

  const list = $('#dash-high-risk');
  if (!stats.high_risk_patients.length) {
    list.innerHTML = '<div class="alert-low">No high-risk patients at this time. 🎉</div>';
  } else {
    list.innerHTML = stats.high_risk_patients.map((p) => {
      const score = Math.round(p.risk_score || 0);
      const action = String(p.suggested_action || '').slice(0, 80);
      return `
        <div class="patient-card">
          <div class="avatar" style="background:#fef2f2">🔴</div>
          <div class="pc-grow">
            <div class="pc-name">${esc(p.full_name)}</div>
            <div class="pc-meta">ID: ${esc(p.patient_id)} · Risk score: ${score}%</div>
            <div class="pc-note">${esc(action)}…</div>
          </div>
          <span class="pill" style="background:#fef2f2;color:#ef4444;border:1.5px solid #ef4444">${score}%</span>
        </div>`;
    }).join('');
  }

  $('#dash-distribution').innerHTML = donutChart(stats.risk_distribution);
}

function setModelStatus(ready) {
  const box = $('#model-status-box');
  box.className = `sb-status ${ready ? 'ok' : 'warn'}`;
  box.textContent = ready ? '✅ AI model ready' : '⚠️ Model not trained — see Settings';
}

/* ─────────────────────────── page: new visit ─────────────────────────── */

let patientsCache = [];

async function refreshPatients() {
  patientsCache = await api('/api/patients');
  return patientsCache;
}

function patientOptions(patients) {
  if (!patients.length) return '<option value="">No patients registered</option>';
  return patients.map((p) =>
    `<option value="${esc(p.patient_id)}">${esc(p.full_name)} (${esc(p.patient_id)})</option>`).join('');
}

async function loadNewVisit() {
  try {
    const patients = await refreshPatients();
    const patientSelect = $('#vitals-patient');
    if (patientSelect) {
      patientSelect.innerHTML = patientOptions(patients);
    }
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function loadVitalsPage() {
  try {
    const patients = await refreshPatients();
    const patientSelect = $('#vitals-patient');
    if (patientSelect) {
      patientSelect.innerHTML = patientOptions(patients);
    }

    const visitForm = $('#visit-form');
    if (!visitForm) return;

    if (!patients.length) {
      visitForm.style.display = 'none';
      if (!$('#no-patients-msg')) {
        $('#vitals-wrap').insertAdjacentHTML('afterbegin',
          '<div class="alert-mid" id="no-patients-msg">No patients registered yet. Please register a patient first.</div>');
      }
    } else {
      visitForm.style.display = '';
      $('#no-patients-msg')?.remove();
    }
  } catch (err) {
    toast(err.message, 'err');
  }
}

function initNewVisit() {
  $('#patient-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const fd = new FormData(form);
    const payload = {
      full_name: fd.get('full_name').trim(),
      phone: fd.get('phone').trim(),
      email: fd.get('email').trim(),
      age: Number(fd.get('age')),
      weeks_pregnant: Number(fd.get('weeks_pregnant')),
      prev_pregnancies: Number(fd.get('prev_pregnancies')),
      family_hypertension: fd.get('family_hypertension') === 'on',
    };
    const msg = $('#patient-form-msg');
    const button = $('button[type=submit]', form);

    busy(button, true, 'Saving …');
    try {
      const out = await post('/api/patients', payload);
      msg.innerHTML = `<div class="alert-low">✅ ${esc(out.message)}</div>
                       <div class="alert-info">Go to the Vitals &amp; Prediction page to record this visit.</div>`;
      form.reset();
      $('#patient-form-msg').innerHTML = `<div class="alert-low">✅ ${esc(out.message)}</div>`;
      await loadNewVisit();
    } catch (err) {
      msg.innerHTML = `<div class="alert-high">${esc(err.message)}</div>`;
    } finally {
      busy(button, false);
    }
  });
}

function initVitalsPage() {
  const visitForm = $('#visit-form');
  if (!visitForm) return;

  visitForm.addEventListener('input', updateVitalsHints);
  updateVitalsHints();

  visitForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const fd = new FormData(visitForm);
    const patientId = $('#vitals-patient').value;
    if (!patientId) { toast('Select a patient first.', 'warn'); return; }

    const payload = {
      patient_id: patientId,
      systolic_bp: Number(fd.get('systolic_bp')),
      diastolic_bp: Number(fd.get('diastolic_bp')),
      blood_sugar: Number(fd.get('blood_sugar')),
      body_temp: Number(fd.get('body_temp')),
      heart_rate: Number(fd.get('heart_rate')),
      weight_kg: fd.get('weight_kg') ? Number(fd.get('weight_kg')) : null,
      height_cm: fd.get('height_cm') ? Number(fd.get('height_cm')) : null,
      proteinuria: fd.get('proteinuria') === 'on',
      notes: fd.get('notes') || '',
    };

    const button = $('button[type=submit]', visitForm);
    const panel = $('#assessment');
    busy(button, true, '🧠 AI engine analysing …');
    panel.innerHTML = '';
    try {
      const out = await post('/api/visits', payload);
      visitForm.reset();
      updateVitalsHints();
      $('#vitals-patient').value = '';
      panel.innerHTML = renderAssessment(out);
      panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
      panel.innerHTML = `<div class="alert-high">${esc(err.message)}</div>`;
    } finally {
      busy(button, false);
    }
  });
}

function updateVitalsHints() {
  const form = $('#visit-form');
  const num = (name) => Number(new FormData(form).get(name));
  const systolic = num('systolic_bp');
  const diastolic = num('diastolic_bp');
  const bs = num('blood_sugar');
  const weight = num('weight_kg');
  const height = num('height_cm');

  let bmiHtml = '';
  if (weight && height) {
    const bmi = weight / ((height / 100) ** 2);
    bmiHtml = `<p style="margin:.5rem 0 0"><strong>BMI:</strong> ${bmi.toFixed(1)} kg/m²</p>`;
    if (bmi < 18.5 || bmi > 24.9) {
      bmiHtml += `<div class="alert-mid">⚠️ BMI ${bmi.toFixed(1)} is outside the healthy range (18.5–24.9).</div>`;
    }
  }
  $('#bmi-readout').innerHTML = bmiHtml;

  const alerts = [];
  if (systolic >= 160 || diastolic >= 110) {
    alerts.push(`<div class="rt-alert">⚡ CRITICAL BP DETECTED — ${systolic}/${diastolic} mmHg.
                 Immediate medical attention required!</div>`);
  } else if (systolic >= 140 || diastolic >= 90) {
    alerts.push(`<div class="alert-mid">⚠️ Elevated BP: ${systolic}/${diastolic} mmHg. Monitor closely.</div>`);
  }
  if (bs > 11.0) {
    alerts.push(`<div class="alert-mid">⚠️ High blood sugar: ${bs} mmol/L — Gestational Diabetes risk.</div>`);
  }
  $('#vitals-alerts').innerHTML = alerts.join('');
}

function renderAssessment(out) {
  const r = out.result;
  const velocity = r.bp_velocity > 0
    ? `<li>BP ↑ ${Math.round(r.bp_velocity)} mmHg since last visit</li>` : '';

  return `
    <hr class="rule" />
    <h3 class="sub-header">🤖 AI Assessment Results</h3>
    <div class="grid-3">
      <div class="card">
        <strong>Risk Classification</strong>
        <p>${riskBadge(r.risk_level)}</p>
        <div class="metric-label">Risk Score</div>
        <div class="metric-value">${esc(r.risk_score)}%</div>
      </div>
      <div class="card">
        <strong>Probability Breakdown</strong>
        <div style="margin-top:.6rem">${probabilityMeters(r.probabilities)}</div>
      </div>
      <div class="card">
        <strong>Top Risk Conditions</strong>
        <ul>${r.top_risks.map((c) => `<li>${esc(c)}</li>`).join('')}${velocity}</ul>
      </div>
    </div>

    ${shapChart(r.shap_reasons)}

    <div class="${ALERT_CLASS[r.risk_level] || 'alert-info'}">
      <strong>📋 Suggested Action Plan</strong><br />${esc(r.suggested_action)}
    </div>

    <div class="alert-low">
      ✅ Visit saved. Next appointment auto-booked: <strong>${esc(out.next_appointment)}</strong>.
      ${out.email_sent ? `Email sent to ${esc(out.patient_name)}.` : 'No email on file — nothing sent.'}
    </div>`;
}

/* ─────────────────────────── page: patient records ─────────────────────────── */

async function loadRecords() {
  try {
    await refreshPatients();
  } catch (err) {
    $('#records-detail').innerHTML = `<div class="alert-high">${esc(err.message)}</div>`;
    return;
  }
  applyRecordsFilter();
}

function applyRecordsFilter() {
  const term = $('#records-search').value.trim().toLowerCase();
  const filtered = term
    ? patientsCache.filter((p) =>
        p.full_name.toLowerCase().includes(term) || p.patient_id.toLowerCase().includes(term))
    : patientsCache;

  const select = $('#records-select');
  const previous = select.value;
  select.innerHTML = patientOptions(filtered);
  if (filtered.some((p) => p.patient_id === previous)) select.value = previous;

  if (!filtered.length) {
    $('#records-detail').innerHTML = '<div class="alert-info">No patients match this search.</div>';
  } else {
    renderRecordDetail(select.value);
  }
}

async function renderRecordDetail(patientId) {
  const panel = $('#records-detail');
  if (!patientId) { panel.innerHTML = ''; return; }
  panel.innerHTML = '<div class="empty"><span class="spinner"></span>Loading …</div>';

  let data;
  try {
    data = await api(`/api/patients/${encodeURIComponent(patientId)}`);
  } catch (err) {
    panel.innerHTML = `<div class="alert-high">${esc(err.message)}</div>`;
    return;
  }

  const { patient, visits, prediction } = data;

  const predictionBlock = prediction ? `
    <p><strong>Latest AI Assessment</strong></p>
    <p>${riskBadge(prediction.risk_level)}</p>
    <div class="metric-label">Risk Score</div>
    <div class="metric-value">${esc(prediction.risk_score)}%</div>` : '';

  const visitTable = visits.length ? `
    <details class="expander">
      <summary>📋 Visit History Table</summary>
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Date</th><th>Systolic</th><th>Diastolic</th>
            <th>Blood Sugar</th><th>HR</th><th>Weight kg</th>
          </tr></thead>
          <tbody>
            ${visits.map((v) => `
              <tr>
                <td>${esc(v.visit_date)}</td>
                <td class="num">${esc(v.systolic_bp)}</td>
                <td class="num">${esc(v.diastolic_bp)}</td>
                <td class="num">${esc(v.blood_sugar)}</td>
                <td class="num">${esc(v.heart_rate)}</td>
                <td class="num">${v.weight_kg == null ? '—' : esc(v.weight_kg)}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </details>` : '';

  panel.innerHTML = `
    <div class="grid-split" style="margin-top:1.25rem">
      <div style="order:2">
        ${visits.length
          ? trendChart(visits, patient.full_name) + visitTable
          : '<div class="alert-info">No visit history for this patient.</div>'}
      </div>
      <div style="order:1">
        <div class="metric-card">
          <div style="font-size:36px;margin-bottom:8px">🤰</div>
          <div style="font-size:20px;font-weight:700">${esc(patient.full_name)}</div>
          <div class="metric-sub">ID: ${esc(patient.patient_id)}</div>
          <div style="margin-top:10px;font-size:13px;line-height:1.8">
            📱 ${esc(patient.phone || '—')}<br />
            ✉️ ${esc(patient.email || '—')}<br />
            🗓️ Age: ${esc(patient.age)} yrs<br />
            🤱 ${esc(patient.weeks_pregnant)} weeks pregnant<br />
            👶 ${esc(patient.prev_pregnancies)} previous pregnancy(ies)<br />
            ${patient.family_hypertension ? '⚠️ Family hypertension' : ''}
          </div>
        </div>
        ${predictionBlock}
      </div>
    </div>
    ${prediction ? `
      <hr class="rule" />
      <p><strong>🔍 AI Reasoning (SHAP)</strong></p>
      ${shapChart(prediction.shap_reasons)}
      <div class="${ALERT_CLASS[prediction.risk_level] || 'alert-info'}">
        <strong>Action:</strong> ${esc(prediction.suggested_action)}
      </div>` : ''}`;
}

function initRecords() {
  $('#records-search').addEventListener('input', applyRecordsFilter);
  $('#records-select').addEventListener('change', (e) => renderRecordDetail(e.target.value));
}

/* ─────────────────────────── page: appointments ─────────────────────────── */

async function loadAppointments() {
  await Promise.all([loadUpcoming(), loadPendingAppointments(), loadBookForm()]);
}

async function loadUpcoming() {
  const box = $('#appt-upcoming');
  try {
    const list = await api('/api/appointments/upcoming?days=14');
    if (!list.length) {
      box.innerHTML = '<div class="alert-info">No upcoming appointments in the next 14 days.</div>';
      return;
    }
    box.innerHTML = list.map((a) => {
      const left = daysUntil(a.scheduled_date);
      const colour = left <= 1 ? '#ef4444' : left <= 3 ? '#f59e0b' : '#22c55e';
      return `
        <div class="patient-card">
          <div class="avatar" style="background:${colour}22">📅</div>
          <div class="pc-grow">
            <div class="pc-name">${esc(a.full_name)}</div>
            <div class="pc-meta">${esc(a.scheduled_date)} · ${esc(a.reason || '')}</div>
            <div class="pc-note">${esc(a.phone || '')}</div>
          </div>
          <span class="pill" style="background:${colour}22;color:${colour};border:1.5px solid ${colour}">${left}d</span>
        </div>`;
    }).join('');
  } catch (err) {
    box.innerHTML = `<div class="alert-high">${esc(err.message)}</div>`;
  }
}

async function loadPendingAppointments() {
  const box = $('#appt-mark');
  try {
    const pending = await api('/api/appointments/pending');
    if (!pending.length) {
      box.innerHTML = '<div class="alert-info">No pending appointments.</div>';
      return;
    }
    box.innerHTML = `
      <label>Select Appointment</label>
      <select id="mark-select">
        ${pending.map((r) => `<option value="${r.appt_id}">
            ${esc(r.full_name)} — ${esc(r.scheduled_date)} (ID ${r.appt_id})
          </option>`).join('')}
      </select>
      <div class="btn-row">
        <button class="btn btn-ok" id="btn-attended">✅ Mark Attended</button>
        <button class="btn btn-danger" id="btn-missed">❌ Mark Missed</button>
      </div>`;

    const act = async (button, path, kind) => {
      const id = $('#mark-select').value;
      busy(button, true, 'Saving …');
      try {
        const out = await post(`/api/appointments/${id}/${path}`, {});
        toast(out.message + (out.email_sent ? ' Email sent to patient.' : ''), kind);
        await loadAppointments();
      } catch (err) {
        toast(err.message, 'err');
        busy(button, false);
      }
    };
    $('#btn-attended').addEventListener('click', (e) => act(e.currentTarget, 'attended', 'ok'));
    $('#btn-missed').addEventListener('click', (e) => act(e.currentTarget, 'missed', 'warn'));
  } catch (err) {
    box.innerHTML = `<div class="alert-high">${esc(err.message)}</div>`;
  }
}

async function loadBookForm() {
  await refreshPatients();
  $('#book-patient').innerHTML = patientOptions(patientsCache);

  const dateInput = $('#book-form input[name=scheduled_date]');
  if (!dateInput.value) {
    const target = new Date();
    target.setDate(target.getDate() + 7);
    dateInput.value = target.toISOString().slice(0, 10);
  }
}

function initAppointments() {
  $('#book-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const fd = new FormData(event.target);
    const patientId = fd.get('patient_id');
    if (!patientId) { toast('Register a patient first.', 'warn'); return; }

    const button = $('button[type=submit]', event.target);
    busy(button, true, 'Booking …');
    try {
      const out = await post('/api/appointments', {
        patient_id: patientId,
        scheduled_date: fd.get('scheduled_date'),
        reason: fd.get('reason') || 'Antenatal follow-up',
      });
      $('#book-msg').innerHTML = `<div class="alert-low">✅ ${esc(out.message)}
        ${out.email_sent ? 'Reminder email sent.' : ''}</div>`;
      await loadUpcoming();
      await loadPendingAppointments();
    } catch (err) {
      $('#book-msg').innerHTML = `<div class="alert-high">${esc(err.message)}</div>`;
    } finally {
      busy(button, false);
    }
  });
}

/* ─────────────────────────── page: protocol assistant (RAG) ─────────────────────────── */

/**
 * Minimal, deliberately conservative markdown for model output: bullets, bold,
 * inline code and [Section] citations. Everything is escaped first — the model
 * text is never trusted as HTML.
 */
function renderAnswer(text) {
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]{2,80})\]/g, '<span class="cite">[$1]</span>');

  const html = [];
  let inList = false;
  for (const rawLine of String(text).split('\n')) {
    const line = rawLine.trim();
    const bullet = line.match(/^(?:[-*•]|\d+\.)\s+(.*)$/);
    if (bullet) {
      if (!inList) { html.push('<ul>'); inList = true; }
      html.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    if (inList) { html.push('</ul>'); inList = false; }
    if (line) html.push(`<p>${inline(line)}</p>`);
  }
  if (inList) html.push('</ul>');
  return html.join('');
}

function appendMessage(role, innerHtml, extraClass = '') {
  const log = $('#chat-log');
  const wrap = document.createElement('div');
  wrap.className = `msg ${role}`;
  wrap.innerHTML = `<div class="bubble ${extraClass}">${innerHtml}</div>`;
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return wrap;
}

async function loadAssistant() {
  const banner = $('#kb-banner');
  try {
    const status = await api('/api/chat/status');
    if (!status.kb_ready) {
      banner.className = 'kb-banner bad';
      banner.innerHTML = '⚠️ Knowledge base not built. Run <code>python ingest.py</code> and restart the server.';
    } else if (!status.groq_ready) {
      banner.className = 'kb-banner bad';
      banner.innerHTML = `⚠️ ${status.chunks} chunks indexed, but <code>GROQ_API_KEY</code> is not set on the server.`;
    } else {
      banner.className = 'kb-banner ok';
      banner.innerHTML = `✅ Grounded in <b>${esc(status.source)}</b> — ${status.chunks} verified chunks ·
                          model <code>${esc(status.model_name)}</code>`;
    }
  } catch (err) {
    banner.className = 'kb-banner bad';
    banner.textContent = err.message;
  }
}

function initAssistant() {
  $('#chat-log').addEventListener('click', (event) => {
    const chip = event.target.closest('.chip');
    if (!chip) return;
    $('#chat-text').value = chip.textContent.trim();
    $('#chat-form').requestSubmit();
  });

  $('#chat-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = $('#chat-text');
    const question = input.value.trim();
    if (!question) return;

    appendMessage('user', esc(question));
    input.value = '';
    input.disabled = true;

    const pending = appendMessage('assistant',
      '<span class="typing"><i></i><i></i><i></i></span>');

    try {
      const out = await post('/chat', { message: question });
      const bubble = $('.bubble', pending);

      if (!out.grounded || out.answer === NOT_FOUND) {
        bubble.className = 'bubble warning';
        bubble.textContent = out.answer;   // verbatim; never reformat the refusal
      } else {
        const sources = out.sources.length ? `
          <div class="sources"><b>Sources</b> · ${esc(out.sources.length)} passage(s) from the verified protocol<br />
            ${out.sources.map((s) => {
              const pages = s.page_end && s.page_end !== s.page_start
                ? `p${s.page_start}–${s.page_end}` : `p${s.page_start}`;
              return `<span class="src-tag">${esc(s.section)} · ${esc(pages)}</span>`;
            }).join('')}
          </div>` : '';
        bubble.innerHTML = renderAnswer(out.answer) + sources;
      }
    } catch (err) {
      const bubble = $('.bubble', pending);
      bubble.className = 'bubble error';
      bubble.textContent = err.message;
    } finally {
      input.disabled = false;
      input.focus();
      $('#chat-log').scrollTop = $('#chat-log').scrollHeight;
    }
  });
}

/* ─────────────────────────── page: email log ─────────────────────────── */

async function loadEmails() {
  const box = $('#email-log');
  box.innerHTML = '<div class="empty"><span class="spinner"></span>Loading …</div>';
  try {
    const logs = await api('/api/emails');
    if (!logs.length) {
      box.innerHTML = '<div class="alert-info">No emails sent yet.</div>';
      return;
    }
    box.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Time</th><th>Patient ID</th><th>Email</th>
            <th>Type</th><th>Status</th><th>Subject</th>
          </tr></thead>
          <tbody>
            ${logs.map((l) => `
              <tr>
                <td>${esc(l.sent_at)}</td>
                <td>${esc(l.patient_id || '—')}</td>
                <td>${esc(l.email || '—')}</td>
                <td>${esc(l.message_type)}</td>
                <td title="${esc(l.status)}">${STATUS_ICON[l.status] || '•'}</td>
                <td>${esc(l.subject)}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  } catch (err) {
    box.innerHTML = `<div class="alert-high">${esc(err.message)}</div>`;
  }
}

/* ─────────────────────────── page: settings ─────────────────────────── */

async function loadSettings() {
  const box = $('#kb-detail');
  try {
    const status = await api('/api/chat/status');
    box.innerHTML = `
      <div class="markdown">
        <p><strong>Source document:</strong> ${esc(status.source)}</p>
        <p><strong>Indexed chunks:</strong> ${esc(status.chunks)}</p>
        <p><strong>Vector store:</strong> ChromaDB (cosine) — ${status.kb_ready ? '✅ ready' : '❌ not built'}</p>
        <p><strong>Generation:</strong> Groq <code>${esc(status.model_name)}</code> —
           ${status.groq_ready ? '✅ configured' : '❌ GROQ_API_KEY not set'}</p>
      </div>`;
  } catch (err) {
    box.innerHTML = `<div class="alert-high">${esc(err.message)}</div>`;
  }
}

function initSettings() {
  const bind = (slider, output) => {
    const s = $(slider);
    s.addEventListener('input', () => { $(output).textContent = s.value; });
  };
  bind('#train-slider', '#train-out');
  bind('#seed-slider', '#seed-out');

  $('#btn-train').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    busy(button, true, 'Training …');
    $('#train-msg').innerHTML = '';
    try {
      const out = await post('/api/model/train', { n_samples: Number($('#train-slider').value) });
      $('#train-msg').innerHTML = `<div class="alert-low">✅ ${esc(out.message)}</div>`;
      setModelStatus(true);
    } catch (err) {
      $('#train-msg').innerHTML = `<div class="alert-high">${esc(err.message)}</div>`;
    } finally {
      busy(button, false);
    }
  });

  $('#btn-seed').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    busy(button, true, 'Seeding …');
    $('#seed-msg').innerHTML = '';
    try {
      const out = await post('/api/seed', { n_patients: Number($('#seed-slider').value) });
      $('#seed-msg').innerHTML = `<div class="alert-low">✅ ${esc(out.message)}</div>`;
      await refreshPatients();
    } catch (err) {
      $('#seed-msg').innerHTML = `<div class="alert-high">${esc(err.message)}</div>`;
    } finally {
      busy(button, false);
    }
  });
}

/* ─────────────────────────── boot ─────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
  initNav();
  initNewVisit();
  initVitalsPage();
  initRecords();
  initAppointments();
  initAssistant();
  initSettings();

  const start = window.location.hash.replace('#', '');
  showPage(LOADERS[start] ? start : 'dashboard');
});
