"""
Watches the Prep while the interface server runs. It auto-answers missing-tip errors (up to MAX_TIP_RETRIES in a row on one step,
then it stops touching the Prep), and, if a Slack webhook is set up, posts to Slack when the Prep needs a person (an error it
cannot fix, tips to reload) and when a run ends. Slack is optional: with no webhook the watcher still answers tip errors, and
whatever would have been posted is only printed to the console. Apart from the tip answer it only GETs from the Prep.

    python slack_watch.py            watch the Prep until Ctrl-C
    python slack_watch.py --test     send one test message and exit
    python slack_watch.py --dry-run  print the messages instead of sending them

The optional Slack incoming-webhook URL comes from the SLACK_WEBHOOK_URL environment variable or from protocols/.slack.json
({"webhook": "https://hooks.slack.com/services/..."}); keep it out of the repo. PREP_HOST overrides the Prep address.
Standard library only.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "http://%s/NimbusLite/api/v1" % os.environ.get("PREP_HOST", "192.168.100.101")
FOLDER = os.path.join(HERE, "protocols")   # where .slack.json lives
POLL_S = 5


def prep(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=20) as r:
            return json.loads(r.read().decode("utf-8-sig") or "null")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def webhook():
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        try:
            with open(os.path.join(FOLDER, ".slack.json"), encoding="utf-8") as f:
                url = json.load(f).get("webhook")
        except (OSError, ValueError):
            url = None
    return url


def send(text, dry=False):
    if dry:
        print(text, "\n", flush=True)
        return True
    url = webhook()
    if not url:   # Slack is optional: say it on the console instead
        print("[watcher, no Slack] " + text.replace("\n", " | "), flush=True)
        return False
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except (urllib.error.URLError, OSError) as e:
        print("Slack send failed:", e, file=sys.stderr)
        return False


def step_of(data, when):
    """The runtime step that was running at `when` (ISO text), else the last one started."""
    steps = data.get("runtimeSteps") or []
    for s in steps:
        if s.get("stepStart") and s["stepStart"] <= when and (not s.get("stepEnd") or when <= s["stepEnd"]):
            return s
    return steps[-1] if steps else None


def error_text(err, data):
    step = step_of(data, err.get("timeStamp", ""))
    lines = [":warning: *Prep needs attention*  run \"%s\"" % data.get("protocolName", "?")]
    if step:
        lines[0] += ", step %s (%s)" % (step.get("stepOrder"), step.get("name"))
    detail = [d.get("message", "").replace("\r\n", " ").strip() for d in err.get("errorData") or []] or [err.get("message", "")]
    lines += ["Error: " + d for d in detail]
    opts = sorted({r["response"] for d in err.get("errorData") or [] for r in d.get("errorResponses") or []} or {r["response"] for r in err.get("errorResponses") or []})
    if opts:
        lines.append("Options on the Prep: " + ", ".join(opts))
    lines.append("Waiting for someone at the instrument.")
    return "\n".join(lines)


MAX_TIP_RETRIES = 3   # missing-tip errors answered with "Move to Next" in a row on one step; the next one is left for a person
def respond(err, response):
    """Answer a runtime error on the Prep with `response` (PUT /errors/runtime, from the Prep's swagger). The group
    answer is 'Continue' and each inner error gets the chosen response, as the Prep's own screen sends it."""
    body = {"id": err["id"], "groupId": err.get("groupId") or err["id"], "selectedResponse": "Continue",
            "errorData": [{"id": d["id"], "selectedResponse": response} for d in err.get("errorData") or []]}
    req = urllib.request.Request(BASE + "/errors/runtime", method="PUT", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except urllib.error.HTTPError as e:
        print("Prep refused the answer:", e.code, e.read().decode("utf-8", "replace")[:200], file=sys.stderr)
        return False


RESPOND = respond   # RESPOND(error, response_name) -> bool; tests replace it


def texts(err):
    return [d.get("message", "") for d in err.get("errorData") or []] or [err.get("message", "")]


def options(err):
    return {r["response"] for d in err.get("errorData") or [] for r in d.get("errorResponses") or []} | {r["response"] for r in err.get("errorResponses") or []}


def auto_response(err):
    """The response to give this error without asking anyone, or None.
    - a tip pick-up problem (wrong or missing tip) offering 'Move to Next', which retries the same step with the next tip;
    - 'unable to find any liquid in the well' offering 'Bottom', which aspirates from the bottom of the well."""
    inner = {r["response"] for d in err.get("errorData") or [] for r in d.get("errorResponses") or []}
    msgs = texts(err)
    if "Move to Next" in inner and any(re.search(r"\btip\b", t, re.I) for t in msgs):
        return "Move to Next"
    if "Bottom" in inner and any("unable to find any liquid" in t for t in msgs):
        return "Bottom"
    return None


def handle(err, data, state, dry=False):
    """Auto-answer the errors auto_response() knows: a missing tip (Move to Next) or 'no liquid found' (Bottom), up to
    MAX_TIP_RETRIES times in a row on one step. After that, or if the Prep refuses the answer, it does not try again: it
    alerts on Slack when a webhook is set up, otherwise only prints. Every other error alerts at once.
    Without RESPOND nothing is auto-answered."""
    step = step_of(data, err.get("timeStamp", ""))
    key = step.get("stepOrder") if step else None
    answer = auto_response(err)
    if answer and RESPOND is not None:
        key = (answer, key)
        n = state["tips"][1] + 1 if state["tips"][0] == key else 1
        state["tips"] = (key, n)
        if n <= MAX_TIP_RETRIES:
            try:
                if RESPOND(err, answer):
                    return "auto"
            except Exception as e:
                print("Auto-answer failed:", e, file=sys.stderr)
        note = "\n(%d of the same error in a row on this step, or the auto-answer failed. Not retrying.)" % n
        send(error_text(err, data) + note, dry)
        return "alert"
    state["tips"] = (None, 0)
    send(error_text(err, data), dry)
    return "alert"


def needs_reload(run):
    """The Prep has stopped to ask for tips (or other deck items) to be reloaded: the run state is 'Reloading'. The state may
    come as its name or as a number, so a numeric state that is not one of the known ones is confirmed through the
    reload instructions, which list the tip racks to refill."""
    st = run.get("protocolRunState")
    if isinstance(st, str):
        return st.lower() == "reloading"
    if st in (0, 10, 20, 30, None) or not run.get("isRunInProgress"):
        return False
    return bool(reload_racks())


def reload_racks():
    items = prep("/protocol-run/reload-instructions")
    return [i for i in items if isinstance(i, dict) and "count" in i] if isinstance(items, list) else []


def reload_text(run):
    racks = reload_racks()
    lines = [":package: *Prep needs tips reloaded*  run \"%s\"" % (run.get("protocolName") or "?")]
    for r in racks:
        lines.append("Position %s: load %s tips" % (r.get("positionName") or r.get("pos") or r.get("deckPosition") or "?", r.get("suggestedCount") or r.get("count")))
    lines.append("Waiting for someone at the instrument.")
    return "\n".join(lines)


def check(state, dry=False):
    """One poll. `state` keeps what has been announced: seen error ids, whether a run was active."""
    run = prep("/protocol-run")
    if not isinstance(run, dict):
        return
    rid = run.get("runDatalId")
    active = bool(run.get("isRunInProgress"))
    data = prep("/run-data/" + rid) if rid else None
    reloading = active and needs_reload(run)
    if reloading and not state.get("reloading"):   # once per reload, not once per poll
        send(reload_text(run), dry)
    state["reloading"] = reloading
    if isinstance(data, dict) and active:
        now = time.time()
        for err in data.get("errorMessages") or []:
            if err.get("handled"):
                continue
            last = state["errors"].get(err["id"])
            if last is None:
                state["errors"][err["id"]] = now
                handle(err, data, state, dry)
    if active and rid:
        state["last"] = (rid, run.get("protocolName"))   # the Prep forgets the run id once it is idle, so keep it
    if state["active"] and not active and state.get("last"):
        state["ending"], state["tries"] = state["last"], 0
        state["last"] = None
    if state.get("ending"):   # the run just stopped: announce its result once the Prep has written it
        erid, name = state["ending"]
        d = prep("/run-data/" + erid)
        state["tries"] += 1
        result = d.get("runResult") if isinstance(d, dict) else None
        if result or state["tries"] >= 6:
            result = result or "unknown (could not read the result)"
            icon = ":white_check_mark:" if result == "Completed" else ":x:"
            send("%s Run \"%s\" ended: %s" % (icon, (d or {}).get("protocolName") or name or "?", result), dry)
            state["ending"] = None
    state["active"], state["rid"] = active, rid


def loop(dry=False):
    state = {"errors": {}, "active": False, "rid": None, "tips": (None, 0), "reloading": False, "last": None, "ending": None, "tries": 0}
    while True:
        try:
            check(state, dry)
        except Exception as e:   # never let one bad poll end the watcher
            print("Slack watcher:", e, file=sys.stderr)
        time.sleep(POLL_S)


def start(host, folder):
    """Run the watcher in a background thread (the interface server calls this). It always runs, because the tip auto-answer
    does not need Slack. Returns whether a Slack webhook is set up."""
    global BASE, FOLDER
    BASE, FOLDER = "http://%s/NimbusLite/api/v1" % host, folder
    import threading
    threading.Thread(target=loop, daemon=True, name="prep-watch").start()
    return bool(webhook())


def main(argv):
    dry = "--dry-run" in argv
    if "--test" in argv:
        sys.exit(0 if send(":test_tube: Test message from the Prep watcher. If you can read this, alerts are working.", dry) else 1)
    print("Watching %s (Ctrl-C to stop); Slack %s" % (BASE, "on" if webhook() else "off (no webhook)"), flush=True)
    loop(dry)


if __name__ == "__main__":
    main(sys.argv[1:])
