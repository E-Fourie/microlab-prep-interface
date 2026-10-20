"""spec_check.py against the shared vectors plus Python-only cases.

    python3 -m unittest discover -s tests -v
"""
import copy
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import spec_check  # noqa: E402

with open(os.path.join(ROOT, "tests", "vectors", "prep-protocol.json"), encoding="utf-8") as fh:
    VECTORS = json.load(fh)

GOOD = {"name": "Good", "deck": {"3": {"lw": "plate_pcr", "label": "P"}, "2": {"lw": "tips_300"}, "4": {"lw": "reservoir_60"}},
        "contents": [{"pos": "4", "wells": "a1", "liquid": "aqueous", "vol": 1000}],
        "steps": [{"type": "transfer", "fromPos": "4", "fromWells": "A1", "toPos": "3", "toWells": "A1", "vol": 10}]}


def mutated(**kw):
    d = copy.deepcopy(GOOD)
    d.update(kw)
    return d


class Vectors(unittest.TestCase):
    def test_every_vector(self):
        self.assertGreaterEqual(len(VECTORS), 20)
        for v in VECTORS:
            with self.subTest(v["name"]):
                self.assertEqual(spec_check.validate(v["input"])["valid"], v["valid"], spec_check.validate(v["input"])["errors"][:3])


class Extra(unittest.TestCase):
    def test_good(self):
        r = spec_check.validate(GOOD)
        self.assertTrue(r["valid"], r["errors"])
        self.assertEqual(r["spec"]["contents"][0]["wells"], "A1")  # upper-cased
        self.assertEqual(r["spec"]["contents"][0]["liquid"], "Aqueous")  # in the Prep's spelling
        self.assertEqual(r["spec"]["specVersion"], 1)

    def test_whitelist_and_instrument_id(self):
        r = spec_check.validate(mutated(instrumentProtocolId="abc", evil={"x": 1}, steps=[{**GOOD["steps"][0], "extra": 1}]))
        self.assertTrue(r["valid"])
        self.assertNotIn("instrumentProtocolId", r["spec"])
        self.assertNotIn("evil", r["spec"])
        self.assertNotIn("extra", r["spec"]["steps"][0])

    def test_booleans_are_not_numbers(self):
        bad = mutated(steps=[{**GOOD["steps"][0], "vol": True}])
        self.assertFalse(spec_check.validate(bad)["valid"])

    def test_numeric_strings_like_javascript(self):
        r = spec_check.validate(mutated(steps=[{**GOOD["steps"][0], "vol": " 12 "}]))
        self.assertTrue(r["valid"])
        self.assertEqual(r["spec"]["steps"][0]["vol"], 12)
        for v in ("abc", "", "  ", "Infinity", "NaN"):
            self.assertFalse(spec_check.validate(mutated(steps=[{**GOOD["steps"][0], "vol": v}]))["valid"], v)

    def test_float_whole_numbers_become_ints(self):
        r = spec_check.validate(mutated(steps=[{"type": "mix", "pos": 3.0, "wells": "A1", "vol": 5.0, "cycles": 3.0}]))
        self.assertTrue(r["valid"], r["errors"])
        self.assertEqual(json.dumps(r["spec"]["steps"][0]), '{"type": "mix", "pos": "3", "wells": "A1", "vol": 5, "cycles": 3}')

    def test_nan_and_deep_nesting_do_not_crash(self):
        self.assertFalse(spec_check.validate(mutated(contents=[{"pos": "4", "wells": "A1", "liquid": "Aqueous", "vol": float("nan")}]))["valid"])
        deep = []
        for _ in range(5000):
            deep = [deep]
        self.assertFalse(spec_check.validate(mutated(description=deep))["valid"])

    def test_control_characters_and_trim(self):
        self.assertFalse(spec_check.validate(mutated(name="a\x00b"))["valid"])
        self.assertFalse(spec_check.validate(mutated(name="a b"))["valid"])
        self.assertFalse(spec_check.validate(mutated(name="\x1fa"))["valid"])  # JavaScript's trim() keeps \x1f, so it is rejected
        self.assertEqual(spec_check.validate(mutated(name="  ﻿Nice\n"))["spec"]["name"], "Nice")

    def test_length_counts_utf16_units(self):
        self.assertTrue(spec_check.validate(mutated(name="x" * 120))["valid"])
        self.assertFalse(spec_check.validate(mutated(name="x" * 121))["valid"])
        self.assertFalse(spec_check.validate(mutated(name="😀" * 61))["valid"])  # 122 UTF-16 units
        self.assertTrue(spec_check.validate(mutated(name="😀" * 60))["valid"])

    def test_wells_regex(self):
        for w, ok in (("A1", True), ("a1:h12", True), ("A1, B2", True), ("A1:B2,C3", True), ("A1:", False), ("Q1", False),
                      ("A123", False), ("A0", False), ("A25", False), ("A24", True), ("A01", False), ("A1:A0", False), ("A١", False), ("A1\nB2", False), ("", False)):
            step = {**GOOD["steps"][0], "toWells": w}
            self.assertEqual(spec_check.validate(mutated(steps=[step]))["valid"], ok, repr(w))

    def test_size_cap(self):
        self.assertTrue(any("Too large" in e for e in spec_check.validate(mutated(junk="y" * 210000))["errors"]))

    def test_placement(self):
        self.assertIn("heater-shaker", spec_check.validate(mutated(deck={"1": {"lw": "tips_300"}}))["errors"][0])
        self.assertIn("always starts empty", spec_check.validate(mutated(deck={"1": {"lw": "plate_pcr"}}))["errors"][0])
        self.assertFalse(spec_check.validate(mutated(deck={"2": {"lw": "reservoir_60"}}))["valid"])
        self.assertTrue(spec_check.validate(mutated(deck={"8": {"lw": "reservoir_60"}}))["valid"])

    def test_only_the_prep_catalogue(self):
        cat = spec_check.catalogue()
        self.assertIn("c_208", cat)
        r = spec_check.validate(mutated(deck={**GOOD["deck"], "3": {"lw": "c_208"}}))
        self.assertTrue(r["valid"], r["errors"])
        self.assertEqual(r["spec"]["labware"], {"c_208": cat["c_208"]})  # the catalogue definition travels with the protocol
        self.assertNotIn("labware", spec_check.validate(GOOD)["spec"])
        self.assertTrue(spec_check.validate(mutated(deck={"3": {"lw": "plate_384"}, "5": {"lw": "plate_flat_96"}}))["valid"])
        brand = {"label": "Brand plate", "kind": "plate", "rows": 16, "cols": 24, "cap": 112.5}
        for bad in ({"c_brand": brand}, {"c_999999": brand}, {"c_208": {**cat["c_208"], "rows": 16}}, "x", []):
            self.assertFalse(spec_check.validate(mutated(labware=bad))["valid"], bad)
        for key in ("c_missing", "c_99999", "reservoir_50", "trough_25"):
            self.assertFalse(spec_check.validate(mutated(deck={"3": {"lw": key}}))["valid"], key)

    def test_liquids_are_prep_liquid_classes(self):
        self.assertFalse(spec_check.validate(mutated(contents=[{"pos": "4", "wells": "A1", "liquid": "Water", "vol": 1}]))["valid"])
        r = spec_check.validate(mutated(contents=[{"pos": "4", "wells": "A1", "liquid": "ethanol 70%", "vol": 1}]))
        self.assertEqual(r["spec"]["contents"][0]["liquid"], "Ethanol 70%")

    def test_shake_pos(self):
        shake = {"type": "shake", "temp": 0, "rpm": 500, "minutes": 1}
        r = spec_check.validate(mutated(steps=[shake]))
        self.assertTrue(r["valid"], r["errors"])
        self.assertEqual(r["spec"]["steps"][0]["pos"], "1")  # always output, default the HHS
        for ok in ("3", 3, 3.0, None, ""):
            self.assertTrue(spec_check.validate(mutated(steps=[{**shake, "pos": ok}]))["valid"], ok)
        self.assertEqual(spec_check.validate(mutated(steps=[{**shake, "pos": 3}]))["spec"]["steps"][0]["pos"], "3")
        for bad in ("9", "HHS", 0, True, [], {}):
            self.assertFalse(spec_check.validate(mutated(steps=[{**shake, "pos": bad}]))["valid"], bad)

    def test_channels_tips_discard_and_label(self):
        st = {**GOOD["steps"][0], "channels": 8, "tips": "step", "tipDiscard": "rack", "label": "Add buffer"}
        r = spec_check.validate(mutated(steps=[st, {"type": "discard", "pos": "3", "wells": "A1", "vol": 5}]))
        self.assertTrue(r["valid"], r["errors"])
        self.assertEqual(r["spec"]["steps"][0]["channels"], 8)
        self.assertEqual(r["spec"]["steps"][0]["label"], "Add buffer")
        self.assertEqual(r["spec"]["steps"][1]["type"], "discard")
        for k, v in (("channels", 4), ("tips", "lots"), ("tipDiscard", "bin")):
            self.assertFalse(spec_check.validate(mutated(steps=[{**GOOD["steps"][0], k: v}]))["valid"], k)

    def test_variables(self):
        var = {"name": "beads", "label": "Bead volume", "default": 20, "min": 5, "max": 100}
        spec = mutated(variables=[var], steps=[{**GOOD["steps"][0], "vol": "$beads"}])
        r = spec_check.validate(spec)
        self.assertTrue(r["valid"], r["errors"])
        self.assertEqual(r["spec"]["steps"][0]["vol"], "$beads")  # the step keeps its "$name"
        self.assertEqual(r["spec"]["variables"], [{**var, "unit": "µL"}])
        filled, errs = spec_check.resolve(spec, {"beads": "50"})
        self.assertEqual(errs, [])
        self.assertEqual(filled["steps"][0]["vol"], 50)
        self.assertNotIn("variables", filled)
        self.assertEqual(spec_check.resolve(spec, {}, defaults=True)[0]["steps"][0]["vol"], 20)
        self.assertEqual(spec_check.resolve(spec, {})[1], ["Bead volume needs a value"])
        for bad in (4, 101, "lots", float("nan"), "Infinity", True):
            self.assertIsNone(spec_check.resolve(spec, {"beads": bad})[0], bad)
        for broken in (mutated(steps=[{**GOOD["steps"][0], "vol": "$nope"}]),
                       mutated(variables=[{**var, "max": 99999}], steps=[{**GOOD["steps"][0], "vol": "$beads"}]),
                       mutated(variables=[var, var]), mutated(variables=[{**var, "name": "1x"}]), mutated(variables=[{**var, "default": 500}]),
                       mutated(variables=[{**var, "min": 50, "max": 10}]), mutated(variables="x")):
            self.assertFalse(spec_check.validate(broken)["valid"], broken)

    def test_not_a_protocol(self):
        for bad in (None, 5, "x", [], [GOOD]):
            self.assertFalse(spec_check.validate(bad)["valid"])

    def test_prototype_like_keys(self):
        self.assertFalse(spec_check.validate(mutated(deck={"1": {"lw": "__proto__"}}))["valid"])
        self.assertFalse(spec_check.validate(mutated(deck={"constructor": {"lw": "plate_pcr"}}))["valid"])


if __name__ == "__main__":
    unittest.main()
