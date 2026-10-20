# Hamilton Microlab Prep Interface

A browser-based control panel for the Hamilton Microlab Prep, replacing the instrument's touchscreen workflow.

**Unofficial.** This project is not made, endorsed or supported by Hamilton Company. "Hamilton" and "Microlab" are trademarks of their owners and are used here only to say which instrument it works with. It drives a laboratory robot: try everything in simulation first, and you are responsible for what you run.
Unofficial and independent of Hamilton. No login (assumes Prep user management is off).

## Run it

On the laptop cabled to the Prep (laptop Ethernet `192.168.100.10`, Prep `192.168.100.101`):

```bash
python3 server.py            # then open http://localhost:8080
```

Python 3; the server itself uses only the standard library (a tray icon is shown if `pystray` and `pillow` are installed, otherwise it is skipped). The page tries the Prep straight away. The **machine pill (top right)** shows the connection;
click it to retry, change the address, switch between the **real Prep and a built-in simulation**, set run behaviour and control sync.
The server proxies `/prep/*` to the instrument (browsers can't call it directly), stores protocols in `./protocols`, and runs
dropped-in Python scripts. It binds to `127.0.0.1` only.

## What it does

- **Protocols** - one list of cards; type doesn't matter. Press **Run** on a card: choose real or practice, and the interface
  checks the Prep, fixes any maintenance blockers automatically, then shows the deck to load. Drop files anywhere to add them.
- **Star = on the Prep.** Everything stored on the Prep is copied in and starred automatically. Starring a protocol uploads it;
  un-starring (or deleting) removes it from the machine, keeping a copy here and a safety copy in `protocols/.deleted/`. Edits on
  either side sync to the other (the Prep wins a clash; your version is kept as a backup). A protocol removed on the touchscreen is
  un-starred here. Scripts and Builder protocols only run from this laptop, so starring them just pins them to the top. Sync pauses during runs.
- **Deck** - 2 columns × 4 positions, numbered down each column. Position 1 is the heater-shaker (HHS, plates only). The 60 mL reagent
  reservoir rack (5 slots, which liquid is in which slot matters) only fits the bottom positions, 4 and 8. Hover a position for what to load there.
- **Builder** - lay out the deck, add steps (transfer / distribute / mix / shake / pause), starting liquids; validates volumes, tips,
  capacities and placement, gives a "how to load" guide, and animates a simulation. Export `.json` or `.py`.
- **Variables** - a protocol can declare variables (a name, the question to ask, a default, and a smallest and largest value) and use one in a step's
  volume, cycles, temperature, speed or minutes instead of a fixed number. When you press **Run**, the page asks for each value (and shows what you will need
  to load for those values), checks they are in range, builds the protocol with them and runs it. Use it for things like a bead clean-up where the amount of
  beads changes from run to run. **Load example → Bead clean-up with variables** shows it.
- **Labware and liquids** - only what the Prep itself lists: the labware in `labware_catalogue.json` and the liquid classes in `liquid_classes.json`
  (both exported from the instrument). The Builder offers its plates, tip racks and 24-tube racks, the 60 mL reagent reservoir rack and the magnet, and every
  liquid must be one of its liquid classes. A protocol cannot bring labware of its own. **Load example** offers examples, including one that moves a plate between the
  heater-shaker and the magnet with the gripper.
- **Maintenance** - live state, blockers, procedures, one-click fix.

### Converter

`compiler.py` turns a Builder protocol into the Prep's own format (learned from one real exported protocol; see `docs/CONVERTER.md`). It is **not yet tried on a live Prep**:
the first real upload is the test, and if the Prep rejects it the reason shows on the card. Run puts the protocol on the Prep for that run only and removes it
afterwards. Anything the converter cannot express faithfully makes it refuse with the reason; Run then opens the Builder simulation instead.
Try it: `python3 compiler.py spec.json [protocols-folder] [name=value ...]` (`name=value` fills a protocol variable).

### Python scripts

Scripts run with `PREP_HOST`, `PREP_BASE` and `PREP_SIMULATE` set. Use the bundled helper:

```python
from prep_sdk import Prep, simulate_default
Prep().run_protocol("<protocolId>", simulate=simulate_default())
```

Scripts must honour `PREP_SIMULATE` themselves if they don't use `prep_sdk`. `prep_api.py` is the original CLI helper.

## Known limits

- The Prep API runs protocols that already exist on the instrument. Builder protocols can be simulated in the browser; a **real** run needs a "Linked Prep protocol ID" (the Builder does not create protocols on the instrument).
- Deck layout and labware capacities are schematic approximations (`LABWARE` in `web/prep-protocol.js`).
- Labware-restore and protocol re-verify blockers have no documented API endpoint, so they are reported as manual steps.
- Protocol sync assumes `GET/POST/DELETE /protocols` as in Hamilton's docs; the schema is only partly documented and none of it has been tested against a live Prep. Try with one protocol first (a safety copy is always kept before anything is removed from the machine).

## License

MIT, see [LICENSE](LICENSE). `labware_catalogue.json` and `liquid_classes.json` are lists exported from a Prep and belong to their owners; see [NOTICE](NOTICE).
