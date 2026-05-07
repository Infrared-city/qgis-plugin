# Infrared City GIS — QGIS Plugin

QGIS plugin that connects to the [Infrared City](https://infrared.city) simulation platform. Distributed via the QGIS plugin repository and GitHub Releases. The plugin code is open source (GPL-2.0+); access to the simulation backend requires a subscription.

## Stack

- Python 3 (whatever QGIS ships — typically 3.9+)
- QGIS ≥ 3.0 (PyQGIS / PyQt5)
- `pb_tool` for plugin packaging (`infrared_city_gis/pb_tool.cfg`)
- Internal services: `infrared-sdk`, REST calls to `api.infrared.city`

## Repository Layout

```
connector-python/
├── infrared_city_gis/         # The QGIS plugin (this is what gets shipped)
│   ├── __init__.py            # Plugin entry point — classFactory()
│   ├── infrared_city_gis.py   # Main plugin class
│   ├── infrared_city_*.{py,ui}# Dialogs (auth, fetch geometry, simulation, bbox, trees)
│   ├── client.py              # HTTP client wrapper around infrared-sdk
│   ├── services/              # Domain helpers (fetch, area_poller, geometry, buildings)
│   ├── models/                # Analysis, vegetation, time-frame parsers
│   ├── visualization/         # Raster rendering helpers
│   ├── utils/                 # Shared utilities
│   ├── icons/                 # Toolbar icons (PNG/SVG)
│   ├── i18n/                  # Translations
│   ├── metadata.txt           # QGIS plugin metadata (version, deps, tags)
│   ├── pb_tool.cfg            # Build config for pb_tool
│   ├── plugin_upload.py       # Manual upload script for plugins.qgis.org
│   └── requirements.txt       # Python deps (installed at runtime)
└── .github/workflows/         # CI: release builds the ZIP on tag push
```

The plugin **must** ship as a single folder (`infrared_city_gis/`) zipped at the root — that's what QGIS expects when users install from ZIP.

## Common Commands

```bash
# Build a plugin ZIP locally (using pb_tool)
cd infrared_city_gis && pb_tool zip

# Or just zip the folder (what CI does on tag push)
zip -r infrared-city-qgis.zip infrared_city_gis/ -x "*__pycache__*" "*.pyc"

# Upload manually to plugins.qgis.org (once approved)
python infrared_city_gis/plugin_upload.py infrared-city-qgis.zip

# Lint
ruff check infrared_city_gis/
```

## Release Process

Triggered by pushing a `v*` tag (see `.github/workflows/release.yml`):

```bash
# Bump version in infrared_city_gis/metadata.txt first, commit, then:
git tag v0.2.2 && git push --tags
```

CI builds the ZIP and creates a GitHub Release. Upload to `plugins.qgis.org` is still **manual** — review the release on plugins.qgis.org before promoting to non-experimental.

See [`docs/deployment.md`](docs/deployment.md) for full deploy details.

## Architecture & Decisions

- [`docs/architecture.md`](docs/architecture.md) — component overview, dialog flow, API contract.
- [`docs/battle-scars.md`](docs/battle-scars.md) — non-obvious gotchas and workarounds (PyQGIS, pb_tool, plugin distribution).
- [`docs/deployment.md`](docs/deployment.md) — how to cut a release, plugins.qgis.org review process.

## License & Distribution

- **GPL-2.0-or-later** — required because plugins link against PyQGIS (also GPL).
- Distributed via plugins.qgis.org (preferred — gets discoverability) and GitHub Releases (fallback).
- The repo name is `connector-python` for historical reasons; the plugin display name is **Infrared City GIS**. Renaming the repo is on the backlog.
