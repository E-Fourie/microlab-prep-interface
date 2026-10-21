"""
Turns a Builder protocol ("spec") into a raw Prep protocol the instrument can run.

    compile_spec(spec, folder=None) -> dict      the body for POST /protocols (the one-off upload path adds makeNameUnique)
    python3 compiler.py spec.json [folder]       prints the raw protocol, or why it cannot be compiled

It only ever emits what it can express faithfully; anything unsupported or ambiguous raises
ConverterUnavailable with the precise reason (nothing is guessed). See docs/CONVERTER.md for how each step is mapped.

Standard library only.
"""

import copy
import itertools
import json
import os
import sys

import labware_map
import spec_check

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS = "ABCDEFGHIJKLMNOP"
HHS = "1"


class ConverterUnavailable(Exception):
    pass


# Built-in labware sizes (spec_check's table only has labels and kinds).
DIMS = {"tips_50": (8, 12), "tips_300": (8, 12), "tips_1000": (8, 12), "plate_pcr": (8, 12), "plate_deep": (8, 12),
        "plate_flat_96": (8, 12), "plate_black_96": (8, 12), "plate_384": (16, 24), "reservoir_60": (5, 1),
        "reservoir_50": (5, 1), "trough_25": (1, 1), "tubes_24": (4, 6), "magnet": (1, 1)}

# ───────────── settings templates (copied in structure from a real exported protocol; ids 0 = "assigned by the Prep") ─────────────
ADD_REAGENT = {
    "multiDispenseSettings": {"id": 0, "usePreAliquot": True, "preAliquotSettings": {"id": 0, "volume": 20, "pipettingHeight": 40, "pipettingHeightStartPoint": "FromLiquidLevel", "sampleDiscardLocation": "Source"},
                              "usePostAliquot": True, "postAliquotSettings": {"id": 0, "volume": 20, "pipettingHeight": 40, "pipettingHeightStartPoint": "FromContainerBottom", "sampleDiscardLocation": "Source"}},
    "aspirateSettings": {"id": 0, "transferVolume": 0, "pipettingHeight": 2, "retractToWellTop": False, "retractDistance": 2, "pipettingHeightStartPoint": "FromLiquidLevel",
                         "mixVolume": 1, "mixCycles": 3, "mixSpeed": 0, "mixHeight": 2, "enableMad": False, "followLiquid": True, "mixHeightStartPoint": "FromLiquidLevel",
                         "offset": {"x": 0, "y": 0}, "useOffset": False},
    "dispenseSettings": {"id": 0, "transferVolume": 0, "pipettingHeight": 10, "retractToWellTop": False, "retractDistance": 2, "pipettingHeightStartPoint": "FromContainerBottom",
                         "mixVolume": 1, "mixCycles": 3, "mixSpeed": 0, "mixHeight": 2, "followLiquid": False, "mixHeightStartPoint": "FromLiquidLevel", "enableMad": False,
                         "offset": {"x": 0, "y": 0}, "useOffset": False},
}
TRANSFER = {  # plate to plate (the real protocol's "Move to plate" step)
    "aspirateSettings": {"id": 0, "transferVolume": 0, "pipettingHeight": 2, "retractToWellTop": False, "retractDistance": 2, "pipettingHeightStartPoint": "FromLiquidLevel",
                         "mixVolume": 1, "mixCycles": 3, "mixSpeed": 0, "mixHeight": 2, "enableMad": False, "followLiquid": True, "mixHeightStartPoint": "FromLiquidLevel",
                         "offset": {"x": 0, "y": 0}, "useOffset": False},
    "dispenseSettings": {"id": 0, "transferVolume": 0, "pipettingHeight": 5, "retractToWellTop": False, "retractDistance": 2, "pipettingHeightStartPoint": "FromContainerBottom",
                         "mixVolume": 1, "mixCycles": 3, "mixSpeed": 0, "mixHeight": 2, "followLiquid": False, "mixHeightStartPoint": "FromLiquidLevel", "enableMad": False,
                         "offset": {"x": 0, "y": 0}, "useOffset": False},
}
# Used when a step sets its own flow rates (customizeLiquid). Copied from the real protocol's Aqueous/Water step; only the
# two flow rates are ever changed.
CUSTOM_LIQUID = {"id": 0, "asFlowRate": 250, "asMixFlowRate": 120, "asBlowOutVolume": 45, "asSwapSpeed": 2, "asSettlingTime": 1, "asAirTransport": 5,
                 "dsFlowRate": 420, "dsMixFlowRate": 120, "dsBlowOutVolume": 45, "dsSwapSpeed": 2, "dsSettlingTime": 0, "dsAirTransport": 5,
                 "correctionCurve": [{"target": t, "value": v, "isUserAdded": False} for t, v in
                                     ((0, 0), (10, 13), (20, 24.2), (50, 55.7), (100, 107.1), (200, 208.9), (500, 514.3), (1000, 1022))],
                 "isUserLiquid": False}
OPT_KEYS = ("aspFlow", "dspFlow", "aspHeight", "dspHeight", "tipDiscard")
HARDWARE = {"hhs": False, "hhc": False, "hepaUv": False, "twoChannel": False, "mph": True, "siteOne": False}
REPORT = {"savePdf": False, "saveJson": False, "savePipettingCsv": False, "pipettingCsvPerStep": False, "reportPath": None}
SMALL_DISPENSE = 5            # uL: at or below this a multi-dispense uses SMALL_ALIQUOT instead of the 20 uL pre/post aliquot
SMALL_ALIQUOT = 5
DEFAULT_PAUSE_SECONDS = 120  # only a placeholder: a message pause has pauseForSetDuration false (a pause with `minutes` is a timed one)
DEFAULT_RPM = 1000            # kept when a step only heats (shake false)
DEFAULT_TEMP = 37             # kept when a step only shakes (heat false)


FIRST_GROUP_ID = 900001    # labware group ids: positive like the Prep's own exports (an id of 0 or below may read as "no group")
FIRST_ENTITY_ID = 910001   # every step, settings block and filled deck position gets its own positive id, as in an export


def _number_entities(o, counter):
    """Give each {"id": 0} in the protocol (steps, settings, filled positions) a unique positive id, in document order.
    Empty deck positions keep 0, as in the Prep's exports; labware objects keep the catalogue's ids."""
    if isinstance(o, dict):
        if "classification" in o and "containerName" in o:   # a catalogue labware object: leave alone
            return
        if o.get("id") == 0 and not (("labwareGroup" in o and o["labwareGroup"] is None) and not o.get("labwarePositions")):
            o["id"] = next(counter)
        for v in o.values():
            _number_entities(v, counter)
    elif isinstance(o, list):
        for v in o:
            _number_entities(v, counter)


def _num(v):
    return int(v) if float(v) == int(v) else v


def expand_wells(text, rows, cols, what):
    """'A1', 'A1:A6', 'A1:H1', 'A1,B2' -> [(row, col)] 1-based, column-major per range (as the Builder does)."""
    out = []
    for part in str(text).split(","):
        a, _, b = part.strip().partition(":")
        b = b or a
        pa, pb = (ROWS.index(a[0].upper()), int(a[1:])), (ROWS.index(b[0].upper()), int(b[1:]))
        for c in range(min(pa[1], pb[1]), max(pa[1], pb[1]) + 1):
            for r in range(min(pa[0], pb[0]), max(pa[0], pb[0]) + 1):
                if r + 1 > rows or c > cols:
                    raise ConverterUnavailable(f"{what}: well {ROWS[r]}{c} is outside the {rows}x{cols} labware")
                out.append((r + 1, c))
    return out


def _addr(wells):
    return [{"row": r, "column": c} for r, c in wells]


class _Compiler:
    def __init__(self, spec, folder):
        self.spec, self.folder = spec, folder
        self.lib = spec_check.lib_for(spec)
        self.catalogue = labware_map.harvest(folder)
        self.mapping = labware_map.read_map(folder)
        self.next_gid = FIRST_GROUP_ID
        self.groups = {}
        self.records = []        # emitted steps (dicts) in order
        self.last = None         # the record the previous spec step produced, while it can still grow or be mixed
        self.has_shake = False

    # ───────── labware ─────────
    def dims(self, key):
        d = self.lib[key]
        return (d["rows"], d["cols"]) if "rows" in d else DIMS[key]

    def kind(self, pos):
        return self.lib[self.spec["deck"][pos]["lw"]]["kind"]

    def resolve_liquids(self):
        """Every liquid in the protocol must be one of the Prep's liquid classes; it is stored under the Prep's own spelling."""
        for c in self.spec["contents"]:
            name = labware_map.find_liquid(c["liquid"])
            if name is None:
                raise ConverterUnavailable(f"Liquid '{c['liquid']}' (position {c['pos']}) is not a liquid class on the Prep. Use one of: " + ", ".join(labware_map.liquids()))
            c["liquid"] = name

    def liquid_at(self, pos, wells):
        """The liquid class loaded in the first source well, else Aqueous."""
        first = wells[0] if wells else None
        for c in self.spec["contents"]:
            if c["pos"] == pos and first and first in {(r, col) for r, col in expand_wells(c["wells"], *self.dims(self.spec["deck"][pos]["lw"]), "contents")}:
                return c["liquid"]
        return "Aqueous"

    def resolve_labware(self):
        used = sorted({d["lw"] for d in self.spec["deck"].values()})
        self.obj, bad = {}, []
        for key in used:
            o, ref = labware_map.resolve_key(self.catalogue, key, self.mapping)
            if ref is None:
                bad.append((key, None))
                continue
            if o is None:
                bad.append((key, ref))
                continue
            self.obj[key] = o
        if bad:
            lines = []
            for key, ref in bad:
                d = self.lib[key]
                rows, cols = self.dims(key)
                lw = {"kind": d["kind"], "rows": rows, "cols": cols}
                cand = labware_map.candidates(self.catalogue, lw)
                why = f"is mapped to '{ref}', which is not in the Prep's labware catalogue" if ref is not None else "has no counterpart in the Prep's labware catalogue"
                lines.append(f"  {key} ({d['label']}) {why}. Known {d['kind']} entries that could fit: "
                             + ("; ".join(labware_map.describe(o) for o in cand) if cand else "none (the Prep's catalogue has nothing of that type and shape)"))
            raise ConverterUnavailable(
                "Labware is not in the Prep's catalogue. Pick catalogue labware in the Builder, or map the key in "
                "protocols/.labware-map.json ({key: catalogue name or id}):\n" + "\n".join(lines))
        for key, o in self.obj.items():
            want = labware_map.CLASSES.get(self.lib[key]["kind"], ())
            if o["classification"] not in want:
                raise ConverterUnavailable(f"{key} is a {self.lib[key]['kind']} but its Hamilton labware {labware_map.describe(o)} is classified '{o['classification']}'")
            rows, cols = self.dims(key)
            if not isinstance(o["id"], int) or isinstance(o["id"], bool):
                raise ConverterUnavailable(f"{key} is mapped to {labware_map.describe(o)}, which has no Prep catalogue id.")
            if self.lib[key]["kind"] in ("tips", "plate", "tubes") and (o["rows"], o["columns"]) != (rows, cols):
                raise ConverterUnavailable(f"{key} is {rows}x{cols} but {labware_map.describe(o)} is {o['rows']}x{o['columns']}")

    def group(self, key, o, liquid=None, tag=None):
        gk = (o["id"], tag)
        if gk not in self.groups:
            gid, self.next_gid = self.next_gid, self.next_gid + 1
            lw = copy.deepcopy(o)
            if lw.get("classification") == "plate":   # the Prep's own plate objects carry these two keys (tip racks do not)
                lw = {"wellMap": {}, "disabledWells": [], **lw}
            self.groups[gk] = {"labware": lw, "id": gid, "groupName": (liquid or o["name"]), "reuseTipsBetweenSteps": False,
                               "liquidName": liquid or "", "liquidType": liquid or "", "liquidVolumeOptions": "CalculateForMe"}
        return self.groups[gk]

    def build_deck(self):
        deck, self.slot_group = [], {}
        liquids = {}
        for c in self.spec["contents"]:
            if c["pos"] in self.spec["deck"] and self.kind(c["pos"]) in ("reservoirs", "trough"):
                rows, cols = self.dims(self.spec["deck"][c["pos"]]["lw"])
                for r, col in expand_wells(c["wells"], rows, cols, f"contents at position {c['pos']}"):
                    liquids[(c["pos"], r - 1)] = c["liquid"]
        for n in range(1, 9):
            pos = str(n)
            e = {"labwarePositions": [], "id": 0, "labwareGroup": None, "position": n, "positionName": "", "liquidVolume": 0}
            d = self.spec["deck"].get(pos)
            if d:
                if pos == HHS:
                    raise ConverterUnavailable("Position 1 (the heater-shaker) holds labware in this protocol. In the Prep's own protocols position 1 stays empty "
                                               "and the Prep carries a plate there itself, so move the plate to positions 2 to 8 and name that position in the shake step.")
                o = self.obj[d["lw"]]
                kind = self.kind(pos)
                e["positionName"] = {"tiprack": "Tip Rack", "plate": "Plate", "trough": "Reservoir", "magnet": "Magnet"}.get(o["classification"], "Labware")
                e["labwareGroup"] = self.group(d["lw"], o)
                if kind in ("reservoirs", "trough"):
                    cont = labware_map.find(self.catalogue, o.get("containerName") or "")
                    if not cont:
                        raise ConverterUnavailable(f"{labware_map.describe(o)} (position {pos}) names no single container in the Prep's catalogue (containerName '{o.get('containerName', '')}'), "
                                                   "so its compartments cannot be described.")
                    for i in range(o["columns"]):
                        lq = liquids.get((pos, i))
                        g = self.group(d["lw"], cont, lq, tag=(pos, i) if lq else "empty")
                        self.slot_group[(pos, i)] = g
                        e["labwarePositions"].append({"row": 1, "column": i + 1, "id": 0, "labwareGroup": g, "position": i + 1, "positionName": "",
                                                      "liquidVolume": _num(cont["volume"]) if lq else 0})
            deck.append(e)
        return deck

    # ───────── steps ─────────
    def run(self):
        sp = self.spec
        self.resolve_liquids()
        self.resolve_labware()
        # a 384-well plate (16 rows) can only be reached by the two-channel head; 96-well work uses the eight-channel head (as in the Prep's own protocols)
        two = any(self.dims(d["lw"])[0] > 8 for d in sp["deck"].values())
        self.channels = "TwoChannel" if two else "EightChannel"
        deck = self.build_deck()
        # a plate/rack is named by the position it starts in; occ: physical position -> that name
        self.occ = {p: p for p, d in sp["deck"].items() if self.kind(p) != "magnet"}
        self.racks = sorted([{"pos": p, "vol": self.lib[d["lw"]]["tipVol"] if "tipVol" in self.lib[d["lw"]] else int(self.obj[d["lw"]]["volume"]),
                              "used": 0, "rows": self.dims(d["lw"])[0], "total": self.dims(d["lw"])[0] * self.dims(d["lw"])[1]}
                             for p, d in sp["deck"].items() if self.kind(p) == "tips"], key=lambda r: r["pos"])
        self.onhhs = None
        for i, s in enumerate(sp["steps"]):
            getattr(self, "step_" + s["type"])(s, i)
        steps = []
        for n, r in enumerate(self.records, 1):
            r["stepOrder"] = n
            steps.append(r)
        hw = dict(HARDWARE, hhs=self.has_shake, twoChannel=self.channels == "TwoChannel", mph=self.channels != "TwoChannel")
        out = {"name": sp["name"]}
        if sp.get("description"):
            out["description"] = sp["description"]
        out.update({"deck": {"id": 0, "positions": deck}, "hardwareRequirements": hw, "steps": steps, "reportSettings": dict(REPORT)})
        _number_entities(out["steps"], itertools.count(FIRST_ENTITY_ID))
        _number_entities(out["deck"]["positions"], itertools.count(FIRST_ENTITY_ID + 100000))
        return out

    def fail(self, i, msg):
        raise ConverterUnavailable(f"Step {i + 1} ({self.spec['steps'][i]['type']}): {msg}")

    def take(self, vol, i):
        avail = [r for r in self.racks if r["used"] < r["total"]]
        if not avail:
            self.fail(i, "out of tips")
        fit = sorted([r for r in avail if r["vol"] >= vol], key=lambda r: r["vol"]) or sorted(avail, key=lambda r: -r["vol"])
        r = fit[0]
        r["used"] += 1
        return self.group(self.spec["deck"][r["pos"]]["lw"], self.obj[self.spec["deck"][r["pos"]]["lw"]])["id"]

    def plate(self, pos, i, role):
        """(home id, rows, cols, kind) of the plate/tubes at physical position pos right now."""
        if pos == HHS:
            self.fail(i, f"{role} is on the heater-shaker; the Prep only reaches a plate there through a shake step")
        h = self.occ.get(pos)
        if h is None:
            self.fail(i, f"{role}: nothing at position {pos} at this point")
        d = self.spec["deck"][h]
        rows, cols = self.dims(d["lw"])
        return h, rows, cols, self.kind(h)

    def emit(self, rec, spec_i, sig, wells_key):
        """Add rec, or grow the previous record when it is the same kind of step and nothing else happened in between."""
        last = self.last
        if last and last["sig"] == sig and last["spec_i"] == spec_i - 1 and not (set(last["wells"]) & set(wells_key)) and not last["mixed"]:
            if rec["stepType"] == "AddReagentStep":
                last["rec"]["destinations"][0]["addresses"] += rec["destinations"][0]["addresses"]
            else:
                last["rec"]["sources"][0]["addresses"] += rec["sources"][0]["addresses"]
                last["rec"]["destinations"][0]["addresses"] += rec["destinations"][0]["addresses"]
            if rec["tipLabwareGroupId"] != last["rec"]["tipLabwareGroupId"]:
                self.fail(spec_i, "tips would come from different kinds of rack inside one Prep step")
            last["wells"] += list(wells_key)
            last["spec_i"] = spec_i
            return
        self.records.append(rec)
        self.last = {"rec": rec, "sig": sig, "spec_i": spec_i, "wells": list(wells_key), "mixed": False}

    @staticmethod
    def multi_dispense(vol):
        """The real protocol primes and finishes every multi-dispense with a 20 uL aliquot; for droplet-sized dispenses (<= 5 uL) that is far too much, so use 5 uL."""
        m = copy.deepcopy(ADD_REAGENT["multiDispenseSettings"])
        if vol <= SMALL_DISPENSE:
            m["preAliquotSettings"]["volume"] = m["postAliquotSettings"]["volume"] = SMALL_ALIQUOT
        return m

    @staticmethod
    def opts(s):
        """The optional pipetting settings of a transfer/distribute step, as a hashable value (part of what lets two steps merge)."""
        return tuple((k, s[k]) for k in OPT_KEYS if k in s)

    @staticmethod
    def apply_opts(s, rec):
        """Write the step's optional settings into the Prep step `rec`; with none given the record is unchanged."""
        a, d = rec["aspirateSettings"], rec["dispenseSettings"]
        if "aspHeight" in s:
            a["pipettingHeight"], a["pipettingHeightStartPoint"] = _num(s["aspHeight"]), "FromLiquidLevel"
        if "dspHeight" in s:
            d["pipettingHeight"], d["pipettingHeightStartPoint"] = _num(s["dspHeight"]), "FromContainerBottom"
        if "aspFlow" in s or "dspFlow" in s:
            c = copy.deepcopy(CUSTOM_LIQUID)
            if "aspFlow" in s:
                c["asFlowRate"] = _num(s["aspFlow"])
            if "dspFlow" in s:
                c["dsFlowRate"] = _num(s["dspFlow"])
            rec["customizeLiquid"], rec["customLiquidSettings"] = True, c
        if s.get("tipDiscard") == "rack":
            rec["tipDiscardLocation"] = "ReturnToRack"

    def step_transfer(self, s, i):
        self.pipette(s, i, s["type"] == "distribute")

    step_distribute = step_transfer

    def pipette(self, s, i, distribute):
        if s["fromPos"] not in self.occ:
            self.fail(i, f"source: nothing at position {s['fromPos']} at this point")
        src_pos = s["fromPos"]
        if src_pos == HHS:
            self.fail(i, "source is on the heater-shaker")
        dh, drows, dcols, dkind = self.plate(s["toPos"], i, "destination")
        sh = self.occ[src_pos]
        skind = self.kind(sh)
        vol = s["vol"]
        if skind in ("reservoirs", "trough"):
            if dkind != "plate":
                self.fail(i, f"from a reservoir the destination must be a plate, not {dkind}")
            slot_rows, slot_cols = self.dims(self.spec["deck"][sh]["lw"])
            slots = expand_wells(s["fromWells"], slot_rows, slot_cols, "source")
            dst = expand_wells(s["toWells"], drows, dcols, "destination")
            if distribute:
                pairs = [(slots[0], dst)]
                if len(slots) != 1:
                    self.fail(i, "distribute needs one source slot")
            else:
                if len(slots) != len(dst):
                    self.fail(i, "transfer needs equal numbers of source and destination wells")
                pairs = [(sl, [d]) for sl, d in zip(slots, dst)]
            if sh != src_pos:
                self.fail(i, "a reservoir cannot be moved")
            for slot, wells in pairs:
                g = self.slot_group.get((sh, slot[0] - 1))
                if g is None or not g["liquidName"]:
                    self.fail(i, f"reservoir slot {ROWS[slot[0] - 1]}{slot[1]} at position {sh} has no liquid in the protocol's contents")
                tip = self.take(vol, i)
                a, d = copy.deepcopy(ADD_REAGENT["aspirateSettings"]), copy.deepcopy(ADD_REAGENT["dispenseSettings"])
                a["transferVolume"] = d["transferVolume"] = _num(vol)
                rec = {"channelSelection": self.channels, "tipLabwareGroupId": tip, "tipLabwareDisplayName": None, "liquidLabwareGroupId": g["id"],
                       "newTipsEachTransfer": False, "mixBeforeAspirate": False, "mixAfterDispense": False, "multiDispense": True,
                       "tipReuseOption": "PerSample", "tipDiscardLocation": "DiscardToWaste",
                       "multiDispenseSettings": self.multi_dispense(vol), "aspirateSettings": a, "dispenseSettings": d,
                       "destinations": [{"position": int(dh), "addresses": _addr(wells)}], "customizeLiquid": False, "customLiquidSettings": None,
                       "stepType": "AddReagentStep", "id": 0, "name": g["liquidName"], "stepOrder": 0, "disabled": False}
                self.apply_opts(s, rec)
                self.emit(rec, i, ("add", sh, slot, dh, vol, self.opts(s)), wells)
            return
        if skind not in ("plate", "tubes") or dkind not in ("plate", "tubes"):
            self.fail(i, f"cannot transfer from {skind} to {dkind}")
        srows, scols = self.dims(self.spec["deck"][sh]["lw"])
        src = expand_wells(s["fromWells"], srows, scols, "source")
        dst = expand_wells(s["toWells"], drows, dcols, "destination")
        if distribute:
            # The Prep cannot aspirate once and multi-dispense from a plate, so one source well to many is one transfer step
            # that keeps its tip (newTipsEachTransfer False): one aspirate per destination, a single tip for the whole step.
            if len(src) != 1:
                self.fail(i, "distribute from a plate or tubes needs exactly one source well")
            src = src * len(dst)
        elif len(src) != len(dst):
            self.fail(i, "transfer needs equal numbers of source and destination wells")
        if self.channels == "EightChannel" and skind == dkind == "plate":
            for role, wells in (("source", src), ("destination", dst)):
                if not spec_check._whole_columns([(r - 1, c - 1) for r, c in wells]):
                    self.fail(i, f"the {role} wells are not whole columns. Plate-to-plate transfers on 96-well plates use the 8-channel head, which can only "
                                 "pipette whole columns (A-H of one column, e.g. A1:H1); the Prep refuses any other pattern. Select full columns, or put a 384-well plate "
                                 "on the deck to use the 2-channel head")
        tips = {self.take(vol, i) for _ in (src[:1] if distribute else src)}
        if len(tips) != 1:
            self.fail(i, "tips would come from different kinds of rack inside one step")
        a, d = copy.deepcopy(TRANSFER["aspirateSettings"]), copy.deepcopy(TRANSFER["dispenseSettings"])
        a["transferVolume"] = d["transferVolume"] = _num(vol)
        rec = {"channelSelection": self.channels, "tipLabwareGroupId": tips.pop(), "liquidType": self.liquid_at(sh, src), "newTipsEachTransfer": not distribute, "mixBeforeAspirate": False,
               "mixAfterDispense": False, "tipReuseOption": "PerSample", "tipDiscardLocation": "DiscardToWaste", "dispenseToWaste": False,
               "aspirateSettings": a, "dispenseSettings": d, "sources": [{"position": int(sh), "addresses": _addr(src)}],
               "destinations": [{"position": int(dh), "addresses": _addr(dst)}], "customizeLiquid": False, "customLiquidSettings": None,
               "stepType": "TransferSamplesStep", "id": 0, "name": "Transfer", "stepOrder": 0, "disabled": False}
        self.apply_opts(s, rec)
        self.emit(rec, i, ("xfer", sh, dh, vol, self.opts(s), distribute), dst)

    def step_mix(self, s, i):
        h, rows, cols, kind = self.plate(s["pos"], i, "mix wells")
        wells = expand_wells(s["wells"], rows, cols, "mix")
        for _ in wells:
            self.take(s["vol"], i)
        last = self.last
        if not (last and last["spec_i"] == i - 1 and last["rec"]["stepType"] == "TransferSamplesStep" and not last["mixed"]
                and last["rec"]["destinations"][0]["position"] == int(h)
                and {(a["row"], a["column"]) for a in last["rec"]["destinations"][0]["addresses"]} == set(wells)):
            self.fail(i, "a mix can only be expressed as mix-after-dispense on the plate-to-plate transfer step right before it, on exactly the wells that step filled. "
                         "(Mixing a reagent addition, or other wells, is not supported.)")
        d = last["rec"]["dispenseSettings"]
        last["rec"]["mixAfterDispense"] = True
        d["mixVolume"], d["mixCycles"] = _num(s["vol"]), s["cycles"]
        last["mixed"] = True
        self.last = last  # keep for adjacency only; "mixed" blocks further growth and a second mix

    def step_shake(self, s, i):
        self.last = None
        sp = s["pos"]
        if sp == HHS:
            if self.onhhs is None:
                self.fail(i, "no plate has been moved onto the heater-shaker (position 1), so there is nothing to shake")
            home = self.onhhs
        else:
            if self.occ.get(sp) != sp:
                self.fail(i, f"the plate at position {sp} is not at its starting position (nothing there, or the gripper moved it). Move it back first; the Prep shakes a plate from where it started")
            home = sp
        if self.kind(home) != "plate":
            self.fail(i, "only plates can be heated or shaken")
        if not (s["rpm"] > 0 or s["temp"] > 0) or s["minutes"] <= 0:
            self.fail(i, "needs a duration above 0 and a speed or a temperature")
        self.has_shake = True
        self.records.append({"platePosition": int(home), "duration": _num(round(s["minutes"] * 60, 3)), "heat": s["temp"] > 0, "shake": s["rpm"] > 0,
                             "heatStartOptions": "PauseStepUntilTemp", "rpm": _num(s["rpm"]) if s["rpm"] > 0 else DEFAULT_RPM,
                             "temperature": _num(s["temp"]) if s["temp"] > 0 else DEFAULT_TEMP,
                             "stepType": "HeatShakeStep", "id": 0, "name": "Heat/Shake Step", "stepOrder": 0, "disabled": False})

    def step_pause(self, s, i):
        self.last = None
        if s.get("minutes"):   # the Prep's timed pause: waits `duration` seconds by itself (its message is not shown, so the text goes in the name)
            self.records.append({"duration": _num(round(s["minutes"] * 60, 3)), "message": None, "pauseForSetDuration": True,
                                 "stepType": "PauseStep", "id": 0, "name": s["message"][:60], "stepOrder": 0, "disabled": False})
            return
        self.records.append({"duration": DEFAULT_PAUSE_SECONDS, "message": s["message"], "pauseForSetDuration": False,
                             "stepType": "PauseStep", "id": 0, "name": "Wait Step", "stepOrder": 0, "disabled": False})

    def step_move(self, s, i):
        self.last = None
        f, t = s["fromPos"], s["toPos"]
        deck = self.spec["deck"]
        home = self.occ.get(f)
        if home is None:
            self.fail(i, f"no plate at position {f} to move")
        if self.kind(home) != "plate":
            self.fail(i, "the gripper only moves plates")
        if t != HHS and t in self.occ:
            self.fail(i, f"position {t} is occupied")
        magnet = t in deck and self.kind(t) == "magnet"
        if t == HHS:
            if f == HHS or home != f:
                self.fail(i, "only a plate at its starting position can go to the heater-shaker (the Prep carries it there itself)")
            self.onhhs = home   # no step: the Prep moves the plate to the heater-shaker and back inside the shake step
            self.occ[HHS] = home
            del self.occ[f]
            return
        if f == HHS:
            self.onhhs = None
            if t == home:
                self.occ[t] = home
                del self.occ[HHS]
                return
            if not magnet:
                self.fail(i, "from the heater-shaker a plate can only go back to its starting position or onto the magnet")
        elif not (magnet and f == home) and not (t == home):
            self.fail(i, "the Prep's move step only carries a plate between its starting position and the magnet")
        mag_pos = t if magnet else (f if f in deck and self.kind(f) == "magnet" else None)
        if mag_pos is not None:   # the Prep only takes a plate onto a magnet whose alignment the plate lists
            plate_o, mag_o = self.obj[deck[home]["lw"]], self.obj[deck[mag_pos]["lw"]]
            need = [a for a in mag_o.get("siteAlignments", []) if spec_check._MAGNET_ALIGN.search(a)]
            if need and not set(need) & set(plate_o.get("siteAlignments", [])):
                self.fail(i, f"{labware_map.describe(plate_o)} is not compatible with the magnet {labware_map.describe(mag_o)} (the plate does not list {', '.join(need)}). "
                             "Use a plate that fits it, or map 'magnet' to a magnet the plate fits in protocols/.labware-map.json")
        self.records.append({"startPosition": int(home), "endPosition": int(t), "stepType": "TransportStep", "id": 0, "name": "Move To/From Magnet Step",
                             "stepOrder": 0, "disabled": False})
        del self.occ[f]
        self.occ[t] = home


def compile_spec(spec, folder=None, values=None):
    """A validated Builder protocol -> the raw Prep protocol (a new dict; `spec` is never changed).
    A protocol with variables needs `values` ({name: number}) for every one of them: they are filled in first."""
    if isinstance(spec, dict) and spec.get("variables"):
        spec, errs = spec_check.resolve(copy.deepcopy(spec), values)
        if errs:
            raise ConverterUnavailable("The protocol has variables that need a value: " + "; ".join(errs))
    folder = folder if folder is not None else os.path.join(HERE, "protocols")
    res = spec_check.validate(copy.deepcopy(spec), labware_map.read_map(folder))
    if not res["valid"]:
        raise ConverterUnavailable("The protocol does not pass the Builder check: " + "; ".join(res["errors"][:3]))
    return _Compiler(res["spec"], folder).run()


def main(argv):
    if len(argv) < 2:
        print("usage: python3 compiler.py spec.json [protocols-folder] [name=value ...]   (name=value fills a protocol variable)", file=sys.stderr)
        return 2
    values = dict(a.split("=", 1) for a in argv[2:] if "=" in a)
    argv = argv[:2] + [a for a in argv[2:] if "=" not in a]
    try:
        with open(argv[1], encoding="utf-8") as fh:
            spec = json.load(fh)
        print(json.dumps(compile_spec(spec, argv[2] if len(argv) > 2 else None, values), indent=2, ensure_ascii=False))
        return 0
    except ConverterUnavailable as e:
        print("Cannot compile: " + str(e), file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(f"Cannot read {argv[1]}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
