"""End-to-end smoke test against the simulation-mode server. No instrument needed.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Smoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.dir = os.path.join(ROOT, "tests", ".tmp_protocols")
        os.makedirs(cls.dir, exist_ok=True)
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--port", str(cls.port), "--protocols", cls.dir],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                cls.req("GET", "/local/config")
                cls.req("PUT", "/local/mock", {"enabled": True, "speed": 40})  # simulation mode, fast
                break
            except Exception:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        shutil.rmtree(cls.dir, ignore_errors=True)

    @classmethod
    def req(cls, method, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"X-Prep-UI": "1", **(headers or {})}
        r = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}", data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                t = resp.read().decode()
                return resp.status, (json.loads(t) if t else None)
        except urllib.error.HTTPError as e:
            t = e.read().decode()
            return e.code, (json.loads(t) if t else None)

    def set_scenario(self, name):
        self.assertEqual(self.req("PUT", "/local/mock", {"enabled": True, "scenario": name, "speed": 40})[0], 200)

    def wait_state(self, want, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            _, s = self.req("GET", "/prep/protocol-run")
            if s["protocolRunState"] == want:
                return
            time.sleep(0.1)
        self.fail(f"never reached state {want}")

    def test_blocked_run_fixed_then_completes(self):
        self.set_scenario("blocked")
        _, v = self.req("GET", "/prep/protocols/validate/demo-normalisation")
        self.assertFalse(v["isValid"])
        self.assertEqual(self.req("POST", "/prep/protocol-run/create", {"protocolId": "x"})[0], 400)
        self.req("POST", "/prep/maintenance/initialize")
        self.req("POST", "/prep/maintenance/daily-clean")
        end = time.time() + 10
        while time.time() < end and self.req("GET", "/prep/maintenance/daily-clean")[1]["status"] != "Ok":
            time.sleep(0.1)
        _, v = self.req("GET", "/prep/protocols/validate/demo-normalisation")
        self.assertTrue(v["isValid"], v)
        self.assertEqual(self.req("POST", "/prep/protocol-run/create", {"protocolId": "demo-normalisation", "simulate": True})[0], 200)
        self.wait_state(20)
        _, instr = self.req("GET", "/prep/protocol-run/load-instructions")
        self.assertEqual({i["deckPosition"] for i in instr}, {"1", "2", "3", "4"})
        self.assertEqual(sorted(i["labwarePosition"] for i in instr if i["deckPosition"] == "4"), [0, 1, 3])  # reservoir slots
        self.req("PUT", "/prep/protocol-run/load-complete", {"loadCompleteAction": 0})
        self.wait_state(0)

    def test_labware_scenario_cannot_be_autofixed(self):
        self.set_scenario("labware")
        self.req("POST", "/prep/maintenance/initialize")
        _, v = self.req("GET", "/prep/protocols/validate/demo-normalisation")
        self.assertFalse(v["isValid"])
        self.assertTrue(self.req("GET", "/prep/settings/must-restore-labware")[1])

    def test_offline_scenario(self):
        self.set_scenario("offline")
        self.assertEqual(self.req("GET", "/prep/system-ready")[0], 502)
        self.set_scenario("clean")
        self.assertEqual(self.req("GET", "/prep/system-ready")[0], 200)

    def test_library_and_script_run(self):
        self.set_scenario("clean")
        code = "from prep_sdk import Prep\nprint('ready:', Prep().call('GET', '/system-ready'))\n"
        self.assertEqual(self.req("PUT", "/local/protocols/t.py", raw=code.encode())[0], 200)
        self.assertEqual(self.req("PUT", "/local/protocols/bad.json", raw=b"{nope")[0], 400)
        self.assertEqual(self.req("PUT", "/local/protocols/evil.sh", raw=b"x")[0], 400)
        _, job = self.req("POST", "/local/jobs", {"name": "t.py", "simulate": True})
        end, out = time.time() + 15, None
        while time.time() < end:
            _, out = self.req("GET", f"/local/jobs/{job['id']}")
            if out["done"]:
                break
            time.sleep(0.1)
        self.assertEqual(out["code"], 0, out)
        self.assertIn("ready: True", out["lines"])

    # ───────── favourites <-> Prep sync ─────────
    def sync(self):
        code, st = self.req("POST", "/local/sync", {})
        self.assertEqual(code, 200)
        return st

    def lib(self):
        return {i["title"]: i for i in self.req("GET", "/local/protocols")[1]}

    def machine(self):
        return {p["name"]: p for p in self.req("GET", "/prep/protocols")[1]}

    def test_sync_rules(self):
        self.set_scenario("clean")
        # 1. everything on the Prep is copied into the library and starred
        self.assertEqual(self.sync()["state"], "synced")
        lib = self.lib()
        for name in ("Plasmid normalisation (demo)", "PCR master-mix setup (demo)"):
            self.assertTrue(lib[name]["favourite"] and lib[name]["onPrep"], lib[name])
        # 2. un-starring removes it from the Prep but keeps the library copy
        pcr = lib["PCR master-mix setup (demo)"]
        self.req("PUT", "/local/favourite", {"name": pcr["name"], "favourite": False})
        self.assertNotIn("PCR master-mix setup (demo)", self.machine())
        kept = self.lib()["PCR master-mix setup (demo)"]
        self.assertFalse(kept["favourite"] or kept["onPrep"])
        self.assertTrue(os.listdir(os.path.join(self.dir, ".deleted")))  # safety copy of what was on the Prep
        # 3. starring it again uploads it
        self.req("PUT", "/local/favourite", {"name": pcr["name"], "favourite": True})
        self.assertIn("PCR master-mix setup (demo)", self.machine())
        self.assertTrue(self.lib()["PCR master-mix setup (demo)"]["onPrep"])
        # 4. editing a starred protocol locally updates the Prep
        name = self.lib()["Plasmid normalisation (demo)"]["name"]
        doc = json.loads(self.req("GET", "/local/protocols/" + urllib.parse.quote(name))[1]["content"])
        doc["raw"]["steps"][0]["message"] = "edited here"
        self.req("PUT", "/local/protocols/" + urllib.parse.quote(name), raw=json.dumps(doc).encode())
        self.sync()
        pid = self.lib()["Plasmid normalisation (demo)"]["machineId"]
        self.assertEqual(self.req("GET", f"/prep/protocols/{pid}")[1]["steps"][0]["message"], "edited here")
        # 5. editing on the Prep updates the library
        self.req("POST", "/prep/protocols", {"id": pid, "name": "Plasmid normalisation (demo)", "deck": {}})
        self.sync()
        doc = json.loads(self.req("GET", "/local/protocols/" + urllib.parse.quote(name))[1]["content"])
        self.assertEqual(doc["raw"]["deck"]["positions"], [])
        # 6. a protocol added on the Prep appears, starred; one deleted on the Prep is un-starred
        self.req("POST", "/prep/protocols", {"name": "Made on the touchscreen", "deck": {}})
        self.sync()
        self.assertTrue(self.lib()["Made on the touchscreen"]["favourite"])
        mid = self.lib()["Made on the touchscreen"]["machineId"]
        self.req("POST", "/prep/protocols/batch-delete", [mid])
        self.sync(); self.sync()  # must be missing two passes in a row before we believe it
        gone = self.lib()["Made on the touchscreen"]
        self.assertFalse(gone["favourite"] or gone["onPrep"])
        # 7. the Prep rejecting an upload is reported on the card, not retried in a loop
        bad = {"_prepSource": {"name": "INVALID one", "simulated": True}, "raw": {"name": "INVALID one"}}
        self.req("PUT", "/local/protocols/bad.prep.json", raw=json.dumps(bad).encode())
        self.req("PUT", "/local/favourite", {"name": "bad.prep.json", "favourite": True})
        card = self.lib()["INVALID one"]
        self.assertIn("rejected", card["error"])
        self.assertFalse(card["onPrep"])
        # 8. scripts can be starred (pinned) without touching the Prep
        self.req("PUT", "/local/protocols/pin.py", raw=b"print(1)")
        before = set(self.machine())
        self.req("PUT", "/local/favourite", {"name": "pin.py", "favourite": True})
        self.assertEqual(set(self.machine()), before)
        self.assertTrue(self.lib()["pin"]["favourite"])

    def test_sync_waits_while_offline_and_never_deletes(self):
        self.set_scenario("clean")
        self.sync()
        names = set(self.machine())
        self.set_scenario("offline")
        self.assertEqual(self.sync()["state"], "offline")
        self.set_scenario("clean")
        self.assertEqual(self.sync()["state"], "synced")
        self.assertTrue(names <= set(self.machine()) | {"Made on the touchscreen"})

    def test_oneoff_script_and_protocol_leave_no_trace(self):
        self.set_scenario("clean")
        self.sync()
        lib_before = set(self.lib())
        # a one-off script runs, and its scratch copy is deleted afterwards
        code = "from prep_sdk import Prep\nprint('one-off ok', Prep().call('GET', '/system-ready'))\n"
        _, job = self.req("POST", "/local/oneoff/script", {"name": "once.py", "content": code, "simulate": True})
        end, out = time.time() + 15, None
        while time.time() < end:
            _, out = self.req("GET", f"/local/jobs/{job['id']}")
            if out["done"]:
                break
            time.sleep(0.1)
        self.assertIn("one-off ok True", out["lines"])
        time.sleep(0.2)
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".oneoff", "once.py")))
        # a one-off Prep protocol is on the Prep only for the run, and sync neither adopts nor deletes it
        _, up = self.req("POST", "/local/oneoff/prep", {"raw": {"name": "Just once", "deck": {}, "steps": [
            {"stepType": "PauseStep", "id": 0, "name": "Wait Step", "stepOrder": 1, "disabled": False, "duration": 5, "message": "hi", "pauseForSetDuration": False}]},
            "name": "Just once"})
        self.assertIn("Just once", self.machine())
        self.sync(); self.sync()
        self.assertIn("Just once", self.machine())
        self.assertNotIn("Just once", self.lib())
        self.assertEqual(self.req("DELETE", f"/local/oneoff/prep/{up['id']}")[0], 200)
        self.assertNotIn("Just once", self.machine())
        self.assertEqual(self.req("DELETE", "/local/oneoff/prep/not-ours")[0], 400)  # only one-offs can be removed this way
        self.assertEqual(set(self.lib()), lib_before)

    def test_guards(self):
        # mutating request without the X-Prep-UI header (what a hostile web page would send)
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/local/protocols/x.py", data=b"print(1)", method="PUT")
        with self.assertRaises(urllib.error.HTTPError) as c:
            urllib.request.urlopen(r, timeout=5)
        self.assertEqual(c.exception.code, 403)
        # foreign Host header (DNS rebinding)
        code, _ = self.req("GET", "/local/config", headers={"Host": "evil.example"})
        self.assertEqual(code, 403)


if __name__ == "__main__":
    unittest.main()
