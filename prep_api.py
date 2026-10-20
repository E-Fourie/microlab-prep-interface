#!/usr/bin/env python3
"""
Simple command-line helper for the Hamilton Microlab Prep REST API.

Run this ON the laptop that is cabled to the Prep (Prep internal PC = 192.168.100.101,
the instrument's static address; laptop Ethernet set to 192.168.100.10).
Uses only the Python standard library, so there is nothing to pip install.

Examples:
    python prep_api.py status
    python prep_api.py protocols
    python prep_api.py run <protocolId>            # SIMULATED run (safe default)
    python prep_api.py run <protocolId> --real     # real run on the instrument

User management is currently disabled on this Prep, so no login is needed and
the commands above work with no --user/--password. If user management is ever
turned back on, give credentials with --user/--password, or set the PREP_USER
and PREP_PASS environment variables.

Endpoints follow Hamilton's developer docs:
https://developer.hamiltoncompany.com/pages/prep_recipe_getting_started
This script has not been tested against a live Prep: try `status` and
`protocols` first, and do a simulated run before a real one.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_HOST = "192.168.100.101"
STATE_LOADING = 20
STATE_IDLE = 0


class Prep:
    def __init__(self, host, base_path="/NimbusLite/api/v1", timeout=15):
        self.base = f"http://{host}{base_path.rstrip('/')}"
        self.timeout = timeout
        self.token = None

    def call(self, method, path, body=None, timeout=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode() or "null"
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            sys.exit(f"HTTP {e.code} from {method} {path}: {detail}")
        except urllib.error.URLError as e:
            sys.exit(f"Could not reach the Prep at {url}: {e.reason}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def login(self, user, password):
        resp = self.call("POST", "/authenticate",
                         {"username": user, "password": password})
        token = resp.get("token") if isinstance(resp, dict) else None
        if not token:
            sys.exit(f"Login did not return a token: {resp}")
        self.token = token
        print("Logged in.")

    def login_if_needed(self, user, password):
        # Log in first when credentials are given: this Prep refuses even the
        # user-management check (401) without a token.
        if user and password:
            self.login(user, password)


def cmd_status(prep, args):
    print("system-ready:", prep.call("GET", "/system-ready"))
    print("protocol-run:", json.dumps(prep.call("GET", "/protocol-run"), indent=2))


def cmd_validate(prep, args):
    # Can be slow (may check deck/camera state) - use --timeout to extend
    # if this times out at the default.
    result = prep.call("GET", f"/protocols/validate/{args.protocol_id}",
                       timeout=args.timeout)
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and result.get("isValid") is False:
        print("\nNot valid to run yet. See validationErrors above.")


def cmd_verification_status(prep, args):
    print("verification/status:",
          json.dumps(prep.call("GET", "/verification/status"), indent=2))
    print("verification/routines:",
          json.dumps(prep.call("GET", "/verification/routines"), indent=2))


def cmd_maintenance_status(prep, args):
    print("settings/must-update-maintenance-routines:",
          json.dumps(prep.call("GET", "/settings/must-update-maintenance-routines"), indent=2))
    print("settings/must-restore-labware:",
          json.dumps(prep.call("GET", "/settings/must-restore-labware"), indent=2))
    print("settings/must-restore-verify-protocols:",
          json.dumps(prep.call("GET", "/settings/must-restore-verify-protocols"), indent=2))
    print("maintenance:",
          json.dumps(prep.call("GET", "/maintenance"), indent=2))


def cmd_protocols(prep, args):
    protocols = prep.call("GET", "/protocols")
    if not isinstance(protocols, list):
        print(json.dumps(protocols, indent=2))
        return
    for p in protocols:
        if isinstance(p, dict):
            print(f"{p.get('id')}\t{p.get('name', '')}")
        else:
            print(p)


def wait_for(prep, predicate, what, timeout):
    start = time.time()
    while True:
        state = prep.call("GET", "/protocol-run")
        if predicate(state):
            return state
        if time.time() - start > timeout:
            sys.exit(f"Timed out waiting for {what}. Last state: {state}")
        time.sleep(2)


def get_labware_names(prep, protocol_id):
    """Position -> {"name": ..., "displayName": ...} from the protocol's
    verification data. /protocol-run/load-instructions itself only gives a
    generic labwareClassification (Plate, TipRack, ...) with no actual
    labware name or tip volume, but /protocols/verify/{id} has the real
    labwareName/labwareDisplayName per deck position (e.g. "300uL Tip Rack
    Filter", "96-well PCR Plate"), so we look it up from there instead."""
    try:
        verify = prep.call("GET", f"/protocols/verify/{protocol_id}")
    except SystemExit:
        print("(Could not fetch labware names from /protocols/verify - "
              "showing load instructions without them.)")
        return {}
    names = {}
    for dp in (verify.get("deckPositions") or []) if isinstance(verify, dict) else []:
        if isinstance(dp, dict) and dp.get("position") is not None:
            names[dp["position"]] = {
                "name": dp.get("labwareName"),
                "displayName": dp.get("labwareDisplayName"),
            }
    return names


def cmd_load_instructions(prep, args):
    labware_names = get_labware_names(prep, args.protocol_id)
    print_load_instructions(prep.call("GET", "/protocol-run/load-instructions"), labware_names)


def print_load_instructions(instructions, labware_names=None):
    labware_names = labware_names or {}
    if not isinstance(instructions, list) or not instructions:
        print("No load instructions returned:", json.dumps(instructions, indent=2))
        return
    print("\nLoad the deck as follows:")
    for item in instructions:
        if not isinstance(item, dict):
            print(" -", item)
            continue
        # All variants inherit positionName/deckPosition/labwarePosition from
        # DeckLoadPositionDto, so check the type-specific fields FIRST
        # (count, carrierRow, liquidName) before falling back to plain labware -
        # a trough/tube still carries labwarePosition and would otherwise be
        # misread as a generic labware slot.
        pos = item.get("deckPosition", "?")
        name = item.get("positionName")
        label = f"Position {pos}" + (f" ({name})" if name and name != pos else "")
        holder_pos = item.get("labwarePosition")
        holder_suffix = f", slot {holder_pos} in the holder" if holder_pos is not None else ""

        lw = labware_names.get(pos, {})
        labware_desc = lw.get("displayName") or lw.get("name")

        if "count" in item:
            tip_desc = f" ({labware_desc})" if labware_desc else ""
            print(f" - {label}{holder_suffix}: load {item.get('count', '?')} tips"
                  f"{tip_desc} (suggested {item.get('suggestedCount', '?')})")
        elif "carrierRow" in item:
            vol = item.get("suggestedVolume", item.get("requiredVolume", "?"))
            print(f" - {label}{holder_suffix}: tube at carrier row "
                  f"{item.get('carrierRow')}, column {item.get('carrierColumn')} - "
                  f"fill with {item.get('liquidName') or 'liquid'}: {vol} "
                  f"(max {item.get('maximumVolume', '?')})")
        elif "liquidName" in item or "unitType" in item:
            vol = item.get("suggestedVolume", item.get("requiredVolume", "?"))
            print(f" - {label}{holder_suffix} (reservoir/trough): "
                  f"fill with {item.get('liquidName') or 'liquid'}: {vol} "
                  f"{item.get('unitType', '')} (max {item.get('maximumVolume', '?')})")
        elif "labwareClassification" in item or holder_pos is not None:
            kind = labware_desc or item.get("labwareClassification", "labware")
            print(f" - {label}: {kind} (slot {holder_pos} in the holder)")
        else:
            print(f" - {label}: {json.dumps(item)}")
    print()


def cancel_any_run_in_progress(prep, timeout):
    state = prep.call("GET", "/protocol-run")
    in_progress = isinstance(state, dict) and (
        state.get("isRunInProgress") or state.get("protocolRunState") not in (STATE_IDLE, None)
    )
    if not in_progress:
        print("No run or simulation currently in progress.")
        return
    print(f"A run is already in progress (state: {state.get('protocolRunState')}, "
          f"isRunInProgress: {state.get('isRunInProgress')}). Aborting it ...")
    prep.call("PUT", "/protocol-run/abort")
    wait_for(prep, lambda s: isinstance(s, dict)
             and s.get("protocolRunState") == STATE_IDLE
             and not s.get("isRunInProgress"),
             "previous run to abort", timeout)
    print("Previous run aborted.")


def home_instrument(prep):
    print("Homing/initializing the instrument ...")
    # This is the closest documented endpoint to "return to home"
    # (it's labelled "Initialize the current instrument").
    # If this isn't the behaviour you want, skip it with --no-home and home
    # from the touchscreen instead.
    prep.call("POST", "/instruments/initialize")
    print("Instrument initialized.")


def cmd_run(prep, args):
    simulate = not args.real
    mode = "SIMULATED" if simulate else "REAL"

    cancel_any_run_in_progress(prep, args.timeout)
    if not args.no_home:
        home_instrument(prep)

    validation = prep.call("GET", f"/protocols/validate/{args.protocol_id}", timeout=60)
    if isinstance(validation, dict) and validation.get("isValid") is False:
        print("Protocol is not valid to run:")
        print(json.dumps(validation.get("validationErrors", validation), indent=2))
        print("\nThis usually means the protocol is tied to an instrument "
              "verification routine that hasn't been run. Check "
              "'python prep_api.py verification-status' and the touchscreen's "
              "verification/QC screens rather than trying to script past this.")
        sys.exit(1)

    print(f"Creating {mode} run for protocol {args.protocol_id} ...")
    if not simulate:
        if input("This will run on the real instrument. Type YES to continue: ") != "YES":
            sys.exit("Cancelled.")
    prep.call("POST", "/protocol-run/create", {
        "protocolId": args.protocol_id,
        "simulate": simulate,
        "recompile": args.recompile,
    })

    print("Waiting for the Prep to reach the loading state ...")
    state = wait_for(prep, lambda s: isinstance(s, dict)
                     and s.get("protocolRunState") == STATE_LOADING,
                     "loading state", args.timeout)

    instructions = prep.call("GET", "/protocol-run/load-instructions")
    labware_names = get_labware_names(prep, args.protocol_id)
    print_load_instructions(instructions, labware_names)
    input("Load the deck as shown above, then press Enter to start the run ... ")

    # loadCompleteAction: 0 = Run, 1 = Abort (per LoadCompleteAction enum)
    prep.call("PUT", "/protocol-run/load-complete", {
        "protocolId": args.protocol_id,
        "simulate": simulate,
        "loadCompleteAction": 0,
    })
    print("Run started. Waiting for completion ...")
    time.sleep(3)
    state = wait_for(prep, lambda s: isinstance(s, dict)
                     and (s.get("protocolRunState") == STATE_IDLE
                          or s.get("isRunInProgress") is False),
                     "run completion", args.timeout)
    print("Finished:", json.dumps(state, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Hamilton Microlab Prep API helper")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Prep IP (default {DEFAULT_HOST})")
    parser.add_argument("--base", default="/NimbusLite/api/v1",
                        help="API base path (default /NimbusLite/api/v1)")
    parser.add_argument("--user", default=os.environ.get("PREP_USER"))
    parser.add_argument("--password", default=os.environ.get("PREP_PASS"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="check the API is up and show the run state")
    sub.add_parser("protocols", help="list protocols and their ids")

    validate = sub.add_parser("validate", help="check whether a protocol is valid to run")
    validate.add_argument("protocol_id")
    validate.add_argument("--timeout", type=int, default=60,
                          help="seconds to wait for a response (default 60)")

    sub.add_parser("verification-status",
                   help="show instrument verification routine status (read-only)")
    sub.add_parser("maintenance-status",
                   help="show maintenance/labware/verify flags and procedure list (read-only)")
    load_instr = sub.add_parser("load-instructions",
                   help="show current load-instructions for an in-progress run (read-only)")
    load_instr.add_argument("protocol_id", help="used to look up labware names/types")

    run = sub.add_parser("run", help="create and run a protocol")
    run.add_argument("protocol_id")
    run.add_argument("--real", action="store_true", help="real run (default is simulated)")
    run.add_argument("--recompile", action="store_true")
    run.add_argument("--timeout", type=int, default=3600, help="seconds to wait per stage")
    run.add_argument("--no-home", action="store_true",
                     help="skip homing/initializing the instrument before this run")

    args = parser.parse_args()
    prep = Prep(args.host, args.base)
    prep.login_if_needed(args.user, args.password)

    {"status": cmd_status, "protocols": cmd_protocols, "validate": cmd_validate,
     "verification-status": cmd_verification_status,
     "maintenance-status": cmd_maintenance_status,
     "load-instructions": cmd_load_instructions, "run": cmd_run}[args.command](prep, args)


if __name__ == "__main__":
    main()
