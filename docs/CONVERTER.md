# Converter: Builder protocol (spec) to raw Prep protocol

`compiler.compile_spec(spec, folder=None)` returns the JSON the Prep's `POST /protocols` takes (no top-level `id`; the one-off upload
adds `makeNameUnique`). CLI: `python3 compiler.py spec.json [protocols-folder]` prints it, or why it cannot be compiled.
Anything unsupported or ambiguous raises `ConverterUnavailable` with the reason; nothing is guessed.

**Status: written from a single real exported protocol, never uploaded to a live Prep.** The first real upload is the real test.
If the Prep rejects it, the card shows the Prep's `validationErrors`; compare the compiled JSON with a protocol pulled from the Prep,
starting with the items under "Unknowns" below.

## Mapping

| Spec | Prep step |
| --- | --- |
| `transfer`/`distribute` from a reservoir slot to a plate | `AddReagentStep`: `liquidLabwareGroupId` is the slot's compartment group; `destinations` is an address list; multi-dispense on. Consecutive steps with the same slot, plate and volume (no other step between, no repeated well) become one step, wells concatenated in order |
| `transfer` plate/tubes to plate/tubes | `TransferSamplesStep`: `sources` and `destinations` are paired by order; grouped like the above |
| `shake` | `HeatShakeStep`: `duration` = minutes x 60 (seconds); `shake` if rpm > 0, `heat` if temp > 0 |
| `pause` | `PauseStep` with the message, `pauseForSetDuration` false |
| `move` | `TransportStep` |
| `mix` | `mixAfterDispense` on the `TransferSamplesStep` right before it, only when it mixes exactly the wells that step filled (`mixVolume`, `mixCycles` in `dispenseSettings`). Otherwise refused |

Wells become `{row, column}`, 1-based, column-major per range (as the Builder orders them). Position numbers in steps are the plate's
**home** (starting) position, as in the Prep's own protocols; the compiler tracks where the gripper put each plate (same logic as the
simulator's `occ`), so a spec's "plate on the magnet at 5" becomes position 3 again. `move 3 -> 5` is `TransportStep(3, 5)`, back is
`(3, 3)`. Only moves between a plate's home and the magnet are emitted. A move onto position 1 (heater-shaker) and back is absorbed: the Prep
carries the plate there itself inside the shake step, and the shake names the plate's home.
Tip rack: the rack the spec's tip simulation takes from (`tipLabwareGroupId`); if tips would come from different kinds of rack within one step it refuses.
The deck lists all 8 positions; position 1 is always empty with `hardwareRequirements.hhs` set when a shake exists.
Not emitted because a spec cannot say it: discard to waste (`dispenseToWaste`), the how-many-samples `RtsaStep`.

## Variables

A protocol may declare `variables: [{name, label, default, min, max, unit}]` and use one in a step's `vol`, `cycles`, `temp`, `rpm` or `minutes` as the text
`"$name"` (for example `"vol": "$beads"`). The Builder shows the default. When someone presses Run, the page asks for each value (`askVariables` in `web/app.js`,
checked live against the same rules), then posts `{name, values}` to `/local/compile`. `spec_check.resolve(spec, values)` fills them in and checks the result again, so a
value that does not suit its field is refused with the reason (HTTP 400, `error: "variables"`). `compiler.compile_spec(spec, folder, values)` does the same, and
`python3 compiler.py spec.json [folder] beads=120 elute=40` from the command line. A protocol with variables and no values never compiles.

## Labware and liquids

Only what the Prep itself lists is used: `labware_catalogue.json` (270 labware with the Prep's real ids) and `liquid_classes.json` (its 16 liquid classes).
Nothing is harvested from pulled protocols any more and the Builder cannot add hardware.

Keys resolve in this order (`labware_map.resolve_key`): `protocols/.labware-map.json` override, the built-in keys (`tips_50`, `tips_300`, `tips_1000`, `plate_pcr`,
`plate_deep`, `plate_flat_96`, `plate_black_96`, `plate_384`, `reservoir_60`, `tubes_24`, `magnet`, each pointing at one catalogue entry in `labware_map.BUILTIN`),
then `c_<id>`, the catalogue entry with that Prep id (what the Builder's labware list produces: plates, tip racks and 24-position tube racks).
`reservoir_50` and `trough_25` have no catalogue counterpart and refuse to compile. An entry must have the right class and, for plates, tips and tubes, the same rows x columns.
A reservoir/trough is only usable if its entry names a single container (`containerName`), as the 5 x 60 mL carrier does.

Every liquid in `contents` must be one of the Prep's liquid classes (case-insensitive); it becomes the compartment group's `liquidName` and `liquidType`.
Plate-to-plate transfers take `liquidType` from the liquid loaded in the first source well, else `Aqueous`.

## Assumptions and unknowns (check these first if the Prep rejects it)

- **Ids.** Steps, settings blocks and filled positions get unique positive ids (910001 up; empty positions stay 0, as in exports) because the Prep rejected id-0 steps ("a protocol must have one enabled step"). Labware group ids are fresh
  positive numbers from 900001, consistent within the document, because 0 would make every reference ambiguous and a positive number could collide with
  ids already in the Prep. Labware catalogue ids are the Prep's own. Unverified: the Prep may want other ids, or omitted keys.
- **hardwareRequirements.** `hhs` true only when a shake exists; with any 16-row (384) plate on the deck the steps use `TwoChannel` (`twoChannel` true, `mph` false); otherwise `EightChannel` (`mph` true, `twoChannel` false); both as in protocols exported from this lab's Prep. Plate labware objects carry `wellMap`/`disabledWells`, as in those exports. The gripper has no flag in the real file.
- **Settings templates** (heights, flow-follow, multi-dispense pre/post aliquots of 20 uL, `customLiquidSettings` null, `tipReuseOption` PerSample, discard to waste,
  `newTipsEachTransfer` true for sample transfers) are copied in structure and values from the real protocol's steps. Aspirate and dispense `transferVolume` carry the
  spec volume. Pipetting heights and liquid classes were tuned for one lab's liquids and plates.
- **Liquid type** is always `Aqueous` (the real file uses `Magnetic Beads` for beads); the spec only has a liquid name.
- **Compartments.** Spec slot A..E is compartment column 1..5 of the carrier (assumed same order as the Prep). Filled slots get `liquidVolume` = the container's capacity
  (as the real file does with `CalculateForMe`), empty ones 0 and a shared empty group.
- **Units.** `HeatShakeStep.duration` and `PauseStep.duration` are seconds (consistent with the real file's values and message, e.g. 120 for "spin for 2 mins").
  A message pause carries a placeholder duration of 120 s that is ignored (`pauseForSetDuration` false). A heat-only shake keeps rpm 1000, a shake-only keeps temperature 37.
- **Mix.** Only mix-after-dispense on a `TransferSamplesStep`. Mixing after a reagent addition (multi-dispense) is refused because it is not known whether the Prep allows it.
- **RtsaStep** ("how many samples") is omitted; whether the Prep requires it is unknown.
- **Position 1.** A plate placed in position 1 is refused; the real protocol keeps it empty.
- **Volumes** are not checked against tips or wells beyond the Builder's own simulation; the Prep validates on upload.
- Reagent addition `distribute` uses one tip for the group in the simulation; the Prep decides its own tip use (`PerSample`).

## Tests

`tests/test_compiler.py` and `tests/fixtures/bead_purification_spec.json` (a spec equivalent of the real protocol). The test that compares structure (key sets per step type,
labware objects, deck shape) with the real protocol runs only if `tests/fixtures/real_bead_purification.json` exists; that file is private to the lab and is git-ignored.
