# Architecture
_Last updated: 2026-07-14_

## Overview

QGIS plugin that exposes the Infrared City simulation platform inside QGIS. Users authenticate, define an area of interest, fetch building geometry (and optionally ground-material surface layers) from the Infrared City platform, run a microclimate simulation — with trees from a `tree-*` point layer and surface materials from `ground-*` polygon layers — and visualize the result raster, all from QGIS dialogs.

## Structure

```
infrared_city_gis/
├── infrared_city_gis.py     # Main plugin class (QGIS lifecycle, menu, toolbar)
├── infrared_city_save_auth.{py,ui}
├── infrared_city_fetch_geometry_dialog.{py,ui}
├── infrared_city_fetch_ground_materials_dialog.py  # Fetch ground-* surface layers
├── infrared_city_select_bbox_dialog.{py,ui}
├── infrared_city_run_multiple_simulation_dialog.{py,ui}  # "Run simulation" (single-tile + area)
├── infrared_city_tree_catalog_dialog.{py,ui}
├── constants.py             # Endpoints, defaults
├── exceptions.py            # Domain exceptions
├── infrared_logger.py       # structlog setup
├── services/                # API + I/O helpers
│   ├── fetch.py             # Building fetch (Infrared City /v2/buildings)
│   ├── fetch_from_registry.py # Registry fetch on startup (models, vegetation, materials)
│   ├── key_check.py         # Verifies an API key (GET /v2/webhooks)
│   ├── sdk_runner.py        # Area simulation via SDK run_area (+ area_poller, merge_area_jobs)
│   ├── sdk_payloads.py      # Builds the typed SDK payload from the dialog state
│   ├── sdk_single_tile.py   # Single-tile simulation via SDK analyses.execute
│   ├── single_tile_selection.py # The armed 512 m box; the toolbar toggle mirrors it
│   ├── area_poller.py       # Long-poll job status
│   ├── qgis_area_buildings.py   # Collect buildings from a QGIS layer selection
│   ├── qgis_area_vegetation.py  # Collect trees (OSM species/genus → registry modelId or archetype)
│   ├── tree_validation.py   # Tree-layer validation against the registry
│   ├── ground_materials.py  # Ground-material catalog, ground-* discovery/collect/validate
│   ├── ground_material_reader.py # Overture read on a worker thread, with a time budget
│   ├── user_errors.py       # Failure → title / summary / advice / tokens charged
│   ├── qgis_http.py         # Direct HTTP through QGIS's network stack (proxy settings apply)
│   ├── secret_manager.py    # API key storage (QSettings, env override)
│   ├── converter.py         # Geometry conversion
│   ├── feature_height.py    # Building height heuristics
│   ├── epw_query.py         # Weather data via SDK weather client (+ epw_parser.py for local EPW upload)
│   └── _geometry_io.py      # Geometry serialization helpers
├── models/                  # Domain models
│   ├── analysis.py          # Simulation request/response shapes
│   ├── timeframes_parser.py # Time-period inputs
│   └── vegetation_types.py  # Tree catalog
├── visualization/           # Raster rendering / styles + ground-* layer display
├── utils/                   # Shared utilities
└── i18n/                    # Translations (Qt .ts files)
```

## Key Components

| Module | Role |
|---|---|
| `infrared_city_gis.py` | QGIS plugin lifecycle — registers menu/toolbar entries, opens dialogs |
| `services/qgis_http.py` | Every direct HTTP call the plugin makes, routed through QGIS's network stack so the user's QGIS proxy settings apply |
| `services/fetch.py` | Pulls building footprints from the Infrared City buildings API (`POST /v2/buildings`, GeoJson), single request with a 512 m-tile fallback |
| `services/sdk_runner.py` | Submits area simulations through the SDK (`client.run_area`; `area_poller` then polls `check_area_state` and merges with `merge_area_jobs`), passing buildings + trees + ground materials |
| `services/sdk_single_tile.py` | Single-tile simulation (`analyses.execute`) with the same inputs embedded in the payload |
| `services/ground_materials.py` | Material catalog (registry-driven, hardcoded fallback), `ground-*` layer discovery, collect + validate for simulation |
| `services/area_poller.py` | Polls long-running simulation job status until done |
| `services/ground_material_reader.py` | Runs the Overture ground-material read (`ground_materials.get_area`) on a worker thread, with an explicit time budget; refuses a second read while one still runs |
| `services/user_errors.py` | Turns any failure into what the user sees: title, plain summary, advice, and whether tokens were charged |
| `models/analysis.py` | Request/response shapes for each simulation type |
| `visualization/` | Converts raw simulation arrays → QGIS-styled raster layers; renders fetched `ground-*` layers with registry colors |

## External Dependencies

- **Infrared City API** (`api.infrared.city/v2`) — simulation backend and building geometry source (`/v2/buildings`, Mapbox-backed core-geometries-service; subscription required)
- **Registry mirror** (`registry.infrared.city`) — the public models / vegetation / materials documents, served without credentials. Read on startup; these used to come from the utilities service (`/v2/utils/registry/*`), which is being retired.
- **Overture Maps** (public bucket `overturemaps-us-west-2`) — ground-material source, read in-process by the SDK with no API key; over S3, or over plain HTTPS where QGIS's Arrow has no S3 support (Windows). See [`ground-materials.md`](ground-materials.md).
- **QGIS / PyQGIS** — host application
- **`infrared-sdk`** (==1.0.0, `[geodata]` extra) — Python SDK; pinned EXACTLY in `requirements.txt`, so a new SDK reaches users only through a new plugin release (a `>=` floor let fresh installs of v1.1.3 pull 1.0.0, which it cannot load). `scripts/preflight.sh` refuses a non-exact pin. The extra brings pyarrow + shapely, which `ground_materials.get_area` needs to read Overture parquet in-process.
- **shapely**, **pyproj**, **mapbox_earcut**, **numpy**, **structlog**, **requests**

## Data Flow

```
Plugin start → registries fetched from the public mirror
                (models, vegetation, materials — no key needed)

User → Auth Dialog → key VERIFIED against the API before saving
                     (GET /v2/webhooks: 401/403 →
                      not saved + "contact connectors@infrared.city";
                      server unreachable → not saved either)
                   → API key stored in QGIS settings
     → Select bbox / Select tile → Fetch buildings (POST /v2/buildings, GeoJson)
       (optional) Fetch ground materials → editable ground-* layers
     → Configure simulation (analysis, time frame, EPW, tree-* layer,
       ground-* layers or auto-fetch)
     → SDK run_area + poll + merge_area_jobs (area) / analyses.execute (single tile)
       → poll job status → download result → render as raster layer
```

All toolbar actions except **Save API Key** are greyed out (with an
explanatory tooltip) until a key is stored and not known-bad. A startup
`key_check.verify_api_key` re-checks a saved key: a confirmed 401/403 locks
the actions and pushes a message-bar warning; a mere outage does NOT — an
already-saved key keeps working offline. (This check used to be a side
effect of the registry refresh, back when the registries were read from the
utilities service with the key attached.)

Weather comes from the SDK weather client's static public catalog
(`geo.infrared.city/weather`, no API key sent) — both the station list for
the dropdown (`services/fetch.fetch_weather_file_names`) and the hourly data
(`services/epw_query.py`). Neither touches the retiring utilities service;
nothing in the plugin may call `/v2/utils` (#47), and
`tests/test_no_utilities_calls.py` fails if a URL for it comes back. (The
data query previously hit a legacy `app.infrared.city` endpoint with a
separate key registry, then `/v2/utils/weather/…`; see battle-scars.) The
half-open plugin `TimeFrame` → inclusive SDK
`TimePeriod` translation, including the multi-month / year-wrap splitting
rules, is documented in `epw_query._time_periods_from_time_frame`.

The area path goes through `services/sdk_runner.py`, the single-tile path
through `services/sdk_single_tile.py`. The legacy raw-REST simulation path
(`client.py` and its `RUN_ANALYSIS_ENDPOINT`) lost its last caller when both
moved to the SDK, and was removed on `feat/qgis4-qt6-compat` (after
v1.1.4).
Trees and ground materials are documented in
[`vegetation-input.md`](vegetation-input.md) and
[`ground-materials.md`](ground-materials.md).

## Building Height Resolution

When you run a simulation from your own QGIS layer, every building polygon needs a height in metres. The plugin resolves this automatically using the following priority order (first match wins):

| Tier | Source | Precision |
|------|--------|-----------|
| 1 | Top-elevation field minus base-elevation field (e.g. `BuildingTo - BuildingBo`, `abs_hmax - abs_hmin`) | Exact |
| 2 | Single height field (e.g. `height`, `building:height`, `hoehe`, `hauteur`, `measuredHeight`) | Exact |
| 3 | Floor-count field × 3 m/floor (e.g. `building:levels`, `levels`, `floors`, `stories`) | Estimate |
| 4 | Z-range of 3D geometry (LoD1/LoD2 CityGML, 3D Shapefile) | Exact |
| 5 | OSM `building=*` type lookup (e.g. `house` → 6 m, `apartments` → 12 m) | Rough estimate |
| 6 | Generic fallback of 6 m — only used when no other tier matched | Last resort |

Buildings that reach neither tier 1–5 nor the generic fallback are **skipped** (logged as a warning). The resolved source tier is stored in the `height_source` property of the exported GeoJSON, which you can inspect in the QGIS attribute table.

### Recognized field names

The lookup is **case-insensitive** and normalises separators (`:` and space become `_`), so `building:height`, `Building Height`, and `building_height` are all equivalent.

**Height fields (tier 2):** `height`, `h`, `bldg_height`, `building_height`, `measuredHeight`, `hoehe`, `h_geb`, `hauteur`, `h_dak_max`, `pand_hoogte`, `z_max`, `max_height`

**Level/floor fields (tier 3):** `building:levels`, `levels`, `floors`, `stories`, `etagen`

**Top/base elevation pairs (tier 1):** `BuildingTo`/`BuildingBo`, `abs_hmax`/`abs_hmin`, `h_top`/`h_base`

### How to control height from your layer

Name your height attribute using any of the recognized field names above and the plugin will pick it up automatically (tier 2). No configuration needed.

If your attribute has a non-standard name, the code supports an `override_field` parameter internally — a future UI release will expose this as a dropdown in the simulation dialog.

### Fetch Geometry dialog path

The **Fetch building geometry** dialog (`services/fetch.py:fetch_geometry_from_infrared`) pulls footprints from the Infrared City buildings API (`POST /v2/buildings`, `outputFormat=GeoJson`) over a fixed **1 km × 1 km** area (1024 m) centred on the entered coordinates, writes a `FeatureCollection`, and loads it as a layer. Heights come from the API response, not from local tag parsing. It tries one request for the whole area first; if that returns nothing it falls back to fetching 512 m tiles (a 2×2 grid for 1024 m) and merging + de-duplicating them, and surfaces a clear error if both paths fail. The tier table above applies to the **separate** path where you run a simulation from your own QGIS buildings layer (`collect_qgis_area_buildings` + `feature_height.py`).

## Why This Shape

- **Plugin = `infrared_city_gis/` folder.** The repo root is just metadata/CI; the QGIS-shipped artifact is the inner folder. This is forced by the QGIS plugin format.
- **`pb_tool` config exists but is currently stale.** Release builds use a plain `zip` step in CI (see `.github/workflows/release.yml`). `pb_tool.cfg` references files that have moved; update it before relying on `pb_tool zip` again.
- **Logic split into `services/` and `models/`.** Keeps dialog files focused on UI only — easier to test the non-UI bits in isolation later.
