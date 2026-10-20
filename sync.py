"""
Favourites <-> Prep sync.

The rule, in one line: a starred (favourite) protocol lives on the Prep; an un-starred one does not.

    * Every protocol stored on the Prep is copied into the library and starred automatically.
    * A starred library protocol in the Prep's own format is uploaded to the Prep.
    * Editing either side updates the other (the Prep wins if both changed; the local version is kept as a backup).
    * Un-starring (or deleting) a protocol removes it from the Prep. The library copy is kept, and a copy of what
      was on the Prep is saved in protocols/.deleted/ first, so nothing is ever lost.
    * A protocol removed on the Prep's touchscreen is un-starred here (its library copy is kept).

Scripts (.py) and Builder protocols only run from this laptop, so starring them just pins them to the top.

State lives in protocols/.prep-sync[.mock].json (one file per Prep, real or simulated).
Standard library only. The Prep is reached through the `call` function the server passes in.
"""

import hashlib
import json
import os
import re
import threading
import time

import prep_upload

PASS_EVERY = 8          # seconds between background passes
RETRY_AFTER = 120       # seconds before retrying a rejected upload/removal that has not changed
MISSING_PASSES = 2      # a protocol must be missing from the Prep this many passes in a row before we believe it


class Unreachable(Exception):
    pass


def canon_hash(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def safe_name(s):
    return re.sub(r"[^\w.\- ()]", "_", str(s or "protocol")).strip()[:80] or "protocol"


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def kind_of(name):
    low = name.lower()
    return "prep" if low.endswith(".prep.json") else low.rsplit(".", 1)[-1]


class Sync:
    def __init__(self, config, call):
        """config: the server's CONFIG dict. call(method, path, body=None) -> (status, parsed_json); raises Unreachable."""
        self.config, self.call = config, call
        self.state_lock = threading.RLock()
        self.pass_lock = threading.Lock()
        self.wake = threading.Event()
        self.ignored = set()   # ids of one-off protocols living on the Prep only for a single run
        self.status = {"state": "starting", "message": "", "last": None, "log": []}
        self.st, self.st_path = None, None

    # ───────────── state ─────────────
    @property
    def folder(self):
        return self.config["protocols"]

    def _profile_path(self):
        return os.path.join(self.folder, ".prep-sync.mock.json" if self.config["mock"] else ".prep-sync.json")

    def _state(self):
        """The state for the current Prep (real or simulated); reloaded when that changes."""
        with self.state_lock:
            p = self._profile_path()
            if self.st_path != p:
                data = read_json(p) or {}
                self.st = {"enabled": True, "favourites": list(data.get("favourites", [])),
                           "links": data.get("links", {}), "missing": {}, "errors": data.get("errors", {})}
                self.st_path = p
            return self.st

    def _save(self):
        with self.state_lock:
            st = self._state()
            tmp = self.st_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"enabled": st["enabled"], "favourites": sorted(set(st["favourites"])),
                           "links": st["links"], "errors": st["errors"]}, fh, indent=1)
            os.replace(tmp, self.st_path)

    def _log(self, msg):
        self.status["log"] = ([{"t": time.time(), "msg": msg}] + self.status["log"])[:25]

    def _set(self, state, message=""):
        self.status.update(state=state, message=message)
        if state == "synced":
            self.status["last"] = time.time()

    # ───────────── local files ─────────────
    def _path(self, name):
        return os.path.join(self.folder, name)

    def _read_doc(self, name):
        doc = read_json(self._path(name))
        return doc if isinstance(doc, dict) and isinstance(doc.get("raw"), dict) else None

    def _write_doc(self, name, doc):
        with self.state_lock:
            with open(self._path(name), "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2)

    def _prep_files(self):
        """name -> doc for every library file that is a Prep-format protocol of the current Prep (real vs simulated)."""
        out = {}
        for f in sorted(os.listdir(self.folder)):
            if f.startswith(".") or not f.lower().endswith(".prep.json"):
                continue
            doc = self._read_doc(f)
            if doc and bool((doc.get("_prepSource") or {}).get("simulated")) == bool(self.config["mock"]):
                out[f] = doc
        return out

    def _unique(self, name):
        base, n = name[:-len(".prep.json")], 2
        while os.path.exists(self._path(name)):
            name, n = f"{base} {n}.prep.json", n + 1
        return name

    def _backup_local(self, name, doc, why):
        """Keep the local version as a separate, un-starred copy (no Prep id, so it never links to anything)."""
        title = (doc.get("_prepSource") or {}).get("name") or doc["raw"].get("name") or name
        bk = self._unique(f"{safe_name(title)} ({why} {time.strftime('%Y-%m-%d %H-%M')}).prep.json")
        self._write_doc(bk, {"_prepSource": {"name": f"{title} ({why})", "simulated": bool(self.config["mock"])},
                             "raw": {**{k: v for k, v in doc["raw"].items() if k != "id"}, "name": f"{title} ({why})"}})
        return bk

    def describe(self, name):
        """Everything the UI needs to draw one protocol card."""
        fp = self._path(name)
        stt = os.stat(fp)
        kind = kind_of(name)
        title, desc = name.rsplit(".", 1)[0].replace("_", " "), ""
        if kind == "prep":
            doc = read_json(fp) or {}
            raw = doc.get("raw") if isinstance(doc.get("raw"), dict) else {}
            title = raw.get("name") or (doc.get("_prepSource") or {}).get("name") or title
            desc = raw.get("description") or ""
        elif kind == "json":
            spec = read_json(fp) or {}
            title, desc = (spec.get("name") or title), (spec.get("description") or "")
        else:
            try:
                head = open(fp, encoding="utf-8", errors="replace").read(1500)
                m = re.match(r'\s*(?:#!.*\n)?\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', head, re.S)
                desc = next((ln.strip() for ln in (m.group(1) if m else "").splitlines() if ln.strip()), "")
            except OSError:
                pass
        with self.state_lock:
            st = self._state()
            fav, link, err = name in st["favourites"], st["links"].get(name), st["errors"].get(name)
        can = kind == "prep"
        out = {"name": name, "kind": kind, "title": str(title), "description": str(desc)[:300], "size": stt.st_size,
                "modified": stt.st_mtime, "favourite": fav, "canSync": can, "onPrep": bool(link),
                "machineId": link["id"] if link else None,
                "pending": "upload" if fav and can and not link else "remove" if link and not fav else None,
                "error": err["message"] if err else None}
        return out

    def list(self):
        sim_only = self.config["mock"]
        out = []
        for f in sorted(os.listdir(self.folder)):
            fp = self._path(f)
            if f.startswith(".") or not os.path.isfile(fp) or not f.lower().endswith((".py", ".json")):
                continue
            if kind_of(f) == "prep":
                doc = read_json(fp) or {}
                if not sim_only and (doc.get("_prepSource") or {}).get("simulated"):
                    continue  # simulated copies stay out of the real library
            out.append(self.describe(f))
        return out

    # ───────────── actions from the UI ─────────────
    def set_favourite(self, name, fav):
        if not os.path.isfile(self._path(name)):
            raise ValueError("no such protocol")
        with self.state_lock:
            st = self._state()
            if fav and name not in st["favourites"]:
                st["favourites"].append(name)
            if not fav and name in st["favourites"]:
                st["favourites"].remove(name)
            st["errors"].pop(name, None)
            self._save()
        return self.describe(name)

    def save_file(self, name, data):
        with self.state_lock:
            with open(self._path(name), "wb") as fh:
                fh.write(data)
        self.wake.set()  # a save triggers a sync pass straight away

    def delete_file(self, name):
        """Deleting from the library = un-starring; the Prep copy (if any) is removed on the next pass."""
        with self.state_lock:
            st = self._state()
            if name in st["favourites"]:
                st["favourites"].remove(name)
            st["errors"].pop(name, None)
            if os.path.isfile(self._path(name)):
                os.remove(self._path(name))
            self._save()
        self.wake.set()

    def set_enabled(self, enabled):
        with self.state_lock:
            self._state()["enabled"] = bool(enabled)
            self._save()
        self.wake.set()

    def view(self):
        st = self._state()
        return {**self.status, "enabled": st["enabled"], "log": self.status["log"][:12]}

    # ───────────── talking to the Prep ─────────────
    def _call(self, method, path, body=None):
        status, data = self.call(method, path, body)
        if status == 502:
            raise Unreachable((data or {}).get("error", "Prep not reachable") if isinstance(data, dict) else "Prep not reachable")
        return status, data

    @staticmethod
    def _items(data):
        rows = data if isinstance(data, list) else (data.get("items") or data.get("protocols")) if isinstance(data, dict) else None
        if rows is None:
            raise RuntimeError("The Prep's protocol list was not in the expected format")
        out = []
        for x in rows:
            if isinstance(x, dict) and x.get("id") is not None:
                out.append({"id": x["id"], "sid": str(x["id"]), "name": x.get("name") or "",
                            "stamp": x.get("dateModified") or x.get("modified") or x.get("lastModified")})
        return out

    def _fetch(self, m, cache):
        if m["sid"] not in cache:
            status, raw = self._call("GET", f"/protocols/{m['sid']}")
            if status != 200 or not isinstance(raw, dict):
                raise RuntimeError(f"Could not read '{m['name'] or m['sid']}' from the Prep (HTTP {status})")
            cache[m["sid"]] = raw
        return cache[m["sid"]]

    def _stamp(self, m, cache):
        return m["stamp"] or canon_hash(self._fetch(m, cache))

    # ───────────── the pass ─────────────
    def run_pass(self, manual=False):
        with self.pass_lock:
            if not self._state()["enabled"] and not manual:
                return self._set("off", "Sync is switched off")
            self._set("syncing")
            try:
                self._pass()
            except Unreachable as e:
                self._set("offline", str(e))
            except Exception as e:  # never let a bug kill the background thread
                self._set("error", f"{type(e).__name__}: {e}")

    def _pass(self):
        status, run = self._call("GET", "/protocol-run")
        if status == 200 and isinstance(run, dict) and (run.get("isRunInProgress") or run.get("protocolRunState")):
            return self._set("paused", "A run is in progress; syncing resumes when it finishes")
        status, data = self._call("GET", "/protocols")
        if status != 200:
            raise RuntimeError(f"The Prep answered HTTP {status} to the protocol list")
        items = [m for m in self._items(data) if m["sid"] not in self.ignored]
        by_id = {m["sid"]: m for m in items}
        raws, st = {}, self._state()
        links, favs, errs, missing = st["links"], st["favourites"], st["errors"], st["missing"]
        now, iso = time.time(), time.strftime("%Y-%m-%dT%H:%M:%S")
        sim = bool(self.config["mock"])
        pending_new, overwritten = [], []

        def link_to(name, m, raw):
            links[name] = {"id": m["sid"], "hash": canon_hash(raw), "stamp": self._stamp(m, raws)}
            if name not in favs:
                favs.append(name)
            errs.pop(name, None)

        def pull(m, name=None):
            raw = self._fetch(m, raws)
            title = m["name"] or raw.get("name") or f"protocol {m['sid']}"
            name = name or self._unique(f"{safe_name(title)} (id {m['sid']}).prep.json")
            self._write_doc(name, {"_prepSource": {"id": m["id"], "name": title, "pulledAt": iso, "simulated": sim}, "raw": raw})
            link_to(name, m, raw)
            return name

        # 1. a library copy that already carries a Prep id (older versions saved these): adopt it, Prep's version wins
        linked_ids = {l["id"] for l in links.values()}
        for name, doc in self._prep_files().items():
            sid = str((doc.get("_prepSource") or {}).get("id") or "")
            if name not in links and sid in by_id and sid not in linked_ids:
                m = by_id[sid]
                if canon_hash(self._fetch(m, raws)) != canon_hash(doc["raw"]):
                    self._backup_local(name, doc, "local copy")
                pull(m, name)
                linked_ids.add(sid)
                self._log(f"Linked '{m['name'] or sid}' with the Prep")

        # 2. removed on the Prep's touchscreen -> un-star here (keep the library copy)
        for name, l in list(links.items()):
            if l["id"] in by_id:
                missing.pop(name, None)
                continue
            missing[name] = missing.get(name, 0) + 1
            if missing[name] >= MISSING_PASSES:
                del links[name]
                missing.pop(name, None)
                if name in favs:
                    favs.remove(name)
                    self._log(f"'{name.split(' (id ')[0]}' was removed on the Prep; un-starred here (copy kept)")

        # 3. every protocol on the Prep
        name_by_id = {l["id"]: n for n, l in links.items()}
        for m in items:
            name = name_by_id.get(m["sid"])
            if name is None:
                created = pull(m)
                self._log(f"Copied '{m['name'] or m['sid']}' from the Prep and starred it")
                name_by_id[m["sid"]] = created
                continue
            if name not in favs:
                self._remove_from_machine(name, m, raws, now)
                continue
            doc = self._read_doc(name)
            link = links[name]
            h = canon_hash(doc["raw"]) if doc else None
            machine_changed = link.get("stamp") != self._stamp(m, raws)
            local_changed = doc is not None and h != link.get("hash")
            if doc is None:
                pull(m, name)
            elif machine_changed and local_changed:
                bk = self._backup_local(name, doc, "your edit")
                pull(m, name)
                self._log(f"'{m['name']}' changed on both sides; the Prep's version won, yours is saved as '{bk}'")
            elif machine_changed:
                pull(m, name)
                self._log(f"Updated '{m['name']}' from the Prep")
            elif local_changed:
                e = errs.get(name)
                if e and e.get("hash") == h and now - e.get("at", 0) < RETRY_AFTER:
                    continue
                if self._push(name, h, doc["raw"], sid=m["sid"]):
                    overwritten.append(name)

        # 4. starred Prep-format protocols that are not on the Prep yet -> upload
        before = {m["sid"] for m in items}
        for name, doc in self._prep_files().items():
            if name in favs and name not in links:
                h = canon_hash(doc["raw"])
                e = errs.get(name)
                if e and e.get("hash") == h and now - e.get("at", 0) < RETRY_AFTER:
                    continue
                title = doc["raw"].get("name") or (doc.get("_prepSource") or {}).get("name") or name
                if self._push(name, h, doc["raw"], title=title):
                    pending_new.append((name, title))

        # 5. learn the new ids / edit stamps of what we just uploaded
        if pending_new or overwritten:
            fresh = {m["sid"]: m for m in self._items(self._call("GET", "/protocols")[1])}
            for name in overwritten:
                m = fresh.get(links[name]["id"])
                if m:
                    links[name]["stamp"] = self._stamp(m, {})
                    links[name]["hash"] = canon_hash(self._read_doc(name)["raw"])
                    self._log(f"Updated '{m['name']}' on the Prep")
            claimed = set(l["id"] for l in links.values())
            fresh_new = [m for sid, m in fresh.items() if sid not in before and sid not in claimed]
            for name, title in pending_new:
                m = next((x for x in fresh_new if x["name"] == title), None) or (fresh_new[0] if len(fresh_new) == 1 and len(pending_new) == 1 else None)
                if not m:
                    errs[name] = {"hash": canon_hash(self._read_doc(name)["raw"]), "at": now,
                                  "message": "Uploaded, but the Prep did not list it. Check the Prep and try again."}
                    continue
                fresh_new.remove(m)
                raw = self._read_doc(name)["raw"]
                links[name] = {"id": m["sid"], "hash": canon_hash(raw), "stamp": self._stamp(m, {})}
                self._log(f"Uploaded '{title}' to the Prep")

        for name in [n for n in list(errs) if not os.path.isfile(self._path(n)) and n not in links]:
            errs.pop(name)
        self._save()
        self._set("synced")

    def _push(self, name, h, raw, title=None, sid=None):
        """Create (title) or update (sid) a protocol on the Prep. True if it ended up valid; otherwise the reason is stored against the file."""
        errs = self._state()["errors"]
        try:
            if sid is not None:
                valid, problems = prep_upload.update(self._call, sid, raw)
            else:
                _, valid, problems = prep_upload.create(self._call, raw, title)
            detail = "; ".join(str(x) for x in problems)
        except prep_upload.UploadError as e:
            valid, detail = False, str(e)
        if valid:
            errs.pop(name, None)
            return True
        errs[name] = {"hash": h, "at": time.time(), "message": f"The Prep rejected this protocol{': ' + detail if detail else ''}"}
        return False

    def _remove_from_machine(self, name, m, raws, now):
        st = self._state()
        errs, links = st["errors"], st["links"]
        e = errs.get(name)
        if e and e.get("kind") == "remove" and now - e.get("at", 0) < RETRY_AFTER:
            return
        raw = self._fetch(m, raws)
        # safety copy of exactly what is on the Prep, outside the library
        trash = os.path.join(self.folder, ".deleted")
        os.makedirs(trash, exist_ok=True)
        stem = safe_name((m["name"] or m["sid"]) + time.strftime(" %Y-%m-%d %H-%M-%S"))
        with open(os.path.join(trash, stem + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"id": m["id"], "name": m["name"], "raw": raw}, fh, indent=2)
        # keep the library copy as current as the Prep's (unless the user has edited it since)
        doc = self._read_doc(name)
        if doc is not None and canon_hash(doc["raw"]) == links[name].get("hash") and canon_hash(raw) != links[name].get("hash"):
            self._write_doc(name, {"_prepSource": {**(doc.get("_prepSource") or {}), "pulledAt": time.strftime("%Y-%m-%dT%H:%M:%S")}, "raw": raw})
        status = prep_upload.delete(self._call, m["sid"])
        if status in (200, 202, 204, 404):
            del links[name]
            errs.pop(name, None)
            self._log(f"Removed '{m['name'] or m['sid']}' from the Prep (copy kept in the library)")
        else:
            errs[name] = {"kind": "remove", "at": now, "hash": None,
                          "message": f"Could not remove this from the Prep (HTTP {status}). It will retry."}

    # ───────────── one-off protocols (run once, never saved) ─────────────
    def upload_temp(self, raw, name):
        """Put a protocol on the Prep just to run it. Sync leaves it alone, and delete_temp() removes it afterwards."""
        with self.pass_lock:
            try:
                sid, valid, problems = prep_upload.create(self._call, raw, raw.get("name") or name)
            except Unreachable as e:
                raise ValueError(f"Can't reach the Prep: {e}")
            except prep_upload.UploadError as e:
                self._dump_upload(raw, None)
                raise ValueError(f"The Prep rejected this protocol: {e}")
            sid = str(sid)
            self.ignored.add(sid)
            self._dump_upload(raw, sid)
            if not valid:
                self.ignored.discard(sid)
                prep_upload.delete(self._call, sid)
                raise ValueError("The Prep rejected this protocol" + (": " + "; ".join(str(x) for x in problems) if problems else ""))
            return sid

    def _dump_upload(self, sent, sid):
        """Keep what we sent and what the Prep stored (protocols/.oneoff/last-upload-*.json) so a protocol the Prep reads differently can be compared."""
        try:
            stored = self._call("GET", f"/protocols/{sid}")[1] if sid is not None else None
            d = os.path.join(self.folder, ".oneoff")
            os.makedirs(d, exist_ok=True)
            for n, v in (("sent", sent), ("stored", stored)):
                with open(os.path.join(d, f"last-upload-{n}.json"), "w", encoding="utf-8") as fh:
                    json.dump(v, fh, indent=1)
            n_sent = len(sent.get("steps") or [])
            n_kept = len((stored or {}).get("steps") or []) if isinstance(stored, dict) else None
            self._log(f"One-off upload: sent {n_sent} steps, the Prep kept {n_kept}" + (f" (isValid {stored.get('isValid')}: {stored.get('validationErrors')})" if isinstance(stored, dict) else ""))
        except Exception as e:  # diagnostics only; never disturb a run
            self._log(f"One-off upload diagnostics failed: {e}")

    def delete_temp(self, sid):
        with self.pass_lock:
            if sid not in self.ignored:
                raise ValueError("not a one-off protocol")
            try:
                prep_upload.delete(self._call, sid)
            finally:
                self.ignored.discard(sid)

    # ───────────── background thread ─────────────
    def start(self):
        def loop():
            time.sleep(0.5)
            while True:
                try:
                    self.run_pass()
                except Exception:
                    pass
                self.wake.wait(PASS_EVERY)
                self.wake.clear()
        threading.Thread(target=loop, daemon=True, name="prep-sync").start()
