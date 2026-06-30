# Architecture
_Last updated: 2026-06-30_

## Overview

QGIS plugin that exposes the Infrared City simulation platform inside QGIS. Users authenticate, define an area of interest, fetch building geometry from the Infrared City buildings API, run a microclimate simulation, and visualize the result raster — all from QGIS dialogs.

## Structure

```
infrared_city_gis/
├── infrared_city_gis.py     # Main plugin class (QGIS lifecycle, menu, toolbar)
├── infrared_city_save_auth.{py,ui}
├── infrared_city_fetch_geometry_dialog.{py,ui}
├── infrared_city_select_bbox_dialog.{py,ui}
├── infrared_city_run_multiple_simulation_dialog.{py,ui}  # "Run simulation" (single-tile + area)
├── infrared_city_tree_catalog_dialog.{py,ui}
├── infrared_city_dialog_base.ui  # Shared dialog base
├── client.py                # HTTP wrapper around infrared-sdk
├── constants.py             # Endpoints, defaults
├── exceptions.py            # Domain exceptions
├── infrared_logger.py       # structlog setup
├── services/                # API + I/O helpers
│   ├── fetch.py             # Building fetch (Infrared City /v2/buildings)
│   ├── area_poller.py       # Long-poll job status
│   ├── converter.py         # Geometry conversion
│   ├── feature_height.py    # Building height heuristics
│   ├── epw_query.py         # Weather data lookup
│   ├── buildings_compare.py # Diff buildings across versions
│   └── _geometry_io.py      # Geometry serialization helpers
├── models/                  # Domain models
│   ├── analysis.py          # Simulation request/response shapes
│   ├── timeframes_parser.py # Time-period inputs
│   └── vegetation_types.py  # Tree catalog
├── visualization/           # Raster rendering / styles
├── utils/                   # Shared utilities
└── i18n/                    # Translations (Qt .ts files)
```

## Key Components

| Module | Role |
|---|---|
| `infrared_city_gis.py` | QGIS plugin lifecycle — registers menu/toolbar entries, opens dialogs |
| `client.py` | Thin HTTP wrapper. Reads API key from auth-dialog-saved credentials |
| `services/fetch.py` | Pulls building footprints from the Infrared City buildings API (`POST /v2/buildings`, GeoJson), single request with a 512 m-tile fallback |
| `services/area_poller.py` | Polls long-running simulation job status until done |
| `models/analysis.py` | Request/response shapes for each simulation type |
| `visualization/` | Converts raw simulation arrays → QGIS-styled raster layers |

## External Dependencies

- **Infrared City API** (`api.infrared.city/v2`) — simulation backend and building geometry source (`/v2/buildings`, Mapbox-backed core-geometries-service; subscription required)
- **QGIS / PyQGIS** — host application
- **`infrared-sdk`** (≥0.4.10) — Python SDK; pinned in `requirements.txt`
- **shapely**, **pyproj**, **mapbox_earcut**, **numpy**, **structlog**, **requests**

## Data Flow

```
User → Auth Dialog → API key stored in QGIS settings
     → Select bbox → Fetch buildings (POST /v2/buildings, GeoJson)
     → Configure simulation → POST /api/run-analysis → poll job status
     → Download result → render as raster layer

(Endpoints defined in `infrared_city_gis/constants.py`; `RUN_ANALYSIS_ENDPOINT` is the entry point.)
```

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
