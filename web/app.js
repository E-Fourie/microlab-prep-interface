/* Microlab Prep Control - core: API, deck map, machine menu, protocols home, run flow, maintenance. Builder lives in builder.js */
'use strict';

// ───────────── helpers ─────────────
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const enc = encodeURIComponent;
const store = {
  get(k, d) { try { const v = localStorage.getItem('prep.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('prep.' + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

class ApiError extends Error {
  constructor(status, detail, path) { super(`HTTP ${status} ${path}: ${detail}`); this.status = status; this.detail = detail; this.path = path; }
}
async function http(path, { method = 'GET', body } = {}) {
  let res;
  try {
    res = await fetch(path, { method, cache: 'no-store', headers: { 'X-Prep-UI': '1', ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) }, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch (e) { throw new ApiError(0, 'The Prep Interface server is not reachable (is server.py running?)', path); }
  const text = await res.text();
  let data = text; try { data = text ? JSON.parse(text) : null; } catch { /* plain text */ }
  if (!res.ok) throw new ApiError(res.status, (data && (data.detail || data.error || data.message)) || text || res.statusText, path);
  return data;
}
const api = (method, path, body) => http('/prep' + path, { method, body });
const local = (method, path, body) => http('/local' + path, { method, body });
const putLocal = (name, text) => fetch('/local/protocols/' + enc(name), { method: 'PUT', headers: { 'X-Prep-UI': '1' }, body: text })
  .then(async r => { const d = await r.json(); if (!r.ok) throw new Error(d.error); return d.name; });

function toast(msg, err, ms) {
  const t = document.createElement('div'); t.className = 'toast' + (err ? ' err' : ''); t.textContent = msg;
  $('#toasts').append(t); setTimeout(() => t.remove(), ms || (err ? 7000 : 3500));
}
function modal(html) { $('#modalBody').innerHTML = html; $('#modal').hidden = false; }
function closeModal() { $('#modal').hidden = true; $('#modalBody').innerHTML = ''; }
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });
addEventListener('keydown', e => { if (e.key === 'Escape') { if (!$('#modal').hidden) closeModal(); closeMenu(); } });
function confirmModal({ title, body, ok = 'Continue', danger = false }) {
  return new Promise(res => {
    modal(`<h2>${esc(title)}</h2><div class="stack-sm">${body}</div><div class="row end"><button class="btn-ghost" id="cmNo">Cancel</button><button class="${danger ? 'btn danger' : 'btn'}" id="cmYes">${esc(ok)}</button></div>`);
    $('#cmNo').onclick = () => { closeModal(); res(false); };
    $('#cmYes').onclick = () => { closeModal(); res(true); };
  });
}
const fmtErr = e => typeof e === 'string' ? e : (e && (e.message || e.description || e.error)) || JSON.stringify(e);
const truthy = x => typeof x === 'boolean' ? x : typeof x === 'string' ? x.toLowerCase() === 'true'
  : x && typeof x === 'object' ? Object.values(x).some(v => v === true) : false;
const ago = t => { if (!t) return 'never'; const s = Math.max(0, Date.now() / 1000 - t); return s < 10 ? 'just now' : s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : new Date(t * 1000).toLocaleString(); };
const STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6L12 17.3 6.1 20.6l1.3-6.6L2.5 9.4l6.6-.8z"/></svg>';

// ───────────── labware catalogue and deck rules (prep-protocol.js and prep-catalogue.js, loaded before this file) ─────────────
// Deck: 8 positions in 2 columns x 4 rows, numbered down each column (1-4 left, 5-8 right).
// Position 1 is the Hamilton Heater Shaker (HHS, plates only). The 60 mL reagent reservoir rack (5 slots) only fits the
// bottom position of each column: 4 and 8. Capacities are approximate; they live in prep-protocol.js (do not edit that copy here).
const { LABWARE, HHS_POS, RESERVOIR_POS, placeError, wellName, expandWells, libFor } = PrepCalc.prepProtocol;
// The labware the page can draw right now: the built-in list plus the hardware the open protocol (and the catalogue) adds.
const LIB = { ...LABWARE };
function useLib(extra) { Object.keys(LIB).forEach(k => { if (!(k in LABWARE)) delete LIB[k]; }); Object.assign(LIB, extra || {}); }
const liqColor = name => { let h = 0; for (const ch of String(name || '?')) h = (h * 31 + ch.charCodeAt(0)) % 360; return `hsl(${h} 55% 52%)`; };

// ───────────── deck map (SVG): 2 columns x 4 rows, filled down each column ─────────────
const TILE = { w: 230, h: 118, gx: 14, gy: 14, rows: 4 };
function glyph(lwKey, x, y, w, h, o = {}) {
  const lw = LIB[lwKey]; if (!lw) return '';
  const mark = n => `${o.hl?.includes(n) ? ' hl' : ''}${o.hl2?.includes(n) ? ' hl2' : ''}`;
  let s = '';
  if (lw.kind === 'trough') {
    const f = o.fills?.A1; const fh = f ? Math.max(.08, f.frac) * (h - 6) : 0;
    s += `<rect class="well${mark('A1')}" x="${x + 6}" y="${y + 2}" width="${w - 12}" height="${h - 4}" rx="8"/>`;
    if (f) s += `<rect x="${x + 8}" y="${y + h - 2 - fh}" width="${w - 16}" height="${fh}" rx="6" fill="${f.color}" opacity=".8"/>`;
    return s;
  }
  if (lw.kind === 'magnet') { // empty magnet: waiting for the gripper to put a plate on it
    s += `<rect class="well" x="${x + 6}" y="${y + 4}" width="${w - 12}" height="${h - 8}" rx="8" stroke-dasharray="4 3"/><rect x="${x + w / 2 - 34}" y="${y + h / 2 - 14}" width="30" height="28" rx="4" fill="#c0504a"/><rect x="${x + w / 2 + 4}" y="${y + h / 2 - 14}" width="30" height="28" rx="4" fill="#3b6ef5"/><text class="rsv-n" x="${x + w / 2}" y="${y + h - 10}" text-anchor="middle">magnet, plate goes here</text>`;
    return s;
  }
  if (lw.kind === 'reservoirs') { // five reservoirs side by side; which liquid sits in which slot matters
    const gap = 7, bw = (w - gap * (lw.rows - 1)) / lw.rows;
    for (let i = 0; i < lw.rows; i++) {
      const n = wellName(i, 0), x0 = x + i * (bw + gap), f = o.fills?.[n], fh = f ? Math.max(.1, f.frac) * (h - 8) : 0;
      s += `<rect class="rsv${mark(n)}" x="${x0}" y="${y + 2}" width="${bw}" height="${h - 4}" rx="6"/>`;
      if (f) s += `<rect x="${x0 + 2}" y="${y + h - 4 - fh}" width="${bw - 4}" height="${fh}" rx="4" fill="${f.color}" opacity=".75"/>`;
      s += `<text class="rsv-n" x="${x0 + bw / 2}" y="${y + h / 2 + 4}" text-anchor="middle">${i + 1}</text>`;
    }
    return s;
  }
  const cell = Math.min(w / lw.cols, h / lw.rows), r = cell * (lw.kind === 'tubes' ? .42 : lw.rows > 8 ? .44 : .4); // 384-well plates have small wells
  const ox = x + (w - cell * lw.cols) / 2, oy = y + (h - cell * lw.rows) / 2;
  let k = 0;
  for (let c = 0; c < lw.cols; c++) for (let rr = 0; rr < lw.rows; rr++, k++) {
    const n = wellName(rr, c), cx = ox + c * cell + cell / 2, cy = oy + rr * cell + cell / 2;
    if (lw.kind === 'tips') {
      const cls = o.used?.has(n) ? 'tip used' : (o.need != null && k < o.need ? 'tip need' : 'tip');
      s += `<circle class="${cls}" cx="${cx}" cy="${cy}" r="${r}"/>`;
    } else {
      const f = o.fills?.[n];
      s += `<circle class="well${mark(n)}${r < 3.5 ? ' tiny' : ''}" cx="${cx}" cy="${cy}" r="${r}"${f ? ` style="fill:${f.color};fill-opacity:${(.3 + .7 * f.frac).toFixed(2)}"` : ''}/>`;
    }
  }
  return s;
}
const slotLabel = sl => sl.pos === HHS_POS && !sl.lw ? 'HHS (empty)' : (sl.pos === HHS_POS ? 'HHS · ' : '') + (sl.magnet ? 'Magnet · ' : '') + (sl.label || (sl.lw ? LIB[sl.lw].label : 'empty'));
/** slots: [{pos, lw, label, fills, hl, hl2, used, need}] in position order; slot i sits in column floor(i/4), row i%4. */
function deckSVG(slots, { sel, done } = {}) {
  const cols = Math.ceil(slots.length / TILE.rows), W = cols * (TILE.w + TILE.gx) + TILE.gx, H = TILE.rows * (TILE.h + TILE.gy) + TILE.gy;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Deck map: ${cols} columns of ${TILE.rows} positions">`;
  slots.forEach((sl, i) => {
    const x = TILE.gx + Math.floor(i / TILE.rows) * (TILE.w + TILE.gx), y = TILE.gy + (i % TILE.rows) * (TILE.h + TILE.gy);
    const cls = ['slot', sel === sl.pos ? 'sel' : '', done?.has(sl.pos) ? 'done' : '', sl.pos === HHS_POS ? 'hhs' : ''].join(' ');
    s += `<g class="${cls}" data-pos="${esc(sl.pos)}"><rect class="tile" x="${x}" y="${y}" width="${TILE.w}" height="${TILE.h}"${sl.lw ? '' : ' stroke-dasharray="5 4"'}/>`;
    s += `<text class="pos" x="${x + 10}" y="${y + 17}">${esc(sl.pos)}</text><text class="lbl" x="${x + 32}" y="${y + 17}">${esc(slotLabel(sl).slice(0, 28))}</text>`;
    if (sl.lw) s += glyph(sl.lw, x + 10, y + 26, TILE.w - 20, TILE.h - 36, sl);
    if (done?.has(sl.pos)) s += `<text x="${x + TILE.w - 22}" y="${y + 18}" style="fill:var(--good);font-size:14px;font-weight:700">✓</text>`;
    s += '</g>';
  });
  return s + '</svg>';
}
const fixedSlots = (n = 8) => Array.from({ length: n }, (_, i) => ({ pos: String(i + 1), lw: null }));

/** Popup card that follows the mouse over a deck position. htmlFor(pos) returns the card's HTML (or '' for none). */
function attachTips(root, htmlFor) {
  const tip = $('#hovercard');
  root.addEventListener('mousemove', e => {
    const g = e.target.closest('.slot'), html = g && htmlFor(g.dataset.pos);
    if (!html) { tip.hidden = true; return; }
    if (tip.hidden || tip.dataset.pos !== g.dataset.pos) { tip.innerHTML = html; tip.dataset.pos = g.dataset.pos; }
    tip.hidden = false;
    const r = tip.getBoundingClientRect(); let x = e.clientX + 18, y = e.clientY + 18;
    if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 18;
    if (y + r.height > innerHeight - 8) y = Math.max(8, innerHeight - r.height - 8);
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  });
  root.addEventListener('mouseleave', () => { tip.hidden = true; });
}
function tipHTML(pos, lwKey, label, lines, notes = []) {
  const lw = LIB[lwKey];
  return `<h4>Position ${esc(pos)}${pos === HHS_POS ? ' · Heater-Shaker (HHS)' : ''}</h4>
    <div class="sub">${esc(lw ? lw.label : 'Empty')}${label ? ' · ' + esc(label) : ''}</div>
    ${lines.length ? `<div><b>Load with</b></div><ul>${lines.map(l => `<li style="--c:${l.color || '#b9c4c1'}"><i></i><span>${esc(l.text)}</span></li>`).join('')}</ul>` : `<div class="sub">${lw ? 'Nothing extra to load here.' : 'Nothing goes here.'}</div>`}
    ${notes.map(n => `<div class="sub">${esc(n)}</div>`).join('')}`;
}

// ───────────── Prep API helpers & maintenance auto-fix ─────────────
const STATE_LABEL = { 0: 'Idle', 10: 'Preparing', 20: 'Loading deck', 30: 'Running' };
const stateLabel = n => STATE_LABEL[n] ?? `State ${n}`;
const normList = r => Array.isArray(r) ? r : (r && (r.items || r.protocols)) || [];

async function getFlags() {
  const get = async p => { try { return truthy(await api('GET', p)); } catch { return false; } };
  return {
    mustUpdate: await get('/settings/must-update-maintenance-routines'),
    mustRestoreLabware: await get('/settings/must-restore-labware'),
    mustRestoreVerify: await get('/settings/must-restore-verify-protocols'),
  };
}
const needsMaint = m => /expired|overdue|due/i.test(m.status || '');
async function listMaintenance() { try { return normList(await api('GET', '/maintenance')); } catch { return []; } }

const MAINT = { name: null }; // set while a maintenance procedure runs, so nothing else is started on top of it
async function runMaintenance(proc, log) {
  if (MAINT.name) throw new Error(`Maintenance '${MAINT.name}' is already running. Wait for it to finish.`);
  MAINT.name = proc.name || proc.id; paintPill();
  try { await runMaintenanceInner(proc, log); } finally { MAINT.name = null; paintPill(); }
}
async function runMaintenanceInner(proc, log) {
  log(`Starting maintenance: ${proc.name || proc.id}`);
  await api('POST', `/maintenance/${enc(proc.id)}`);
  const start = Date.now();
  for (;;) {
    await sleep(2000);
    const d = await api('GET', `/maintenance/${enc(proc.id)}`);
    const st = String(d.status || '');
    if (/^ok$/i.test(st)) { log(`Maintenance '${proc.name || proc.id}' complete.`, 'g'); return; }
    if (/error|fail|abort/i.test(st)) throw new Error(`Maintenance '${proc.name || proc.id}' ended with status ${st}`);
    if (Date.now() - start > 20 * 60e3) throw new Error(`Maintenance '${proc.name || proc.id}' timed out (last status ${st}). Check the touchscreen for a prompt.`);
  }
}
/** Returns {fixed:[], manual:[]}; manual = blockers the API cannot clear. */
async function fixBlockers(log, issue) {
  if (MAINT.name) throw new Error(`Maintenance '${MAINT.name}' is already running. Wait for it to finish.`);
  const out = { fixed: [], manual: [] };
  const flags = await getFlags();
  if (flags.mustUpdate) {
    log('Maintenance routines need updating - initialising them …', 'w');
    await api('POST', '/maintenance/initialize'); out.fixed.push('Updated maintenance routines'); log('Maintenance routines updated.', 'g');
  }
  for (const m of (await listMaintenance()).filter(needsMaint)) {
    await runMaintenance(m, log); out.fixed.push(`Ran maintenance: ${m.name || m.id}`);
  }
  if (flags.mustRestoreLabware) out.manual.push('Labware must be restored - do this from the Prep touchscreen.');
  if (flags.mustRestoreVerify) out.manual.push('Protocols must be re-verified - do this from the Prep touchscreen.');
  out.manual.forEach(m => issue('error', 'Manual step needed', m));
  return out;
}
async function waitIdle(timeoutMs = 90000) {
  const t0 = Date.now();
  for (;;) {
    const s = await api('GET', '/protocol-run');
    if (s && s.protocolRunState === 0 && !s.isRunInProgress) return;
    if (Date.now() - t0 > timeoutMs) throw new Error('Timed out waiting for the Prep to become idle');
    await sleep(1500);
  }
}

// ───────────── load instructions → deck positions ─────────────
function guessLw(item, names, pos) {
  const nm = names[pos] || {}; const txt = `${nm.name || ''} ${nm.displayName || ''}`;
  if (LABWARE[nm.name]) return nm.name;
  if (/50\s*ml.*(tube|adapt)/i.test(txt) && RESERVOIR_POS.includes(pos)) return 'reservoir_50';
  if (/60\s*ml|reagent reservoir/i.test(txt) || (RESERVOIR_POS.includes(pos) && 'liquidName' in item && +item.maximumVolume >= 50)) return 'reservoir_60';
  const tip = /(\d+)\s*[uµ]l.*tip|tip.*?(\d+)\s*[uµ]l/i.exec(txt);
  if (/tip/i.test(txt) || item.labwareClassification === 'TipRack' || 'count' in item) {
    const v = tip && +(tip[1] || tip[2]); return v >= 1000 ? 'tips_1000' : v && v <= 50 ? 'tips_50' : 'tips_300';
  }
  if ('carrierRow' in item || /tube/i.test(txt)) return 'tubes_24';
  if ('liquidName' in item || 'unitType' in item || /trough|reservoir/i.test(txt)) return 'trough_25';
  if (/deep/i.test(txt)) return 'plate_deep';
  return 'plate_pcr';
}
function describeItem(item, names) {
  const pos = item.deckPosition ?? '?', nm = names[pos] || {}, lw = nm.displayName || nm.name;
  if ('count' in item) return `${item.count} tips${lw ? ` (${lw})` : ''}, suggested ${item.suggestedCount ?? '?'}`;
  if ('carrierRow' in item) return `Tube row ${item.carrierRow}, column ${item.carrierColumn}: ${item.liquidName || 'liquid'}, ${item.suggestedVolume ?? item.requiredVolume ?? '?'} (max ${item.maximumVolume ?? '?'})`;
  if ('liquidName' in item || 'unitType' in item) return `${item.liquidName || 'Liquid'}: ${item.suggestedVolume ?? item.requiredVolume ?? '?'} ${item.unitType || ''} (max ${item.maximumVolume ?? '?'})`;
  return `${lw || item.labwareClassification || 'Labware'}`;
}
/** Group the Prep's load instructions by deck position. A reservoir rack lists all 5 slots: which liquid sits where matters. */
function buildDeck(items, names) {
  const groups = new Map();
  for (const it of items) { const p = String(it.deckPosition ?? '?'); (groups.get(p) || groups.set(p, []).get(p)).push(it); }
  return [...groups].map(([pos, its]) => {
    const lw = guessLw(its[0], names, pos), L = LABWARE[lw], fills = {}, lines = [];
    if (L.kind === 'reservoirs') {
      for (let i = 0; i < L.rows; i++) {
        const it = its.find(x => +x.labwarePosition === i);
        if (!it) { lines.push({ text: `Slot ${i + 1}: leave empty`, color: '#d4d1c4' }); continue; }
        const liq = it.liquidName || 'liquid', vol = it.suggestedVolume ?? it.requiredVolume;
        lines.push({ text: `Slot ${i + 1}: ${liq}, ${vol ?? '?'} ${it.unitType || 'mL'} (max ${it.maximumVolume ?? 60})`, color: liqColor(liq) });
        fills[wellName(i, 0)] = { color: liqColor(liq), frac: vol && it.maximumVolume ? Math.min(1, vol / it.maximumVolume) : .5 };
      }
    } else for (const it of its) {
      lines.push({ text: describeItem(it, names), color: 'liquidName' in it ? liqColor(it.liquidName) : undefined });
      if ('carrierRow' in it && L.kind === 'tubes') fills[wellName(+it.carrierRow - 1, +it.carrierColumn - 1)] = { color: liqColor(it.liquidName), frac: .7 };
    }
    return { pos, lw, label: its[0].positionName || '', lines, fills, need: L.kind === 'tips' ? 96 : undefined };
  });
}
async function getLabwareNames(pid) {
  try {
    const v = await api('GET', `/protocols/verify/${enc(pid)}`); const out = {};
    for (const d of (v && v.deckPositions) || []) if (d.position != null) out[String(d.position)] = { name: d.labwareName, displayName: d.labwareDisplayName };
    return out;
  } catch { return {}; }
}

// ───────────── navigation ─────────────
const VIEWS = {};
let current = null;
function navigate(v) { location.hash = '#' + v; }
let hashRevert = false;
async function mount() {
  if (hashRevert) { hashRevert = false; return; }
  const h = location.hash.slice(1);
  let v = VIEWS[h] ? h : 'protocols';
  if (v === 'run' && !RUN.title) v = 'protocols';
  if (current === 'builder' && v !== 'builder' && VIEWS.builder.dirty()) {
    const dest = location.hash; hashRevert = true; location.hash = '#builder'; // stay put until they decide
    const leave = await confirmModal({ title: 'Leave the builder?', ok: 'Leave without saving', danger: true,
      body: '<p>This protocol has changes that haven\'t been saved. If you leave now, your work will be lost.</p><p class="small muted">Press Cancel, then <b>Save</b> to keep it.</p>' });
    if (!leave) return;
    BUILDER.snap = JSON.stringify(BUILDER.proto); location.hash = dest; return;
  }
  if (current && current !== v) VIEWS[current].leave?.();
  current = v; $('#hovercard').hidden = true;
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.view === (v === 'run' ? 'protocols' : v)));
  VIEWS[v].mount($('#main'));
}
window.addEventListener('hashchange', mount);

// ───────────── state ─────────────
const S = {
  cfg: { host: '', base: '', mock: false, folder: '', scenario: '', scenarios: [] },
  settings: { autoMaint: true, homeFirst: true }, // always on
  library: [], libKey: '', sync: null, conn: 'checking', connErr: '', runState: null, q: '', tab: 'all',
};
async function loadLibrary(quiet) {
  try { S.library = await local('GET', '/protocols'); } catch (e) { S.library = []; if (!quiet) toast(fmtErr(e.detail || e), true); }
  return S.library;
}
const byName = n => S.library.find(x => x.name === n);

/** Add files. Raw Prep protocols are wrapped for the library; everything added is starred (so Prep protocols go to the machine). */
async function uploadFiles(files) {
  const added = [];
  for (const f of files) {
    if (!/\.(py|json)$/i.test(f.name)) { toast(`${f.name}: only .py and .json files can be added`, true); continue; }
    try {
      let name = f.name, text = await f.text();
      if (/\.json$/i.test(name)) {
        const o = JSON.parse(text);
        if (o && typeof o === 'object' && !Array.isArray(o) && !o._prepSource && !Array.isArray(o.steps)) { // a plain Prep protocol export
          text = JSON.stringify({ _prepSource: { name: o.name || name.replace(/\.json$/i, ''), simulated: S.cfg.mock }, raw: o }, null, 2);
          name = name.replace(/(\.prep)?\.json$/i, '') + '.prep.json';
        } else if (o._prepSource && !/\.prep\.json$/i.test(name)) name = name.replace(/\.json$/i, '') + '.prep.json';
      }
      const saved = await putLocal(name, text);
      await local('PUT', '/favourite', { name: saved, favourite: true });
      added.push(saved);
    } catch (e) { toast(`${f.name}: ${fmtErr(e.detail || e)}`, true); }
  }
  if (added.length) { await loadLibrary(); toast(`Added ${added.length} protocol${added.length > 1 ? 's' : ''}`); if (current === 'protocols') VIEWS.protocols.draw(); }
}
const ov = $('#dropOverlay'); let dragDepth = 0;
addEventListener('dragenter', e => { if (e.dataTransfer?.types?.includes('Files')) { dragDepth++; ov.classList.add('show'); } });
addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; ov.classList.remove('show'); $$('.dz', ov).forEach(z => z.classList.remove('over')); } });
addEventListener('dragover', e => { e.preventDefault(); const z = e.target.closest?.('.dz'); $$('.dz', ov).forEach(x => x.classList.toggle('over', x === z)); });
addEventListener('drop', e => {
  e.preventDefault(); dragDepth = 0; ov.classList.remove('show');
  const once = e.target.closest?.('.dz')?.dataset.mode === 'once'; $$('.dz', ov).forEach(z => z.classList.remove('over'));
  const files = [...e.dataTransfer.files]; if (!files.length) return;
  once ? runOnce(files[0]) : uploadFiles(files);
});

// ───────────── machine menu (top right): connection, real/simulation, sync, behaviour ─────────────
const pill = $('#statusPill'), menu = $('#machineMenu');
const SCENARIO_INFO = {
  blocked: { title: 'Needs maintenance', text: 'Routines are out of date and a procedure has expired. Shows the automatic fix before a run.' },
  clean: { title: 'Ready to go', text: 'Nothing is blocking runs. The smoothest path.' },
  labware: { title: 'Needs labware restored', text: 'Can only be cleared on the touchscreen, so the app shows a manual step.' },
  verification: { title: 'Failed verification', text: "The protocol won't validate, for a reason that isn't maintenance." },
  offline: { title: 'Unplugged', text: 'Every call fails, as if the cable had been pulled out.' },
};
const menuOpen = () => !menu.hidden;
function closeMenu() { menu.hidden = true; pill.setAttribute('aria-expanded', 'false'); }
function openMenu() { menu.hidden = false; pill.setAttribute('aria-expanded', 'true'); drawMenu(); pollStatus(); }
pill.onclick = e => { e.stopPropagation(); menuOpen() ? closeMenu() : openMenu(); };
document.addEventListener('click', e => { if (menuOpen() && !menu.contains(e.target) && !pill.contains(e.target)) closeMenu(); });

async function setMock(body) {
  await local('PUT', '/mock', body);
  S.cfg = await local('GET', '/config'); S.conn = 'checking'; S.libKey = '';
  await pollStatus(); await loadLibrary(true);
  if (menuOpen()) drawMenu();
  if (!RUN.active && current && current !== 'run') VIEWS[current].mount($('#main'));
}
function stateLine() {
  const sim = S.cfg.mock, busy = S.runState && (S.runState.isRunInProgress || S.runState.protocolRunState);
  if (S.conn === 'checking') return { cls: '', text: 'Trying to connect…' };
  if (S.conn === 'bad') return { cls: 'bad', text: `Can't reach the Prep at ${S.cfg.host}` };
  const st = stateLabel(S.runState && S.runState.protocolRunState);
  return { cls: sim ? 'sim' : 'ok', text: sim ? `Simulation · ${st}` : `Connected to the Prep · ${st}`, busy };
}
function paintPill() {
  const s = stateLine();
  pill.className = 'status-pill ' + (RUN.active || s.busy || MAINT.name ? 'busy' : s.cls);
  $('#statusText').textContent = S.conn === 'bad' ? 'Prep not connected' : S.conn === 'checking' ? 'Connecting…' : S.cfg.mock ? 'Simulation' : RUN.active || s.busy ? 'Prep running' : MAINT.name ? 'Maintenance' : 'Prep connected';
  const ms = $('#mState'); if (ms) { ms.className = 'state ' + s.cls; $('#mStateText').textContent = s.text; }
  const mn = $('#mNote'); if (mn) mn.textContent = S.conn === 'bad' ? (S.cfg.mock ? S.connErr : 'Check the Ethernet cable and that this laptop is set to 192.168.100.10, then press Connect.') : '';
}
function drawMenu() {
  const sim = S.cfg.mock;
  menu.innerHTML = `
    <section><h4>Machine</h4>
      <div class="state" id="mState"><span class="dot"></span><span id="mStateText"></span></div>
      <div class="small muted" id="mNote"></div>
      <div class="seg full" id="mMode"><button data-m="real" class="${sim ? '' : 'on'}">Real Prep</button><button data-m="sim" class="${sim ? 'on' : ''}">Simulation</button></div>
      ${sim ? `<div class="small muted">A built-in fake Prep is answering. Nothing real moves.</div>
        <h4>What should the simulated Prep be like?</h4>
        <div class="opts" id="mScn" role="radiogroup">${(S.cfg.scenarios || []).map(x => { const d = SCENARIO_INFO[x] || { title: x, text: '' }; return `<button class="opt ${x === S.cfg.scenario ? 'on' : ''}" role="radio" aria-checked="${x === S.cfg.scenario}" data-s="${esc(x)}"><b>${esc(d.title)}</b><span>${esc(d.text)}</span></button>`; }).join('')}</div>
        <button class="btn-ghost" id="mReset">Reset simulation</button>`
      : `<div class="field"><label class="f" for="mHost">Prep address</label><input id="mHost" value="${esc(S.cfg.host)}"></div>
        <div class="field"><label class="f" for="mBase">API path</label><input id="mBase" value="${esc(S.cfg.base)}"></div>
        <button class="btn" id="mConnect">Connect</button>`}
    </section>`;
  paintPill();
  $$('#mMode button').forEach(b => b.onclick = () => { const want = b.dataset.m === 'sim'; if (want !== sim) setMock({ enabled: want }).then(() => toast(want ? 'Simulation on: nothing real will move' : 'Switched to the real Prep')).catch(e => toast(fmtErr(e.detail || e), true)); });
  if ($('#mConnect')) $('#mConnect').onclick = async () => {
    const b = $('#mConnect'); b.disabled = true; b.textContent = 'Trying…';
    try { S.cfg = await local('PUT', '/config', { host: $('#mHost').value, base: $('#mBase').value }); S.conn = 'checking'; await pollStatus(); toast(S.conn === 'ok' ? 'Connected to the Prep' : `Still can't reach ${S.cfg.host}`, S.conn !== 'ok'); }
    catch (e) { toast(fmtErr(e.detail || e), true); }
    if (menuOpen()) drawMenu();
  };
  $$('#mScn .opt').forEach(b => b.onclick = () => setMock({ scenario: b.dataset.s }).then(() => toast('Simulated Prep: ' + (SCENARIO_INFO[b.dataset.s]?.title || b.dataset.s))));
  if ($('#mReset')) $('#mReset').onclick = () => setMock({ scenario: S.cfg.scenario }).then(() => toast('Simulation reset'));
}
let pollGen = 0;
async function pollStatus() {
  const gen = ++pollGen; // a slow answer from an older check must not overwrite a newer one (e.g. after switching mode)
  let conn = 'ok', err = '', rs = null;
  try { await api('GET', '/system-ready'); rs = await api('GET', '/protocol-run'); }
  catch (e) { conn = 'bad'; err = fmtErr(e.detail || e); }
  if (gen !== pollGen) return;
  S.conn = conn; S.connErr = err; S.runState = rs;
  try { S.sync = await local('GET', '/sync'); } catch { /* server down */ }
  if (current !== 'protocols') loadLibrary(true); // the Protocols page polls the library itself
  paintPill();
  if (current === 'protocols') VIEWS.protocols.notice?.();
}

// ───────────── RUN engine ─────────────
const STAGES = ['Preflight', 'Maintenance', 'Validate', 'Create run', 'Load deck', 'Run', 'Done'];
const STAGE_LABEL = { Preflight: 'Get ready', Maintenance: 'Auto-fix', Validate: 'Check', 'Create run': 'Prepare', 'Load deck': 'Load deck', Run: 'Run', Done: 'Done' };
const STAGE_SAY = { Preflight: 'Checking the Prep and getting it ready…', Maintenance: 'Fixing a maintenance blocker automatically. The Prep may move, so keep clear.', Validate: 'Checking the protocol can run…',
  'Create run': 'Preparing the run…', 'Load deck': 'Load the deck as shown, tick each item, then press the green button.', Run: 'Running. You can leave this page open.', Done: 'All done.' };
const RUN = { active: false, kind: 'prep', stages: {}, log: [], issues: [], deck: null, checks: new Set(), gate: null, job: null, abortFlag: false, title: '', progress: '' };
const runUI = () => VIEWS.run.update && current === 'run' && VIEWS.run.update();
function rlog(msg, cls = '') { RUN.log.push({ t: new Date().toLocaleTimeString(), msg, cls }); runUI(); }
function rissue(level, title, detail = '') { RUN.issues.push({ level, title, detail }); runUI(); }
function rstage(name, st) { RUN.stages[name] = st; runUI(); paintPill(); }
function resetRun(title, kind) { Object.assign(RUN, { active: true, kind, stages: {}, log: [], issues: [], deck: null, checks: new Set(), gate: null, job: null, abortFlag: false, title, progress: '' }); paintPill(); }
const checkAbort = () => { if (RUN.abortFlag) throw Object.assign(new Error('Stopped'), { aborted: true }); };

async function runInstrument({ id, name, simulate, onDone, onStart }) {
  resetRun(name || id, 'prep'); RUN.simulate = simulate; runUI();
  let finishedOk = false;
  try {
    rstage('Preflight', 'active');
    try { await api('GET', '/system-ready'); } catch (e) { throw new Error(`Can't reach the Prep at ${S.cfg.host}. Open the machine menu (top right) to check the connection. ${fmtErr(e.detail || e)}`); }
    const st = await api('GET', '/protocol-run');
    if (st && (st.isRunInProgress || st.protocolRunState)) {
      rissue('warn', 'A run was already in progress', `State: ${stateLabel(st.protocolRunState)}. Stopping it first.`);
      await api('PUT', '/protocol-run/abort'); await waitIdle(); rlog('Previous run stopped.', 'g');
    }
    if (S.settings.homeFirst) { rlog('Homing the instrument …'); await api('POST', '/instruments/initialize'); }
    rstage('Preflight', 'done'); checkAbort();

    rstage('Validate', 'active');
    let ok = false;
    for (let attempt = 0; attempt < 3 && !ok; attempt++) {
      const v = await api('GET', `/protocols/validate/${enc(id)}`);
      if (!(v && v.isValid === false)) { ok = true; break; }
      const errs = (v.validationErrors || []).map(fmtErr);
      errs.forEach(e => rissue('error', 'The protocol is not runnable yet', e));
      if (!S.settings.autoMaint) throw new Error('The protocol is blocked and automatic fixing is switched off.');
      rstage('Validate', 'fail'); rstage('Maintenance', 'active');
      const blockers = (await listMaintenance()).filter(needsMaint).length || Object.values(await getFlags()).some(Boolean);
      if (!blockers) throw new Error('Blocked, but not by maintenance: likely an instrument verification routine. ' + errs.join(' | '));
      rissue('info', 'Maintenance is blocking this run, fixing it automatically', 'Some routines move the instrument. Watch the Prep.');
      const r = await fixBlockers(rlog, rissue);
      r.fixed.forEach(f => rissue('ok', 'Fixed', f));
      if (r.manual.length) throw new Error('Cannot continue: a manual step is needed on the Prep (see above).');
      rstage('Maintenance', 'done'); rstage('Validate', 'active'); checkAbort();
    }
    if (!ok) throw new Error('Still not valid after maintenance. See the messages above.');
    if (!RUN.stages.Maintenance) rstage('Maintenance', 'done');
    rstage('Validate', 'done'); checkAbort();

    rstage('Create run', 'active');
    const create = () => api('POST', '/protocol-run/create', { protocolId: id, simulate, recompile: false });
    try { await create(); } catch (e) {
      if (S.settings.autoMaint && /maint/i.test(fmtErr(e.detail))) { rissue('warn', 'Run creation blocked by maintenance', fmtErr(e.detail)); await fixBlockers(rlog, rissue); await create(); } else throw e;
    }
    for (let i = 0; ; i++) { checkAbort(); const s = await api('GET', '/protocol-run'); if (s.protocolRunState === 20) break; if (i > 900) throw new Error('Timed out waiting for the loading state'); await sleep(1500); }
    rstage('Create run', 'done'); rstage('Load deck', 'active');

    const [instr, names] = [await api('GET', '/protocol-run/load-instructions'), await getLabwareNames(id)];
    RUN.deck = { positions: buildDeck(normList(instr).filter(x => x && typeof x === 'object'), names) };
    const go = await new Promise(res => { RUN.gate = res; runUI(); });
    RUN.gate = null; checkAbort();
    if (!go) { await api('PUT', '/protocol-run/load-complete', { protocolId: id, simulate, loadCompleteAction: 1 }); throw Object.assign(new Error('Cancelled at the load step'), { aborted: true }); }
    // find which tips are really on each rack, so the Prep never tries to pick up from an empty spot
    const racks = [...new Set(normList(instr).filter(x => x && typeof x === 'object' && (/tip/i.test(x.labwareClassification || '') || x.suggestedCount != null)).map(x => +x.deckPosition))].filter(Number.isFinite);
    const residualTips = [];
    for (const pos of racks) {
      try {
        rlog(`Checking tips at position ${pos} …`);
        const d = await api('GET', `/protocol-verification/detect-tips/${enc(id)}/${pos}`);
        const n = d && Array.isArray(d.addresses) ? d.addresses.length : 0;
        if (d && (d.useAllAddresses || n)) { residualTips.push({ deckPosition: pos, residualTipData: d }); rlog(d.useAllAddresses ? `Position ${pos}: full rack.` : `Position ${pos}: ${n} tips found.`, 'g'); }
        else rissue('warn', `No tips detected at position ${pos}`, 'Check the tip rack is loaded and has tips.');
      } catch (e) { rissue('warn', `Could not check the tips at position ${pos}`, `Carrying on without it. ${fmtErr(e.detail || e)}`); }
    }
    checkAbort();
    rstage('Load deck', 'done'); rstage('Run', 'active');
    await api('PUT', '/protocol-run/load-complete', { protocolId: id, simulate, loadCompleteAction: 0, ...(residualTips.length ? { residualTips } : {}) });
    rlog('Run started.'); try { onStart?.(); } catch { /* reporting is best effort */ } await sleep(3000);
    const t0 = Date.now();
    for (;;) {
      if (RUN.abortFlag) { await api('PUT', '/protocol-run/abort'); throw Object.assign(new Error('Run stopped'), { aborted: true }); }
      const s = await api('GET', '/protocol-run');
      RUN.progress = `${stateLabel(s.protocolRunState)} · ${Math.round((Date.now() - t0) / 1000)} s`; runUI();
      if (s.protocolRunState === 0 || s.isRunInProgress === false) break;
      await sleep(2000);
    }
    rstage('Run', 'done');
    const took = Math.round((Date.now() - t0) / 1000);
    RUN.progress = 'Homing the Prep…'; rlog('Run finished. Homing the instrument …');
    try { await api('POST', '/instruments/initialize'); rlog('Instrument homed.', 'g'); }
    catch (e) { rissue('warn', "The run finished, but the Prep couldn't be homed", fmtErr(e.detail || e)); }
    rstage('Done', 'done'); finishedOk = true;
    rissue('ok', 'Run finished', `${simulate ? 'Practice run' : 'Run'} completed in ${took} s.`);
  } catch (e) {
    const cur = STAGES.find(s => RUN.stages[s] === 'active' || RUN.stages[s] === 'fail');
    if (cur) rstage(cur, 'fail');
    if (e.aborted) rissue('warn', e.message); else rissue('error', 'The run stopped', fmtErr(e.detail || e));
    rlog(fmtErr(e.detail || e), 'e');
  } finally { RUN.active = false; RUN.deck = null; RUN.gate = null; runUI(); try { await onDone?.(); } catch { /* temporary copy already gone */ } pollStatus();
    // a clean finish goes straight back to the protocols; a failed or stopped run stays so the messages can be read
    if (finishedOk && current === 'run') { navigate('protocols'); toast(`“${name || id}” finished${RUN.issues.some(i => i.level === 'warn') ? ' (with a warning, see the run log)' : ''}`); }
  }
}

async function runScript(name, title, simulate, oneoffText) {
  resetRun(title, 'script'); RUN.simulate = simulate; runUI();
  try {
    rstage('Run', 'active');
    const { id } = oneoffText != null ? await local('POST', '/oneoff/script', { name, content: oneoffText, simulate }) : await local('POST', '/jobs', { name, simulate }); RUN.job = id; let since = 0;
    for (;;) {
      const d = await local('GET', `/jobs/${id}?since=${since}`); since = d.next;
      d.lines.forEach(l => RUN.log.push({ t: '', msg: l, cls: /error|traceback|exception/i.test(l) ? 'e' : /warn/i.test(l) ? 'w' : '' }));
      runUI();
      if (d.done) {
        if (d.code === 0) { rstage('Run', 'done'); rstage('Done', 'done'); rissue('ok', 'Finished', 'The script completed normally.'); }
        else { rstage('Run', 'fail'); rissue('error', 'The script failed', `Exit code ${d.code}. See the details below.`); }
        break;
      }
      await sleep(600);
    }
  } catch (e) { rissue('error', 'Could not run this', fmtErr(e.detail || e)); }
  finally { RUN.active = false; RUN.job = null; runUI(); }
}

/** Ask for the values of a protocol's variables (e.g. how much of the bead reagent to add). Resolves { name: number } or null if cancelled. */
function askVariables(title, spec) {
  const vars = spec.variables || [], Calc = PrepCalc.prepProtocol;
  return new Promise(res => {
    modal(`<h2>Settings for “${esc(title)}”</h2><p class="small muted">This protocol has settings that change from run to run. Enter them now; the protocol is built with these values.</p>
      <div class="stack-sm">${vars.map((v, i) => `<label class="field"><span>${esc(v.label || v.name)}${v.unit ? ` (${esc(v.unit)})` : ''}</span><input type="number" step="any" id="var${i}" data-name="${esc(v.name)}" value="${esc(v.default)}" min="${esc(v.min)}" max="${esc(v.max)}"><span class="small muted">Between ${esc(v.min)} and ${esc(v.max)}${v.unit ? ' ' + esc(v.unit) : ''}. Usual value ${esc(v.default)}.</span></label>`).join('')}</div>
      <div id="varMsg" class="small" aria-live="polite"></div>
      <div class="row end"><button class="btn-ghost" id="vNo">Cancel</button><button class="btn" id="vYes">Continue</button></div>`);
    const read = () => Object.fromEntries(vars.map((v, i) => [v.name, $('#var' + i).value]));
    const update = () => {
      const r = Calc.resolve(spec, read()), msg = $('#varMsg');
      if (!r.spec) { msg.innerHTML = r.errors.map(e => `<div class="notice warn">${esc(e)}</div>`).join(''); $('#vYes').disabled = true; return; }
      const need = Calc.validate(r.spec).load.liquids; // what has to be loaded for these values
      msg.innerHTML = need.length ? `<div class="notice info"><b>You will need to load:</b>${need.map(l => `<div>Position ${esc(l.pos)}, ${esc(l.slot)}: ${esc(l.liquid)}, at least ${esc(l.need)} µL</div>`).join('')}</div>` : '';
      $('#vYes').disabled = false;
    };
    vars.forEach((v, i) => $('#var' + i).oninput = update);
    $('#vNo').onclick = () => { closeModal(); res(null); };
    $('#vYes').onclick = () => { const values = read(); closeModal(); res(values); };
    update();
  });
}
function startModal(title, forceSim, once) {
  return new Promise(res => {
    modal(`<h2>Run “${esc(title)}”?</h2>
      ${once ? '<div class="notice info">One-off run: this won\'t be saved to your protocols.</div>' : ''}<p>The Prep will check itself, fix any maintenance blockers automatically, then show you what to load where.${forceSim ? '' : ' Keep the deck area clear: it moves.'}</p>
      ${forceSim ? '<div class="notice info">Simulation is on, so nothing real will move.</div>' : `<label class="check"><input type="checkbox" id="prac"><div><b>Practice run (simulate)</b><div class="small muted">Goes through the motions without moving liquid.</div></div></label>`}
      <div class="row end"><button class="btn-ghost" id="smNo">Cancel</button><button class="btn" id="smYes">▶ Start run</button></div>`);
    $('#smNo').onclick = () => { closeModal(); res(null); };
    $('#smYes').onclick = () => { const practice = !!$('#prac')?.checked; closeModal(); res({ practice }); };
  });
}
async function startFlow(item) {
  if (RUN.active) return navigate('run');
  if (MAINT.name) return toast(`Maintenance '${MAINT.name}' is still running. Wait for it to finish before starting a run.`, true);
  let spec = null;
  if (item.kind === 'json') {
    spec = JSON.parse((await local('GET', '/protocols/' + enc(item.name))).content);
    if (!spec.instrumentProtocolId) return runBuilderProtocol(item, spec);
  }
  const choice = await startModal(item.title, S.cfg.mock); if (!choice) return;
  const simulate = S.cfg.mock || choice.practice;
  if (item.kind === 'py') { const p = runScript(item.name, item.title, simulate); navigate('run'); return p; }
  let id = spec && spec.instrumentProtocolId;
  if (item.kind === 'prep') {
    try {
      id = item.machineId;
      if (!id) {
        toast('Putting this protocol on the Prep first…');
        await local('PUT', '/favourite', { name: item.name, favourite: true }); await loadLibrary(true);
        const it = byName(item.name); id = it && it.machineId;
        if (!id) throw new Error((it && it.error) || "Couldn't put this protocol on the Prep. Check the connection in the machine menu (top right).");
      }
    } catch (e) { return toast(fmtErr(e.detail || e), true); }
  }
  const p = runInstrument({ id, name: item.title, simulate }); navigate('run'); return p;
}

/** Run a Builder protocol: ask for its variables, compile it with the converter (compiler.py) and run it. If it can't be compiled, show the simulation. Nothing starts by itself. */
async function runBuilderProtocol(item, spec) {
  let raw, values;
  if (spec.variables && spec.variables.length) { values = await askVariables(item.title, spec); if (!values) return; } // the person running it fills in the variables first
  try { raw = (await local('POST', '/compile', item.name ? { name: item.name, values } : { spec, values })).raw; }
  catch (e) {
    if (e.status !== 501) return toast(fmtErr(e.detail || e), true);
    toast("This protocol can't be run on the robot: " + fmtErr(e.detail || e.message || 'the converter refused it') + " Opening the simulation instead.", true, 15000);
    window.BUILDER_OPEN = spec; window.BUILDER_FILE = item.name || null; return navigate('builder');
  }
  const choice = await startModal(item.title, S.cfg.mock, true); if (!choice) return;
  let id;
  try { toast('Putting it on the Prep for this run…'); ({ id } = await local('POST', '/oneoff/prep', { raw, name: item.title })); }
  catch (e) { return toast(fmtErr(e.detail || e), true); }
  const p = runInstrument({ id, name: item.title, simulate: S.cfg.mock || choice.practice, onDone: () => local('DELETE', '/oneoff/prep/' + enc(id)) });
  navigate('run'); return p;
}

/** Run a dropped/picked file once, without adding it to the library. */
async function runOnce(file) {
  if (MAINT.name) return toast(`Maintenance '${MAINT.name}' is still running. Wait for it to finish before starting a run.`, true);
  if (RUN.active) { toast('A run is already in progress', true); return navigate('run'); }
  if (!/\.(py|json)$/i.test(file.name)) return toast('Only .py and .json files can be run', true);
  try {
    const text = await file.text(), title = file.name.replace(/\.(prep\.)?(py|json)$/i, '');
    if (/\.py$/i.test(file.name)) {
      const ch = await startModal(title, S.cfg.mock, true); if (!ch) return;
      const p = runScript(file.name, title, S.cfg.mock || ch.practice, text); navigate('run'); return p;
    }
    const o = JSON.parse(text);
    if (o && Array.isArray(o.steps)) { // a Builder protocol: compile and run it (or, if it is linked to a protocol already on the Prep, run that one)
      if (!o.instrumentProtocolId) return runBuilderProtocol({ title: o.name || title }, o);
      const ch = await startModal(o.name || title, S.cfg.mock, true); if (!ch) return;
      const p = runInstrument({ id: o.instrumentProtocolId, name: o.name || title, simulate: S.cfg.mock || ch.practice }); navigate('run'); return p;
    }
    const raw = o && o.raw && typeof o.raw === 'object' ? o.raw : o, name = raw.name || (o._prepSource && o._prepSource.name) || title;
    const ch = await startModal(name, S.cfg.mock, true); if (!ch) return;
    toast('Putting it on the Prep for this run…');
    const { id } = await local('POST', '/oneoff/prep', { raw, name });
    const p = runInstrument({ id, name, simulate: S.cfg.mock || ch.practice, onDone: () => local('DELETE', '/oneoff/prep/' + enc(id)) }); navigate('run'); return p;
  } catch (e) { toast(e instanceof SyntaxError ? "That file isn't valid JSON" : fmtErr(e.detail || e), true); }
}

// ───────────── PROTOCOLS (home) ─────────────
function statusChips(it) { return prepChips(it); }
function prepChips(it) {
  if (it.pending === 'upload') return '<span class="chip warn">Uploading to the Prep…</span>';
  if (it.pending === 'remove') return '<span class="chip warn">Removing from the Prep…</span>';
  if (it.canSync) return it.onPrep ? '<span class="chip ok">✓ On the Prep</span>' : '<span class="chip">Not on the Prep</span>';
  return it.favourite ? '<span class="chip info">Pinned · runs from this laptop</span>' : '';
}
const starTip = it => it.canSync ? (it.favourite ? 'Starred: kept on the Prep. Click to remove it from the Prep.' : 'Click to star it and put it on the Prep.') : (it.favourite ? 'Pinned to the top. Click to unpin.' : 'Click to pin to the top.');
function cardHTML(it) {
  return `<article class="pcard ${it.favourite ? 'fav' : ''}" data-n="${esc(it.name)}">
    <div class="pcard-top"><h3>${esc(it.title)}</h3><button class="star ${it.favourite ? 'on' : ''}" data-a="star" aria-pressed="${it.favourite}" title="${esc(starTip(it))}" aria-label="${esc(starTip(it))}">${STAR}</button></div>
    <p class="pdesc">${esc(it.description || ' ')}</p>
    <div class="pcard-meta">${statusChips(it)}</div>
    ${it.error ? `<div class="pcard-err">${esc(it.error)}</div>` : ''}
    <div class="pcard-actions"><button class="btn" data-a="run">▶ Run</button><button class="btn-ghost" data-a="edit">Edit</button><button class="btn-ghost" data-a="export">Export</button><span class="spacer"></span><button class="btn-ghost quiet" data-a="del">Delete</button></div>
  </article>`;
}
VIEWS.protocols = {
  timer: null,
  async mount(el) {
    el.innerHTML = `<div class="stack">
      <div class="page-head"><div><h1>Your <em>protocols</em></h1><p class="subtitle">Everything on the Prep appears here automatically. Drop any file on the page to add it, or run it once without saving. <b>Star</b> a protocol to keep it on the Prep; un-star it to take it off the machine (a copy always stays here).</p></div>
        <div class="row"><button class="btn" id="add">＋ Add protocol</button><button class="btn-ghost" id="once" title="Run a .py or .json file now without saving it">Run a file once</button><a class="btn-ghost" href="#builder">Build one</a></div></div>
      <input type="file" id="fi" multiple accept=".py,.json" hidden><input type="file" id="fo" accept=".py,.json" hidden>
      <div id="notice"></div>
      <div class="toolbar"><div class="tabs" id="tabs"><button data-t="all">All</button><button data-t="fav">★ Starred</button></div>
        <div class="search"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg><input type="search" id="q" placeholder="Search protocols" value="${esc(S.q)}" aria-label="Search protocols"></div></div>
      <div class="cards" id="cards"></div></div>`;
    $('#once').onclick = () => $('#fo').click(); $('#fo').onchange = e => { if (e.target.files[0]) runOnce(e.target.files[0]); e.target.value = ''; };
    $('#add').onclick = () => $('#fi').click(); $('#fi').onchange = e => { uploadFiles([...e.target.files]); e.target.value = ''; };
    $('#q').oninput = e => { S.q = e.target.value; this.draw(); };
    $$('#tabs button').forEach(b => b.onclick = () => { S.tab = b.dataset.t; this.draw(); });
    S.libKey = ''; delete $('#notice')?.dataset.k; // this page is freshly built, so force a full draw
    await loadLibrary(); this.draw(); this.notice();
    clearInterval(this.timer); this.timer = setInterval(async () => { await loadLibrary(true); this.draw(); }, 4000);
  },
  leave() { clearInterval(this.timer); },
  notice() {
    const n = $('#notice'); if (!n) return;
    const k = `${S.conn}|${S.cfg.mock}|${RUN.active}`; if (n.dataset.k === k) return; n.dataset.k = k;
    n.innerHTML = RUN.active ? `<div class="notice info"><b>A run is in progress:</b> ${esc(RUN.title)}<span class="spacer"></span><a class="btn sm" href="#run">View it</a></div>`
      : S.conn === 'bad' ? `<div class="notice bad"><div><b>Can't reach the Prep.</b> You can still see and edit your saved protocols; changes sync when it's back.</div><span class="spacer"></span><button class="btn sm" id="nTry">Try again</button><button class="btn-ghost sm" id="nSim">Use simulation</button></div>` : '';
    if ($('#nTry')) $('#nTry').onclick = async () => { S.conn = 'checking'; paintPill(); await pollStatus(); };
    if ($('#nSim')) $('#nSim').onclick = () => setMock({ enabled: true });
  },
  draw() {
    const cards = $('#cards'); if (!cards) return;
    $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === S.tab));
    const q = S.q.trim().toLowerCase();
    const list = S.library.filter(it => (S.tab === 'all' || it.favourite) && (!q || `${it.title} ${it.description}`.toLowerCase().includes(q)))
      .sort((a, b) => (b.favourite - a.favourite) || a.title.localeCompare(b.title));
    const key = JSON.stringify([list, S.tab, q, RUN.active]); if (key === S.libKey) return; S.libKey = key;
    cards.innerHTML = list.map(cardHTML).join('') || `<div class="panel" style="grid-column:1/-1"><div class="empty"><h2>${S.library.length ? 'No matches' : 'No protocols yet'}</h2><span>${S.library.length ? 'Try a different search.' : 'Protocols stored on the Prep show up here by themselves. You can also drop a file anywhere on this page, or press “Add protocol”.'}</span></div></div>`;
    $$('.pcard', cards).forEach(c => {
      const it = byName(c.dataset.n);
      $$('[data-a]', c).forEach(b => b.onclick = () => this.act(b.dataset.a, it));
    });
    this.notice();
  },
  async act(a, it) {
    try {
      if (a === 'run') return await startFlow(it);
      if (a === 'edit') return await editItem(it);
      if (a === 'export') {
        const f = await local('GET', '/protocols/' + enc(it.name));
        return downloadText(it.name, f.content, it.name.endsWith('.json') ? 'application/json' : 'text/plain');
      }
      if (a === 'star') {
        if (it.favourite && it.canSync && it.onPrep && !await confirmModal({ title: `Remove “${it.title}” from the Prep?`, ok: 'Un-star and remove',
          body: '<p>It will be deleted from the machine. A copy stays in this library, and you can star it again any time to put it back.</p>' })) return;
        it.favourite = !it.favourite; it.pending = it.canSync ? (it.favourite ? 'upload' : it.onPrep ? 'remove' : null) : null; S.libKey = ''; this.draw();
        await local('PUT', '/favourite', { name: it.name, favourite: it.favourite });
        await loadLibrary(true); S.libKey = ''; this.draw(); pollStatus();
        return;
      }
      if (a === 'del') {
        if (!await confirmModal({ title: `Delete “${it.title}”?`, ok: 'Delete', danger: true,
          body: `<p>This removes it from the library${it.onPrep ? ' and from the Prep (a safety copy of the Prep version is kept in the protocols folder)' : ''}.</p>` })) return;
        await local('DELETE', '/protocols/' + enc(it.name)); await loadLibrary(true); S.libKey = ''; this.draw();
      }
    } catch (e) { toast(fmtErr(e.detail || e), true); await loadLibrary(true); S.libKey = ''; this.draw(); }
  },
};

/** Best-effort deck summary; the GET response shape is not fully documented, so every access is defensive. */
function prepDeckSummary(raw) {
  const rows = [];
  for (const pos of (raw && raw.deck && raw.deck.positions) || []) {
    const lps = pos.labwarePositions || [], liq = lps.map(l => l.labwareGroup && l.labwareGroup.liquidName).filter(Boolean);
    const vols = lps.map(l => l.liquidVolume).filter(v => v != null && v !== 0);
    rows.push(`<tr><td><b>${esc(pos.position)}</b></td><td>${lps.length}</td><td>${esc([...new Set(liq)].join(', ') || '-')}</td><td>${esc(vols.join(', ') || '-')}</td></tr>`);
  }
  return rows.length ? `<div class="table-wrap"><table><thead><tr><th>Deck position</th><th>Labware slots</th><th>Liquids</th><th>Volumes</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>` : '';
}
async function editItem(it) {
  if (it.kind === 'json') { window.BUILDER_OPEN = JSON.parse((await local('GET', '/protocols/' + enc(it.name))).content); window.BUILDER_FILE = it.name; return navigate('builder'); }
  const f = await local('GET', '/protocols/' + enc(it.name));
  if (it.kind === 'py') {
    modal(`<h2>${esc(it.title)}</h2><textarea id="src" rows="22" spellcheck="false">${esc(f.content)}</textarea><div class="row end"><button class="btn-ghost" id="x">Close</button><button class="btn" id="sv">Save</button></div>`);
    $('#x').onclick = closeModal; $('#sv').onclick = async () => { try { await putLocal(it.name, $('#src').value); closeModal(); toast('Saved'); S.libKey = ''; await loadLibrary(true); VIEWS.protocols.draw(); } catch (e) { toast(fmtErr(e), true); } };
    return;
  }
  const doc = JSON.parse(f.content);
  modal(`<h2>${esc(it.title)}</h2>
    <p class="small muted">${it.favourite ? 'This protocol is starred, so saving here updates the Prep automatically.' : 'This protocol is not starred. Star it to put it on the Prep.'} Edits are checked by the Prep itself.</p>
    ${prepDeckSummary(doc.raw)}
    <div class="field"><label class="f" for="praw">Protocol details (advanced)</label><textarea id="praw" rows="16" spellcheck="false">${esc(JSON.stringify(doc.raw, null, 2))}</textarea></div>
    <div id="pres"></div>
    <div class="row end"><button class="btn-ghost" id="pclose">Close</button><button class="btn-ghost" id="pdup">Save as a copy</button><button class="btn" id="psave">Save${it.favourite ? ' &amp; sync' : ''}</button></div>`);
  const parse = () => { try { return JSON.parse($('#praw').value); } catch (e) { $('#pres').innerHTML = `<div class="issue error"><span class="ico">✕</span><div><b>That isn't valid JSON</b>${esc(e.message)}</div></div>`; return null; } };
  $('#pclose').onclick = closeModal;
  $('#psave').onclick = async () => {
    const raw = parse(); if (!raw) return; doc.raw = raw;
    try {
      await putLocal(it.name, JSON.stringify(doc, null, 2));
      if (it.favourite) { S.sync = await local('POST', '/sync', {}); }
      await loadLibrary(true); const now = byName(it.name); S.libKey = ''; VIEWS.protocols.draw?.();
      $('#pres').innerHTML = now && now.error ? `<div class="issue error"><span class="ico">✕</span><div><b>The Prep didn't accept this</b>${esc(now.error)}</div></div>` : `<div class="issue ok"><span class="ico">✓</span><div><b>Saved${it.favourite ? ' and synced to the Prep' : ''}.</b></div></div>`;
    } catch (e) { $('#pres').innerHTML = `<div class="issue error"><span class="ico">✕</span><div><b>Couldn't save</b>${esc(fmtErr(e.detail || e))}</div></div>`; }
  };
  $('#pdup').onclick = async () => {
    const raw = parse(); if (!raw) return; const title = (raw.name || it.title) + ' (copy)';
    try { const n = await putLocal(`${title.replace(/[^\w.\- ()]/g, '_')}.prep.json`, JSON.stringify({ _prepSource: { name: title, simulated: S.cfg.mock }, raw: { ...raw, name: title, id: undefined } }, null, 2)); closeModal(); toast('Saved a copy. Star it to put it on the Prep.'); await loadLibrary(true); S.libKey = ''; VIEWS.protocols.draw(); } catch (e) { toast(fmtErr(e), true); }
  };
}

// ───────────── RUN view ─────────────
VIEWS.run = {
  mount(el) {
    el.innerHTML = `<div class="stack" id="runpage"></div>`;
    this.update();
  },
  update() {
    const page = $('#runpage'); if (!page) return;
    const stageList = RUN.kind === 'script' ? ['Run', 'Done'] : STAGES;
    const active = stageList.find(s => RUN.stages[s] === 'active') || stageList.find(s => RUN.stages[s] === 'fail');
    const finished = !RUN.active;
    const say = finished ? (RUN.stages.Done === 'done' ? 'All done.' : 'The run did not finish.') : STAGE_SAY[active] || 'Starting…';
    const issues = RUN.issues.map(i => `<div class="issue ${i.level}"><span class="ico">${{ error: '✕', warn: '!', info: 'i', ok: '✓' }[i.level]}</span><div><b>${esc(i.title)}</b>${i.detail ? esc(i.detail) : ''}</div></div>`).join('');
    let deck = '';
    if (RUN.deck) {
      const n = Math.max(8, Math.ceil(RUN.deck.positions.length / 4) * 4), sl = fixedSlots(n);
      RUN.deck.positions.forEach(p => {
        const idx = /^[1-9]\d*$/.test(p.pos) && +p.pos <= n && !sl[+p.pos - 1].lw ? +p.pos - 1 : sl.findIndex(s => !s.lw);
        sl[idx] = { pos: p.pos, lw: p.lw, label: p.label, fills: p.fills, need: p.need };
      });
      deck = `<div class="panel"><div class="card-head"><div><h2>Load the deck</h2><span class="small muted">Hover a position on the map to see what goes in it.</span></div></div>
        <div class="grid cols-deck"><div class="deck-wrap" id="deckmap">${deckSVG(sl, { done: new Set() })}</div>
        <div class="checklist">${RUN.deck.positions.map(p => `<div class="check" data-pos="${esc(p.pos)}"><div><b>Position ${esc(p.pos)}${p.pos === HHS_POS ? ' · Heater-Shaker' : ''} · ${esc(LIB[p.lw].label)}</b>${p.lines.map(l => `<div class="small muted">${esc(l.text)}</div>`).join('')}</div></div>`).join('')}</div></div>
        <div class="row"><button class="btn lg" id="loaded" ${RUN.gate ? '' : 'disabled'}>✓ Yes, loaded</button><button class="btn-ghost" id="cancelLoad">Cancel</button></div></div>`;
    }
    const stdin = RUN.job ? `<div class="row"><input id="stdin" placeholder="Send input to the script (e.g. press Enter / type YES)" style="flex:1"><button class="btn sm" id="sendin">Send</button></div>` : '';
    page.innerHTML = `<a class="back" href="#protocols">← All protocols</a>
      <div class="page-head"><div><h1>${esc(RUN.title)}</h1><p class="subtitle">${RUN.simulate ? 'Practice run: nothing real moves.' : 'Real run on the Prep.'} <span class="mono">${esc(RUN.progress)}</span></p></div>
        <div class="row">${RUN.active ? '<button class="btn danger" id="stop">■ Stop</button>' : '<a class="btn" href="#protocols">Back to protocols</a>'}</div></div>
      <div class="panel"><div class="stages">${stageList.map(s => `<div class="stage ${RUN.stages[s] || ''}"><i></i>${STAGE_LABEL[s]}</div>`).join('')}</div>
        <div class="say">${esc(say)}</div>${issues ? `<div class="issues">${issues}</div>` : ''}</div>
      ${deck}
      <div class="panel"><details class="more"><summary>Technical details</summary><div class="stack-sm"><div class="console" id="console">${RUN.log.map(l => `<span class="t">${l.t}</span> <span class="${l.cls}">${esc(l.msg)}</span>`).join('\n')}</div>${stdin}</div></details></div>`;
    const c = $('#console'); if (c) c.scrollTop = c.scrollHeight;
    if (RUN.deck) attachTips($('#deckmap'), pos => {
      const p = RUN.deck.positions.find(x => x.pos === pos);
      return p ? tipHTML(pos, p.lw, p.label, p.lines, RESERVOIR_POS.includes(pos) ? ['Reagent reservoir position (bottom of the column).'] : []) : tipHTML(pos, null, '', []);
    });
    if ($('#loaded')) $('#loaded').onclick = () => RUN.gate && RUN.gate(true);
    if ($('#cancelLoad')) $('#cancelLoad').onclick = () => RUN.gate && RUN.gate(false);
    if ($('#stop')) $('#stop').onclick = async () => { RUN.abortFlag = true; if (RUN.gate) RUN.gate(false); if (RUN.job) await local('POST', `/jobs/${RUN.job}/kill`); toast('Stopping…'); };
    const send = async () => { const i = $('#stdin'); if (i && RUN.job) { await local('POST', `/jobs/${RUN.job}/stdin`, { text: i.value }); i.value = ''; } };
    if ($('#sendin')) { $('#sendin').onclick = send; $('#stdin').onkeydown = e => e.key === 'Enter' && send(); }
  },
};

// ───────────── MAINTENANCE view ─────────────
VIEWS.instrument = {
  async mount(el) {
    el.innerHTML = `<div class="stack">
      <div class="page-head"><div><h1>Machine <em>maintenance</em></h1><p class="subtitle">What's blocking runs, and the Prep's maintenance procedures. Maintenance is fixed automatically when you start a run; you can also do it here.</p></div></div>
      <div class="grid cols-2"><div class="panel" id="state"></div><div class="panel"><div class="card-head"><h2>Blockers</h2><button class="btn" id="fix">Fix blockers now</button></div><div id="blk" class="muted small">Checking…</div><div class="console" id="fixlog" style="height:150px"></div></div></div>
      <div class="panel"><h2>Maintenance procedures</h2><div class="table-wrap" id="maint"></div></div></div>`;
    this.refresh();
    $('#fix').onclick = async () => {
      const log = (m, c = '') => { const l = $('#fixlog'); if (l) { l.innerHTML += `<span class="${c}">${esc(m)}</span>\n`; l.scrollTop = l.scrollHeight; } };
      if (MAINT.name || RUN.active) return toast(MAINT.name ? `Maintenance '${MAINT.name}' is already running.` : 'A run is in progress.', true);
      $('#fix').disabled = true;
      try { const r = await fixBlockers(log, (lv, t, d) => log(`${t}: ${d}`, 'e')); log(r.fixed.length ? 'All blockers fixed.' : 'Nothing needed fixing.', 'g'); }
      catch (e) { log(fmtErr(e.detail || e), 'e'); }
      $('#fix').disabled = false; this.refresh();
    };
  },
  async refresh() {
    const set = (id, h) => { const e = $('#' + id); if (e) e.innerHTML = h; };
    try {
      const [ready, run, flags, maint] = [await api('GET', '/system-ready'), await api('GET', '/protocol-run'), await getFlags(), await listMaintenance()];
      set('state', `<h2>Status</h2><div class="table-wrap"><table><tbody><tr><td>Prep ready</td><td><span class="chip ${truthy(ready) || ready ? 'ok' : 'err'}">${truthy(ready) || ready ? 'Yes' : 'No'}</span></td></tr><tr><td>State</td><td>${esc(stateLabel(run.protocolRunState))}</td></tr><tr><td>Connected to</td><td class="mono">${S.cfg.mock ? 'Simulation' : esc(S.cfg.host)}</td></tr></tbody></table></div>`);
      const bl = [flags.mustUpdate && 'Maintenance routines must be updated (fixed automatically)', ...maint.filter(needsMaint).map(m => `Expired: ${m.name || m.id} (fixed automatically)`), flags.mustRestoreLabware && 'Labware must be restored (do this on the Prep touchscreen)', flags.mustRestoreVerify && 'Protocols must be re-verified (do this on the Prep touchscreen)'].filter(Boolean);
      set('blk', bl.length ? `<div class="issues">${bl.map(b => `<div class="issue ${/touchscreen/.test(b) ? 'error' : 'warn'}"><span class="ico">!</span><div>${esc(b)}</div></div>`).join('')}</div>` : '<div class="issue ok"><span class="ico">✓</span><div>Nothing is blocking runs.</div></div>');
      set('maint', `<table><thead><tr><th>Procedure</th><th>Status</th><th>Last run</th><th></th></tr></thead><tbody>${maint.map(m => `<tr><td><b>${esc(m.name || m.id)}</b><div class="small muted">${esc(m.info || '')}</div></td><td><span class="chip ${/^ok$/i.test(m.status) ? 'ok' : 'warn'}">${esc(m.status)}</span></td><td class="small">${esc(m.lastExecutionTime || '-')}</td><td class="right"><button class="btn-ghost sm" data-m="${esc(m.id)}">Run now</button></td></tr>`).join('') || '<tr><td colspan="4" class="muted" style="text-align:center">No procedures returned.</td></tr>'}</tbody></table>`);
      $$('[data-m]').forEach(b => b.onclick = async () => { if (MAINT.name || RUN.active) return toast(MAINT.name ? `Maintenance '${MAINT.name}' is already running.` : 'A run is in progress.', true); b.disabled = true; try { await runMaintenance({ id: b.dataset.m, name: b.dataset.m }, m => toast(m)); } catch (e) { toast(fmtErr(e.detail || e), true); } this.refresh(); });
    } catch (e) { set('state', `<div class="issue error"><span class="ico">✕</span><div><b>Can't reach the Prep</b>${esc(fmtErr(e.detail || e))}<br>Use the machine menu (top right) to retry or switch to simulation.</div></div>`); set('blk', ''); }
  },
};

// ───────────── boot ─────────────
(async function boot() {
  try { S.cfg = await local('GET', '/config'); } catch (e) { toast(fmtErr(e.detail || e), true); }
  mount(); pollStatus(); setInterval(pollStatus, 6000);
})();

// Easter egg: click the app name five times quickly.
(() => {
  const el = document.querySelector('.brand span'); let n = 0, t = 0;
  if (el) el.addEventListener('click', () => { const now = Date.now(); n = now - t < 1500 ? n + 1 : 1; t = now; if (n === 5) { n = 0; toast('Made with care by E-Fourie 🧪'); } });
})();
