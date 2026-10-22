// ═══════════════════════════════════════════════════════
// Hamilton Microlab Prep protocols, as the Builder describes them. The Builder page and
// the converter's checker (spec_check.py) agree on what a protocol is and what is allowed.
//
// A protocol ("spec") is plain data:
//   { name, description, deck: { "1": { lw, label } ... },
//     contents: [{ pos, wells, liquid, vol }],
//     steps: [ transfer | distribute (shown as "Add reagent") | mix | discard | shake | pause (optional minutes: a timed pause) | move ] }
//     (every pipetting step may carry channels: 8 to use the 8-channel head (tips are picked up a rack column at a time) and tips: 'each' (fresh
//     tips for every column) or 'step' (one set for the whole step); tips are never reused, and when the racks run out the machine
//     pauses for one to be refilled;
//     without channels one tip is used per well)
//     (discard takes vol uL out of each of the wells at pos to waste, one tip per well: { type, pos, wells, vol })
//     (every step may also carry label, a name shown in the Builder and the playback)
//     (transfer/distribute may also carry aspFlow, dspFlow in uL/s, aspHeight, dspHeight in mm)
//     (every pipetting step may carry tipDiscard 'waste' | 'rack': 'rack' puts each used tip back in the place it came from. It is still a used
//     tip and is never picked up again, and the machine does not count it as a spare: when the fresh tips run out it asks for a refill
//     even though used tips are sitting in the rack, so those are taken out and fresh ones put in)
// A shake step names the plate to shake by `pos` (default 1, the heater-shaker itself). A plate anywhere else is carried to
// the heater-shaker by the gripper and stays there until a move step takes it away.
//
// Nothing here runs anything. check() cleans a spec that came from outside (it keeps only the fields
// below, with the right types and limits) and says what is wrong; simulate() plays it through to
// find problems (empty wells, out of tips, overfilled wells) and to say how to load the machine;
// validate() does both. A spec that passes validate() with no errors is safe to hand to the laptop.
//
// Deck: 8 positions in 2 columns of 4, numbered down each column (1-4, then 5-8).
// Position 1 is the Heater-Shaker (HHS): it always starts empty, and takes plates only. Every plate has a home at another position,
// and the gripper only moves a plate to or from the HHS or a magnet (never straight from one ordinary position to another). The 60 mL reagent reservoir rack (5 slots)
// only fits the bottom positions, 4 and 8. A movable magnet is a position a plate can be moved onto
// by the gripper. Capacities and layout are schematic approximations.
//
// Variables: a protocol may declare `variables: [{ name, label, default, min, max, unit }]` and use one in a step's vol, cycles,
// temp, rpm or minutes as the text "$name" (e.g. vol: "$beads"). The Builder shows the default; whoever runs the protocol is asked
// for each value first. resolve(spec, values) turns the protocol into a plain one (no variables), which is what gets compiled.
//
// Labware and liquids: only the Prep's own. The labware is the built-in keys below plus the Prep's catalogue (prep-catalogue.js, keys
// c_<Prep id>, e.g. c_208), and every liquid in `contents` must be one of the Prep's liquid classes. A protocol cannot bring its own
// hardware. check() fills `labware` with the catalogue definitions the deck uses, so a protocol stays readable on its own.
//
// spec_check.py is the Python port of check(), resolve() and the placement rules: change both together and add a vector (tests/vectors).
// ═══════════════════════════════════════════════════════
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./prep-catalogue.js'));
  else (root.PrepCalc = root.PrepCalc || {}).prepProtocol = factory(root.PrepCalc.prepCatalogue);
})(typeof self !== 'undefined' ? self : this, function (Catalogue) {
  const LABWARE = {
    tips_50: { label: '50 µL tip rack', kind: 'tips', rows: 8, cols: 12, tipVol: 50 },
    tips_300: { label: '300 µL tip rack', kind: 'tips', rows: 8, cols: 12, tipVol: 300 },
    tips_1000: { label: '1000 µL tip rack', kind: 'tips', rows: 8, cols: 12, tipVol: 1000 },
    plate_pcr: { label: '96-well PCR plate', kind: 'plate', rows: 8, cols: 12, cap: 200 },
    plate_deep: { label: '96 deep-well plate', kind: 'plate', rows: 8, cols: 12, cap: 2000 },
    plate_flat_96: { label: 'Corning 96-well flat-bottom plate (3596)', kind: 'plate', rows: 8, cols: 12, cap: 360 },
    plate_black_96: { label: 'Corning 96-well black, clear flat-bottom plate (3603)', kind: 'plate', rows: 8, cols: 12, cap: 360 },
    plate_384: { label: 'Corning 384-well flat-bottom plate', kind: 'plate', rows: 16, cols: 24, cap: 112 },
    reservoir_60: { label: '60 mL reagent reservoir rack (5 slots)', kind: 'reservoirs', rows: 5, cols: 1, cap: 60000, only: ['4', '8'] },
    tubes_24: { label: '1.5 mL tube rack (24)', kind: 'tubes', rows: 4, cols: 6, cap: 1500 },
    magnet: { label: 'Movable magnet', kind: 'magnet', rows: 1, cols: 1 },
  };
  const STEP_TYPES = ['transfer', 'distribute', 'mix', 'discard', 'shake', 'pause', 'move'];
  const HHS_POS = '1';
  const RESERVOIR_POS = LABWARE.reservoir_60.only;
  const POSITIONS = ['1', '2', '3', '4', '5', '6', '7', '8'];
  const ROWS = 'ABCDEFGHIJKLMNOP';
  const CATALOGUE_KEY = /^c_\d{1,9}$/; // c_<Prep id>: plates, tip racks and 24-tube racks from the Prep's own catalogue
  const CATALOGUE = Catalogue.LABWARE;
  // rpm is the Hamilton Heater Shaker's maximum (2000), so the Builder never accepts a speed the machine cannot do.
  const LIMITS = { labware: 30, name: 120, description: 500, label: 60, liquid: 60, wells: 80, message: 200, contents: 400, steps: 600, vol: 10000, flow: 1000, height: 50, cycles: 50, rpm: 2000, minutes: 600, temp: 105, bytes: 200000, variables: 20, unit: 12 };

  const wellName = (r, c) => ROWS[r] + (c + 1);
  const fitsHHS = k => LABWARE[k] && LABWARE[k].kind === 'plate';
  const slotName = (lwKey, well, lib = LABWARE) => (lib[lwKey] && lib[lwKey].kind === 'reservoirs' ? `slot ${ROWS.indexOf(well[0]) + 1}` : well);
  const keyOf = (pos, well) => `${pos}:${well}`;
  /** Every well of a piece of labware, whatever its size (a 384-well plate has 384). */
  const allWells = lw => expandWells(`A1:${wellName(lw.rows - 1, lw.cols - 1)}`, lw);

  /** Why labware `k` cannot be set down at deck position `pos` when the run starts ('' if it can). The heater-shaker starts empty. */
  function placeError(k, pos, lib = LABWARE) {
    const lw = lib[k]; if (!lw) return '';
    if (String(pos) === HHS_POS) return `Position 1 is the heater-shaker (HHS) and always starts empty. A Shake step moves a plate onto it, so give ${lw.label} a home at another position.`;
    return landError(k, pos, lib);
  }
  /** Why labware `k` cannot be put at `pos` during the run, by a move or a shake ('' if it can). */
  function landError(k, pos, lib = LABWARE) {
    const lw = lib[k]; if (!lw) return '';
    if (String(pos) === HHS_POS && lw.kind !== 'plate') return `Position 1 is the heater-shaker (HHS) and only takes plates, not ${lw.label}.`;
    if (lw.only && !lw.only.includes(String(pos))) return `The ${lw.label} only fits the bottom positions (${lw.only.join(' and ')}), not position ${pos}.`;
    return '';
  }
  function parseWell(w) { const m = /^([A-P])(\d{1,2})$/i.exec(String(w).trim()); return m && +m[2] >= 1 ? [ROWS.indexOf(m[1].toUpperCase()), +m[2] - 1] : null; }
  /** "A1", "A1:A6", "A1:H1", "A1,B2" → well names, column-major, checked against the labware. */
  function expandWells(spec, lw) {
    const out = [];
    for (const part of String(spec || '').split(',').map(s => s.trim()).filter(Boolean)) {
      const [a, b = a] = part.split(':'); const p = parseWell(a), q = parseWell(b);
      if (!p || !q) throw new Error(`Bad well "${part}"`);
      for (let c = Math.min(p[1], q[1]); c <= Math.max(p[1], q[1]); c++)
        for (let r = Math.min(p[0], q[0]); r <= Math.max(p[0], q[0]); r++) {
          if (lw && (r >= lw.rows || c >= lw.cols)) throw new Error(`Well ${wellName(r, c)} is outside a ${lw.rows}×${lw.cols} ${lw.label}`);
          out.push(wellName(r, c));
        }
    }
    return out;
  }

  /** Everything a deck may use: the built-in keys and the Prep's catalogue. (The argument is kept for callers; a protocol cannot add labware.) */
  const LIBRARY = Object.assign({}, LABWARE, CATALOGUE);
  const libFor = () => LIBRARY;
  const sameShape = (a, b) => !!a && !!b && a.kind === b.kind && a.rows === b.rows && a.cols === b.cols;

  // ───────────── variables ─────────────
  const VAR_NAME = /^[A-Za-z][A-Za-z0-9_]{0,23}$/;
  const VAR_REF = /^\$([A-Za-z][A-Za-z0-9_]{0,23})$/;
  // The step fields a variable may stand in for, and the range each allows.
  const VAR_FIELDS = { vol: [0, LIMITS.vol], cycles: [1, LIMITS.cycles], temp: [0, LIMITS.temp], rpm: [0, LIMITS.rpm], minutes: [0, LIMITS.minutes] };
  const refName = v => { const m = typeof v === 'string' ? VAR_REF.exec(v.trim()) : null; return m ? m[1] : null; };

  // ───────────── check: clean a spec from outside ─────────────
  const CONTROL = new RegExp('[\\u0000-\\u001f\\u007f\\u2028\\u2029]');
  function text(v, max, path, errors, { required = false } = {}) {
    if (v === undefined || v === null || v === '') { if (required) errors.push({ path, msg: 'is required' }); return ''; }
    if (typeof v !== 'string') { errors.push({ path, msg: 'must be text' }); return ''; }
    const s = v.trim();
    if (CONTROL.test(s)) { errors.push({ path, msg: 'must be plain text with no control characters' }); return ''; }
    if (s.length > max) { errors.push({ path, msg: `must be at most ${max} characters` }); return s.slice(0, max); }
    if (required && !s) errors.push({ path, msg: 'is required' });
    return s;
  }
  function num(v, path, errors, { min = -Infinity, max = Infinity, int = false, fallback = 0 } = {}) {
    const n = typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
    if (typeof n !== 'number' || !isFinite(n)) { errors.push({ path, msg: 'must be a number' }); return fallback; }
    if (int && !Number.isInteger(n)) { errors.push({ path, msg: 'must be a whole number' }); return fallback; }
    if (n < min || n > max) { errors.push({ path, msg: `must be between ${min} and ${max}` }); return fallback; }
    return n;
  }
  const isObj = v => v !== null && typeof v === 'object' && !Array.isArray(v);
  function pos(v, path, errors, { optional = false } = {}) {
    if ((v === undefined || v === null || v === '') && optional) return '';
    const s = typeof v === 'number' ? String(v) : v;
    if (typeof s !== 'string' || !POSITIONS.includes(s)) { errors.push({ path, msg: 'must be a deck position from 1 to 8' }); return ''; }
    return s;
  }
  /** One of the Prep's liquid classes, in the Prep's spelling (matched without regard to case). */
  function liquid(v, path, errors) {
    const t = text(v, LIMITS.liquid, path, errors, { required: true });
    if (!t) return '';
    const name = Catalogue.findLiquid(t);
    if (!name) { errors.push({ path, msg: `must be one of the Prep's liquid classes: ${Catalogue.LIQUIDS.join(', ')}` }); return t; }
    return name;
  }
  function wells(v, path, errors) {
    const s = text(v, LIMITS.wells, path, errors, { required: true });
    if (s && !/^[A-Pa-p](?:[1-9]|1\d|2[0-4])(:[A-Pa-p](?:[1-9]|1\d|2[0-4]))?(\s*,\s*[A-Pa-p](?:[1-9]|1\d|2[0-4])(:[A-Pa-p](?:[1-9]|1\d|2[0-4]))?)*$/.test(s)) errors.push({ path, msg: 'must be wells such as A1, A1:A6 or A1,B2 (columns 1 to 24)' });
    return s.toUpperCase();
  }

  /** Clean a protocol from outside. Returns { spec, errors: [{ path, msg }] }; spec only ever holds the known fields. */
  function check(input) {
    const errors = [];
    if (!isObj(input)) return { spec: null, errors: [{ path: '', msg: 'A protocol must be an object' }] };
    let size = 0; try { size = JSON.stringify(input).length; } catch (e) { return { spec: null, errors: [{ path: '', msg: 'Not valid JSON data' }] }; }
    if (size > LIMITS.bytes) return { spec: null, errors: [{ path: '', msg: `Too large (limit ${LIMITS.bytes / 1000} KB)` }] };
    // Variables: cleaned first. Steps that use one are checked with its default in place, then get the "$name" back.
    const variables = [], refs = [];
    if (input.variables !== undefined && !Array.isArray(input.variables)) errors.push({ path: 'variables', msg: 'must be a list' });
    else if (Array.isArray(input.variables)) {
      if (input.variables.length > LIMITS.variables) errors.push({ path: 'variables', msg: `has too many variables (limit ${LIMITS.variables})` });
      input.variables.slice(0, LIMITS.variables).forEach((v, i) => {
        const p = `variables[${i}]`;
        if (!isObj(v)) return errors.push({ path: p, msg: 'must be an object' });
        const name = typeof v.name === 'string' ? v.name.trim() : '';
        if (!VAR_NAME.test(name)) return errors.push({ path: `${p}.name`, msg: 'must be a name of letters, digits and underscores that starts with a letter (up to 24 characters)' });
        if (variables.some(x => x.name === name)) return errors.push({ path: `${p}.name`, msg: `"${name}" is used twice` });
        const min = v.min === undefined || v.min === null || v.min === '' ? 0 : num(v.min, `${p}.min`, errors, { min: -1e6, max: 1e6 });
        const max = v.max === undefined || v.max === null || v.max === '' ? LIMITS.vol : num(v.max, `${p}.max`, errors, { min: -1e6, max: 1e6 });
        const def = num(v.default, `${p}.default`, errors, { min: -1e6, max: 1e6 });
        if (min > max) errors.push({ path: p, msg: 'min must not be above max' });
        else if (def < min || def > max) errors.push({ path: `${p}.default`, msg: `must be between ${min} and ${max}` });
        variables.push({ name, label: text(v.label, LIMITS.label, `${p}.label`, errors) || name, default: def, min, max, unit: text(v.unit === undefined ? 'µL' : v.unit, LIMITS.unit, `${p}.unit`, errors) });
      });
    }
    if (Array.isArray(input.steps)) {
      const stepsIn = input.steps.map((s, i) => {
        if (!isObj(s)) return s;
        let copy = s;
        Object.keys(VAR_FIELDS).forEach(f => {
          const nm = refName(s[f]); if (nm === null) return;
          const v = variables.find(x => x.name === nm), p = `steps[${i}].${f}`;
          if (!v) return errors.push({ path: p, msg: `uses the variable "${nm}", which is not declared under variables` });
          const [lo, hi] = VAR_FIELDS[f];
          if (v.min < lo || v.max > hi) errors.push({ path: p, msg: `the variable "${nm}" allows ${v.min} to ${v.max}, but this field only takes ${lo} to ${hi}` });
          if (copy === s) copy = Object.assign({}, s);
          copy[f] = v.default; refs.push({ i, f, name: nm });
        });
        return copy;
      });
      input = Object.assign({}, input, { steps: stepsIn });
    }
    const spec = { specVersion: 1, name: text(input.name, LIMITS.name, 'name', errors, { required: true }), description: text(input.description, LIMITS.description, 'description', errors), deck: {}, contents: [], steps: [] };

    // A protocol cannot bring its own hardware: a `labware` field is only accepted when each entry IS a catalogue entry (the same key, kind and size).
    if (input.labware !== undefined && !isObj(input.labware)) errors.push({ path: 'labware', msg: 'must be an object' });
    else if (isObj(input.labware)) {
      const entries = Object.entries(input.labware);
      if (entries.length > LIMITS.labware) errors.push({ path: 'labware', msg: `has too many definitions (limit ${LIMITS.labware})` });
      entries.slice(0, LIMITS.labware).forEach(([key, d]) => {
        const p = `labware.${String(key).slice(0, 30)}`;
        if (!CATALOGUE_KEY.test(key) || !CATALOGUE[key]) return errors.push({ path: p, msg: "is not in the Prep's labware catalogue. Only the Prep's own labware can be used (keys like c_208, see GET /tools/prep-labware)" });
        if (!isObj(d) || !sameShape(d, CATALOGUE[key])) errors.push({ path: p, msg: `does not match the catalogue entry ${key} (${CATALOGUE[key].label}, ${CATALOGUE[key].rows} × ${CATALOGUE[key].cols} ${CATALOGUE[key].kind})` });
      });
    }
    const lib = libFor(spec);

    if (!isObj(input.deck)) errors.push({ path: 'deck', msg: 'must be an object' });
    else for (const [k, d] of Object.entries(input.deck)) {
      const p = `deck.${String(k).slice(0, 8)}`;
      if (!POSITIONS.includes(k)) { errors.push({ path: p, msg: 'is not a deck position (1 to 8)' }); continue; }
      if (!isObj(d) || typeof d.lw !== 'string' || !Object.prototype.hasOwnProperty.call(lib, d.lw)) { errors.push({ path: p, msg: `needs labware: a built-in key (${Object.keys(LABWARE).join(', ')}) or a Prep catalogue key such as c_208` }); continue; }
      spec.deck[k] = { lw: d.lw, label: text(d.label, LIMITS.label, `${p}.label`, errors) };
      if (CATALOGUE[d.lw]) (spec.labware = spec.labware || {})[d.lw] = Object.assign({}, CATALOGUE[d.lw]); // the catalogue definition, so the protocol reads on its own
    }

    const contents = Array.isArray(input.contents) ? input.contents : (input.contents === undefined ? [] : (errors.push({ path: 'contents', msg: 'must be a list' }), []));
    if (contents.length > LIMITS.contents) errors.push({ path: 'contents', msg: `has too many rows (limit ${LIMITS.contents})` });
    contents.slice(0, LIMITS.contents).forEach((c, i) => {
      const p = `contents[${i}]`;
      if (!isObj(c)) return errors.push({ path: p, msg: 'must be an object' });
      spec.contents.push({ pos: pos(c.pos, `${p}.pos`, errors), wells: wells(c.wells, `${p}.wells`, errors), liquid: liquid(c.liquid, `${p}.liquid`, errors), vol: num(c.vol, `${p}.vol`, errors, { min: 0, max: LIMITS.vol * 20 }) });
    });

    const steps = Array.isArray(input.steps) ? input.steps : (errors.push({ path: 'steps', msg: 'must be a list' }), []);
    if (steps.length > LIMITS.steps) errors.push({ path: 'steps', msg: `has too many steps (limit ${LIMITS.steps})` });
    steps.slice(0, LIMITS.steps).forEach((s, i) => {
      const p = `steps[${i}]`;
      if (!isObj(s) || !STEP_TYPES.includes(s.type)) return errors.push({ path: p, msg: `needs a type from: ${STEP_TYPES.join(', ')}` });
      const vol = () => num(s.vol, `${p}.vol`, errors, { min: 0, max: LIMITS.vol });
      const before = spec.steps.length;
      const head = st => { // optional 8-channel settings, shared by every pipetting step
        if (s.channels !== undefined && s.channels !== null && s.channels !== '') { if (s.channels === 1 || s.channels === 8) st.channels = s.channels; else errors.push({ path: `${p}.channels`, msg: 'must be 1 or 8' }); }
        if (s.tipDiscard !== undefined && s.tipDiscard !== null && s.tipDiscard !== '') {
          if (s.tipDiscard === 'waste' || s.tipDiscard === 'rack') st.tipDiscard = s.tipDiscard; else errors.push({ path: `${p}.tipDiscard`, msg: 'must be "waste" or "rack"' });
        }
        if (s.tips !== undefined && s.tips !== null && s.tips !== '') { if (['each', 'step'].includes(s.tips)) st.tips = s.tips; else if (s.tips === 'sample') st.tips = 'each'; else errors.push({ path: `${p}.tips`, msg: 'must be "each" or "step"' }); }
        return st;
      };
      if (s.type === 'transfer' || s.type === 'distribute') {
        const st = { type: s.type, fromPos: pos(s.fromPos, `${p}.fromPos`, errors), fromWells: wells(s.fromWells, `${p}.fromWells`, errors), toPos: pos(s.toPos, `${p}.toPos`, errors), toWells: wells(s.toWells, `${p}.toWells`, errors), vol: vol() };
        // optional pipetting settings; left out means the Prep's defaults
        const opt = (k, o) => { if (s[k] !== undefined && s[k] !== null && s[k] !== '') st[k] = num(s[k], `${p}.${k}`, errors, o); };
        opt('aspFlow', { min: 1, max: LIMITS.flow }); opt('dspFlow', { min: 1, max: LIMITS.flow });
        opt('aspHeight', { min: -LIMITS.height, max: LIMITS.height }); opt('dspHeight', { min: 0, max: LIMITS.height });
        spec.steps.push(head(st));
      }
      else if (s.type === 'mix') spec.steps.push(head({ type: 'mix', pos: pos(s.pos, `${p}.pos`, errors), wells: wells(s.wells, `${p}.wells`, errors), vol: vol(), cycles: num(s.cycles === undefined ? 3 : s.cycles, `${p}.cycles`, errors, { min: 1, max: LIMITS.cycles, int: true, fallback: 1 }) }));
      else if (s.type === 'discard') spec.steps.push(head({ type: 'discard', pos: pos(s.pos, `${p}.pos`, errors), wells: wells(s.wells, `${p}.wells`, errors), vol: vol() }));
      else if (s.type === 'shake') spec.steps.push({ type: 'shake', pos: pos(s.pos === undefined || s.pos === '' || s.pos === null ? HHS_POS : s.pos, `${p}.pos`, errors), temp: num(s.temp === undefined ? 0 : s.temp, `${p}.temp`, errors, { min: 0, max: LIMITS.temp }), rpm: num(s.rpm, `${p}.rpm`, errors, { min: 0, max: LIMITS.rpm }), minutes: num(s.minutes, `${p}.minutes`, errors, { min: 0, max: LIMITS.minutes }) });
      else if (s.type === 'move') spec.steps.push({ type: 'move', fromPos: pos(s.fromPos, `${p}.fromPos`, errors), toPos: pos(s.toPos, `${p}.toPos`, errors) });
      else {
        const st = { type: 'pause', message: text(s.message, LIMITS.message, `${p}.message`, errors, { required: true }) };
        // minutes: wait that long by itself and carry on (the Prep's timed pause). Without it the run waits for someone to press Continue.
        if (s.minutes !== undefined && s.minutes !== null && s.minutes !== '') st.minutes = num(s.minutes, `${p}.minutes`, errors, { min: 0.1, max: LIMITS.minutes });
        spec.steps.push(st);
      }
      if (s.label !== undefined && s.label !== null && s.label !== '' && spec.steps.length > before) spec.steps[spec.steps.length - 1].label = text(s.label, LIMITS.label, `${p}.label`, errors);
    });
    if (variables.length) spec.variables = variables;
    if (spec.steps.length === steps.length) refs.forEach(r => { if (spec.steps[r.i] && r.f in spec.steps[r.i]) spec.steps[r.i][r.f] = '$' + r.name; }); // the steps keep their "$name"
    return { spec, errors };
  }

  /**
   * Fill in a protocol's variables: `values` is { name: number }, a name left out takes its default only when `defaults` is true.
   * Returns { spec, errors: [text] } where spec is a plain protocol (no variables, every "$name" replaced by its number) that has been
   * through check() again, so a value that does not suit its field is caught here. spec is null when there are errors.
   */
  function resolveVariables(input, values = {}, { defaults = false } = {}) {
    const c = check(input);
    if (!c.spec || c.errors.length) return { spec: null, errors: c.errors.map(e => (e.path ? `${e.path} ${e.msg}` : e.msg)) };
    const errors = [], chosen = {};
    (c.spec.variables || []).forEach(v => {
      const given = values && Object.prototype.hasOwnProperty.call(values, v.name) ? values[v.name] : undefined;
      if (given === undefined || given === null || given === '') { if (defaults) chosen[v.name] = v.default; else errors.push(`${v.label} needs a value`); return; }
      const n = typeof given === 'string' ? Number(given) : given;
      if (typeof n !== 'number' || !isFinite(n)) return errors.push(`${v.label} must be a number`);
      if (n < v.min || n > v.max) return errors.push(`${v.label} must be between ${v.min} and ${v.max} ${v.unit}`.trim());
      chosen[v.name] = n;
    });
    if (errors.length) return { spec: null, errors };
    const plain = JSON.parse(JSON.stringify(c.spec)); delete plain.variables;
    plain.steps.forEach(st => Object.keys(VAR_FIELDS).forEach(f => { const nm = refName(st[f]); if (nm !== null) st[f] = chosen[nm]; }));
    const again = check(plain);
    return again.errors.length ? { spec: null, errors: again.errors.map(e => (e.path ? `${e.path} ${e.msg}` : e.msg)) } : { spec: again.spec, errors: [] };
  }

  // ───────────── simulate: play the protocol through ─────────────
  /**
   * Dry-run a (checked) protocol: tracks tips and per-well volumes, follows plates the gripper moves, and
   * collects errors/warnings. Returns { errors, warnings, events, initial, load } where each event has
   * { i, text, hl, hl2 (position -> wells), snap } and load says how to load the machine:
   * { tips: [{ pos, n }], liquids: [{ pos, well, slot, liquid, have, need }] }.
   */
  /**
   * Rules the Prep enforces that need the deck as the steps play out. Returns [{ step, msg }]. spec_check.head_errors is the Python twin.
   * - Plate-to-plate transfers on 96-well plates run on the 8-channel head (the 2-channel head is used when a 384-well plate is on the deck and
   *   takes single wells), and the 8-channel head can only pipette whole columns (A-H of one column).
   * - A plate only goes onto the magnet if it lists the magnet's alignment (the PCR plate does not fit the Ring Stand magnet), except fully skirted plates (low profile too), which the gripper takes anywhere.
   */
  function headErrors(spec) {
    const lib = libFor(spec), deck = spec.deck, out = [];
    const rows = k => (lib[k] ? lib[k].rows : 8);
    const eight = !Object.values(deck).some(d => rows(d.lw) > 8);
    const occ = {};
    Object.entries(deck).forEach(([pos, d]) => { if (lib[d.lw] && lib[d.lw].kind !== 'magnet') occ[pos] = pos; });
    const kind = pos => (occ[pos] !== undefined ? lib[deck[occ[pos]].lw].kind : null);
    const list = text => { try { return expandWells(text, null).map(parseWell); } catch (e) { return []; } };
    const whole = ws => ws.length > 0 && ws.length % 8 === 0 && ws.every(([r, c], n) => r === n % 8 && c === ws[n - (n % 8)][1]);
    const plateId = k => (Catalogue.BUILTIN[k] ? Catalogue.BUILTIN[k].id : CATALOGUE_KEY.test(k) ? +k.slice(2) : null);
    const magnetAlign = k => (lib[k] && lib[k].align) || (k === 'magnet' ? Catalogue.MAGNET_ALIGNMENT : null); // the alignment a plate must list to sit on magnet k
    const fullSkirt = k => { const id = plateId(k), e = id === null ? null : Catalogue.ALL.find(x => x.id === id); return /full skirt/i.test(e ? e.displayName : (lib[k] && lib[k].label) || ''); }; // fully skirted plates (low profile too) are gripped fine on any magnet
    const misfit = (k, m) => { const id = plateId(k), al = magnetAlign(m); return id !== null && !fullSkirt(k) && !!al && Catalogue.MAGNET_FIT[id] !== undefined && !Catalogue.MAGNET_FIT[id].includes(al); };
    spec.steps.forEach((s, i) => {
      if (s.type === 'transfer' && eight && kind(s.fromPos) === 'plate' && kind(s.toPos) === 'plate') {
        [['Source', s.fromWells], ['Destination', s.toWells]].forEach(([role, text]) => {
          if (!whole(list(text))) out.push({ step: i, msg: `${role} wells ${text} are not whole columns. A plate-to-plate transfer on 96-well plates uses the 8-channel head, which can only pipette whole columns (A-H of one column, e.g. A1:H1). Select full columns.` });
        });
      } else if (s.type === 'shake' && s.pos !== HHS_POS && occ[s.pos] !== undefined && occ[HHS_POS] === undefined) {
        occ[HHS_POS] = occ[s.pos]; delete occ[s.pos];
      } else if (s.type === 'move' && occ[s.fromPos] !== undefined && occ[s.toPos] === undefined) {
        const key = deck[occ[s.fromPos]].lw, to = deck[s.toPos];
        if (to && lib[to.lw] && lib[to.lw].kind === 'magnet' && kind(s.fromPos) === 'plate' && misfit(key, to.lw))
          out.push({ step: i, msg: `${lib[key].label} does not fit the ${lib[to.lw].label}: the Prep only accepts plates that list the magnet's alignment (${magnetAlign(to.lw)}). Pick a different magnet (the Prep's own catalogue has several) or a compatible plate such as the 96 deep-well plate.` });
        occ[s.toPos] = occ[s.fromPos]; delete occ[s.fromPos];
      }
    });
    return out;
  }

  function simulate(p) {
    if (p && p.variables && p.variables.length) { // the Builder shows the plan with each variable at its default
      const r = resolveVariables(p, {}, { defaults: true });
      return r.spec ? simulate(r.spec) : { errors: r.errors.map(msg => ({ step: null, msg })), warnings: [], events: [], initial: { occ: {}, vol: {}, tips: [] }, load: { tips: [], liquids: [] } };
    }
    const lib = libFor(p);
    const errors = [], warnings = [], events = [], asp = {};
    const vol = {}; // "plateId:well" -> { liquid, vol }; a plate's id is the position it starts in, so contents follow it when the gripper moves it
    const occ = {}; // physical position -> id of the plate/rack currently there (a magnet starts empty)
    Object.entries(p.deck).forEach(([pos, d]) => { if (lib[d.lw] && lib[d.lw].kind !== 'magnet') occ[pos] = pos; });
    const movable = pos => pos === HHS_POS || (!!p.deck[pos] && !!lib[p.deck[pos].lw] && lib[p.deck[pos].lw].kind === 'magnet'); // the only places the gripper picks up from or sets down on
    const lwAt = pos => (occ[pos] != null ? lib[p.deck[occ[pos]].lw] : undefined);
    p.contents.forEach((c, i) => {
      const lw = lwAt(c.pos);
      if (!lw) return errors.push({ step: null, msg: `Contents row ${i + 1}: ${p.deck[c.pos] && lib[p.deck[c.pos].lw] && lib[p.deck[c.pos].lw].kind === 'magnet' ? `position ${c.pos} is a magnet, so it starts empty. Put the plate somewhere else and add a Move step` : `position ${c.pos} has no labware`}.` });
      try {
        expandWells(c.wells, lw).forEach(w => {
          const k = keyOf(c.pos, w);
          if (vol[k]) errors.push({ step: null, msg: `Position ${c.pos} ${slotName(p.deck[c.pos].lw, w, lib)} is given two different contents.` });
          vol[k] = { liquid: c.liquid || 'liquid', vol: +c.vol || 0 };
          if (vol[k].vol > lw.cap) warnings.push({ step: null, msg: `Position ${c.pos} ${slotName(p.deck[c.pos].lw, w, lib)} starts above its ${lw.cap} µL capacity.` });
        });
      } catch (e) { errors.push({ step: null, msg: `Contents row ${i + 1}: ${e.message}` }); }
    });
    Object.entries(p.deck).forEach(([pos, d]) => { const e = placeError(d.lw, pos, lib); if (e) errors.push({ step: null, msg: e }); });
    headErrors(p).forEach(e => errors.push(e));
    const racks = Object.keys(p.deck).filter(k => lib[p.deck[k].lw] && lib[p.deck[k].lw].kind === 'tips' && k !== HHS_POS).sort().map(pos => ({ pos, tipVol: lib[p.deck[pos].lw].tipVol, used: 0, sum: 0, back: new Set(), rows: lib[p.deck[pos].lw].rows, total: lib[p.deck[pos].lw].rows * lib[p.deck[pos].lw].cols }));
    if (p.steps.some(s => s.type !== 'pause' && s.type !== 'shake' && s.type !== 'move') && !racks.length) errors.push({ step: null, msg: 'No tip rack on the deck.' });
    // Running out of tips is a warning, once per step: the machine pauses so more tips can be loaded.
    const outOfTips = new Set();
    let cur = null; // the event of the step being played, so a tip reload can be shown on it
    const take = (v, i, n = 1) => { // n tips at once: the 8-channel head takes a whole rack column, so a partly used column is skipped
      const room = () => racks.filter(r => r.used + (n > 1 ? (r.rows - (r.used % r.rows)) % r.rows : 0) + n <= r.total);
      let avail = room();
      if (!avail.length && !racks.length) return null; // no tip rack at all, which is reported separately
      if (!avail.length) { // the machine pauses and a rack is filled again (the one that ran dry first), then the step carries on
        if (!outOfTips.has(i)) { outOfTips.add(i); warnings.push({ step: i, msg: 'Runs out of tips here. The machine will pause so you can load more.' }); }
        const dry = racks[0];
        if (cur) { const full = snap(); full.tips = racks.map(r => ({ pos: r.pos, used: r === dry ? r.total : r.used, back: [...r.back] })); full.stillBack = dry.back.size; full.hl = JSON.parse(JSON.stringify(cur.hl)); full.reloaded = dry.pos; cur.reloadSnaps.push(full); } // the racks as they were when it ran dry
        dry.used = 0; dry.back.clear(); // used tips that were put back are taken out, and fresh ones go in
        if (cur) delete cur.hl[dry.pos]; // tips picked from that rack before the reload were on the box that has just been refilled
        avail = room(); if (!avail.length) return null;
      }
      const fit = avail.filter(r => r.tipVol >= v).sort((a, b) => a.tipVol - b.tipVol)[0] || avail.sort((a, b) => b.tipVol - a.tipVol)[0];
      if (fit.tipVol < v) warnings.push({ step: i, msg: `${v} µL exceeds the largest available tip (${fit.tipVol} µL).` });
      if (n > 1) fit.used += (fit.rows - (fit.used % fit.rows)) % fit.rows;
      const k = fit.used; fit.used += n; fit.sum += n; return { pos: fit.pos, name: wellName(k % fit.rows, Math.floor(k / fit.rows)), names: Array.from({ length: n }, (_, j) => wellName((k + j) % fit.rows, Math.floor((k + j) / fit.rows))) };
    };
    // Tips a step still has to pick up, one number per pick-up. One tip per well without the 8-channel head (null); with it, a rack
    // column of up to 8 at a time. Tips are always fresh: used tips are never picked up again, even when they were put back in the rack.
    const pickups = (s, wells, byDefault) => {
      if (s.channels !== 8) return null;
      const mode = s.tips || byDefault, cols = {};
      wells.forEach(w => { const pw = parseWell(w); if (pw) (cols[pw[1]] = cols[pw[1]] || new Set()).add(pw[0]); });
      if (mode === 'step') return [{ n: Math.min(8, new Set(wells.map(w => (parseWell(w) || [0])[0])).size) }];
      return Object.keys(cols).map(c => ({ n: Math.min(8, cols[c].size) }));
    };
    // Pick up the tips for one pick-up and light them on the rack for this step.
    const grab = (v, i, it = { n: 1 }) => {
      const t = take(v, i, it.n);
      if (!t) return;
      if (cur && cur.putBack) { const rk = racks.find(r => r.pos === t.pos); t.names.forEach(nm => rk.back.add(nm)); } // used, but back in the slot it came from
      if (cur) { const l = (cur.hl[t.pos] = cur.hl[t.pos] || []); t.names.forEach(nm => { if (!l.includes(nm)) l.push(nm); }); }
    };
    const resolve = (pos, wellSpec, i, role) => {
      const lw = lwAt(pos);
      if (!lw) { errors.push({ step: i, msg: p.deck[pos] && lib[p.deck[pos].lw] && lib[p.deck[pos].lw].kind === 'magnet' ? `${role}: the magnet at position ${pos} has no plate on it at this point.` : `${role}: position "${pos}" has no labware.` }); return null; }
      if (lw.kind === 'tips') { errors.push({ step: i, msg: `${role}: position ${pos} is a tip rack.` }); return null; }
      try { return { lw, wells: expandWells(wellSpec, lw), pos, id: occ[pos] }; } catch (e) { errors.push({ step: i, msg: `${role}: ${e.message}` }); return null; }
    };
    const aspirate = (id, w, v, i) => {
      const k = keyOf(id, w), c = vol[k]; asp[k] = (asp[k] || 0) + v;
      if (!c || c.vol <= 0) { errors.push({ step: i, msg: `${k} is empty - nothing to aspirate.` }); return c && c.liquid; }
      if (c.vol < v) errors.push({ step: i, msg: `${k} has only ${c.vol.toFixed(1)} µL but ${v} µL is needed.` });
      c.vol -= v; return c.liquid;
    };
    const dispense = (id, w, v, liquid, lw, i) => {
      const k = keyOf(id, w), c = vol[k] || (vol[k] = { liquid, vol: 0 });
      if (c.vol > 0 && liquid && c.liquid !== liquid && !c.liquid.includes(liquid)) c.liquid = `${c.liquid} + ${liquid}`; else if (!c.vol) c.liquid = liquid || c.liquid;
      c.vol += v; if (c.vol > lw.cap) errors.push({ step: i, msg: `${k} would hold ${c.vol.toFixed(1)} µL, over its ${lw.cap} µL capacity.` });
    };
    const snap = () => ({ occ: { ...occ }, vol: JSON.parse(JSON.stringify(vol)), tips: racks.map(r => ({ pos: r.pos, used: r.used, back: [...r.back] })) });
    const initial = snap();
    p.steps.forEach((s, i) => {
      const ev = { i, text: '', hl: {}, hl2: {} }; ev.reloadSnaps = []; ev.putBack = s.tipDiscard === 'rack'; cur = ev;
      const mark = (m, pos, ws) => { const list = (m[pos] = m[pos] || []); ws.forEach(w => { if (!list.includes(w)) list.push(w); }); };
      const v = +s.vol;
      const needsVol = ['transfer', 'distribute', 'mix', 'discard'].includes(s.type);
      if (needsVol && !(v > 0)) errors.push({ step: i, msg: 'Volume must be greater than 0.' });
      else if (needsVol && v < 1) warnings.push({ step: i, msg: `${v} µL is below typical pipetting accuracy.` });
      if (s.type === 'transfer' || s.type === 'distribute') {
        const src = resolve(s.fromPos, s.fromWells, i, 'Source'), dst = resolve(s.toPos, s.toWells, i, 'Destination');
        ev.text = `${s.type === 'transfer' ? 'Transfer' : 'Add reagent'} ${v} µL ${s.fromPos}:${s.fromWells} → ${s.toPos}:${s.toWells}`;
        if (src && dst && v > 0) {
          if (s.type === 'transfer') {
            if (src.wells.length !== dst.wells.length) errors.push({ step: i, msg: `Transfer needs equal well counts (source ${src.wells.length}, destination ${dst.wells.length}). Use Add reagent for 1 → many.` });
            else {
              const pk = pickups(s, src.wells, 'each');
              if (pk) pk.forEach(it => grab(v, i, it));
              src.wells.forEach((w, n) => { if (!pk) grab(v, i); const l = aspirate(src.id, w, v, i); dispense(dst.id, dst.wells[n], v, l, dst.lw, i); });
            }
          } else if (src.wells.length !== 1) errors.push({ step: i, msg: 'Add reagent needs exactly one source well.' });
          else { const pk = pickups(s, dst.wells, 'step'); if (pk) pk.forEach(it => grab(v, i, it)); else grab(v, i); dst.wells.forEach(w => { const l = aspirate(src.id, src.wells[0], v, i); dispense(dst.id, w, v, l, dst.lw, i); }); }
          mark(ev.hl, src.pos, src.wells); mark(ev.hl2, dst.pos, dst.wells);
        }
      } else if (s.type === 'mix') {
        const r = resolve(s.pos, s.wells, i, 'Mix wells'); ev.text = `Mix ${v} µL × ${s.cycles} at ${s.pos}:${s.wells}`;
        if (r && v > 0) { const pk = pickups(s, r.wells, 'each'); if (pk) pk.forEach(it => grab(v, i, it)); r.wells.forEach(w => { if (!pk) grab(v, i); const c = vol[keyOf(r.id, w)]; if (!c || c.vol < v) warnings.push({ step: i, msg: `${keyOf(r.id, w)} holds less than the ${v} µL mix volume.` }); }); mark(ev.hl2, r.pos, r.wells); }
      } else if (s.type === 'discard') {
        const r = resolve(s.pos, s.wells, i, 'Discard wells'); ev.text = `Discard ${v} µL to waste from ${s.pos}:${s.wells}`; ev.waste = true;
        if (r && v > 0) { const pk = pickups(s, r.wells, 'each'); if (pk) pk.forEach(it => grab(v, i, it)); r.wells.forEach(w => { if (!pk) grab(v, i); aspirate(r.id, w, v, i); }); mark(ev.hl, r.pos, r.wells); }
      } else if (s.type === 'shake') {
        // The gripper carries the plate to the heater-shaker by itself and leaves it there: bring it back with a Move step.
        const sp = s.pos || HHS_POS, L = lwAt(sp);
        ev.text = `${sp !== HHS_POS ? `Gripper: plate ${sp} → HHS. ` : ''}HHS: ${s.temp ? 'heat to ' + s.temp + ' °C, ' : ''}shake ${s.rpm} rpm for ${s.minutes} min`;
        if (!L) errors.push({ step: i, msg: `No plate at position ${sp} to heat/shake at this point.` });
        else if (L.kind !== 'plate') errors.push({ step: i, msg: `Only plates can be heated or shaken, not ${L.label}.` });
        else if (sp !== HHS_POS && occ[HHS_POS] != null) errors.push({ step: i, msg: `The heater-shaker already has a plate on it. Move that plate off before shaking the plate at position ${sp}.` });
        else if (sp !== HHS_POS) { const id = occ[sp]; occ[HHS_POS] = id; delete occ[sp]; mark(ev.hl, sp, allWells(L)); mark(ev.hl2, HHS_POS, allWells(L)); }
        else mark(ev.hl2, sp, allWells(L));
        if (!(s.rpm >= 0 && s.minutes > 0)) errors.push({ step: i, msg: 'Shake needs rpm ≥ 0 and minutes > 0.' });
      } else if (s.type === 'move') { // the gripper carries a plate (and its contents) to a free position, e.g. onto the magnet
        ev.text = `Gripper: move plate ${s.fromPos || '?'} → ${s.toPos || '?'}`;
        const L = lwAt(s.fromPos);
        if (!s.fromPos || !s.toPos) errors.push({ step: i, msg: 'Choose which position to move the plate from and to.' });
        else if (!L) errors.push({ step: i, msg: `There is no plate at position ${s.fromPos} at this point to move.` });
        else if (L.kind !== 'plate') errors.push({ step: i, msg: `The gripper only moves plates, not ${L.label}.` });
        else if (s.fromPos === s.toPos) errors.push({ step: i, msg: 'The plate is already there.' });
        else if (occ[s.toPos] != null) errors.push({ step: i, msg: `Position ${s.toPos} is already occupied.` });
        else if (!movable(s.fromPos) && !movable(s.toPos)) errors.push({ step: i, msg: `The gripper only moves plates to or from the heater-shaker (position 1) or a magnet, so it cannot take a plate from position ${s.fromPos} to position ${s.toPos}.` });
        else if (landError(p.deck[occ[s.fromPos]].lw, s.toPos, lib)) errors.push({ step: i, msg: landError(p.deck[occ[s.fromPos]].lw, s.toPos, lib) });
        else { const id = occ[s.fromPos]; mark(ev.hl, s.fromPos, allWells(L)); occ[s.toPos] = id; delete occ[s.fromPos]; mark(ev.hl2, s.toPos, allWells(L)); }
      } else ev.text = s.minutes ? `Wait ${s.minutes} min (timed pause): ${s.message}` : `Pause: ${s.message}`;
      if (s.label) ev.text = `${s.label}: ${ev.text}`;
      // Each time the tip racks ran dry gets its own playback step: empty racks, the pause, then the step carries on with fresh ones.
      ev.reloadSnaps.forEach(sn => events.push({ i, text: `${s.label ? s.label + ': ' : ''}Out of tips. The machine pauses so you can refill the rack at position ${sn.reloaded}${sn.stillBack ? ` (it still holds ${sn.stillBack} used tips that were put back, but the machine only counts fresh ones, so take those out and put in fresh tips)` : ''}, then carries on`, hl: sn.hl, hl2: {}, reload: true, snap: sn }));
      ev.reloadSnaps.forEach(sn => { delete sn.hl; delete sn.stillBack; });
      delete ev.reloadSnaps; delete ev.putBack;
      ev.snap = snap(); events.push(ev);
    });
    const load = { tips: racks.filter(r => r.sum).map(r => ({ pos: r.pos, n: r.sum })), liquids: [] };
    Object.entries(asp).forEach(([k, need]) => {
      const [pos, well] = k.split(':'), c0 = initial.vol[k];
      load.liquids.push({ pos, well, slot: slotName(p.deck[pos] && p.deck[pos].lw, well, lib), liquid: (c0 && c0.liquid) || '?', have: (c0 && c0.vol) || 0, need: Math.ceil(need * 1.1 + 5) });
    });
    return { errors, warnings, events, initial, load };
  }

  /**
   * Check then simulate. Returns { valid, spec, errors: [text], warnings: [text], load, steps: [text] }.
   * `valid` is true only when both stages found no errors. The raw simulation (events and snapshots for
   * animation) is returned as `simulation` when asked for with { detail: true }.
   */
  function validate(input, { detail = false } = {}) {
    const c = check(input);
    if (!c.spec || c.errors.length) return { valid: false, spec: c.spec, errors: c.errors.map(e => (e.path ? `${e.path} ${e.msg}` : e.msg)), warnings: [], load: { tips: [], liquids: [] }, steps: [] };
    const sim = simulate(c.spec);
    const where = e => (e.step != null ? `Step ${e.step + 1}: ${e.msg}` : e.msg);
    const out = { valid: sim.errors.length === 0, spec: c.spec, errors: sim.errors.map(where), warnings: sim.warnings.map(where), load: sim.load, steps: sim.events.map(e => e.text) };
    if (detail) out.simulation = sim;
    return out;
  }

  return { LABWARE, CATALOGUE, CATALOGUE_KEY, LIQUIDS: Catalogue.LIQUIDS, findLiquid: Catalogue.findLiquid, libFor, resolve: resolveVariables, VAR_FIELDS, refName, STEP_TYPES, POSITIONS, HHS_POS, RESERVOIR_POS, LIMITS, wellName, parseWell, expandWells, placeError, landError, headErrors, fitsHHS, slotName, check, simulate, validate };
});
