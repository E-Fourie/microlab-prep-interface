"""Spec -> raw Prep protocol converter (compiler.py, labware_map.py). No instrument, no network.

The comparison with the lab's real protocol runs only when tests/fixtures/real_bead_purification.json exists
(it is private to the lab and never committed); everything else works without it.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import compiler  # noqa: E402
import labware_map  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
REAL = os.path.join(FIX, "real_bead_purification.json")
SPEC = json.load(open(os.path.join(FIX, "bead_purification_spec.json"), encoding="utf-8"))

DECK = {"2": {"lw": "tips_300"}, "3": {"lw": "plate_deep"}, "4": {"lw": "reservoir_60"}, "5": {"lw": "magnet"}, "7": {"lw": "plate_flat_96"}}
CONTENTS = [{"pos": "4", "wells": "A1", "liquid": "Aqueous", "vol": 40000}, {"pos": "4", "wells": "B1", "liquid": "Magnetic Beads", "vol": 40000}]


def spec(steps, deck=None, **kw):
    return {"specVersion": 1, "name": "T", "deck": deck or DECK, "contents": CONTENTS, "steps": steps, **kw}


def dist(slot="A1", wells="A1:H1", vol=10, to="3"):
    return {"type": "distribute", "fromPos": "4", "fromWells": slot, "toPos": to, "toWells": wells, "vol": vol}


def xfer(src="3", s="A1:H1", dst="7", d="A1:H1", vol=20):
    return {"type": "transfer", "fromPos": src, "fromWells": s, "toPos": dst, "toWells": d, "vol": vol}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def c(self, s):
        return compiler.compile_spec(s, self.dir)

    def fails(self, s, *words):
        with self.assertRaises(compiler.ConverterUnavailable) as cx:
            self.c(s)
        for w in words:
            self.assertIn(w.lower(), str(cx.exception).lower())
        return str(cx.exception)


class BeadPurification(Base):
    def setUp(self):
        super().setUp()
        self.raw = self.c(SPEC)

    def test_step_sequence(self):
        self.assertEqual([s["stepType"] for s in self.raw["steps"]],
                         ["AddReagentStep", "HeatShakeStep", "AddReagentStep", "HeatShakeStep", "PauseStep", "AddReagentStep", "HeatShakeStep", "PauseStep",
                          "AddReagentStep", "HeatShakeStep", "PauseStep", "TransportStep", "PauseStep", "AddReagentStep", "AddReagentStep", "TransportStep",
                          "AddReagentStep", "HeatShakeStep", "PauseStep", "PauseStep", "TransportStep", "TransferSamplesStep"])
        self.assertEqual([s["stepOrder"] for s in self.raw["steps"]], list(range(1, 23)))

    def test_steps_and_groups_have_unique_positive_ids(self):
        ids = [s["id"] for s in self.raw["steps"]]
        self.assertTrue(all(i > 0 for i in ids) and len(set(ids)) == len(ids))
        groups = {p["labwareGroup"]["id"] for p in self.raw["deck"]["positions"] if p["labwareGroup"]}
        self.assertTrue(all(g > 0 for g in groups))
        self.assertTrue(all(s["tipLabwareGroupId"] in groups for s in self.raw["steps"] if "tipLabwareGroupId" in s))
        self.assertTrue(all(s["disabled"] is False for s in self.raw["steps"]))

    def test_positions_use_home_ids(self):
        st = self.raw["steps"]
        self.assertEqual([(s["startPosition"], s["endPosition"]) for s in st if s["stepType"] == "TransportStep"], [(3, 5), (3, 3), (3, 5)])
        self.assertEqual({s["platePosition"] for s in st if s["stepType"] == "HeatShakeStep"}, {3})
        for s in st:
            if s["stepType"] == "AddReagentStep":
                self.assertEqual([d["position"] for d in s["destinations"]], [3])  # also while the plate is on the magnet (physical 5)
        last = st[-1]
        self.assertEqual((last["sources"][0]["position"], last["destinations"][0]["position"]), (3, 7))

    def test_addresses_one_based_column_major(self):
        a = self.raw["steps"][0]["destinations"][0]["addresses"]
        self.assertEqual(len(a), 96)
        self.assertEqual(a[:3], [{"row": 1, "column": 1}, {"row": 2, "column": 1}, {"row": 3, "column": 1}])
        self.assertEqual(a[-1], {"row": 8, "column": 12})

    def test_durations_in_seconds(self):
        hs = [s for s in self.raw["steps"] if s["stepType"] == "HeatShakeStep"]
        self.assertEqual([s["duration"] for s in hs], [180, 15, 45, 45, 180])
        self.assertTrue(all(s["shake"] and not s["heat"] for s in hs))
        self.assertTrue(self.raw["hardwareRequirements"]["hhs"])

    def test_group_references_resolve(self):
        pos = self.raw["deck"]["positions"]
        ids = {}
        for p in pos:
            if p["labwareGroup"]:
                ids[p["labwareGroup"]["id"]] = p["labwareGroup"]
            for lp in p["labwarePositions"]:
                ids[lp["labwareGroup"]["id"]] = lp["labwareGroup"]
        racks = {p["labwareGroup"]["id"] for p in pos if p["labwareGroup"] and p["labwareGroup"]["labware"]["classification"] == "tiprack"}
        self.assertEqual(len(racks), 1)  # identical racks share one group
        liquids = []
        for s in self.raw["steps"]:
            if "tipLabwareGroupId" in s:
                self.assertIn(s["tipLabwareGroupId"], racks)
            if s["stepType"] == "AddReagentStep":
                liquids.append(ids[s["liquidLabwareGroupId"]]["liquidName"])
                self.assertEqual(s["name"], liquids[-1])
        self.assertEqual(liquids, ["Aqueous", "Aqueous with Detergent", "Aqueous", "Magnetic Beads", "Ethanol 70%", "Ethanol 70%", "Aqueous"])

    def test_deck_shape(self):
        pos = self.raw["deck"]["positions"]
        self.assertEqual([p["position"] for p in pos], list(range(1, 9)))
        p1 = pos[0]
        self.assertEqual((p1["labwareGroup"], p1["id"], p1["labwarePositions"]), (None, 0, []))
        car = pos[3]
        self.assertEqual(len(car["labwarePositions"]), 5)
        self.assertEqual([lp["labwareGroup"]["liquidName"] for lp in car["labwarePositions"]], ["Aqueous", "Aqueous with Detergent", "Aqueous", "Magnetic Beads", "Ethanol 70%"])
        self.assertEqual(car["labwarePositions"][0]["labwareGroup"]["labware"]["id"], 58)
        self.assertEqual(car["labwareGroup"]["labware"]["id"], 57)
        self.assertEqual([p["labwareGroup"]["labware"]["id"] if p["labwareGroup"] else None for p in pos], [None, 7, 93, 57, 43, 7, 225, 57])
        w = pos[7]["labwarePositions"]
        self.assertEqual([lp["labwareGroup"]["liquidName"] for lp in w], ["Aqueous", "", "", "", ""])

    def test_top_level(self):
        self.assertNotIn("id", self.raw)
        self.assertEqual(self.raw["name"], SPEC["name"])
        self.assertTrue(set(self.raw) >= {"name", "deck", "hardwareRequirements", "steps", "reportSettings"})
        json.dumps(self.raw)

    def test_no_mutation_and_deterministic(self):
        s = copy.deepcopy(SPEC)
        a = compiler.compile_spec(s, self.dir)
        self.assertEqual(s, SPEC)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(compiler.compile_spec(s, self.dir), sort_keys=True))

    @unittest.skipUnless(os.path.exists(REAL), "the lab's real protocol is not in this checkout")
    def test_structure_matches_real_protocol(self):
        real = json.load(open(REAL, encoding="utf-8"))
        keys = {}
        for s in real["steps"]:
            keys.setdefault(s["stepType"], set(s))
        for s in self.raw["steps"]:
            self.assertEqual(set(s), keys[s["stepType"]], s["stepType"])
        rp = real["deck"]["positions"]
        for mine, theirs in zip(self.raw["deck"]["positions"], rp):
            self.assertEqual(set(mine), set(theirs))
            if theirs["labwareGroup"]:
                self.assertEqual(set(mine["labwareGroup"]), set(theirs["labwareGroup"]))
                self.assertEqual(set(mine["labwareGroup"]["labware"]), set(theirs["labwareGroup"]["labware"]))  # the catalogue's own copy may differ in detail
            for a, b in zip(mine["labwarePositions"], theirs["labwarePositions"]):
                self.assertEqual(set(a), set(b))
                self.assertEqual(set(a["labwareGroup"]), set(b["labwareGroup"]))
                self.assertEqual(set(a["labwareGroup"]["labware"]), set(b["labwareGroup"]["labware"]))
        self.assertEqual(set(self.raw["hardwareRequirements"]), set(real["hardwareRequirements"]))
        self.assertEqual(self.raw["hardwareRequirements"], dict(real["hardwareRequirements"], twoChannel=False, mph=True))  # 96-well work: eight-channel head
        self.assertTrue(all(s["channelSelection"] == "EightChannel" for s in self.raw["steps"] if "channelSelection" in s))
        self.assertEqual(set(self.raw["reportSettings"]), set(real["reportSettings"]))
        # nested settings blocks have the same keys too
        ra = next(s for s in real["steps"] if s["stepType"] == "AddReagentStep")
        ma = next(s for s in self.raw["steps"] if s["stepType"] == "AddReagentStep")
        for k in ("aspirateSettings", "dispenseSettings", "multiDispenseSettings"):
            self.assertEqual(set(ma[k]), set(ra[k]), k)
        rt = real["steps"][-1]
        mt = self.raw["steps"][-1]
        for k in ("aspirateSettings", "dispenseSettings"):
            self.assertEqual(set(mt[k]), set(rt[k]), k)


class Mapping(Base):
    def test_distribute_to_address_list(self):
        r = self.c(spec([dist(slot="B1", wells="A1:C1", vol=7)]))
        (s,) = r["steps"]
        self.assertEqual(s["stepType"], "AddReagentStep")
        self.assertEqual(s["aspirateSettings"]["transferVolume"], 7)
        self.assertEqual(s["dispenseSettings"]["transferVolume"], 7)
        self.assertEqual(s["name"], "Magnetic Beads")
        self.assertEqual(s["destinations"], [{"position": 3, "addresses": [{"row": 1, "column": 1}, {"row": 2, "column": 1}, {"row": 3, "column": 1}]}])

    def test_grouping(self):
        r = self.c(spec([dist(wells="A1:H1"), dist(wells="A2:H2"), dist(wells="A3:H3", vol=11), dist(slot="B1", wells="A4:H4", vol=11)]))
        self.assertEqual(len(r["steps"]), 3)
        self.assertEqual(len(r["steps"][0]["destinations"][0]["addresses"]), 16)
        self.assertEqual(r["steps"][0]["destinations"][0]["addresses"][8], {"row": 1, "column": 2})

    def test_no_grouping_across_other_steps_or_repeated_wells(self):
        self.assertEqual(len(self.c(spec([dist(), {"type": "pause", "message": "x"}, dist(wells="A2:H2")]))["steps"]), 3)
        self.assertEqual(len(self.c(spec([dist(), dist()]))["steps"]), 2)

    def test_transfer_from_reservoir_pairs_per_slot(self):
        r = self.c(spec([{"type": "transfer", "fromPos": "4", "fromWells": "A1:B1", "toPos": "3", "toWells": "A1:B1", "vol": 5}]))
        self.assertEqual([s["name"] for s in r["steps"]], ["Aqueous", "Magnetic Beads"])

    def test_plate_to_plate(self):
        r = self.c(spec([xfer(s="A1:B1", d="C1:D1")]))
        (s,) = r["steps"]
        self.assertEqual(s["stepType"], "TransferSamplesStep")
        self.assertFalse(s["dispenseToWaste"])
        self.assertEqual([a["row"] for a in s["sources"][0]["addresses"]], [1, 2])
        self.assertEqual([a["row"] for a in s["destinations"][0]["addresses"]], [3, 4])
        self.assertEqual((s["sources"][0]["position"], s["destinations"][0]["position"]), (3, 7))
        self.assertEqual(len(self.c(spec([xfer(s="A1:B1", d="A1:B1"), xfer(s="A2:B2", d="A2:B2")]))["steps"]), 1)

    def test_pipetting_options(self):
        o = {"aspFlow": 100, "dspFlow": 200, "aspHeight": -1, "dspHeight": 3, "tipDiscard": "rack"}
        (s,) = self.c(spec([{**xfer(s="A1:B1", d="A1:B1"), **o}]))["steps"]
        self.assertTrue(s["customizeLiquid"])
        self.assertEqual((s["customLiquidSettings"]["asFlowRate"], s["customLiquidSettings"]["dsFlowRate"]), (100, 200))
        self.assertEqual((s["aspirateSettings"]["pipettingHeight"], s["aspirateSettings"]["pipettingHeightStartPoint"]), (-1, "FromLiquidLevel"))
        self.assertEqual((s["dispenseSettings"]["pipettingHeight"], s["dispenseSettings"]["pipettingHeightStartPoint"]), (3, "FromContainerBottom"))
        self.assertEqual(s["tipDiscardLocation"], "ReturnToRack")
        (a,) = self.c(spec([{**dist(), "dspFlow": 50}]))["steps"]
        self.assertEqual((a["customLiquidSettings"]["asFlowRate"], a["customLiquidSettings"]["dsFlowRate"]), (250, 50))
        self.assertEqual(a["tipDiscardLocation"], "DiscardToWaste")

    def test_pipetting_options_stop_steps_merging(self):
        a, b = xfer(s="A1:B1", d="A1:B1"), {**xfer(s="A2:B2", d="A2:B2"), "aspFlow": 100}
        self.assertEqual(len(self.c(spec([a, b]))["steps"]), 2)
        self.assertNotIn("aspFlow", json.dumps(self.c(spec([a]))))

    def test_pause_and_shake_variants(self):
        r = self.c(spec([{"type": "pause", "message": "Seal"}, {"type": "shake", "pos": "3", "temp": 37, "rpm": 0, "minutes": 2},
                         {"type": "shake", "pos": "3", "temp": 40, "rpm": 800, "minutes": 1.5}]))
        p, h1, h2 = r["steps"]
        self.assertEqual((p["message"], p["pauseForSetDuration"]), ("Seal", False))
        self.assertEqual((h1["heat"], h1["shake"], h1["duration"], h1["temperature"]), (True, False, 120, 37))
        self.assertEqual((h2["heat"], h2["shake"], h2["duration"], h2["rpm"]), (True, True, 90, 800))

    def test_no_shake_no_hhs(self):
        self.assertFalse(self.c(spec([dist()]))["hardwareRequirements"]["hhs"])

    def test_tip_rack_choice(self):
        deck = dict(DECK, **{"6": {"lw": "tips_300"}})
        steps = [dist(wells="A1:H1")] + [dist(slot="B1", wells="A%d:H%d" % (c, c)) for c in range(2, 13)]
        r = self.c(spec(steps, deck=deck))
        gids = {s["tipLabwareGroupId"] for s in r["steps"]}
        self.assertEqual(len(gids), 1)  # both racks are the same labware, so one group

    def test_hhs_round_trip_is_absorbed(self):
        r = self.c(spec([{"type": "move", "fromPos": "3", "toPos": "1"}, {"type": "shake", "temp": 0, "rpm": 500, "minutes": 1},
                         {"type": "move", "fromPos": "1", "toPos": "3"}]))
        self.assertEqual([s["stepType"] for s in r["steps"]], ["HeatShakeStep"])
        self.assertEqual(r["steps"][0]["platePosition"], 3)

    def test_mix_after_transfer(self):
        r = self.c(spec([xfer(s="A1:B1", d="A1:B1"), {"type": "mix", "pos": "7", "wells": "A1:B1", "vol": 15, "cycles": 4}]))
        (s,) = r["steps"]
        self.assertTrue(s["mixAfterDispense"])
        self.assertEqual((s["dispenseSettings"]["mixVolume"], s["dispenseSettings"]["mixCycles"]), (15, 4))

    def test_mix_unsupported_cases(self):
        self.fails(spec([{"type": "mix", "pos": "3", "wells": "A1", "vol": 5, "cycles": 3}]), "step 1", "mix")
        self.fails(spec([dist(), {"type": "mix", "pos": "3", "wells": "A1:H1", "vol": 5, "cycles": 3}]), "step 2", "mix")
        self.fails(spec([xfer(s="A1:B1", d="A1:B1"), {"type": "mix", "pos": "7", "wells": "A1", "vol": 5, "cycles": 3}]), "mix")


class Errors(Base):
    def test_unmapped_labware_lists_keys_and_candidates(self):
        deck = dict(DECK, **{"6": {"lw": "plate_pcr"}, "8": {"lw": "reservoir_60"}})
        labware_map.write_map(self.dir, {"reservoir_60": "no such labware"})  # a key mapped to something the Prep does not list
        msg = self.fails(spec([dist()], deck=deck), "reservoir_60", ".labware-map.json")
        self.assertNotIn("plate_pcr (", msg)  # plate_pcr maps to the catalogue
        self.assertIn("Hamilton, 5 x 60mL Reagent Reservoir", msg)  # a known reservoir is offered

    def test_mapping_file_and_catalogue_keys(self):
        deck = dict(DECK, **{"6": {"lw": "plate_pcr"}})
        s = spec([dist(), xfer(dst="6")], deck=deck)
        self.assertEqual(self.c(s)["deck"]["positions"][5]["labwareGroup"]["labware"]["name"], "P96w200jjxtwHAM")
        labware_map.write_map(self.dir, {"plate_pcr": "DW96w2000hmsr7HAM"})  # override with another catalogue entry of the same shape
        self.assertEqual(self.c(s)["deck"]["positions"][5]["labwareGroup"]["labware"]["id"], 93)
        labware_map.write_map(self.dir, {"plate_pcr": "NoSuchThing"})
        self.fails(s, "NoSuchThing")
        labware_map.write_map(self.dir, {"plate_pcr": 9999})
        self.fails(s, "9999")
        # a labware list entry from the Builder: c_<catalogue id>
        defs, _ = labware_map.builder_defs()
        self.assertIn("c_144", defs)
        s2 = spec([dist()], deck=dict(DECK, **{"6": {"lw": "c_144"}}), labware={"c_144": defs["c_144"]})
        self.assertEqual(self.c(s2)["deck"]["positions"][5]["labwareGroup"]["labware"]["id"], 144)

    def test_variables_are_filled_in_before_compiling(self):
        var = [{"name": "beads", "label": "Bead volume", "default": 20, "min": 5, "max": 100}]
        s = spec([dist(vol="$beads")], variables=var)
        raw = self.c(s) if False else compiler.compile_spec(s, self.dir, {"beads": 55})
        self.assertEqual(raw["steps"][0]["aspirateSettings"]["transferVolume"], 55)
        self.assertNotIn("variables", raw)
        self.fails(s, "variables", "needs a value")
        with self.assertRaises(compiler.ConverterUnavailable):
            compiler.compile_spec(s, self.dir, {"beads": 500})

    def test_liquids_must_be_prep_liquid_classes(self):
        bad = spec([dist()])
        bad["contents"] = [dict(CONTENTS[0], liquid="dyed water")]
        self.fails(bad, "liquid classes")
        ok = spec([dist()])
        ok["contents"] = [dict(CONTENTS[0], liquid="aqueous")]
        self.assertEqual(self.c(ok)["steps"][0]["name"], "Aqueous")

    def test_mapping_checks_shape_and_class(self):
        deck = dict(DECK, **{"6": {"lw": "plate_384"}})
        labware_map.write_map(self.dir, {"plate_384": "DW96w2000hmsr7HAM"})
        self.fails(spec([dist()], deck=deck), "16x24")
        labware_map.write_map(self.dir, {"plate_384": "235902"})
        self.fails(spec([dist()], deck=deck), "classified")

    def test_plate_moved_away_then_shaken(self):
        self.fails(spec([{"type": "move", "fromPos": "3", "toPos": "5"}, {"type": "shake", "pos": "3", "rpm": 500, "minutes": 1}]), "starting position")

    def test_invalid_spec(self):
        self.fails({"name": "x"}, "check")

    def test_plate_in_position_one(self):
        self.fails(spec([dist(to="1")], deck=dict(DECK, **{"1": {"lw": "plate_deep"}})), "position 1")

    def test_unsupported_moves(self):
        self.fails(spec([{"type": "move", "fromPos": "3", "toPos": "6"}]), "starting position and the magnet")
        self.fails(spec([{"type": "move", "fromPos": "3", "toPos": "5"}, {"type": "move", "fromPos": "5", "toPos": "6"}]), "step 2")

    def test_unsupported_transfers(self):
        self.fails(spec([{"type": "distribute", "fromPos": "3", "fromWells": "A1", "toPos": "7", "toWells": "A1:C1", "vol": 5}]), "distribute")
        self.fails(spec([{"type": "transfer", "fromPos": "3", "fromWells": "A1", "toPos": "4", "toWells": "A1", "vol": 5}]), "step 1")

    def test_reservoir_slot_without_liquid(self):
        self.fails(spec([dist(slot="C1")]), "no liquid")

    def test_shake_on_hhs_without_plate(self):
        self.fails(spec([{"type": "shake", "temp": 0, "rpm": 500, "minutes": 1}]), "step 1")


class MapFile(Base):
    def test_roundtrip_and_validation(self):
        self.assertEqual(labware_map.read_map(self.dir), {})
        self.assertEqual(labware_map.write_map(self.dir, {"a": " X ", "b": 5}), {"a": "X", "b": 5})
        self.assertEqual(labware_map.read_map(self.dir), {"a": "X", "b": 5})
        with self.assertRaises(ValueError):
            labware_map.write_map(self.dir, {"a": ""})
        with self.assertRaises(ValueError):
            labware_map.write_map(self.dir, {"a": ["x"]})


class Endpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socket, subprocess, time, urllib.request
        sk = socket.socket(); sk.bind(("127.0.0.1", 0)); cls.port = sk.getsockname()[1]; sk.close()
        cls.dir = tempfile.mkdtemp()
        env = {k: v for k, v in os.environ.items() }
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--port", str(cls.port), "--protocols", cls.dir],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{cls.port}/local/labware-map", headers={"X-Prep-UI": "1"})).read()
                break
            except Exception:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(); shutil.rmtree(cls.dir, ignore_errors=True)

    def call(self, method, body=None):
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/local/labware-map", method=method, headers={"X-Prep-UI": "1"}, data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_get_put(self):
        g = self.call("GET")
        self.assertEqual(g["map"], {})
        self.assertIn(93, [o["id"] for o in g["known"]])
        self.assertEqual(self.call("PUT", {"plate_pcr": "93"})["map"], {"plate_pcr": "93"})
        self.assertEqual(self.call("GET")["map"], {"plate_pcr": "93"})
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError):
            self.call("PUT", ["nope"])

    def test_compile_with_variable_values(self):
        import urllib.error, urllib.request
        example = os.path.join(ROOT, "web", "example_bead_cleanup_variable.json")
        shutil.copy(example, os.path.join(self.dir, "Bead clean-up.json"))

        def compile_(values):
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/local/compile", method="POST", headers={"X-Prep-UI": "1"},
                                         data=json.dumps({"name": "Bead clean-up.json", "values": values}).encode())
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        code, body = compile_({"beads": 120, "elute": 40})
        self.assertEqual(code, 200)
        self.assertEqual([x["aspirateSettings"]["transferVolume"] for x in body["raw"]["steps"] if x["stepType"] == "AddReagentStep"], [120, 40])
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/local/compile", method="POST", headers={"X-Prep-UI": "1"},
                                     data=json.dumps({"spec": json.load(open(example, encoding="utf-8")), "values": {"beads": 50, "elute": 20}}).encode())
        with urllib.request.urlopen(req) as r:  # a protocol that is not in the library (a dropped file) compiles from the spec itself
            self.assertEqual(json.loads(r.read())["raw"]["steps"][0]["aspirateSettings"]["transferVolume"], 50)
        code, body = compile_({"beads": 120})  # a value missing
        self.assertEqual((code, body["error"]), (400, "variables"))
        self.assertIn("Elution volume per sample needs a value", body["detail"])
        code, body = compile_({"beads": 5000, "elute": 40})  # out of range
        self.assertEqual(code, 400)
        self.assertIn("between 10 and 200", body["detail"])


if __name__ == "__main__":
    unittest.main()
