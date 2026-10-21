"""The labware/liquid list the Builder is offered (the Prep's own catalogue) on the real server."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATE = {"label": "Brand 384", "kind": "plate", "rows": 16, "cols": 24, "cap": 112}


class Catalogue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()
        cls.dir = tempfile.mkdtemp()
        env = {k: v for k, v in os.environ.items() }
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--port", str(cls.port), "--protocols", cls.dir],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                cls.req("GET", "/local/labware")
                break
            except Exception:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()
        shutil.rmtree(cls.dir, ignore_errors=True)

    @classmethod
    def req(cls, method, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}", method=method, headers={"X-Prep-UI": "1"},
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_only_the_prep_catalogue_is_offered(self):
        code, body = self.req("GET", "/local/labware")
        self.assertEqual(code, 200)
        self.assertIn("c_144", body["labware"])  # a catalogue plate, keyed by its Prep id
        self.assertEqual(sorted(body["builtin"]), ["magnet", "reservoir_60"])  # the only built-in keys the Builder offers
        self.assertIn("Aqueous", body["liquids"])
        self.assertEqual(len(body["liquids"]), 16)
        for key, d in body["labware"].items():
            self.assertRegex(key, r"^c_\d+$")
            self.assertIn(d["kind"], ("plate", "tips", "tubes", "magnet"))
        self.assertEqual(sorted(k for k, d in body["labware"].items() if d["kind"] == "magnet"), ["c_39", "c_40", "c_41", "c_42", "c_43"])  # the 96-well magnets
        self.assertEqual(self.req("PUT", "/local/labware/c_brand", PLATE)[0], 405)  # nothing can be added
        self.assertEqual(self.req("DELETE", "/local/labware/c_144")[0], 405)


if __name__ == "__main__":
    unittest.main()
