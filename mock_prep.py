"""
Fake Hamilton Microlab Prep for development (used by `server.py --mock`).

Implements the subset of the REST API the interface uses, as a small state machine,
so the whole UI can be exercised with no instrument attached.

Scenarios (`--mock-scenario`):
    blocked        maintenance routines need updating AND a procedure is expired (exercises auto-fix)
    clean          nothing blocks runs (default)
    labware        "must restore labware" is set - cannot be cleared via the API (exercises the manual-step path)
    verification   validation fails for a non-maintenance reason (exercises the "not maintenance" path)
    offline        every call fails as if the Prep were unplugged

Speed (`--mock-speed N`) divides all simulated durations by N (use e.g. 20 in tests).
"""

import re
import threading
import time

SCENARIOS = ("clean", "blocked", "labware", "verification", "offline")

STATE_IDLE, STATE_PREPARING, STATE_LOADING, STATE_RUNNING = 0, 10, 20, 30


class MockPrep:
    def __init__(self, scenario="clean", speed=1.0):
        if scenario not in SCENARIOS:
            raise ValueError(f"scenario must be one of {SCENARIOS}")
        self.scenario, self.speed = scenario, max(float(speed), 0.01)
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            s = self.scenario
            self.must_update = s == "blocked"
            self.must_restore_labware = s == "labware"
            self.verification_block = s == "verification"
            self.maint = [
                {"id": "daily-clean", "name": "Daily clean", "status": "Expired" if s == "blocked" else "Ok",
                 "info": "Wipe channels and deck", "lastExecutionTime": "2026-10-01T08:00:00Z", "_done_at": None},
                {"id": "weekly-seal", "name": "Weekly seal check", "status": "Ok",
                 "info": "Check piston seals", "lastExecutionTime": "2026-10-10T08:00:00Z", "_done_at": None},
            ]
            self.state, self.run = STATE_IDLE, None
            self.seq = 0
            self.protocols = [{"id": "demo-normalisation", "name": "Plasmid normalisation (demo)", "dateModified": "2026-10-12T09:00:00Z"},
                              {"id": "demo-pcr-setup", "name": "PCR master-mix setup (demo)", "dateModified": "2026-10-12T09:00:00Z"}]
            for x in self.protocols:
                x["raw"] = {"name": x["name"], "hardwareRequirements": {}, "reportSettings": {},
                            "steps": [{"stepType": "PauseStep", "id": 1, "name": "Wait Step", "stepOrder": 1, "disabled": False,
                                       "duration": 120, "message": "demo", "pauseForSetDuration": False}],
                            "deck": {"id": 1, "positions": [
                                {"position": 1, "id": 11, "labwarePositions": [], "labwareGroup": {"labware": {"id": 5}, "id": 31, "groupName": "plate"}},
                                {"position": 3, "id": 13, "labwarePositions": [{"row": 1, "column": 1, "id": 14, "labwareGroup": {"labware": {"id": 9}, "id": 32, "liquidName": "TE buffer"}, "liquidVolume": 12.5}],
                                 "labwareGroup": None}]}}

    # --- time-driven state
    def _t(self, seconds):
        return seconds / self.speed

    def _tick(self):
        now = time.time()
        for m in self.maint:
            if m["_done_at"] and now >= m["_done_at"]:
                m.update(status="Ok", lastExecutionTime=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), _done_at=None)
        r = self.run
        if r:
            if r.get("started") and now - r["started"] > self._t(12):
                self.state, self.run = STATE_IDLE, None
            elif not r.get("started") and self.state == STATE_PREPARING and now - r["created"] > self._t(2):
                self.state = STATE_LOADING

    def _blockers(self):
        errs = []
        if self.must_update:
            errs.append("Maintenance routines must be updated before running protocols.")
        for m in self.maint:
            if m["status"] != "Ok":
                errs.append(f"Maintenance procedure '{m['name']}' is {m['status']}.")
        if self.must_restore_labware:
            errs.append("Labware must be restored from the touchscreen before running protocols.")
        if self.verification_block:
            errs.append("Instrument verification routine 'Channel check' has not been run.")
        return errs

    def _stamp(self):
        self.seq += 1
        return time.strftime("%Y-%m-%dT%H:%M:%S") + f".{self.seq:04d}Z"

    def _deck(self, deck):
        """A deck as POSTed (labwareId groups) -> as stored (labware object, ids assigned); None if a group is malformed, as the real Prep 500s."""
        groups, out = {}, []

        def grp(g):
            if not g:
                return None
            if not isinstance(g.get("labwareId"), int):
                raise ValueError("labwareId")
            key = (g["labwareId"], g.get("groupName"), g.get("liquidName"), g.get("liquidType"))
            if key not in groups:
                self.seq += 1
                groups[key] = {"labware": {"id": g["labwareId"], "name": str(g["labwareId"])}, "id": 3000 + self.seq,
                               **{k: g.get(k) for k in ("groupName", "reuseTipsBetweenSteps", "liquidName", "liquidType", "liquidVolumeOptions")}}
            return groups[key]
        try:
            for pos in deck.get("positions") or []:
                self.seq += 1
                out.append({**pos, "id": 4000 + self.seq, "labwareGroup": grp(pos.get("labwareGroup")),
                            "labwarePositions": [{**lp, "id": 4000 + self.seq, "labwareGroup": grp(lp.get("labwareGroup"))} for lp in pos.get("labwarePositions") or []]})
        except ValueError:
            return None
        return {"id": deck.get("id") or 0, "positions": out}

    @staticmethod
    def _groups(deck):
        for p in deck.get("positions") or []:
            for g in [p.get("labwareGroup")] + [lp.get("labwareGroup") for lp in p.get("labwarePositions") or []]:
                if g:
                    yield g

    @staticmethod
    def _public(m):
        return {k: v for k, v in m.items() if not k.startswith("_")}

    # --- API
    def handle(self, method, path, body):
        """Return (http_status, json_body) for a request to /NimbusLite/api/v1<path>."""
        if self.scenario == "offline":
            return 502, {"error": "Could not reach the Prep: [mock offline scenario]"}
        with self.lock:
            self._tick()
            p = path.strip("/")
            if p == "system-ready":
                return 200, True
            if p == "protocol-run" and method == "GET":
                return 200, {"protocolRunState": self.state, "isRunInProgress": self.state != STATE_IDLE,
                             "protocolId": self.run and self.run["id"]}
            if p == "protocols" and method == "GET":
                return 200, [{k: v for k, v in x.items() if k != "raw"} for x in self.protocols]
            if p == "protocols" and method == "POST":
                if not body.get("name"):
                    return 400, {"message": "name required"}
                if "INVALID" in body["name"]:  # lets tests exercise the "Prep rejected it" path
                    return 200, {"isValid": False, "validationErrors": ["Mock: this protocol is not valid"]}
                # like the real Prep: only the name and deck are kept (steps are added one by one), labware is given as labwareId, ids are assigned here
                deck = self._deck(body.get("deck") or {})
                if deck is None:
                    return 500, "Internal Server Error"
                stamp = self._stamp()
                raw = {"deck": deck, "steps": [], "hardwareRequirements": {}, "reportSettings": {}}
                x = next((x for x in self.protocols if body.get("id") is not None and str(x["id"]) == str(body["id"])), None)
                if x:
                    x.update(name=body["name"], raw={**x.get("raw", {}), "name": body["name"], "deck": deck}, dateModified=stamp)
                else:
                    name = body["name"]
                    if body.get("makeNameUnique") and any(y["name"] == name for y in self.protocols):
                        name += " (2)"
                    self.protocols.append({"id": f"mock-{self.seq}-{len(self.protocols)}", "name": name, "raw": {**raw, "name": name}, "dateModified": stamp})
                return 201, {"isValid": False, "validationErrors": ["Protocol has not been validated and cannot be run."]}
            m3 = re.fullmatch(r"protocols/([^/]+)/step(?:/([^/]+))?", p)
            if m3:
                x = next((x for x in self.protocols if str(x["id"]) == m3.group(1)), None)
                if not x:
                    return 500, "Invalid protocol id"
                steps = x["raw"].setdefault("steps", [])
                if method == "POST":
                    groups = {g["id"] for g in self._groups(x["raw"]["deck"])}
                    for k in ("tipLabwareGroupId", "liquidLabwareGroupId"):
                        if body.get(k) is not None and body[k] not in groups:
                            return 400, {"message": f"{k} {body[k]} is not a group on this protocol's deck"}
                    self.seq += 1
                    steps.append({**body, "id": 5000 + self.seq})
                    x["dateModified"] = self._stamp()
                    return 201, steps[-1]
                if method == "DELETE" and m3.group(2):
                    x["raw"]["steps"] = [s for s in steps if str(s["id"]) != m3.group(2)]
                    x["dateModified"] = self._stamp()
                    return 200, None
            m4 = re.fullmatch(r"deck/([^/]+)", p)
            if m4 and method == "PUT":
                x = next((x for x in self.protocols if str(x["id"]) == m4.group(1)), None)
                deck = self._deck(body)
                if not x or deck is None:
                    return (404, {"message": "no such protocol"}) if not x else (500, "Internal Server Error")
                x["raw"]["deck"], x["dateModified"] = deck, self._stamp()
                return 200, None
            m5 = re.fullmatch(r"protocols/(rename|report-settings)/([^/]+)", p)
            if m5 and method == "PUT":
                x = next((x for x in self.protocols if str(x["id"]) == m5.group(2)), None)
                if not x:
                    return 404, {"message": "no such protocol"}
                if m5.group(1) == "rename":
                    x["name"] = x["raw"]["name"] = body.get("newName") or x["name"]
                else:
                    x["raw"]["reportSettings"] = body
                x["dateModified"] = self._stamp()
                return 200, None
            if p.startswith("protocol-verification/detect-tips/"):
                return 200, {"useAllAddresses": True, "addresses": []}
            if p.startswith("protocols/validate/"):
                errs = self._blockers()
                x = next((x for x in self.protocols if str(x["id"]) == p.rsplit("/", 1)[1]), None)
                if x and x.get("raw") and not x["raw"].get("steps"):
                    errs = errs + ["A protocol must contain at least one enabled step."]
                return 200, {"isValid": not errs, "validationErrors": errs}
            if p.startswith("protocols/verify/"):
                return 200, {"deckPositions": [
                    {"position": "1", "labwareName": "plate_pcr", "labwareDisplayName": "96-well PCR Plate"},
                    {"position": "2", "labwareName": "tips_300", "labwareDisplayName": "300uL Tip Rack Filter"},
                    {"position": "3", "labwareName": "tubes_24", "labwareDisplayName": "1.5 mL Tube Rack"},
                    {"position": "4", "labwareName": "reservoir_60", "labwareDisplayName": "60 mL Reagent Reservoir Rack"}]}
            if p == "protocols/batch-delete" and method == "POST":   # the real Prep has no DELETE /protocols/{id} (405)
                ids = {str(i) for i in (body if isinstance(body, list) else [])}
                self.protocols = [x for x in self.protocols if str(x["id"]) not in ids]
                return 204, None
            m2 = re.fullmatch(r"protocols/([^/]+)", p)
            if m2 and method == "GET" and m2.group(1) not in ("names", "preview"):
                x = next((x for x in self.protocols if str(x["id"]) == m2.group(1)), None)
                if not x:
                    return 404, {"message": "no such protocol"}
                return 200, {**x["raw"], "id": x["id"], "name": x["name"]}
            if m2 and method == "DELETE":
                return 405, None
            if p == "protocol-run/create":
                if self._blockers():
                    return 400, {"message": "Run blocked: " + " ".join(self._blockers())}
                self.state = STATE_PREPARING
                self.run = {"id": body.get("protocolId"), "simulate": body.get("simulate", True), "created": time.time()}
                return 200, None
            if p == "protocol-run/load-instructions":
                return 200, [
                    {"positionName": "Plate", "deckPosition": "1", "labwarePosition": 0, "labwareClassification": "Plate"},
                    {"positionName": "Tips", "deckPosition": "2", "labwarePosition": 0, "labwareClassification": "TipRack", "count": 96, "suggestedCount": 96},
                    {"positionName": "Tubes", "deckPosition": "3", "labwarePosition": 0, "carrierRow": 1, "carrierColumn": 1, "liquidName": "Plasmid stock", "suggestedVolume": 500, "maximumVolume": 1500},
                    {"positionName": "Reagents", "deckPosition": "4", "labwarePosition": 0, "liquidName": "TE buffer", "unitType": "mL", "suggestedVolume": 12.5, "maximumVolume": 60},
                    {"positionName": "Reagents", "deckPosition": "4", "labwarePosition": 1, "liquidName": "Master mix", "unitType": "mL", "suggestedVolume": 8, "maximumVolume": 60},
                    {"positionName": "Reagents", "deckPosition": "4", "labwarePosition": 3, "liquidName": "Ethanol 80%", "unitType": "mL", "suggestedVolume": 30, "maximumVolume": 60}]
            if p == "protocol-run/load-complete":
                if self.run:
                    self.run["started"] = time.time()
                    self.state = STATE_RUNNING
                return 200, None
            if p == "protocol-run/abort":
                self.state, self.run = STATE_IDLE, None
                return 200, None
            if p == "instruments/initialize":
                return 200, None
            if p == "settings/must-update-maintenance-routines":
                return 200, self.must_update
            if p == "settings/must-restore-labware":
                return 200, self.must_restore_labware
            if p == "settings/must-restore-verify-protocols":
                return 200, False
            if p == "maintenance/initialize":
                self.must_update = False
                return 200, None
            if p == "maintenance/errors":
                return 200, []
            if p == "maintenance" and method == "GET":
                return 200, [self._public(m) for m in self.maint]
            m = re.fullmatch(r"maintenance/([^/]+)", p)
            if m:
                for item in self.maint:
                    if item["id"] == m.group(1):
                        if method == "POST":
                            item.update(status="Running", _done_at=time.time() + self._t(4))
                            return 200, None
                        return 200, self._public(item)
                return 404, {"message": "unknown procedure"}
            if p in ("verification/status", "verification/routines"):
                return 200, []
            return 404, {"message": f"mock: no route for {method} {path}"}
