"""
Checker for Prep protocols ("specs"), a faithful Python port of check(), resolve() and the deck placement rules in
web/prep-protocol.js (a byte-identical copy of the shared prep-protocol.js).

It cleans a spec that came from outside (keeps only the known fields, with the right types and limits) and says what
is wrong. Nothing here runs anything. Both checkers run the same vectors (tests/vectors/prep-protocol.json), where
"valid" means: check() finds no errors and no deck placement errors.

Standard library only. Keep the rules in step with the JavaScript: when a rule changes there, change it here and add a vector.
"""

import json
import math
import re

import labware_map

LABWARE = {
    "tips_50": {"label": "50 µL tip rack", "kind": "tips"},
    "tips_300": {"label": "300 µL tip rack", "kind": "tips"},
    "tips_1000": {"label": "1000 µL tip rack", "kind": "tips"},
    "plate_pcr": {"label": "96-well PCR plate", "kind": "plate"},
    "plate_deep": {"label": "96 deep-well plate", "kind": "plate"},
    "plate_flat_96": {"label": "Corning 96-well flat-bottom plate (3596)", "kind": "plate"},
    "plate_black_96": {"label": "Corning 96-well black, clear flat-bottom plate (3603)", "kind": "plate"},
    "plate_384": {"label": "Corning 384-well flat-bottom plate", "kind": "plate"},
    "reservoir_60": {"label": "60 mL reagent reservoir rack (5 slots)", "kind": "reservoirs", "only": ["4", "8"]},
    "tubes_24": {"label": "1.5 mL tube rack (24)", "kind": "tubes"},
    "magnet": {"label": "Movable magnet", "kind": "magnet"},
}
STEP_TYPES = ["transfer", "distribute", "mix", "discard", "shake", "pause", "move"]
CATALOGUE_KEY = re.compile(r"c_[0-9]{1,9}")  # c_<Prep id>: plates, tip racks and 24-tube racks from the Prep's own catalogue
VAR_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,23}")
HHS_POS = "1"
POSITIONS = ["1", "2", "3", "4", "5", "6", "7", "8"]
LIMITS = {"labware": 30, "name": 120, "description": 500, "label": 60, "liquid": 60, "wells": 80, "message": 200, "contents": 400,
          "steps": 600, "vol": 10000, "flow": 1000, "height": 50, "cycles": 50, "rpm": 2000, "minutes": 600, "temp": 105, "bytes": 200000,
          "variables": 20, "unit": 12}
# The step fields a variable may stand in for, and the range each allows.
VAR_FIELDS = {"vol": (0, LIMITS["vol"]), "cycles": (1, LIMITS["cycles"]), "temp": (0, LIMITS["temp"]), "rpm": (0, LIMITS["rpm"]),
              "minutes": (0, LIMITS["minutes"])}

# JavaScript's String.prototype.trim() whitespace (Python's str.strip() differs, e.g. it also strips \x1c-\x1f)
JS_WS = "\t\n\v\f\r                  　﻿"
CONTROL = re.compile("[\u0000-\u001f\u007f  ]")
_W = r"[A-Pa-p](?:[1-9]|1[0-9]|2[0-4])"  # one well, columns 1 to 24
WELLS = re.compile(_W + "(:" + _W + ")?([" + re.escape(JS_WS) + r"]*,[" + re.escape(JS_WS) + r"]*" + _W + "(:" + _W + ")?)*")
NUMBER = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?")
MISSING = object()  # a field that is absent (JavaScript's undefined), as opposed to null


def js_len(s):
    """Length as JavaScript counts it (UTF-16 code units)."""
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def js_number(s):
    """Number(string) for a non-blank string; nan when JavaScript would give NaN."""
    s = s.strip(JS_WS)
    try:
        if NUMBER.fullmatch(s):
            return float(s)
        if re.fullmatch(r"0[xX][0-9a-fA-F]+", s):
            return float(int(s, 16))
        if re.fullmatch(r"0[oO][0-7]+", s):
            return float(int(s[2:], 8))
        if re.fullmatch(r"0[bB][01]+", s):
            return float(int(s[2:], 2))
        if re.fullmatch(r"[+-]?Infinity", s):
            return float(s.replace("Infinity", "inf"))
    except (ValueError, OverflowError):
        pass
    return math.nan


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _text(v, maximum, path, errors, required=False):
    if v is MISSING or v is None or v == "":
        if required:
            errors.append({"path": path, "msg": "is required"})
        return ""
    if not isinstance(v, str):
        errors.append({"path": path, "msg": "must be text"})
        return ""
    s = v.strip(JS_WS)
    if CONTROL.search(s):
        errors.append({"path": path, "msg": "must be plain text with no control characters"})
        return ""
    if js_len(s) > maximum:
        errors.append({"path": path, "msg": f"must be at most {maximum} characters"})
        return s[:maximum]
    if required and not s:
        errors.append({"path": path, "msg": "is required"})
    return s


def _num(v, path, errors, minimum=-math.inf, maximum=math.inf, integer=False, fallback=0):
    n = js_number(v) if isinstance(v, str) and v.strip(JS_WS) != "" else v
    if not _is_number(n):
        errors.append({"path": path, "msg": "must be a number"})
        return fallback
    try:
        finite = math.isfinite(n)
    except OverflowError:  # an integer too big for a float
        finite = False
    if not finite:
        errors.append({"path": path, "msg": "must be a number"})
        return fallback
    if integer and not (isinstance(n, int) or float(n).is_integer()):
        errors.append({"path": path, "msg": "must be a whole number"})
        return fallback
    if n < minimum or n > maximum:
        errors.append({"path": path, "msg": f"must be between {minimum:g} and {maximum:g}"})
        return fallback
    return int(n) if isinstance(n, float) and n.is_integer() else n  # 3.0 and 3 are the same number in JSON


def _pos(v, path, errors, optional=False):
    if (v is MISSING or v is None or v == "") and optional:
        return ""
    s = str(int(v)) if _is_number(v) and float(v).is_integer() else v  # JavaScript's String(number)
    if not isinstance(s, str) or s not in POSITIONS:
        errors.append({"path": path, "msg": "must be a deck position from 1 to 8"})
        return ""
    return s


def _wells(v, path, errors):
    s = _text(v, LIMITS["wells"], path, errors, required=True)
    if s and not WELLS.fullmatch(s):
        errors.append({"path": path, "msg": "must be wells such as A1, A1:A6 or A1,B2"})
    return s.upper()


def _vol(s, path, errors):
    return _num(s.get("vol", MISSING), path, errors, 0, LIMITS["vol"])


def catalogue():
    """The Prep's own plates, tip racks and 24-tube racks, keyed c_<Prep id> (what the Builder offers)."""
    if "cat" not in _CACHE:
        _CACHE["cat"] = labware_map.builder_defs()[0]
    return _CACHE["cat"]


_CACHE = {}


def lib_for(spec=None):
    """Everything a deck may use: the built-in keys and the Prep's catalogue (a protocol cannot add labware)."""
    return {**LABWARE, **catalogue()}


def _same_shape(a, b):
    return isinstance(a, dict) and a.get("kind") == b["kind"] and a.get("rows") == b["rows"] and a.get("cols") == b["cols"] \
        and not isinstance(a.get("rows"), bool) and not isinstance(a.get("cols"), bool)


def _liquid(v, path, errors):
    """One of the Prep's liquid classes, in the Prep's spelling (matched without regard to case)."""
    t = _text(v, LIMITS["liquid"], path, errors, required=True)
    if not t:
        return ""
    name = labware_map.find_liquid(t)
    if name is None:
        errors.append({"path": path, "msg": "must be one of the Prep's liquid classes: " + ", ".join(labware_map.liquids())})
        return t
    return name


def ref_name(v):
    """The variable a step field names ('$beads' -> 'beads'), or None."""
    return v.strip(JS_WS)[1:] if isinstance(v, str) and re.fullmatch(r"\$[A-Za-z][A-Za-z0-9_]{0,23}", v.strip(JS_WS)) else None


def check(data):
    """Clean a protocol from outside. Returns (spec, errors [{path, msg}]); spec only ever holds the known fields."""
    errors = []
    if not isinstance(data, dict):
        return None, [{"path": "", "msg": "A protocol must be an object"}]
    try:
        size = js_len(json.dumps(data, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    except (ValueError, TypeError, RecursionError):
        return None, [{"path": "", "msg": "Not valid JSON data"}]
    if size > LIMITS["bytes"]:
        return None, [{"path": "", "msg": f"Too large (limit {LIMITS['bytes'] // 1000} KB)"}]

    # Variables: cleaned first. Steps that use one are checked with its default in place, then get the "$name" back.
    variables, refs = [], []
    raw_vars = data.get("variables", MISSING)
    if raw_vars is not MISSING and not isinstance(raw_vars, list):
        errors.append({"path": "variables", "msg": "must be a list"})
    elif isinstance(raw_vars, list):
        if len(raw_vars) > LIMITS["variables"]:
            errors.append({"path": "variables", "msg": f"has too many variables (limit {LIMITS['variables']})"})
        for i, v in enumerate(raw_vars[:LIMITS["variables"]]):
            p = f"variables[{i}]"
            if not isinstance(v, dict):
                errors.append({"path": p, "msg": "must be an object"})
                continue
            name = v.get("name").strip(JS_WS) if isinstance(v.get("name"), str) else ""
            if not VAR_NAME.fullmatch(name):
                errors.append({"path": f"{p}.name", "msg": "must be a name of letters, digits and underscores that starts with a letter (up to 24 characters)"})
                continue
            if any(x["name"] == name for x in variables):
                errors.append({"path": f"{p}.name", "msg": f'"{name}" is used twice'})
                continue

            def opt(k, default):
                x = v.get(k, MISSING)
                return default if (x is MISSING or x is None or x == "") else _num(x, f"{p}.{k}", errors, -1e6, 1e6)
            lo, hi = opt("min", 0), opt("max", LIMITS["vol"])
            default = _num(v.get("default", MISSING), f"{p}.default", errors, -1e6, 1e6)
            if lo > hi:
                errors.append({"path": p, "msg": "min must not be above max"})
            elif default < lo or default > hi:
                errors.append({"path": f"{p}.default", "msg": f"must be between {lo:g} and {hi:g}"})
            unit = v.get("unit", MISSING)
            variables.append({"name": name, "label": _text(v.get("label", MISSING), LIMITS["label"], f"{p}.label", errors) or name,
                              "default": default, "min": lo, "max": hi,
                              "unit": _text("µL" if unit is MISSING else unit, LIMITS["unit"], f"{p}.unit", errors)})
    raw_steps = data.get("steps", MISSING)
    if isinstance(raw_steps, list):
        steps_in = []
        for i, s0 in enumerate(raw_steps):
            if not isinstance(s0, dict):
                steps_in.append(s0)
                continue
            copy_ = s0
            for f, (flo, fhi) in VAR_FIELDS.items():
                nm = ref_name(s0.get(f))
                if nm is None:
                    continue
                v = next((x for x in variables if x["name"] == nm), None)
                p = f"steps[{i}].{f}"
                if v is None:
                    errors.append({"path": p, "msg": f'uses the variable "{nm}", which is not declared under variables'})
                    continue
                if v["min"] < flo or v["max"] > fhi:
                    errors.append({"path": p, "msg": f'the variable "{nm}" allows {v["min"]:g} to {v["max"]:g}, but this field only takes {flo} to {fhi}'})
                if copy_ is s0:
                    copy_ = dict(s0)
                copy_[f] = v["default"]
                refs.append((i, f, nm))
            steps_in.append(copy_)
        data = {**data, "steps": steps_in}

    spec = {"specVersion": 1, "name": _text(data.get("name", MISSING), LIMITS["name"], "name", errors, required=True),
            "description": _text(data.get("description", MISSING), LIMITS["description"], "description", errors),
            "deck": {}, "contents": [], "steps": []}

    # A protocol cannot bring its own hardware: a labware field is only accepted when each entry IS a catalogue entry
    cat = catalogue()
    raw_lw = data.get("labware", MISSING)
    if raw_lw is not MISSING and not isinstance(raw_lw, dict):
        errors.append({"path": "labware", "msg": "must be an object"})
    elif isinstance(raw_lw, dict):
        if len(raw_lw) > LIMITS["labware"]:
            errors.append({"path": "labware", "msg": f"has too many definitions (limit {LIMITS['labware']})"})
        for key, d in list(raw_lw.items())[:LIMITS["labware"]]:
            p = f"labware.{str(key)[:30]}"
            if not CATALOGUE_KEY.fullmatch(key) or key not in cat:
                errors.append({"path": p, "msg": "is not in the Prep's labware catalogue. Only the Prep's own labware can be used (keys like c_208)"})
            elif not _same_shape(d, cat[key]):
                c = cat[key]
                errors.append({"path": p, "msg": f"does not match the catalogue entry {key} ({c['label']}, {c['rows']} × {c['cols']} {c['kind']})"})
    lib = lib_for(spec)

    deck = data.get("deck", MISSING)
    if not isinstance(deck, dict):
        errors.append({"path": "deck", "msg": "must be an object"})
    else:
        for k, d in deck.items():
            p = f"deck.{str(k)[:8]}"
            if k not in POSITIONS:
                errors.append({"path": p, "msg": "is not a deck position (1 to 8)"})
                continue
            if not isinstance(d, dict) or not isinstance(d.get("lw"), str) or d["lw"] not in lib:
                errors.append({"path": p, "msg": f"needs labware: a built-in key ({', '.join(LABWARE)}) or a Prep catalogue key such as c_208"})
                continue
            spec["deck"][k] = {"lw": d["lw"], "label": _text(d.get("label", MISSING), LIMITS["label"], f"{p}.label", errors)}
            if d["lw"] in cat:  # the catalogue definition, so the protocol reads on its own
                spec.setdefault("labware", {})[d["lw"]] = dict(cat[d["lw"]])

    contents = data.get("contents", MISSING)
    if contents is MISSING:
        contents = []
    elif not isinstance(contents, list):
        errors.append({"path": "contents", "msg": "must be a list"})
        contents = []
    if len(contents) > LIMITS["contents"]:
        errors.append({"path": "contents", "msg": f"has too many rows (limit {LIMITS['contents']})"})
    for i, c in enumerate(contents[:LIMITS["contents"]]):
        p = f"contents[{i}]"
        if not isinstance(c, dict):
            errors.append({"path": p, "msg": "must be an object"})
            continue
        spec["contents"].append({"pos": _pos(c.get("pos", MISSING), f"{p}.pos", errors),
                                 "wells": _wells(c.get("wells", MISSING), f"{p}.wells", errors),
                                 "liquid": _liquid(c.get("liquid", MISSING), f"{p}.liquid", errors),
                                 "vol": _num(c.get("vol", MISSING), f"{p}.vol", errors, 0, LIMITS["vol"] * 20)})

    steps = data.get("steps", MISSING)
    if not isinstance(steps, list):
        errors.append({"path": "steps", "msg": "must be a list"})
        steps = []
    if len(steps) > LIMITS["steps"]:
        errors.append({"path": "steps", "msg": f"has too many steps (limit {LIMITS['steps']})"})
    for i, s in enumerate(steps[:LIMITS["steps"]]):
        p = f"steps[{i}]"
        if not isinstance(s, dict) or not isinstance(s.get("type"), str) or s["type"] not in STEP_TYPES:
            errors.append({"path": p, "msg": f"needs a type from: {', '.join(STEP_TYPES)}"})
            continue
        t, g = s["type"], s.get
        before = len(spec["steps"])

        def head(st):  # optional 8-channel settings, shared by every pipetting step
            v = g("head", MISSING)
            if not (v is MISSING or v is None or v == ""):
                if v in ("two", "eight"):
                    st["head"] = v
                else:
                    errors.append({"path": f"{p}.head", "msg": 'must be "two" or "eight"'})
            v = g("channels", MISSING)
            if not (v is MISSING or v is None or v == ""):
                if _is_number(v) and v in (1, 8):
                    st["channels"] = int(v)
                else:
                    errors.append({"path": f"{p}.channels", "msg": "must be 1 or 8"})
            v = g("tipDiscard", MISSING)
            if not (v is MISSING or v is None or v == ""):
                if v in ("waste", "rack"):
                    st["tipDiscard"] = v
                else:
                    errors.append({"path": f"{p}.tipDiscard", "msg": 'must be "waste" or "rack"'})
            v = g("tips", MISSING)
            if not (v is MISSING or v is None or v == ""):
                if v in ("each", "step"):
                    st["tips"] = v
                elif v == "sample":
                    st["tips"] = "each"
                else:
                    errors.append({"path": f"{p}.tips", "msg": 'must be "each" or "step"'})
            return st

        if t in ("transfer", "distribute"):
            st = {"type": t, "fromPos": _pos(g("fromPos", MISSING), f"{p}.fromPos", errors),
                  "fromWells": _wells(g("fromWells", MISSING), f"{p}.fromWells", errors),
                  "toPos": _pos(g("toPos", MISSING), f"{p}.toPos", errors),
                  "toWells": _wells(g("toWells", MISSING), f"{p}.toWells", errors), "vol": _vol(s, f"{p}.vol", errors)}
            # optional pipetting settings; left out means the Prep's defaults
            for k, lo, hi in (("aspFlow", 1, LIMITS["flow"]), ("dspFlow", 1, LIMITS["flow"]),
                              ("aspHeight", -LIMITS["height"], LIMITS["height"]), ("dspHeight", 0, LIMITS["height"])):
                v = g(k, MISSING)
                if not (v is MISSING or v is None or v == ""):
                    st[k] = _num(v, f"{p}.{k}", errors, lo, hi)
            v = g("multiDispense", MISSING)   # false: one aspirate and one dispense per well (a reagent step only), a fresh tip for each well
            if not (v is MISSING or v is None or v == ""):
                if isinstance(v, bool):
                    st["multiDispense"] = v
                else:
                    errors.append({"path": f"{p}.multiDispense", "msg": "must be true or false"})
            spec["steps"].append(head(st))
        elif t == "mix":
            cycles = g("cycles", MISSING)
            spec["steps"].append(head({"type": "mix", "pos": _pos(g("pos", MISSING), f"{p}.pos", errors),
                                       "wells": _wells(g("wells", MISSING), f"{p}.wells", errors), "vol": _vol(s, f"{p}.vol", errors),
                                       "cycles": _num(3 if cycles is MISSING else cycles, f"{p}.cycles", errors, 1, LIMITS["cycles"], integer=True, fallback=1)}))
        elif t == "discard":
            spec["steps"].append(head({"type": "discard", "pos": _pos(g("pos", MISSING), f"{p}.pos", errors),
                                       "wells": _wells(g("wells", MISSING), f"{p}.wells", errors), "vol": _vol(s, f"{p}.vol", errors)}))
        elif t == "shake":
            temp = g("temp", MISSING)
            sp = g("pos", MISSING)  # the plate to shake, by its starting position (default 1, the heater-shaker itself)
            spec["steps"].append({"type": "shake", "pos": _pos(HHS_POS if sp is MISSING or sp is None or sp == "" else sp, f"{p}.pos", errors), "temp": _num(0 if temp is MISSING else temp, f"{p}.temp", errors, 0, LIMITS["temp"]),
                                  "rpm": _num(g("rpm", MISSING), f"{p}.rpm", errors, 0, LIMITS["rpm"]),
                                  "minutes": _num(g("minutes", MISSING), f"{p}.minutes", errors, 0, LIMITS["minutes"])})
        elif t == "move":
            spec["steps"].append({"type": "move", "fromPos": _pos(g("fromPos", MISSING), f"{p}.fromPos", errors),
                                  "toPos": _pos(g("toPos", MISSING), f"{p}.toPos", errors)})
        else:
            st = {"type": "pause", "message": _text(g("message", MISSING), LIMITS["message"], f"{p}.message", errors, required=True)}
            mins = g("minutes", MISSING)   # a timed pause: wait this long by itself; left out, the run waits for Continue
            if not (mins is MISSING or mins is None or mins == ""):
                st["minutes"] = _num(mins, f"{p}.minutes", errors, 0.1, LIMITS["minutes"])
            spec["steps"].append(st)
        lab = g("label", MISSING)
        if not (lab is MISSING or lab is None or lab == "") and len(spec["steps"]) > before:
            spec["steps"][-1]["label"] = _text(lab, LIMITS["label"], f"{p}.label", errors)
    if variables:
        spec["variables"] = variables
    if len(spec["steps"]) == len(steps):  # the steps keep their "$name"
        for i, f, nm in refs:
            if i < len(spec["steps"]) and f in spec["steps"][i]:
                spec["steps"][i][f] = "$" + nm
    return spec, errors


def resolve(data, values=None, defaults=False):
    """Fill in a protocol's variables: `values` is {name: number}; a name left out takes its default only when `defaults` is true.
    Returns (spec, errors [text]): spec is a plain protocol (no variables, every "$name" replaced by its number) that has been
    through check() again, so a value that does not suit its field is caught here. spec is None when there are errors."""
    spec, errs = check(data)
    if spec is None or errs:
        return None, [f"{e['path']} {e['msg']}" if e["path"] else e["msg"] for e in errs]
    values = values if isinstance(values, dict) else {}
    errors, chosen = [], {}
    for v in spec.get("variables", []):
        given = values.get(v["name"], MISSING)
        if given is MISSING or given is None or given == "":
            if defaults:
                chosen[v["name"]] = v["default"]
            else:
                errors.append(f"{v['label']} needs a value")
            continue
        n = js_number(given) if isinstance(given, str) else given
        if not _is_number(n) or not math.isfinite(n):
            errors.append(f"{v['label']} must be a number")
        elif n < v["min"] or n > v["max"]:
            errors.append(f"{v['label']} must be between {v['min']:g} and {v['max']:g} {v['unit']}".strip())
        else:
            chosen[v["name"]] = int(n) if isinstance(n, float) and n.is_integer() else n
    if errors:
        return None, errors
    plain = json.loads(json.dumps(spec))
    plain.pop("variables", None)
    for st in plain["steps"]:
        for f in VAR_FIELDS:
            nm = ref_name(st.get(f))
            if nm is not None:
                st[f] = chosen[nm]
    again, errs = check(plain)
    if errs:
        return None, [f"{e['path']} {e['msg']}" if e["path"] else e["msg"] for e in errs]
    return again, []


def place_error(lw_key, pos, lib=None):
    """Why labware `lw_key` cannot be set down at deck position `pos` when the run starts ('' if it can). The heater-shaker starts empty."""
    lib = lib or lib_for()
    lw = lib.get(lw_key)
    if not lw:
        return ""
    if str(pos) == HHS_POS:
        return f"Position 1 is the heater-shaker (HHS) and always starts empty. A Shake step moves a plate onto it, so give {lw['label']} a home at another position."
    return land_error(lw_key, pos, lib)


def land_error(lw_key, pos, lib=None):
    """Why labware `lw_key` cannot be put at `pos` during the run, by a move or a shake ('' if it can)."""
    lib = lib or lib_for()
    lw = lib.get(lw_key)
    if not lw:
        return ""
    if str(pos) == HHS_POS and lw["kind"] != "plate":
        return f"Position 1 is the heater-shaker (HHS) and only takes plates, not {lw['label']}."
    if lw.get("only") and str(pos) not in lw["only"]:
        return f"The {lw['label']} only fits the bottom positions ({' and '.join(lw['only'])}), not position {pos}."
    return ""


_MAGNET_ALIGN = re.compile(r"_Magnet$|_RingStand$")
_PART = re.compile(r"([A-P])(\d{1,2})(?::([A-P])(\d{1,2}))?")


def magnet_misfit(plate_key, mapping=None, magnet_key="magnet"):
    """True when the plate does not list the magnet's alignment, so the Prep refuses "Move To/From Magnet" for it.
    `magnet_key` is the deck key of the magnet: the built-in "magnet" (the Ring Stand) or a catalogue magnet c_<id>. `mapping` is
    protocols/.labware-map.json ({key: catalogue name or id}), which may point a key at another magnet."""
    cat = labware_map.harvest()
    plate, magnet = (labware_map.resolve_key(cat, k, mapping)[0] for k in (plate_key, magnet_key))
    if not plate or plate.get("classification") != "plate" or not magnet:
        return False
    need = next((a for a in magnet.get("siteAlignments", []) if _MAGNET_ALIGN.search(a)), None)
    return need is not None and need not in plate.get("siteAlignments", [])


def _well_list(text):
    """'A1', 'A1:H1', 'A1,B2' -> [(row, col)] 0-based, column-major per range (as the Builder does); None when unreadable."""
    out = []
    for part in str(text).split(","):
        m = _PART.fullmatch(part.strip().upper())
        if not m:
            return None
        r1, c1 = ROWS_ALPHA.index(m.group(1)), int(m.group(2))
        r2, c2 = (ROWS_ALPHA.index(m.group(3)), int(m.group(4))) if m.group(3) else (r1, c1)
        out += [(r, c - 1) for c in range(min(c1, c2), max(c1, c2) + 1) for r in range(min(r1, r2), max(r1, r2) + 1)]
    return out


ROWS_ALPHA = "ABCDEFGHIJKLMNOP"


def _whole_columns(wells):
    """True when the wells are whole columns A-H, one column at a time (what the 8-channel head can pipette)."""
    return bool(wells) and len(wells) % 8 == 0 and all(r == n % 8 and c == wells[n - n % 8][1] for n, (r, c) in enumerate(wells))


def head_errors(spec, mapping=None):
    """Rules that need the deck as the steps play out (the JavaScript simulate() has them too).
    - Plate-to-plate transfers on 96-well plates run on the 8-channel head, which can only pipette whole columns (the 2-channel head
      is used when a 384-well plate is on the deck, and takes single wells).
    - A plate only goes onto the magnet if it lists the magnet's alignment.
    - A tube used as a source needs a liquid in `contents`."""
    lib, deck, out = lib_for(spec), spec["deck"], []
    rows = lambda k: 16 if k == "plate_384" else lib[k].get("rows", 8)
    eight = not any(rows(d["lw"]) > 8 for d in deck.values())
    occ = {p: p for p, d in deck.items() if lib[d["lw"]]["kind"] != "magnet"}
    kind = lambda p: lib[deck[occ[p]]["lw"]]["kind"] if p in occ else None
    loaded = {(c["pos"], w) for c in spec["contents"] for w in _well_list(c["wells"]) or []}
    for i, st in enumerate(spec["steps"]):
        t = st["type"]
        if t in ("transfer", "distribute") and kind(st["fromPos"]) == "tubes":
            empty = [w for w in _well_list(st["fromWells"]) or [] if (occ[st["fromPos"]], w) not in loaded]
            if empty:
                many = len(empty) > 1
                out.append(f"Step {i + 1}: Source tube{'s' if many else ''} {', '.join(ROWS_ALPHA[r] + str(c + 1) for r, c in empty)} at position {st['fromPos']} "
                           f"{'have' if many else 'has'} no liquid in the protocol's contents. Add the liquid to the Contents list: the Prep needs to know what each tube it draws from holds.")
        if t == "transfer" and (st["head"] == "eight" if "head" in st else eight) and kind(st["fromPos"]) == "plate" and kind(st["toPos"]) == "plate":
            for role, wells in (("Source", st["fromWells"]), ("Destination", st["toWells"])):
                if not _whole_columns(_well_list(wells) or []):
                    out.append(f"Step {i + 1}: {role} wells {wells} are not whole columns. A plate-to-plate transfer on 96-well plates uses the 8-channel head, which can only pipette whole columns (A-H of one column, e.g. A1:H1).")
        elif t == "shake" and st["pos"] != HHS_POS and st["pos"] in occ and HHS_POS not in occ:
            occ[HHS_POS] = occ.pop(st["pos"])
        elif t == "move" and st["fromPos"] in occ and st["toPos"] not in occ:
            f, to = st["fromPos"], st["toPos"]
            if to in deck and lib[deck[to]["lw"]]["kind"] == "magnet" and kind(f) == "plate" and magnet_misfit(deck[occ[f]]["lw"], mapping, deck[to]["lw"]):
                out.append(f"Step {i + 1}: {lib[deck[occ[f]]['lw']]['label']} does not fit the {lib[deck[to]['lw']]['label']}: the Prep only accepts plates that list the magnet's alignment. Pick a different magnet (the Prep's catalogue has several) or a compatible plate such as the 96 deep-well plate.")
            occ[to] = occ.pop(f)
    return out


def validate(data, mapping=None):
    """check() then the deck placement rules and the head/magnet rules. Returns {valid, spec, errors: [text]}; the spec is only usable when valid."""
    spec, errs = check(data)
    out = [f"{e['path']} {e['msg']}" if e["path"] else e["msg"] for e in errs]
    if spec is not None and not errs:
        out += [e for e in (place_error(d["lw"], pos, lib_for(spec)) for pos, d in spec["deck"].items()) if e]
        out += head_errors(spec, mapping)
    return {"valid": spec is not None and not out, "spec": spec, "errors": out}
