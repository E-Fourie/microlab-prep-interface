# Development without the instrument (simulation mode)

Simulation mode replaces the Prep with a built-in fake (`mock_prep.py`) so the whole interface - run engine,
maintenance auto-fix, deck maps, library, pull/push, scripts - works with no machine attached.

## Start it

Run `python3 server.py` as normal, open the page, click the **machine pill (top right)** and choose **Simulation**.
Choose **Real Prep** to go back. There are no launch flags for this. Simulation shows an amber pill, with a scenario picker and **Reset**
in the same menu. Python scripts run from the library are pointed at the fake Prep too (`PREP_HOST`/`PREP_BASE` are set for them).
Simulated protocols are kept apart from the real ones (`simulated: true` in their `_prepSource`, and a separate sync state file).

## Scenarios

| Scenario | What it exercises |
| --- | --- |
| `blocked` | Maintenance routines need updating and a procedure is expired - the run is auto-fixed, then continues |
| `clean` (default) | Nothing blocks runs: ready to go |
| `labware` | "Must restore labware" - not fixable via the API, so shown as a manual step |
| `verification` | Validation fails for a non-maintenance reason |
| `offline` | Every call fails, as if the cable were unplugged |

Tests speed it up with `PUT /local/mock {"enabled": true, "speed": 40}`.
Changing scenario or pressing Reset re-arms the fake from scratch.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Starts the server on a free port, switches it to simulation over the API, and drives the full flow over HTTP: blocked run → fix → run,
unfixable labware, offline, library + script runs, favourites ↔ Prep sync, and the CSRF/Host guards. No instrument or network needed.

## One protocol format

`web/prep-protocol.js` defines the labware, deck rules and limits, variables, `check()`, `resolve()`, `simulate()` and `validate()`; `web/prep-catalogue.js` is generated
from the instrument's exports by `node scripts/build-prep-catalogue.js <folder with labware_catalogue.json and liquid_classes.json>`. `app.js` and `builder.js` use them as
`PrepCalc.prepProtocol` (loaded before `app.js`). The builder simulates its own half-filled working protocol with `simulate()`; `check()` is for protocols from outside.

`spec_check.py` is the Python port of `check()`, `resolve()` and the deck placement rules, used before compiling. Both checkers run every vector in
`tests/vectors/prep-protocol.json`; "valid" means no `check()` errors and no deck placement errors. Add a vector whenever a rule is added: the Python and JavaScript
checkers must agree on all of them.

## Layout

- `server.py` - static files, `/prep/*` proxy, `/local/*` API (protocol folder, jobs, config, mock switch)
- `sync.py` - favourites ↔ Prep sync (background thread, state in `protocols/.prep-sync*.json`)
- `spec_check.py` - Python port of the shared protocol checker; `compiler.py` - spec -> raw Prep protocol (`ConverterUnavailable` when it cannot be done faithfully); `labware_map.py` - labware key -> Prep catalogue (labware_catalogue.json, liquid_classes.json) (`GET`/`PUT /local/labware-map`, `protocols/.labware-map.json`)
- catalogue: `GET /local/labware` lists what the Builder offers (the Prep's labware and liquid classes); nothing can be added
- `mock_prep.py` - the fake Prep; add an endpoint here when the UI starts using a new one
- `web/prep-protocol.js`, `web/prep-catalogue.js` - the protocol rules and the Prep's labware and liquids
- `web/select.js` - styles every native `<select>` (keeps it hidden and in sync); `web/example_*.json` - the Builder's examples
- `web/app.js` - protocols home, machine menu, run engine, deck map, maintenance; `web/builder.js` - builder + simulator
- `prep_sdk.py` - helper for Python protocols
