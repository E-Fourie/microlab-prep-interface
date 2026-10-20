"""
prep_sdk - importable helper for the Hamilton Microlab Prep REST API.

Standard library only. Scripts dropped into the Prep Interface run with these
environment variables set by the server, so they work unchanged from the UI:

    PREP_HOST       Prep address            (default 192.168.100.101)
    PREP_BASE       API base path           (default /NimbusLite/api/v1)
    PREP_SIMULATE   "1" = simulated run, "0" = real run  (default "1")

Minimal script:

    from prep_sdk import Prep, simulate_default
    prep = Prep()
    print(prep.call("GET", "/system-ready"))
    prep.run_protocol("<protocolId>", simulate=simulate_default())
"""

import json
import os
import time
import urllib.error
import urllib.request

STATE_IDLE = 0
STATE_LOADING = 20


class PrepError(Exception):
    pass


def simulate_default():
    """True unless the launcher explicitly asked for a real run."""
    return os.environ.get("PREP_SIMULATE", "1") != "0"


class Prep:
    def __init__(self, host=None, base_path=None, timeout=15):
        host = host or os.environ.get("PREP_HOST", "192.168.100.101")
        base_path = base_path or os.environ.get("PREP_BASE", "/NimbusLite/api/v1")
        self.base = f"http://{host}{base_path.rstrip('/')}"
        self.timeout = timeout
        self.token = None

    def call(self, method, path, body=None, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("X-Prep-UI", "1")  # lets scripts use the Interface's mock proxy; ignored by the Prep
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode() or "null"
        except urllib.error.HTTPError as e:
            raise PrepError(f"HTTP {e.code} from {method} {path}: {e.read().decode(errors='replace')}")
        except urllib.error.URLError as e:
            raise PrepError(f"Could not reach the Prep at {self.base}: {e.reason}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def wait_for(self, predicate, what, timeout=3600, poll=2):
        start = time.time()
        while True:
            state = self.call("GET", "/protocol-run")
            if predicate(state):
                return state
            if time.time() - start > timeout:
                raise PrepError(f"Timed out waiting for {what}. Last state: {state}")
            time.sleep(poll)

    def run_protocol(self, protocol_id, simulate=True, recompile=False, confirm=input):
        """Create a run, wait for loading, print load instructions, then start it."""
        v = self.call("GET", f"/protocols/validate/{protocol_id}", timeout=60)
        if isinstance(v, dict) and v.get("isValid") is False:
            raise PrepError(f"Protocol not valid to run: {json.dumps(v.get('validationErrors', v))}")
        self.call("POST", "/protocol-run/create",
                  {"protocolId": protocol_id, "simulate": simulate, "recompile": recompile})
        self.wait_for(lambda s: isinstance(s, dict) and s.get("protocolRunState") == STATE_LOADING,
                      "loading state")
        print(json.dumps(self.call("GET", "/protocol-run/load-instructions"), indent=2))
        confirm("Load the deck as shown above, then press Enter to start the run ... ")
        self.call("PUT", "/protocol-run/load-complete",
                  {"protocolId": protocol_id, "simulate": simulate, "loadCompleteAction": 0})
        time.sleep(3)
        state = self.wait_for(lambda s: isinstance(s, dict) and (
            s.get("protocolRunState") == STATE_IDLE or s.get("isRunInProgress") is False), "run completion")
        print("Finished:", json.dumps(state, indent=2))
        return state
