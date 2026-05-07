# Architecture
_Last updated: 2026-05-07_

## Overview

QGIS plugin that exposes the Infrared City simulation platform inside QGIS. Users authenticate, define an area of interest, fetch building geometry from OpenStreetMap, run a microclimate simulation, and visualize the result raster — all from QGIS dialogs.

## Structure

```
infrared_city_gis/
├── infrared_city_gis.py     # Main plugin class (QGIS lifecycle, menu, toolbar)
├── infrared_city_save_auth.{py,ui}
├── infrared_city_fetch_geometry_dialog.{py,ui}
├── infrared_city_select_bbox_dialog.{py,ui}
├── infrared_city_run_simulation_dialog.{py,ui}
├── infrared_city_run_multiple_simulation_dialog.{py,ui}
├── infrared_city_tree_catalog_dialog.{py,ui}
├── infrared_city_dialog_base.ui  # Shared dialog base
├── client.py                # HTTP wrapper around infrared-sdk
├── constants.py             # Endpoints, defaults
├── exceptions.py            # Domain exceptions
├── infrared_logger.py       # structlog setup
├── services/                # API + I/O helpers
│   ├── fetch.py             # OSM building fetch
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
| `services/fetch.py` | Pulls building footprints from OSM (Overpass / Infrared OSM proxy) |
| `services/area_poller.py` | Polls long-running simulation job status until done |
| `models/analysis.py` | Request/response shapes for each simulation type |
| `visualization/` | Converts raw simulation arrays → QGIS-styled raster layers |

## External Dependencies

- **Infrared City API** (`api.infrared.city/v2`) — simulation backend (subscription required)
- **OpenStreetMap** — building geometry source (via Overpass or proxy)
- **QGIS / PyQGIS** — host application
- **`infrared-sdk`** (≥0.4.2) — Python SDK; pinned in `requirements.txt`
- **shapely**, **pyproj**, **mapbox_earcut**, **numpy**, **structlog**, **requests**

## Data Flow

```
User → Auth Dialog → API key stored in QGIS settings
     → Select bbox → Fetch buildings (OSM)
     → Configure simulation → POST /jobs → poll until done
     → Download result → render as raster layer
```

## Why This Shape

- **Plugin = `infrared_city_gis/` folder.** The repo root is just metadata/CI; the QGIS-shipped artifact is the inner folder. This is forced by the QGIS plugin format.
- **`pb_tool` for packaging.** Standard QGIS plugin tooling. Generates the ZIP from `pb_tool.cfg`.
- **Logic split into `services/` and `models/`.** Keeps dialog files focused on UI only — easier to test the non-UI bits in isolation later.
