# Manual Testing Guide

A structured checklist for manually testing the **Infrared City GIS** QGIS
plugin against a running backend. Covers every function reachable from the
plugin's toolbar dialogs. Work top to bottom — later sections assume an API
key is set and building geometry exists.

> This is a **manual / exploratory** guide (there is no automated UI test).
> Tick each ☐ as you go; note the plugin version (`metadata.txt`) and QGIS
> version in your test report.

## Prerequisites

- QGIS 3.44 – 3.x **and** QGIS 4.x with the plugin installed (from ZIP or the repo folder). `metadata.txt` declares `qgisMaximumVersion=4.99`, and the Plugin Manager offers the plugin on that promise alone, so run the full round on **both** a QGIS 3 and a QGIS 4 install before every release.
- An Infrared City API key with an active subscription.
- Test data:
  - A **building** layer or coordinates to fetch one.
  - A **tree** layer — use [`examples/osm-trees-sample.geojson`](examples/osm-trees-sample.geojson) (rename the loaded layer to contain `tree-`), or a real OSM export.
- Network access to `api.infrared.city`.

## Toolbar reference

The plugin adds these actions (left to right):

| # | Action | Opens |
|---|--------|-------|
| 1 | Save API Key | Auth dialog |
| 2 | Fetch building geometry | Fetch-geometry dialog |
| 3 | Fetch ground materials | Ground-materials fetch dialog |
| 4 | Select tile | Makes a 512 m selection on the map (no mode, no state) |
| 5 | Tree catalog | Tree-catalog dialog |
| 6 | Run simulation | Run-simulation dialog |

---

## 1. API key

- ☐ **Save a valid key** — Save API Key → paste key → save. Expect "verified and saved" success message, dialog closes, all toolbar icons enabled.
- ☐ **Change the key** — reopen Save API Key, paste a different valid key, save. Expect the new key to take effect (subsequent fetches/simulations use it; registries re-fetch).
- ☐ **Invalid key** — save a bogus key. Expect a rejection dialog referencing connectors@infrared.city, the key is NOT saved, and (if no valid key was stored before) the other toolbar icons stay greyed out.
- ☐ **No key** — with no key saved, all toolbar icons except Save API Key are greyed out.
- ☐ **Offline save** — disconnect network, save a plausible key. Expect a "could not verify / NOT saved" warning distinct from the invalid-key rejection; the key is not stored.
- ☐ **Startup with revoked saved key** — a bad stored key can no longer be *saved* (save validates first); it arises when a key is revoked/expires *after* saving. Setup: save a valid key, then revoke that key (delete it in the infrared.city dashboard) — or overwrite it unvalidated from the QGIS Python console: `QSettings().setValue("infrared_city/api_key", "bogus")`. Restart QGIS. Expect a message-bar warning, icons greyed out except Save API Key, and a hover tooltip on the greyed icons explaining a valid key is required.
- ☐ **Startup offline with good saved key** — restart QGIS with a valid key stored but no network. Expect icons to stay ENABLED (an outage must not lock the plugin); calls fail later with connection errors.

## 2. Fetch building geometry

- ☐ **Fetch by coordinates** — Fetch building geometry → enter coordinates → fetch. Expect a buildings layer loaded over a ~1 km × 1 km area, with heights.
- ☐ **Empty area** — fetch over an area with no buildings. Expect a clear "nothing found" message (after the single-request + tile-fallback attempts), not a crash.
- ☐ **Rendering** — the buildings layer is styled and visible on the map.

## 3. Select tile (single-tile mode)

The toolbar button is a **toggle**. Pressed means a 512×512 m box is armed and
the next run is ONE job (~10 tokens) instead of the four the area tiler would
charge. Submitting a simulation ends the mode; a ground-material fetch does not.

- ☐ **Pick a tile** — Select tile → click on the map. Expect the buildings in the box highlighted **and the toolbar button pressed**.
- ☐ **Empty tile is refused** — pick a tile with no buildings. Expect a warning, nothing armed, and the button **not** pressed.
- ☐ **Cancelling leaves it off** — press Select tile, close the dialog without picking. Expect the button released.
- ☐ **Run Simulation reads 1 tile** — with the button pressed, expect the title **1 tile · ~10 tokens**. This is the regression to watch: it must not say 4 or 9. The run is the picked BOX, not the hull of the highlighted buildings, which extends past it.
- ☐ **Fetch ground materials keeps the mode** — fetch with the button pressed, then reopen Run Simulation. Expect it **still** armed, so you can run on the materials you just fetched without re-picking.
- ☐ **A run ends the mode** — submit a simulation. Expect the highlight gone **and** the button released, together. Reopening Run Simulation is back to area mode.
- ☐ **Release returns to area mode** — click the pressed button. Expect a message-bar confirmation, the highlight cleared, and the button released.
- ☐ **Release does not re-open the pick dialog** — clicking a pressed button only releases.
- ☐ **A hand-cleared selection is reconciled** — with the button pressed, clear the QGIS selection yourself, then open Run Simulation. Expect it to have dropped back to area mode rather than running an invisible tile.
- ☐ **Saving an API key releases it** — a tile picked under one account must not carry into another.

## 4. Fetch ground materials

- ☐ **Requires a building selection** — with no building features selected, open Fetch ground materials. Expect *"Please select a building area first"*.
- ☐ **Tile-count preview** — select building features, open the dialog. Expect the selection size shown in tiles.
- ☐ **>100 tiles rejected** — select a large area (> 100 tiles). Expect a "select a smaller area" message and the fetch **blocked**.
- ☐ **Fetch succeeds** — select a reasonable area → Fetch. Expect one editable `ground-<material>` layer **per material** (asphalt, concrete, vegetation, soil, water — no building layer; buildings come from the Fetch Geometry dialog), added to the project.
- ☐ **Result dialog** — a summary lists the created layers **by layer name** with feature counts.
- ☐ **Repeated fetch numbers layers** — fetch again (different/overlapping area). Expect `ground-asphalt-2`, etc. — no overwrite, both sets present.
- ☐ **No data** — fetch over an area with no ground-material data. Expect *"No ground material data was found"*, not a crash.
- ☐ **Editable** — the `ground-*` layers are memory layers you can edit before a run.

- ☐ **The dialog stays alive during the read** — start a fetch and watch: the clock keeps ticking and the window repaints. It must be possible to close the dialog mid-read. (The dialog is modal, so QGIS itself is out of reach until it closes — what is being checked is that nothing is frozen, not that you can work meanwhile.)
- ☐ **The status line counts up** — expect `Reading ground materials from Overture… m:ss`, ticking once a second, with a note that it can take minutes on a slow connection.
- ☐ **A timeout says what to do** — if the read times out (limit: 5 min per read, 10 min in total), expect a "Fetch Timed Out" dialog naming the connection, NOT the API key (this read sends no key).
- ☐ **A failed download is named** — block `overturemaps-us-west-2.s3.us-west-2.amazonaws.com` (hosts file or firewall) and fetch. Expect *Ground Materials Could Not Be Read* with "No tokens were charged" and a proxy/firewall hint — NOT *Unexpected Error*, and NOT an API-key message.
- ☐ **Closing mid-read is safe** — close the dialog while a read is running. Expect no crash and no result appearing later; the download finishes in the background and its result is dropped (it cannot be cancelled).
- ☐ **A second fetch waits for the first** — close the dialog mid-read, reopen it and press Fetch straight away. Expect *Previous Fetch Still Running* and no second download. Once the first read has finished, a fetch works again.

## 5. Run simulation

- ☐ **No selection** — with nothing selected and no tile picked, open Run Simulation. Expect a *No Selection* notice saying what to select, and no error traceback in the plugin log.

### 5a. Single tile

- ☐ Arm a tile with **Select tile**, then Run Simulation. Expect the title to read **1 tile · ~10 tokens**, and no area tiling.
- ☐ Run it through: expect a result raster loaded and styled.

- ☐ **A failed auto-fetch is not silent** — tick *Use Infrared City ground materials*, run with the network blocked or a very slow connection. Expect the status line to warn that QGIS may not respond for up to 2 minutes, then a message-bar warning that STAYS (no auto-hide) saying the simulation is running WITHOUT them and, after a timeout, pointing to the Ground Materials dialog. QGIS must respond again within ~2 minutes, and the run itself should still complete. Check both single-tile and area.

### 5b. Area (multiple tiles)

- ☐ Without a tile pick, select an area, Run Simulation. Expect a tile-count preview and a multi-tile area run.
- ☐ Run it through: expect the merged result raster.

### 5c. Area too large

- ☐ Select an area **> 100 tiles**. Expect a clear error / "select a smaller area" and the run **blocked** (same 100-tile cap as ground fetch).

### 5d. Analysis-type option visibility (important)

Change the **analysis type** and confirm the **Tree layer** and **Ground
materials** sections appear/hide per this matrix. Also confirm the **weather /
Upload EPW** control shows only on weather-based analyses.

The matrix mirrors what the backend models actually consume (lambda-models):
wind models do no vegetation meshing and read no materials; only UTCI/TCS
use ground materials — the solar/daylight family and SVF accept but ignore
them.

| Analysis type | Tree option | Ground materials | Weather / EPW |
|---|:--:|:--:|:--:|
| Wind Speed | ✗ | ✗ | ✗ |
| Pedestrian Wind Comfort (PWC) | ✗ | ✗ | ✓ |
| Thermal Comfort Index (UTCI) | ✓ | ✓ | ✓ |
| Thermal Comfort Statistics (TCS) | ✓ | ✓ | ✓ |
| Solar Radiation | ✓ | ✗ | ✓ |
| Daylight Availability | ✓ | ✗ | ✗ |
| Direct Sun Hours | ✓ | ✗ | ✗ |
| Sky View Factors | ✓ | ✗ | ✗ |

- ☐ **Wind Speed / PWC** — no Tree section, no Ground-materials section.
- ☐ **Sky View Factors** — Tree section **shown**, Ground-materials section **hidden** (the one case where they differ).
- ☐ **UTCI / TCS / Solar / Daylight / Direct Sun Hours** — both sections shown.
- ☐ **Weather-based (PWC / UTCI / TCS / Solar Radiation)** — a weather-file selector + **Upload EPW…** control is present; other analyses have none.
- ☐ Switching analysis type updates the sections live (no stale widgets).

### 5e. Trees

Pick a `tree-*` layer on a tree-supporting analysis (e.g. UTCI):

- ☐ **Breakdown reported** — the dialog shows a breakdown, e.g. *"N tree point(s) detected — X as a catalog species (…); Y by their OSM type as an archetype; Z as the default broadleaf."*
- ☐ **OSM sample** — load `examples/osm-trees-sample.geojson`: expect precise species (Quercus robur, Pinus pinea), archetypes (Acer, Picea, needleleaved), and the bare default to be counted correctly.
- ☐ **No blocking** — a layer where **no** tree resolves to a registry species still submits (they run as archetypes). The run is never blocked for "no tree type".
- ☐ **Untagged trees** — a layer of bare `natural=tree` points (no species/genus) submits and runs (broadleaf default).
- ☐ **Catalog override** — tick *"Use tree catalog tree type"*: the label switches to "using the tree catalog tree type for all of them", and every tree uses the selected catalog species.
- ☐ **No tree layer** — with no `tree-*` layer, the tree section is empty/quiet and the run proceeds without vegetation.

### 5f. Ground materials

On a ground-supporting analysis (e.g. UTCI), with `ground-*` layers present:

- ☐ **Opt-in list** — the Ground materials list opens with **nothing ticked**; one row per `ground-*` layer (`material — layer name`).
- ☐ **One per material** — ticking a second `asphalt` layer unticks the first (radio-like).
- ☐ **Validation (ticked only)** — a ticked layer with no features in the selection is reported (*"Not found on the selected area: …"*); silence otherwise.
- ☐ **Auto-fetch** — tick *"Use Infrared City ground materials"*: the list disables; Infrared City's own layers are fetched at submit.
- ☐ **No layers** — with no `ground-*` layers, the list is hidden but the auto-fetch option remains.

### 5g. Combined / negative runs

- ☐ **Trees + ground materials** — UTCI run with a `tree-*` layer selected **and** a `ground-*` layer ticked. Expect both included; result raster reflects them.
- ☐ **Without trees, without ground materials** — same analysis, no tree layer, nothing ticked. Expect a clean run using server defaults (surfaces at default emissivity, no vegetation).
- ☐ **Trees only** / **ground only** — each independently included when the other is absent.

## 6. Tree catalog

- ☐ **Lists species** — Tree catalog shows the registry species with default height / crown (fetched on API-key save).
- ☐ **Info label** — selecting a species shows its Latin name + dimensions (Small/Medium/Large changes the dimensions).
- ☐ **Override wiring** — a selection here is applied only when *"Use tree catalog tree type"* is ticked in Run Simulation (see §5e).
- ☐ **Doc link** — the "vegetation input guide" link opens.

## 7. Results

- ☐ Each completed simulation loads a **result raster** styled for its analysis type.
- ☐ **The layer name says what the run was** — `IC result - <analysis> · <inputs>`, e.g. `IC result - thermal-comfort-index · July, Afternoon`. Run the same analysis twice with different inputs and expect two distinguishable names. Sky-view-factors has no inputs, so it keeps the bare name.
- ☐ **A run adds only its raster** — no extra building-outline layer appears after a single-tile or area run; the only buildings layer is the one the Fetch Geometry dialog created.
- ☐ **Nothing falls off the top of the legend** — on a UTCI run, check the hottest areas (open sun, water) are coloured rather than white/transparent. The backend legend can be narrower than the data.
- ☐ **The legend follows the run, not the full scale** — a UTCI run is coloured over the backend's legend range (e.g. 21–30 °C), not the registry's full −40…46 °C scale, and each band is labelled with its own value. Set a manual min/max in the dialog and expect it to win over both.
- ☐ **The same scenario legends the same in both modes** — run one tile, then the same ground as an area, and expect comparable colour scales.
- ☐ Area runs merge tiles into one coherent raster (no gaps/seams beyond expected tile edges).
- ☐ Re-running overwrites/adds results without corrupting existing layers.

---

## Quick regression pass

A fast smoke test after a change:

1. ☐ Save API key.
2. ☐ Fetch buildings for a small area.
3. ☐ Single-tile UTCI run with the OSM tree sample → result raster + correct tree breakdown.
4. ☐ Fetch ground materials for the same area → `ground-*` layers.
5. ☐ Area UTCI run with trees + one ground layer ticked → result raster.
6. ☐ Switch analysis to Wind Speed → tree + ground sections disappear.
