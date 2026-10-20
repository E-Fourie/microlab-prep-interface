"""
Maps the Builder's labware keys (tips_300, plate_deep, c_132, ...) to entries of the Prep's own labware catalogue, and
holds the liquid classes the Prep knows. Nothing outside these two files is ever offered or compiled.

  labware_catalogue.json   the labware the Prep lists (ids are the Prep's real ids)
  liquid_classes.json      the liquid classes the Prep lists
Which catalogue entry a spec key means:
  1. protocols/.labware-map.json  {specKey: catalogue name, displayName or id}   (optional override; edit it or PUT /local/labware-map)
  2. BUILTIN below (the Builder's built-in keys).
  3. c_<id>: the catalogue entry with that id (what the Builder's labware list produces).
Anything else is "unmapped": the compiler refuses and lists which known entries could fit.

Standard library only.
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOGUE_FILE = os.path.join(HERE, "labware_catalogue.json")
LIQUID_FILE = os.path.join(HERE, "liquid_classes.json")
MAP_FILE = ".labware-map.json"

# spec key -> catalogue `name`. reservoir_50 and trough_25 have no counterpart in the Prep's catalogue, so they are not offered.
BUILTIN = {
    "tips_50": "235966",
    "tips_300": "235902",
    "tips_1000": "235904",
    "plate_pcr": "P96w200jjxtwHAM",
    "plate_deep": "DW96w2000hmsr7HAM",
    "plate_flat_96": "P96w360ijet9COS",
    "plate_black_96": "P96w360dmqt1COS",
    "plate_384": "P384w112dm8qnCOS",
    "reservoir_60": "C5pREAGENTijbvxHAM",
    "tubes_24": "C24p1ckjvwHAM",
    "magnet": "C1p1fmsrtHAM",
}
CUSTOM_ID = re.compile(r"c_(\d{1,9})")

# spec kind -> catalogue classifications that can stand in for it (tubes: unknown, so any container-like entry)
CLASSES = {"tips": ("tiprack",), "plate": ("plate",), "reservoirs": ("trough",), "trough": ("trough", "container"),
           "tubes": ("carrier",), "magnet": ("magnet",)}


def is_labware(o):
    return (isinstance(o, dict) and isinstance(o.get("name"), str) and "classification" in o and "rows" in o
            and "columns" in o and "id" in o and ("displayName" in o or "volume" in o))


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


_cache = {}


def harvest(folder=None):
    """id -> labware object: the Prep's catalogue (the folder argument is kept for callers; nothing is read from it)."""
    if "cat" not in _cache:
        _cache["cat"] = {o["id"]: o for o in (_read(CATALOGUE_FILE) or {}).get("labware", []) if is_labware(o)}
    return _cache["cat"]


def liquids():
    """The Prep's liquid class names, in the Prep's order."""
    if "liq" not in _cache:
        _cache["liq"] = [x["name"] for x in (_read(LIQUID_FILE) or {}).get("liquids", []) if isinstance(x.get("name"), str)]
    return _cache["liq"]


def find_liquid(name):
    """The Prep's spelling of liquid class `name` (case-insensitive), or None."""
    s = str(name).strip().lower()
    return next((x for x in liquids() if x.lower() == s), None)


def resolve_key(catalogue, key, mapping=None):
    """The catalogue entry a spec labware key means, or (None, reference tried)."""
    ref = (mapping or {}).get(key, BUILTIN.get(key))
    if ref is None:
        m = CUSTOM_ID.fullmatch(key)
        ref = int(m.group(1)) if m else None
    return (find(catalogue, ref) if ref is not None else None), ref


# the built-in keys the Builder still offers: the magnet and the 5 x 60 mL reservoir rack are special kinds only a built-in key can express
OFFERED_BUILTIN = ("reservoir_60", "magnet")


def builder_defs(catalogue=None):
    """What the Builder may offer, from the Prep's catalogue only:
    ({c_<id>: definition}, {built-in key: catalogue display name}). Plates, tip racks and 24-position tube racks are c_<id> entries;
    the magnet and the 5 x 60 mL reservoir are the two built-in keys, labelled with their catalogue names."""
    cat = catalogue or harvest()
    builtin = {}
    for k in OFFERED_BUILTIN:
        o = find(cat, BUILTIN[k])
        if o:
            builtin[k] = str(o.get("displayName") or o["name"])
    out = {}
    for o in sorted(cat.values(), key=lambda o: str(o.get("displayName") or o["name"]).lower()):
        label = str(o.get("displayName") or o["name"]).strip()
        label = label if len(label) <= 60 else label[:59] + "…"
        c, r = o["classification"], (o["rows"], o["columns"])
        if c == "plate" and r[0] * r[1] > 1:
            out[f"c_{o['id']}"] = {"label": label, "kind": "plate", "rows": r[0], "cols": r[1], "cap": o["volume"] or 1}
        elif c == "tiprack":
            out[f"c_{o['id']}"] = {"label": label, "kind": "tips", "rows": r[0], "cols": r[1], "tipVol": o["volume"]}
        elif c == "carrier" and r == (4, 6):
            out[f"c_{o['id']}"] = {"label": label, "kind": "tubes", "rows": r[0], "cols": r[1], "cap": 1500}
    return out, builtin


def read_map(folder):
    data = _read(os.path.join(folder, MAP_FILE)) if folder else None
    return {str(k): v for k, v in data.items()} if isinstance(data, dict) else {}


def write_map(folder, mapping):
    clean = {}
    for k, v in mapping.items():
        if not isinstance(k, str) or not k or not isinstance(v, (str, int)) or isinstance(v, bool) or not str(v).strip():
            raise ValueError("the map is {specKey: Hamilton labware name or id}")
        clean[k] = v if isinstance(v, int) else v.strip()
    path = os.path.join(folder, MAP_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return clean


def find(catalogue, ref):
    """A catalogue entry by id, name or displayName (case-insensitive), or None."""
    s = str(ref).strip().lower()
    for o in catalogue.values():
        if str(o["id"]) == s or str(o.get("name", "")).lower() == s or str(o.get("displayName", "")).lower() == s:
            return o
    return None


def candidates(catalogue, lw):
    """Known entries that could stand in for spec labware definition `lw` ({kind, rows, cols})."""
    out = []
    for o in catalogue.values():
        if o["classification"] not in CLASSES.get(lw["kind"], ()):
            continue
        if lw["kind"] in ("tips", "plate") and (o["rows"], o["columns"]) != (lw["rows"], lw["cols"]):
            continue
        out.append(o)
    return sorted(out, key=lambda o: str(o.get("displayName") or o["name"]))


def describe(o):
    return f"{o.get('displayName') or o['name']} (name {o['name']}, id {o['id']})"
