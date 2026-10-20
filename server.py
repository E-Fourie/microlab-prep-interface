#!/usr/bin/env python3
"""
Hamilton Microlab Prep Interface - local server.

Run this ON the laptop cabled to the Prep, then open http://localhost:8080

    python3 server.py                 # talk to the real Prep (192.168.100.101)
    python3 server.py --host 10.0.0.5 --port 9000

Simulation (a built-in fake Prep) is switched on from the machine menu in the page, top right.

Why a server at all: browsers block a web page from calling the Prep directly
(CORS), so this proxies /prep/* to the instrument's REST API. It also owns the
protocol folder (./protocols) and runs dropped-in Python scripts.

Standard library only. Binds to 127.0.0.1 and rejects foreign Host/Origin
headers, because it can run scripts on this machine.
"""

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import signal

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
CONFIG = {"host": "192.168.100.101", "base": "/NimbusLite/api/v1",
          "protocols": os.path.join(ROOT, "protocols"), "mock": False, "port": 8080, "scenario": "clean", "speed": 1.0}
ALLOWED_EXT = (".py", ".json")
JOBS = {}
JOBS_LOCK = threading.Lock()


# ───────────────────────── mock Prep (dev / no instrument) ─────────────────────────
from mock_prep import MockPrep, SCENARIOS  # noqa: E402
from sync import Sync  # noqa: E402
import compiler  # noqa: E402
import labware_map  # noqa: E402
import spec_check  # noqa: E402

MOCK = MockPrep()


def set_mock(enabled, scenario=None, speed=None):
    global MOCK
    CONFIG["mock"] = bool(enabled)
    if scenario is not None or speed is not None:
        CONFIG["scenario"] = scenario or CONFIG["scenario"]
        CONFIG["speed"] = float(speed or CONFIG["speed"])
        MOCK = MockPrep(CONFIG["scenario"], CONFIG["speed"])
    SYNC.wake.set()


# ───────────────────────── talking to the Prep (real or simulated) ─────────────────────────
def prep_request(method, path, query="", raw=None, auth=None):
    """One call to the Prep's REST API -> (status, content_type, bytes). Never raises: unreachable gives a 502."""
    def js(status, obj):
        return status, "application/json", json.dumps(obj).encode()
    if CONFIG["mock"]:
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        status, obj = MOCK.handle(method, path, body if isinstance(body, (dict, list)) else {})
        return js(status, obj)
    url = f"http://{CONFIG['host']}{CONFIG['base'].rstrip('/')}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, data=raw or None, method=method)
    req.add_header("Accept", "application/json")
    if raw:
        req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", auth)
    try:
        slow = any(k in path for k in ("validate", "create", "initialize", "load-complete")) or method in ("POST", "DELETE")
        with urllib.request.urlopen(req, timeout=90 if slow else 3) as r:
            return r.status, r.headers.get("Content-Type", "application/json"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "text/plain"), e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return js(502, {"error": f"Could not reach the Prep at {CONFIG['host']}: {getattr(e, 'reason', e)}"})


def prep_json(method, path, body=None):
    status, _, data = prep_request(method, path, raw=json.dumps(body).encode() if body is not None else None)
    try:
        return status, (json.loads(data) if data else None)
    except ValueError:
        return status, data.decode(errors="replace")


SYNC = Sync(CONFIG, prep_json)


# ───────────────────────── script runner ─────────────────────────
def safe_name(name):
    name = os.path.basename(urllib.parse.unquote(name or "")).strip()
    if not name or name.startswith(".") or not name.lower().endswith(ALLOWED_EXT):
        return None
    return re.sub(r"[^\w.\- ()]", "_", name)


def start_job(name, simulate, oneoff=False):
    """Run a .py from the protocol folder, or (oneoff) from the hidden scratch folder, deleting it when it finishes."""
    path = os.path.join(CONFIG["protocols"], ".oneoff" if oneoff else "", name)
    if not os.path.isfile(path) or not name.lower().endswith(".py"):
        raise ValueError("No such .py script in the protocol folder")
    host, base = CONFIG["host"], CONFIG["base"]
    if CONFIG["mock"]:  # point scripts at this server's built-in fake Prep
        host, base = f"127.0.0.1:{CONFIG['port']}", "/prep"
    env = dict(os.environ, PREP_HOST=host, PREP_BASE=base,
               PREP_SIMULATE="1" if simulate else "0", PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen([sys.executable, "-u", path], cwd=CONFIG["protocols"], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    job = {"id": uuid.uuid4().hex[:10], "name": name, "simulate": simulate, "lines": [], "done": False,
           "code": None, "proc": proc, "started": time.time()}

    def pump():
        for line in proc.stdout:
            job["lines"].append(line.rstrip("\n"))
        job["code"] = proc.wait()
        job["done"] = True
        if oneoff:
            try:
                os.remove(path)
            except OSError:
                pass

    threading.Thread(target=pump, daemon=True).start()
    with JOBS_LOCK:
        JOBS[job["id"]] = job
    return job


# ───────────────────────── HTTP ─────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "PrepInterface/1.0"

    def log_message(self, fmt, *args):
        if "/local/jobs/" not in self.path and not self.path.startswith("/prep/protocol-run"):
            sys.stderr.write("%s %s\n" % (self.command, self.path))

    # --- helpers
    def send_json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 5_000_000:
            raise ValueError("body too large")
        return self.rfile.read(n) if n else b""

    def guard(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("localhost", "127.0.0.1", "[::1]"):
            self.send_json(403, {"error": "bad Host header"})
            return False
        if self.command not in ("GET", "HEAD"):
            origin = self.headers.get("Origin")
            if self.headers.get("X-Prep-UI") != "1" or (origin and urllib.parse.urlparse(origin).hostname not in ("localhost", "127.0.0.1", "::1")):
                self.send_json(403, {"error": "missing X-Prep-UI header"})
                return False
        return True

    def do_GET(self): self.route()
    def do_POST(self): self.route()
    def do_PUT(self): self.route()
    def do_DELETE(self): self.route()

    def route(self):
        if not self.guard():
            return
        try:
            u = urllib.parse.urlparse(self.path)
            p = u.path
            if p.startswith("/prep/"):
                return self.proxy(p[len("/prep"):], u.query)
            if p.startswith("/local/"):
                return self.local(p[len("/local/"):], urllib.parse.parse_qs(u.query))
            return self.static(p)
        except ValueError as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:  # keep the server alive on handler bugs
            self.send_json(500, {"error": f"{type(e).__name__}: {e}"})

    # --- static
    def static(self, p):
        if self.command != "GET":
            return self.send_json(405, {"error": "method not allowed"})
        rel = "index.html" if p in ("/", "") else p.lstrip("/")
        full = os.path.realpath(os.path.join(WEB, rel))
        if not full.startswith(os.path.realpath(WEB) + os.sep) or not os.path.isfile(full):
            return self.send_json(404, {"error": "not found"})
        data = open(full, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(full)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # --- proxy to the Prep
    def proxy(self, path, query):
        status, ctype, data = prep_request(self.command, path, query, self.body(), self.headers.get("Authorization"))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data or b"")))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data or b"")

    # --- local API
    def local(self, path, q):
        parts = path.strip("/").split("/")
        m = self.command
        if parts[0] == "config":
            if m == "PUT":
                cfg = json.loads(self.body() or b"{}")
                host = str(cfg.get("host", CONFIG["host"])).strip()
                if not re.fullmatch(r"[\w.\-]+(:\d+)?", host):
                    raise ValueError("invalid host")
                CONFIG["host"] = host
                if cfg.get("base"):
                    CONFIG["base"] = "/" + str(cfg["base"]).strip("/")
            return self.send_json(200, {"host": CONFIG["host"], "base": CONFIG["base"], "mock": CONFIG["mock"],
                                        "folder": CONFIG["protocols"], "scenario": CONFIG["scenario"],
                                        "scenarios": list(SCENARIOS)})
        if parts[0] == "sync":
            if m == "POST":  # {enabled?: bool, now?: bool} - run a pass now and wait for it
                req = json.loads(self.body() or b"{}")
                SYNC.run_pass(manual=True)
            return self.send_json(200, SYNC.view())
        if parts[0] == "compile" and m == "POST":  # {name, values}: a saved Builder protocol -> the raw Prep protocol. values fills the protocol's variables.
            req = json.loads(self.body() or b"{}")
            name = safe_name(req.get("name"))
            fp = os.path.join(CONFIG["protocols"], name or "")
            if not name or not name.endswith(".json") or not os.path.isfile(fp):
                return self.send_json(404, {"error": "not found"})
            doc = json.load(open(fp, encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("variables"):  # a protocol with variables is only compiled once every one has a value in range
                doc, errs = spec_check.resolve(doc, req.get("values"))
                if errs:
                    return self.send_json(400, {"error": "variables", "detail": "; ".join(errs)})
            res = spec_check.validate(doc)
            if not res["valid"]:
                return self.send_json(400, {"error": "This protocol does not pass the check: " + "; ".join(res["errors"][:3])})
            try:
                return self.send_json(200, {"raw": compiler.compile_spec(res["spec"], CONFIG["protocols"])})
            except compiler.ConverterUnavailable as e:
                return self.send_json(501, {"error": "converter-unavailable", "detail": str(e)})
        if parts[0] == "labware-map":  # builder labware key -> Hamilton labware (protocols/.labware-map.json), used by the converter
            if m == "GET":
                return self.send_json(200, {"map": labware_map.read_map(CONFIG["protocols"]),
                                            "known": [{"id": o["id"], "name": o["name"], "displayName": o.get("displayName"), "classification": o["classification"],
                                                       "rows": o["rows"], "columns": o["columns"]} for o in labware_map.harvest(CONFIG["protocols"]).values()]})
            if m == "PUT":
                body = json.loads(self.body() or b"null")
                if not isinstance(body, dict):
                    raise ValueError("expected {builderKey: Hamilton labware name or id}")
                return self.send_json(200, {"map": labware_map.write_map(CONFIG["protocols"], body)})
        if parts[0] == "labware" and m == "GET":  # what the Builder may offer: the Prep's own catalogue and liquid classes, nothing else
            defs, builtin = labware_map.builder_defs()  # builtin: {key: catalogue name}
            return self.send_json(200, {"labware": defs, "builtin": builtin, "liquids": labware_map.liquids()})
        if parts[0] == "labware":
            return self.send_json(405, {"error": "Labware comes from the Prep's catalogue (labware_catalogue.json) and cannot be added here"})
        if parts[0] == "favourite" and m == "PUT":  # {name, favourite} - star/un-star, then sync straight away
            req = json.loads(self.body() or b"{}")
            name = safe_name(req.get("name"))
            if not name:
                raise ValueError("bad name")
            SYNC.set_favourite(name, bool(req.get("favourite")))
            SYNC.run_pass()
            return self.send_json(200, {"item": SYNC.describe(name) if os.path.isfile(os.path.join(CONFIG["protocols"], name)) else None, "sync": SYNC.view()})
        if parts[0] == "mock":
            if m == "PUT":  # {enabled?, scenario?}; any change re-arms the mock from scratch
                req = json.loads(self.body() or b"{}")
                sc = req.get("scenario")
                if sc is not None and sc not in SCENARIOS:
                    raise ValueError(f"scenario must be one of {list(SCENARIOS)}")
                set_mock(req.get("enabled", CONFIG["mock"]), sc or CONFIG["scenario"], req.get("speed") or CONFIG["speed"])
            return self.send_json(200, {"mock": CONFIG["mock"], "scenario": CONFIG["scenario"]})
        if parts[0] == "protocols":
            if len(parts) == 1 and m == "GET":
                return self.send_json(200, SYNC.list())
            name = safe_name(parts[1] if len(parts) > 1 else q.get("name", [""])[0])
            if not name:
                raise ValueError("file must be a .py or .json with a plain name")
            fp = os.path.join(CONFIG["protocols"], name)
            if m == "GET":
                if not os.path.isfile(fp):
                    return self.send_json(404, {"error": "not found"})
                return self.send_json(200, {"name": name, "content": open(fp, encoding="utf-8", errors="replace").read()})
            if m in ("POST", "PUT"):
                data = self.body()
                if name.endswith(".json"):
                    json.loads(data or b"null")
                SYNC.save_file(name, data)
                return self.send_json(200, {"name": name, "size": len(data)})
            if m == "DELETE":
                SYNC.delete_file(name)
                return self.send_json(200, {"deleted": name})
        if parts[0] == "oneoff":  # run something once without adding it to the library
            req = json.loads(self.body() or b"{}") if m in ("POST",) else {}
            if parts[1:] == ["script"] and m == "POST":
                name = safe_name(req.get("name"))
                if not name or not name.lower().endswith(".py"):
                    raise ValueError("a one-off script must be a .py file")
                scratch = os.path.join(CONFIG["protocols"], ".oneoff")
                os.makedirs(scratch, exist_ok=True)
                with open(os.path.join(scratch, name), "w", encoding="utf-8") as fh:
                    fh.write(str(req.get("content", "")))
                return self.send_json(200, {"id": start_job(name, bool(req.get("simulate", True)), oneoff=True)["id"]})
            if parts[1:] == ["prep"] and m == "POST":  # put a protocol on the Prep temporarily
                if not isinstance(req.get("raw"), dict):
                    raise ValueError("expected a protocol")
                return self.send_json(200, {"id": SYNC.upload_temp(req["raw"], str(req.get("name") or "One-off protocol"))})
            if len(parts) == 3 and parts[1] == "prep" and m == "DELETE":
                SYNC.delete_temp(parts[2])
                return self.send_json(200, {"deleted": parts[2]})
        if parts[0] == "jobs":
            if len(parts) == 1 and m == "POST":
                req = json.loads(self.body() or b"{}")
                name = safe_name(req.get("name"))
                if not name:
                    raise ValueError("bad script name")
                job = start_job(name, bool(req.get("simulate", True)))
                return self.send_json(200, {"id": job["id"]})
            job = JOBS.get(parts[1]) if len(parts) > 1 else None
            if not job:
                return self.send_json(404, {"error": "no such job"})
            if len(parts) == 2 and m == "GET":
                since = int(q.get("since", ["0"])[0])
                return self.send_json(200, {"lines": job["lines"][since:], "next": len(job["lines"]),
                                            "done": job["done"], "code": job["code"]})
            if len(parts) == 3 and parts[2] == "stdin" and m == "POST":
                text = json.loads(self.body() or b"{}").get("text", "")
                try:
                    job["proc"].stdin.write(text + "\n")
                    job["proc"].stdin.flush()
                except (BrokenPipeError, ValueError):
                    pass
                return self.send_json(200, {"ok": True})
            if len(parts) == 3 and parts[2] == "kill" and m == "POST":
                job["proc"].kill()
                return self.send_json(200, {"ok": True})
        self.send_json(404, {"error": "unknown local route"})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=CONFIG["host"], help="Prep address (default %(default)s)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--protocols", default=CONFIG["protocols"], help="protocol folder (default ./protocols)")
    a = ap.parse_args()
    CONFIG.update(host=a.host, protocols=os.path.abspath(a.protocols), port=a.port)
    os.makedirs(CONFIG["protocols"], exist_ok=True)
    SYNC.start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"Prep Interface on http://localhost:{a.port}  (Prep at {a.host}; switch to simulation from the machine menu, top right)")
    print(f"Protocol folder: {CONFIG['protocols']}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

def start_my_server():
    main()
    pass 



# ==========================================
# 2. SYSTEM TRAY LOGIC
# ==========================================
def load_tray_icon():
    """A plain icon for the system tray (drawn here, so there is no image file to ship)."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (64, 64), "#0f6b6b")
    ImageDraw.Draw(image).rectangle((16, 16, 48, 48), outline="white", width=4)
    return image

def on_quit(icon, item):
    # Stops the system tray icon loop
    icon.stop()
    # Force kills the background Python process cleanly
    os.kill(os.getpid(), signal.SIGTERM)
    sys.exit(0)

def setup_tray():
    """Show a tray icon with a Quit item. Needs the optional pystray and pillow packages; without them the server just keeps running."""
    try:
        from pystray import Icon, Menu, MenuItem
        image = load_tray_icon()
    except ImportError:
        print("pystray/pillow not installed: running without a tray icon (Ctrl+C to stop).")
        threading.Event().wait()
        return
    menu = Menu(MenuItem("Quit Prep Interface", on_quit))
    icon = Icon("PrepInterface", image, "Prep Interface", menu)
    icon.run()

# ==========================================
# 3. EXECUTION FLOW
# ==========================================
if __name__ == "__main__":
    # Spins up your Hamilton server code in a separate background thread
    server_thread = threading.Thread(target=start_my_server, daemon=True)
    server_thread.start()
    
    # Launches the tray icon on the main thread (keeps the script alive)
    setup_tray()
