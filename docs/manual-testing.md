# Manual Testing Guide

A structured checklist for manually testing the **Infrared City GIS** QGIS
plugin against a running backend. Covers every function reachable from the
plugin's toolbar dialogs. Work top to bottom — later sections assume an API
key is set and building geometry exists.

> This is a **manual / exploratory** guide (there is no automated UI test).
> Tick each ☐ as you go; note the plugin version (`metadata.txt`) and QGIS
> version in your test report.

## Prerequisites

- QGIS ≥ 3.44 with the plugin installed (from ZIP or the repo folder).
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
| 4 | Select tile | Map tool (one-shot tile pick) |
| 5 | Tree catalog | Tree-catalog dialog |
| 6 | Run simulation | Run-simulation dialog |

---

## 1. API key

- ☐ **Save a valid key** — Save API Key → paste key → save. Expect a success message, dialog closes, no duplicate dialog re-open.
- ☐ **Change the key** — reopen Save API Key, paste a different valid key, save. Expect the new key to take effect (subsequent fetches/simulations use it; registries re-fetch).
- ☐ **Invalid key** — save a bogus key, then attempt a fetch. Expect a clear error (auth/subscription), not a crash.
- ☐ **No key** — with no key saved, open Run Simulation / Fetch. Expect a clear "save your API key first"-style message.

## 2. Fetch building geometry

- ☐ **Fetch by coordinates** — Fetch building geometry → enter coordinates → fetch. Expect a buildings layer loaded over a ~1 km × 1 km area, with heights.
- ☐ **Empty area** — fetch over an area with no buildings. Expect a clear "nothing found" message (after the single-request + tile-fallback attempts), not a crash.
- ☐ **Rendering** — the buildings layer is styled and visible on the map.

## 3. Select tile (single-tile mode)

- ☐ **Pick a tile** — Select tile → click on the map. Expect a single 512×512 m tile selection to be stored.
- ☐ **Feeds Run Simulation** — open Run Simulation immediately after: it runs in **single-tile mode** (see §5).
- ☐ **Feeds Fetch ground materials** — with a pending tile pick, Fetch ground materials covers that one tile (the pick is *peeked*, so Run Simulation still gets it).

## 4. Fetch ground materials

- ☐ **Requires a building selection** — with no building features selected, open Fetch ground materials. Expect *"Please select a building area first"*.
- ☐ **Tile-count preview** — select building features, open the dialog. Expect the selection size shown in tiles.
- ☐ **>100 tiles rejected** — select a large area (> 100 tiles). Expect a "select a smaller area" message and the fetch **blocked**.
- ☐ **Fetch succeeds** — select a reasonable area → Fetch. Expect one editable `ground-<material>` layer **per material** (asphalt, concrete, vegetation, soil, water, building), added to the project.
- ☐ **Result dialog** — a summary lists the created layers **by layer name** with feature counts.
- ☐ **Repeated fetch numbers layers** — fetch again (different/overlapping area). Expect `ground-asphalt-2`, etc. — no overwrite, both sets present.
- ☐ **No data** — fetch over an area with no ground-material data. Expect *"No ground material data was found"*, not a crash.
- ☐ **Editable** — the `ground-*` layers are memory layers you can edit before a run.

## 5. Run simulation

### 5a. Single tile

- ☐ Make a **Select tile** pick, then Run Simulation. Expect the title to indicate single-tile mode (**1 tile · ~10 tokens**), no area tiling.
- ☐ Run it through: expect a result raster loaded and styled.

### 5b. Area (multiple tiles)

- ☐ Without a tile pick, select an area, Run Simulation. Expect a tile-count preview and a multi-tile area run.
- ☐ Run it through: expect the merged result raster.

### 5c. Area too large

- ☐ Select an area **> 100 tiles**. Expect a clear error / "select a smaller area" and the run **blocked** (same 100-tile cap as ground fetch).

### 5d. Analysis-type option visibility (important)

Change the **analysis type** and confirm the **Tree layer** and **Ground
materials** sections appear/hide per this matrix. Also confirm the **weather /
Upload EPW** control shows only on weather-based analyses.

| Analysis type | Tree option | Ground materials | Weather / EPW |
|---|:--:|:--:|:--:|
| Wind Speed | ✗ | ✗ | ✗ |
| Pedestrian Wind Comfort (PWC) | ✗ | ✗ | ✓ |
| Thermal Comfort Index (UTCI) | ✓ | ✓ | ✓ |
| Thermal Comfort Statistics (TCS) | ✓ | ✓ | ✓ |
| Solar Radiation | ✓ | ✓ | ✓ |
| Daylight Availability | ✓ | ✓ | ✗ |
| Direct Sun Hours | ✓ | ✓ | ✗ |
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
- ☐ **Auto-fetch** — tick *"Use Infrared ground materials"*: the list disables; Infrared's own layers are fetched at submit.
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
