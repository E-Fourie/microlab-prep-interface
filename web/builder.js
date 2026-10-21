/* Protocol builder: deck editor, step editor, validator + simulator with an animated deck map. */
'use strict';

const BUILDER = {
  proto: null, file: null, hw: {}, builtin: {}, liquids: [], selSlot: '1', res: null, idx: -1, timer: null, speed: 700,
};
const newProto = () => ({ name: 'Untitled protocol', description: '', instrumentProtocolId: '', variables: [], deck: {}, contents: [], steps: [] });
const varFields = PrepCalc.prepProtocol.VAR_FIELDS, refName = PrepCalc.prepProtocol.refName;
const STEP_DEFAULTS = {
  transfer: { type: 'transfer', fromPos: '', fromWells: 'A1', toPos: '', toWells: 'A1', vol: 10 },
  distribute: { type: 'distribute', fromPos: '', fromWells: 'A1', toPos: '', toWells: 'A1:H1', vol: 10 },
  mix: { type: 'mix', pos: '', wells: 'A1', vol: 20, cycles: 3 },
  shake: { type: 'shake', pos: '1', temp: 37, rpm: 800, minutes: 5 },
  move: { type: 'move', fromPos: '', toPos: '' },
  pause: { type: 'pause', message: 'Operator action needed' },
};
/** Hardware the builder knows: the user's catalogue plus what the open protocol carries (the protocol's own wins). */
const OPT_FIELDS = ['aspFlow', 'dspFlow', 'aspHeight', 'dspHeight', 'tipDiscard'];
const hwDefs = () => ({ ...BUILDER.hw, ...(BUILDER.proto?.labware || {}) });
/** The protocol as saved, exported and simulated: only the custom hardware it uses is embedded in `labware`. */
function embedLw(p) {
  const defs = hwDefs(), used = {};
  Object.values(p.deck).forEach(d => { if (d && defs[d.lw]) used[d.lw] = defs[d.lw]; });
  const { labware, ...rest } = p;
  return Object.keys(used).length ? { ...rest, labware: used } : rest;
}
const posLabel = (p, k) => { const d = p.deck[k]; return `${k} · ` + (d ? (k === HHS_POS ? 'HHS · ' : '') + (d.label || LIB[d.lw].label) : k === HHS_POS ? 'HHS (empty)' : 'empty'); };

function builderFills(snapshot) {
  const out = {};
  for (const [k, c] of Object.entries(snapshot?.vol || {})) {
    const [id, well] = k.split(':'), lw = LIB[BUILDER.proto.deck[id]?.lw]; if (!lw || c.vol <= 0) continue;
    (out[id] ||= {})[well] = { color: liqColor(c.liquid), frac: Math.min(1, c.vol / lw.cap) };
  }
  return out;
}
function builderSlots() {
  const p = BUILDER.proto, ev = BUILDER.res.events[BUILDER.idx], snapshot = ev ? ev.snap : BUILDER.res.initial, fills = builderFills(snapshot);
  return fixedSlots(8).map(sl => {
    const id = snapshot.occ[sl.pos], d = id != null ? p.deck[id] : null, fixture = p.deck[sl.pos], magnet = LIB[fixture?.lw]?.kind === 'magnet';
    if (!d) return magnet ? { ...sl, lw: fixture.lw, label: fixture.label } : sl;
    const t = snapshot.tips.find(x => x.pos === sl.pos), used = new Set();
    if (t) for (let k = 0; k < t.used; k++) used.add(wellName(k % 8, Math.floor(k / 8)));
    return { ...sl, lw: d.lw, label: d.label, magnet, fills: fills[id], hl: ev?.hl[sl.pos], hl2: ev?.hl2[sl.pos], used: t ? used : undefined };
  });
}

function exportPy(p) {
  const spec = JSON.stringify(p, null, 1).replace(/'/g, '\\u0027');
  return `#!/usr/bin/env python3
"""${p.name.replace(/"/g, "'")} - exported from the Microlab Prep Control builder.

Prints the deck plan and steps. If INSTRUMENT_PROTOCOL_ID is set, runs that protocol on the
Prep via prep_sdk (simulated unless the launcher sets PREP_SIMULATE=0).
"""
import json
from prep_sdk import Prep, PrepError, simulate_default

INSTRUMENT_PROTOCOL_ID = ${JSON.stringify(p.instrumentProtocolId || '')}
SPEC = json.loads(r'''${spec}''')

print(f"=== {SPEC['name']} ===")
for pos, d in sorted(SPEC["deck"].items()):
    print(f"Position {pos}: {d['lw']} {d.get('label', '')}")
for c in SPEC["contents"]:
    print(f"  fill {c['pos']}:{c['wells']} with {c['vol']} uL {c['liquid']}")
for i, s in enumerate(SPEC["steps"], 1):
    print(f"Step {i}: {s}")

if INSTRUMENT_PROTOCOL_ID:
    try:
        Prep().run_protocol(INSTRUMENT_PROTOCOL_ID, simulate=simulate_default())
    except PrepError as e:
        raise SystemExit(str(e))
else:
    print("No INSTRUMENT_PROTOCOL_ID set: plan printed only.")
`;
}
const downloadText = (name, text, type = 'text/plain') => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); };
const fileSafe = n => (n || 'protocol').replace(/[^\w.\- ()]/g, '_').slice(0, 80);

VIEWS.builder = {
  leave() { this.stop(); BUILDER.optOpen = null; BUILDER.proto = null; BUILDER.file = null; BUILDER.selSlot = '1'; }, // each visit starts a new protocol
  async mount(el) {
    BUILDER.el = el; await loadLibrary();
    try { const cat = await local('GET', '/labware'); BUILDER.hw = cat.labware || {}; BUILDER.builtin = cat.builtin || {}; BUILDER.liquids = cat.liquids || []; } catch { BUILDER.hw = {}; BUILDER.builtin = {}; BUILDER.liquids = []; }
    if (window.BUILDER_OPEN) { BUILDER.proto = window.BUILDER_OPEN; BUILDER.file = window.BUILDER_FILE || null; window.BUILDER_OPEN = window.BUILDER_FILE = null; }
    if (!BUILDER.proto) BUILDER.proto = newProto();
    BUILDER.snap = JSON.stringify(BUILDER.proto); BUILDER.idx = -1; this.render();
  },
  dirty() { return !!BUILDER.proto && JSON.stringify(BUILDER.proto) !== BUILDER.snap; },
  // A field's change event fires when it loses focus, which is on the mouse-down of whatever the person clicked next.
  // Redrawing then would replace that button before the click lands, so wait until the pointer is released.
  changed() { if (BUILDER.ptr) { BUILDER.later = true; return; } this.stop(); BUILDER.idx = -1; this.render(); },
  render() {
    const B = BUILDER, p = B.proto, el = B.el; if (!el) return;
    const scrollY = window.scrollY; // every simulation step redraws the page; keep the reader where they are
    useLib(hwDefs());
    B.res = PrepCalc.prepProtocol.simulate(embedLw(p)); // the builder's own working copy (may be half filled in), not check().spec
    const r = B.res, sel = p.deck[B.selSlot];
    const lwOpt = ([k, v]) => `<option value="${k}" ${sel?.lw === k ? 'selected' : ''}>${esc(v.label)}</option>`, fits = ([k]) => !placeError(k, B.selSlot, LIB);
    const builtin = Object.entries(B.builtin).filter(([k]) => fits([k])).map(([k, name]) => lwOpt([k, { label: name }])).join(''), catalogue = Object.entries(hwDefs()).filter(fits), added = catalogue.filter(([, v]) => v.kind !== 'magnet').map(lwOpt).join(''), magnets = catalogue.filter(([, v]) => v.kind === 'magnet').map(lwOpt).join('');
    const lwOpts = added || magnets ? `<optgroup label="Magnet / reagent reservoir">${builtin}</optgroup>${magnets ? `<optgroup label="Magnets (Prep catalogue)">${magnets}</optgroup>` : ''}<optgroup label="Prep catalogue">${added}</optgroup>` : builtin;
    const posOpts = cur => `<option value="">position…</option>` + Array.from({ length: 8 }, (_, n) => String(n + 1)).map(k => `<option value="${k}" ${cur === k ? 'selected' : ''}>${esc(posLabel(p, k))}</option>`).join('');
    // a shake acts on a plate by its starting position (the Prep carries it to the heater-shaker and back); 1 is the heater-shaker itself
    const shakeOpts = cur => Array.from({ length: 8 }, (_, n) => String(n + 1)).filter(k => k === HHS_POS || k === cur || (p.deck[k] && LIB[p.deck[k].lw].kind === 'plate')).map(k => `<option value="${k}" ${cur === k ? 'selected' : ''}>${esc(posLabel(p, k))}</option>`).join('');
    const field = (label, inner) => `<div class="field"><label class="f">${label}</label>${inner}</div>`;
    const contents = p.contents.map((c, i) => {
      if (c.pos !== B.selSlot) return '';
      const wells = LIB[sel.lw].kind === 'reservoirs'
        ? field('Slot', `<select data-c="${i}" data-f="wells">${Array.from({ length: 5 }, (_, n) => `<option value="${wellName(n, 0)}" ${c.wells === wellName(n, 0) ? 'selected' : ''}>Slot ${n + 1}</option>`).join('')}</select>`)
        : field('Wells', `<input data-c="${i}" data-f="wells" value="${esc(c.wells)}">`);
      return `<div class="fields">${wells}${field('Liquid', `<select data-c="${i}" data-f="liquid">${[...new Set([...(B.liquids || []), c.liquid])].map(l => `<option ${c.liquid === l ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>`)}${field('Volume per well (µL)', `<input type="number" data-c="${i}" data-f="vol" value="${esc(c.vol)}">`)}<div class="field" style="justify-content:flex-end"><button class="btn-ghost" data-delc="${i}">Remove</button></div></div>`;
    }).join('');
    const ev = r.events[B.idx];
    // Optional per-step pipetting settings; blank means the Prep's own default.
    const pipetting = (s, i) => {
      const num = (label, key, ph, extra = '') => field(label, `<input type="number" data-o="${i}" data-f="${key}" value="${esc(s[key] ?? '')}" placeholder="${ph}" step="any" ${extra}>`);
      const set = OPT_FIELDS.some(k => s[k] !== undefined), open = set || B.optOpen?.has(i);
      return `<details class="more step-opts" data-optd="${i}" ${open ? 'open' : ''} style="grid-column:1/-1"><summary>Pipetting options${set ? ' (customised)' : ''}</summary>
        <div class="fields">${num('Aspirate speed (µL/s)', 'aspFlow', 'default', 'min="1" max="1000"')}${num('Dispense speed (µL/s)', 'dspFlow', 'default', 'min="1" max="1000"')}
        ${num('Aspirate height above liquid surface (mm)', 'aspHeight', 'default', 'min="-50" max="50"')}${num('Dispense height above well bottom (mm)', 'dspHeight', 'default', 'min="0" max="50"')}
        ${field('Used tips', `<select data-o="${i}" data-f="tipDiscard"><option value="" ${!s.tipDiscard ? 'selected' : ''}>Default (waste)</option><option value="waste" ${s.tipDiscard === 'waste' ? 'selected' : ''}>Discard to waste</option><option value="rack" ${s.tipDiscard === 'rack' ? 'selected' : ''}>Return to the tip rack</option></select>`)}</div>
        <div class="small muted">Leave a box empty to use the Prep's default. A negative aspirate height goes below the surface (needs liquid-level sensing). Tips returned to the rack are not picked up again.</div></details>`;
    };
    const stepCard = (s, i) => {
      const f = (label, key, extra = '') => {
        const vars = p.variables || [], num = extra.includes('type="number"'), ref = num ? refName(s[key]) : null;
        const v = ref && vars.find(x => x.name === ref);
        // a variable may stand in for this number: pick it from the list next to the box
        const pick = num && varFields[key] && vars.length ? `<select data-vp="${i}" data-f="${key}" aria-label="${esc(label)}: fixed number or a variable"><option value="">Fixed number</option>${vars.map(x => `<option value="${esc(x.name)}" ${x.name === ref ? 'selected' : ''}>Variable: ${esc(x.label || x.name)}</option>`).join('')}${ref && !v ? `<option value="${esc(ref)}" selected>Variable: ${esc(ref)} (not declared)</option>` : ''}</select>` : '';
        return field(label, `<input data-s="${i}" data-f="${key}" value="${esc(ref ? (v ? v.default : '') : s[key])}" ${extra} ${ref ? 'disabled' : ''}>${pick}`);
      };
      const ps = (label, key) => field(label, `<select data-s="${i}" data-f="${key}">${posOpts(s[key])}</select>`);
      const body = s.type === 'move' ? ps('Move plate from', 'fromPos') + ps('To position', 'toPos') : s.type === 'pause' ? f('Message', 'message') : s.type === 'shake' ? field('Plate to shake (position)', `<select data-s="${i}" data-f="pos">${shakeOpts(s.pos || HHS_POS)}</select>`) + f('Temp °C (0 = off)', 'temp', 'type="number"') + f('RPM', 'rpm', 'type="number"') + f('Minutes', 'minutes', 'type="number"') : s.type === 'mix' ? ps('Position', 'pos') + f('Wells', 'wells') + f('Volume', 'vol', 'type="number"') + f('Cycles', 'cycles', 'type="number"')
        : ps('From', 'fromPos') + f('Wells', 'fromWells') + ps('To', 'toPos') + f('Wells', 'toWells') + f('Volume', 'vol', 'type="number"') + pipetting(s, i);
      const issues = [...r.errors.filter(e => e.step === i).map(e => `<div class="step-issue" style="color:var(--danger)">✕ ${esc(e.msg)}</div>`), ...r.warnings.filter(e => e.step === i).map(e => `<div class="step-issue" style="color:var(--warn)">! ${esc(e.msg)}</div>`)].join('');
      return `<div class="step ${B.idx === i ? 'now' : ''}"><div class="step-head"><span class="kbd">${i + 1}</span><b style="text-transform:capitalize">${s.type}</b><span class="spacer"></span><button class="btn-ghost sm" data-mv="${i}:-1" aria-label="Move up">↑</button><button class="btn-ghost sm" data-mv="${i}:1" aria-label="Move down">↓</button><button class="btn-ghost sm" data-dels="${i}" aria-label="Delete step">✕</button></div><div class="fields">${body}</div>${issues}</div>`;
    };
    const loadChecks = `${r.load.tips.map(t => `<div class="item"><span class="chip info">tips</span><span><b>Position ${t.pos}</b>: at least ${t.n} tips (${LIB[p.deck[t.pos].lw].label})</span></div>`).join('')}${r.load.liquids.map(l => `<div class="item"><span class="chip ${l.have >= l.need ? 'ok' : 'warn'}">liquid</span><span><b>Position ${l.pos}, ${l.slot}</b>: ${esc(l.liquid)}, at least ${l.need} µL (incl. 10% + 5 µL dead volume), currently ${l.have} µL</span></div>`).join('')}`;
    const hint = B.selSlot === HHS_POS ? 'Heater-Shaker (HHS): plates only.' : RESERVOIR_POS.includes(B.selSlot) ? 'This position also fits the 60 mL reagent reservoir rack and the 50 mL tube carrier.' : '';
    const varRows = (p.variables || []).map((v, i) => `<div class="fields">${field('Name (used in steps)', `<input data-v="${i}" data-f="name" value="${esc(v.name)}" maxlength="24">`)}${field('Question to ask', `<input data-v="${i}" data-f="label" value="${esc(v.label)}" maxlength="60">`)}${field('Default', `<input type="number" step="any" data-v="${i}" data-f="default" value="${esc(v.default)}">`)}${field('Smallest', `<input type="number" step="any" data-v="${i}" data-f="min" value="${esc(v.min)}">`)}${field('Largest', `<input type="number" step="any" data-v="${i}" data-f="max" value="${esc(v.max)}">`)}${field('Unit', `<input data-v="${i}" data-f="unit" value="${esc(v.unit)}" maxlength="12">`)}<div class="field" style="justify-content:flex-end"><button class="btn-ghost" data-delv="${i}">Remove</button></div></div>`).join('');
    const varsPanel = `<div class="panel"><div class="card-head"><h2>Variables (optional)</h2></div><div class="small muted">A variable is a number the person running the protocol is asked for each time, such as how much bead reagent to add. Declare it here, then pick it in a step's number box instead of typing a number. The simulation uses the default.</div>${varRows || '<div class="small muted">None. Every number in the steps is fixed.</div>'}<div><button class="btn-ghost" id="addv">+ Add variable</button></div></div>`;
    el.innerHTML = `<div class="stack">
      <div class="page-head"><div><h1>Build &amp; <em>simulate</em></h1><p class="subtitle">Lay out the deck, add steps, and the builder tells you how to load the machine and flags problems before anything runs. Press ▶ to watch it play out on the deck map.</p></div></div>
      <div class="panel"><div class="grid cols-2">${field('Name', `<input id="pname" value="${esc(p.name)}">`)}${field('Description', `<input id="pdesc" value="${esc(p.description)}">`)}</div>
        <div class="row"><button class="btn" id="save">Save</button><div style="min-width:210px"><select id="exSel" aria-label="Load an example"><option value="">Load example…</option><option value="example_plasmid_normalisation.json">Plasmid normalisation</option><option value="example_gripper_hhs_magnet.json">Gripper: HHS and magnet</option><option value="example_bead_cleanup_variable.json">Bead clean-up with variables</option></select></div><button class="btn-ghost" id="expPy">Export .py</button><button class="btn-ghost" id="expJson">Export .json</button><span class="spacer"></span></div></div>
      <div class="grid cols-2">
        <div class="panel"><div class="card-head"><h2>1 · Deck</h2><span class="small muted">Hover for what to load · click to edit</span></div>
          <div class="deck-wrap" id="deck">${deckSVG(builderSlots(), { sel: B.selSlot })}</div>
          <div class="legend"><span style="--c:#3b6ef5">tips to use</span><span style="--c:var(--debit)">source (aspirating)</span><span style="--c:var(--chart-neutral)">destination</span></div>
          <div class="stack-sm"><h3>Position ${esc(B.selSlot)}${B.selSlot === HHS_POS ? ' · Heater-Shaker (HHS)' : ''}</h3>${hint ? `<div class="small muted">${hint}</div>` : ''}
          ${field('Labware', `<select id="lw"><option value="">- empty -</option>${lwOpts}</select>`)}
          ${sel ? `${field('Label', `<input id="lwlabel" value="${esc(sel.label || '')}">`)}${!['tips', 'magnet'].includes(LIB[sel.lw].kind) ? `<h3>Starting contents</h3>${LIB[sel.lw].kind === 'reservoirs' ? '<div class="small muted">Each slot holds one liquid. Which liquid sits in which slot matters, so load them exactly as shown.</div>' : ''}${contents || '<div class="small muted">Empty.</div>'}<div><button class="btn-ghost" id="addc">+ Add liquid</button></div>` : ''}` : ''}</div></div>
        ${varsPanel}
        <div class="panel"><div class="card-head"><h2>2 · Steps</h2><div class="row">${Object.keys(STEP_DEFAULTS).map(t => `<button class="btn-ghost sm" data-add="${t}" style="text-transform:capitalize">+ ${t}</button>`).join('')}</div></div>
          <div class="steps">${p.steps.map(stepCard).join('') || '<div class="empty">No steps yet. Add one above.</div>'}</div></div></div>
      <div class="panel"><div class="card-head"><h2>3 · Check, load guide &amp; simulate</h2>
        <div class="row"><button class="btn" id="play" ${p.steps.length ? '' : 'disabled'}>${B.timer ? '⏸ Pause' : '▶ Simulate'}</button><button class="btn-ghost" id="stepb" ${p.steps.length ? '' : 'disabled'}>Step ›</button><button class="btn-ghost" id="rst">Reset</button></div></div>
        <div class="issues">${ev ? `<div class="issue info"><span class="ico">${B.idx + 1}</span><div><b>${esc(ev.text)}</b></div></div>` : ''}
        ${r.errors.map(e => `<div class="issue error"><span class="ico">✕</span><div><b>${e.step != null ? 'Step ' + (e.step + 1) : 'Setup'}</b>${esc(e.msg)}</div></div>`).join('')}
        ${r.warnings.map(e => `<div class="issue warn"><span class="ico">!</span><div><b>${e.step != null ? 'Step ' + (e.step + 1) : 'Setup'}</b>${esc(e.msg)}</div></div>`).join('')}
        ${!r.errors.length && p.steps.length ? '<div class="issue ok"><span class="ico">✓</span><div><b>No errors.</b>The plan fits the deck, tips and volumes.</div></div>' : ''}</div>
        <h3>How to load the machine</h3><div class="list">${loadChecks || '<div class="muted small">Add steps to see the load guide.</div>'}</div></div></div>`;
    attachTips($('#deck'), pos => {
      const d = p.deck[pos]; if (d && LIB[d.lw]?.kind === 'magnet') return tipHTML(pos, 'magnet', d.label, [], ['Movable magnet: the gripper moves a plate onto it. Nothing is loaded here at the start.']); if (!d) return tipHTML(pos, null, '', [], pos === HHS_POS ? ['Heater-Shaker: plates only.'] : RESERVOIR_POS.includes(pos) ? ['Fits a 60 mL reagent reservoir rack.'] : []);
      const L = LIB[d.lw], need = r.load.liquids.filter(l => l.pos === pos), lines = [], t = r.load.tips.find(x => x.pos === pos);
      if (t) lines.push({ text: `At least ${t.n} tips` });
      const content = well => p.contents.find(c => { try { return c.pos === pos && expandWells(c.wells, L).includes(well); } catch { return false; } });
      if (L.kind === 'reservoirs') for (let i = 0; i < L.rows; i++) {
        const w = wellName(i, 0), n = need.find(x => x.well === w), c = content(w);
        lines.push(c ? { text: `Slot ${i + 1}: ${c.liquid}${n ? `, at least ${n.need} µL` : `, ${c.vol} µL`}`, color: liqColor(c.liquid) } : { text: `Slot ${i + 1}: leave empty`, color: '#d4d1c4' });
      } else need.forEach(n => lines.push({ text: `${n.slot}: ${n.liquid}, at least ${n.need} µL`, color: liqColor(n.liquid) }));
      return tipHTML(pos, d.lw, d.label, lines);
    });
    this.wire();
    window.scrollTo(0, scrollY);
  },
  wire() {
    const B = BUILDER, p = B.proto, el = B.el, ch = () => this.changed();
    $('#pname').onchange = e => { p.name = e.target.value; };
    $('#pdesc').onchange = e => { p.description = e.target.value; };
    $$('.slot', el).forEach(g => g.onclick = () => { B.selSlot = g.dataset.pos; this.render(); });
    $('#lw').onchange = e => { if (e.target.value) p.deck[B.selSlot] = { lw: e.target.value, label: p.deck[B.selSlot]?.label || '' }; else { delete p.deck[B.selSlot]; p.contents = p.contents.filter(c => c.pos !== B.selSlot); } ch(); };
    if ($('#lwlabel')) $('#lwlabel').onchange = e => { p.deck[B.selSlot].label = e.target.value; ch(); };
    if ($('#addc')) $('#addc').onclick = () => { p.contents.push({ pos: B.selSlot, wells: 'A1', liquid: B.liquids[0] || 'Aqueous', vol: 100 }); ch(); };
    $$('[data-c]', el).forEach(i => i.onchange = () => { p.contents[+i.dataset.c][i.dataset.f] = i.type === 'number' ? +i.value : i.value; ch(); });
    $$('[data-delc]', el).forEach(b => b.onclick = () => { p.contents.splice(+b.dataset.delc, 1); ch(); });
    // variables: declared in the panel, chosen in a step's number box
    $$('[data-vp]', el).forEach(i => i.onchange = () => { const s = p.steps[+i.dataset.vp], v = (p.variables || []).find(x => x.name === (i.value || refName(s[i.dataset.f]))); s[i.dataset.f] = i.value ? '$' + i.value : (v ? v.default : 0); ch(); });
    $$('[data-v]', el).forEach(i => i.onchange = () => {
      const v = p.variables[+i.dataset.v], old = v.name; v[i.dataset.f] = i.type === 'number' ? (i.value === '' ? '' : +i.value) : i.value;
      if (i.dataset.f === 'name' && old !== v.name) p.steps.forEach(st => Object.keys(varFields).forEach(k => { if (refName(st[k]) === old) st[k] = '$' + v.name; })); // steps follow a rename
      ch();
    });
    $$('[data-delv]', el).forEach(b => b.onclick = () => { const gone = p.variables.splice(+b.dataset.delv, 1)[0]; p.steps.forEach(st => Object.keys(varFields).forEach(k => { if (gone && refName(st[k]) === gone.name) st[k] = gone.default; })); ch(); });
    if ($('#addv')) $('#addv').onclick = () => { p.variables = p.variables || []; let n = p.variables.length + 1; while (p.variables.some(v => v.name === 'var' + n)) n++; p.variables.push({ name: 'var' + n, label: 'Volume (µL)', default: 20, min: 0, max: 1000, unit: 'µL' }); ch(); };
    $$('[data-s]', el).forEach(i => i.onchange = () => { p.steps[+i.dataset.s][i.dataset.f] = i.type === 'number' ? +i.value : i.value; ch(); });
    $$('[data-o]', el).forEach(i => i.onchange = () => { const s = p.steps[+i.dataset.o]; if (i.value === '') delete s[i.dataset.f]; else s[i.dataset.f] = i.type === 'number' ? +i.value : i.value; ch(); });
    $$('[data-optd]', el).forEach(d => d.ontoggle = () => { (B.optOpen ||= new Set())[d.open ? 'add' : 'delete'](+d.dataset.optd); });
    $$('[data-add]', el).forEach(b => b.onclick = () => { const s = { ...STEP_DEFAULTS[b.dataset.add] }, ks = Object.keys(p.deck).sort(), tipless = ks.filter(k => !['tips', 'magnet'].includes(LIB[p.deck[k].lw].kind)); if ('fromPos' in s) { s.fromPos = tipless[0] || ''; s.toPos = tipless[1] || tipless[0] || ''; } if ('pos' in s) s.pos = tipless[0] || '';
      if (s.type === 'move') { s.fromPos = ks.find(k => LIB[p.deck[k].lw].kind === 'plate' && k !== HHS_POS) || ks.find(k => LIB[p.deck[k].lw].kind === 'plate') || ''; s.toPos = ks.find(k => LIB[p.deck[k].lw].kind === 'magnet') || ''; } p.steps.push(s); ch(); });
    $$('[data-dels]', el).forEach(b => b.onclick = () => { p.steps.splice(+b.dataset.dels, 1); ch(); });
    $$('[data-mv]', el).forEach(b => b.onclick = () => { const [i, d] = b.dataset.mv.split(':').map(Number), j = i + d; if (j >= 0 && j < p.steps.length) { [p.steps[i], p.steps[j]] = [p.steps[j], p.steps[i]]; ch(); } });

    $('#exSel').onchange = async e => {
      const file = e.target.value; e.target.value = ''; if (!file) return;
      if (this.dirty() && !await confirmModal({ title: 'Load the example?', ok: 'Replace my protocol', danger: true, body: '<p>This replaces the protocol you are building. Unsaved changes will be lost.</p>' })) return this.render();
      try { B.proto = await http('/' + file); B.file = null; B.snap = JSON.stringify(B.proto); B.selSlot = '1'; this.changed(); } catch (err) { toast(fmtErr(err.detail || err), true); this.render(); }
    };
    $('#play').onclick = () => B.timer ? (this.stop(), this.render()) : this.play();
    $('#stepb').onclick = () => { this.stop(); this.advance(); };
    $('#rst').onclick = () => { this.stop(); B.idx = -1; this.render(); };
    $('#save').onclick = async () => {
      const name = B.file || fileSafe(p.name) + '.json';
      try { B.file = await putLocal(name, JSON.stringify(embedLw(p), null, 2)); B.snap = JSON.stringify(p); toast(`Saved “${p.name}”`); await loadLibrary(true); S.libKey = ''; } catch (e) { toast(fmtErr(e), true); }
    };
    $('#expJson').onclick = () => downloadText(fileSafe(p.name) + '.json', JSON.stringify(embedLw(p), null, 2), 'application/json');
    $('#expPy').onclick = () => {
      const name = fileSafe(p.name).replace(/\.json$/, '') + '.py', text = exportPy(embedLw(p));
      downloadText(name, text); putLocal(name, text).then(() => { toast(`Exported ${name} (also saved with your protocols)`); loadLibrary(true); S.libKey = ''; });
    };
  },
  advance() {
    const B = BUILDER, n = B.res.events.length; if (!n) return false;
    if (B.idx >= n - 1) return false; B.idx++; this.render(); return B.idx < n - 1;
  },
  play() {
    const B = BUILDER; if (B.idx >= B.res.events.length - 1) B.idx = -1;
    const tick = () => { if (!this.advance()) { this.stop(); this.render(); } else B.timer = setTimeout(tick, B.speed); };
    B.timer = setTimeout(tick, 0); this.render();
  },
  stop() { clearTimeout(BUILDER.timer); BUILDER.timer = null; },
};
// stop playback when leaving the builder
addEventListener('beforeunload', e => { if (current === 'builder' && VIEWS.builder.dirty()) { e.preventDefault(); e.returnValue = ''; } });
addEventListener('hashchange', () => { if (location.hash !== '#builder') VIEWS.builder.stop(); });
// keep track of the pointer so changed() can wait for a click to finish
addEventListener('pointerdown', () => { BUILDER.ptr = true; }, true);
for (const ev of ['pointerup', 'pointercancel']) addEventListener(ev, () => setTimeout(() => { BUILDER.ptr = false; if (BUILDER.later) { BUILDER.later = false; if (current === 'builder') VIEWS.builder.changed(); } }, 0), true);
