"""
Puts a raw protocol (as compiler.py makes it, or as GET /protocols/{id} returns it) onto the Prep.

The Prep's POST /protocols takes only a name and a deck. It silently drops `steps`, and wants each labware group as
`labwareId` (not the full `labware` object). So a protocol is created in pieces, as the Prep's own app does:

    POST /protocols                   name + deck (all ids 0: the Prep assigns them; identical empty groups are merged)
    GET  /protocols/{id}              learn the group ids the Prep assigned
    POST /protocols/{id}/step         each step, with its group references pointed at those ids
    GET  /protocols/validate/{id}     the Prep's own verdict

An existing protocol is updated by deleting its steps, PUT /deck/{id}, and adding the steps again.
Standard library only. `call(method, path, body=None) -> (status, json)` is the server's Prep caller.
"""

import copy

GROUP_KEYS = ("tipLabwareGroupId", "liquidLabwareGroupId")
DEFAULT_REPORT = {"savePdf": False, "saveJson": False, "savePipettingCsv": False, "pipettingCsvPerStep": False, "reportPath": None}


class UploadError(Exception):
    pass


def _group_dto(g):
    if not g:
        return None
    lw = g.get("labwareId")
    if lw is None and isinstance(g.get("labware"), dict):
        lw = g["labware"].get("id")
    if not isinstance(lw, int):
        raise UploadError("a deck labware group has no labware id")
    return {"labwareId": lw, "id": 0, "groupName": g.get("groupName"), "reuseTipsBetweenSteps": bool(g.get("reuseTipsBetweenSteps")),
            "liquidName": g.get("liquidName") or "", "liquidType": g.get("liquidType") or "",
            "liquidVolumeOptions": g.get("liquidVolumeOptions") or "CalculateForMe"}


def deck_dto(deck, deck_id=0):
    """A deck in the GET shape (or compiler shape) -> the shape POST /protocols and PUT /deck accept."""
    out = []
    for p in sorted(deck.get("positions") or [], key=lambda x: x["position"]):
        out.append({"position": p["position"], "id": 0, "positionName": p.get("positionName") or "", "liquidVolume": p.get("liquidVolume") or 0,
                    "labwareGroup": _group_dto(p.get("labwareGroup")),
                    "labwarePositions": [{"row": lp["row"], "column": lp["column"], "id": 0, "positionName": lp.get("positionName") or "",
                                          "liquidVolume": lp.get("liquidVolume") or 0, "labwareGroup": _group_dto(lp.get("labwareGroup"))}
                                         for lp in (p.get("labwarePositions") or [])]})
    return {"id": deck_id, "positions": out}


def group_map(sent, stored):
    """{group id in the protocol being uploaded: group id the Prep assigned}, by walking both decks in the same order."""
    gm = {}
    stored_pos = {p["position"]: p for p in stored.get("positions") or []}
    for p in sent.get("positions") or []:
        sp = stored_pos.get(p["position"])
        if not sp:
            continue
        pairs = [(p.get("labwareGroup"), sp.get("labwareGroup"))]
        spp = {(x["row"], x["column"]): x for x in sp.get("labwarePositions") or []}
        pairs += [(lp.get("labwareGroup"), (spp.get((lp["row"], lp["column"])) or {}).get("labwareGroup")) for lp in p.get("labwarePositions") or []]
        for a, b in pairs:
            if a and b and a.get("id") is not None:
                gm[a["id"]] = b["id"]
    return gm


def _zero_ids(o):
    if isinstance(o, dict):
        return {k: (0 if k == "id" else _zero_ids(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [_zero_ids(v) for v in o]
    return o


def step_dto(step, gm):
    s = _zero_ids(step)
    for k in GROUP_KEYS:
        if s.get(k) is not None:
            if s[k] not in gm:
                raise UploadError(f"step '{s.get('name')}' uses a labware group ({s[k]}) that is not on the deck")
            s[k] = gm[s[k]]
    return s


def _ok(status):
    return status in (200, 201, 204)


def delete(call, pid):
    """Remove a protocol from the Prep. (The Prep has no DELETE /protocols/{id}; it answers 405. Removal is a batch call.) Returns the HTTP status."""
    ids = [int(pid) if str(pid).isdigit() else pid]   # the real Prep's ids are integers (the simulation's are strings)
    return call("POST", "/protocols/batch-delete", ids)[0]


def _msg(data):
    if isinstance(data, dict):
        ve = data.get("validationErrors") or []
        return "; ".join(str(x.get("message") if isinstance(x, dict) else x) for x in ve) or str(data.get("message") or data.get("error") or data.get("title") or "")
    return str(data or "")


def _add_steps(call, pid, raw, gm):
    """Add every step in order. Returns nothing; raises UploadError naming the step the Prep refused."""
    for n, step in enumerate(sorted(raw.get("steps") or [], key=lambda s: s.get("stepOrder") or 0), 1):
        dto = step_dto(step, gm)
        dto["stepOrder"] = n
        status, data = call("POST", f"/protocols/{pid}/step", dto)
        if not _ok(status):
            raise UploadError(f"the Prep refused step {n} ('{step.get('name')}', {step.get('stepType')}): HTTP {status} {_msg(data)}".strip())


def _finish(call, pid, raw):
    rs = raw.get("reportSettings")
    if isinstance(rs, dict) and rs != DEFAULT_REPORT:
        call("PUT", f"/protocols/report-settings/{pid}", rs)
    status, data = call("GET", f"/protocols/validate/{pid}")
    if _ok(status) and isinstance(data, dict):
        return bool(data.get("isValid")), list(data.get("validationErrors") or [])
    return False, [f"the Prep could not check the protocol (HTTP {status})"]


def create(call, raw, name):
    """Create a new protocol on the Prep. Returns (id, is_valid, validation_errors); the id is kept even if the Prep calls it invalid."""
    body = {"name": name, "deck": deck_dto(raw.get("deck") or {}), "makeNameUnique": True, "skipValidation": True}
    status, data = call("POST", "/protocols", body)
    if not _ok(status):
        raise UploadError(f"the Prep refused the deck: HTTP {status} {_msg(data)}".strip())
    status, listing = call("GET", "/protocols")
    rows = listing if isinstance(listing, list) else []
    mine = [x for x in rows if isinstance(x, dict) and x.get("id") is not None and (x.get("name") or "").startswith(name)]
    if not mine:
        raise UploadError("uploaded, but the Prep did not list it")
    pid = max(x["id"] for x in mine)   # makeNameUnique may have added a suffix; the newest id is ours
    try:
        _populate(call, pid, raw)
    except UploadError:
        delete(call, pid)   # never leave a half-built protocol on the machine
        raise
    valid, errs = _finish(call, pid, raw)
    return pid, valid, errs


def _populate(call, pid, raw):
    status, stored = call("GET", f"/protocols/{pid}")
    if not _ok(status) or not isinstance(stored, dict):
        raise UploadError(f"could not read the new protocol back (HTTP {status})")
    _add_steps(call, pid, raw, group_map(raw.get("deck") or {}, stored.get("deck") or {}))


def update(call, pid, raw):
    """Replace the deck and steps of protocol `pid` with `raw`'s. Returns (is_valid, validation_errors)."""
    status, cur = call("GET", f"/protocols/{pid}")
    if not _ok(status) or not isinstance(cur, dict):
        raise UploadError(f"could not read protocol {pid} from the Prep (HTTP {status})")
    for st in cur.get("steps") or []:
        status, data = call("DELETE", f"/protocols/{pid}/step/{st['id']}")
        if not _ok(status):
            raise UploadError(f"could not clear the old steps (HTTP {status} {_msg(data)})".strip())
    deck = deck_dto(raw.get("deck") or {}, deck_id=(cur.get("deck") or {}).get("id") or pid)
    cur_pos = {p["position"]: p for p in (cur.get("deck") or {}).get("positions") or []}
    for p in deck["positions"]:   # reuse the Prep's own position ids so the positions are updated, not duplicated
        p["id"] = (cur_pos.get(p["position"]) or {}).get("id") or 0
    status, data = call("PUT", f"/deck/{pid}", deck)
    if not _ok(status):
        raise UploadError(f"the Prep refused the deck: HTTP {status} {_msg(data)}".strip())
    new_name = raw.get("name")
    if new_name and new_name != cur.get("name"):
        call("PUT", f"/protocols/rename/{pid}", {"newName": new_name})
    _populate(call, pid, raw)
    return _finish(call, pid, raw)
