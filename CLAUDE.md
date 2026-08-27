# Infrared City GIS — QGIS Plugin

QGIS plugin that connects to the [Infrared City](https://infrared.city) simulation platform. Distributed via the QGIS plugin repository and GitHub Releases. The plugin code is open source (GPL-2.0+); access to the simulation backend requires a subscription.

## Stack

- Python 3 (whatever QGIS ships — typically 3.9+)
- QGIS 3.44 (current LTR) – 3.x, PyQGIS via the `qgis.PyQt` shim — always import through `qgis.PyQt.*`, never `PyQt5.*` directly, and use scoped Qt enums (`Qt.CheckState.Checked`, not `Qt.Checked`) so one codebase serves Qt5 and Qt6
- QGIS 4 (Qt6, released 2026-03) is served by the **same package** — `qgisMaximumVersion=4.99`, no separate branch, ZIP or `supportsQt6` flag (that one was removed from QGIS core). The Plugin Manager reads `metadata.txt` before loading any code, so the cap is a promise to the user, not a capability check: never ship a raised cap that the `docs/manual-testing.md` round has not actually passed on QGIS 4. See `docs/battle-scars.md` for what a grep cannot catch.
- `pb_tool` for plugin packaging (`infrared_city_gis/pb_tool.cfg`)
- Internal services: `infrared-sdk`, REST calls to `api.infrared.city`

## Repository Layout

```
qgis-plugin/
├── infrared_city_gis/         # The QGIS plugin (this is what gets shipped)
│   ├── __init__.py            # Plugin entry point — classFactory()
│   ├── infrared_city_gis.py   # Main plugin class
│   ├── infrared_city_*.{py,ui}# Dialogs (auth, fetch geometry, ground materials, simulation, bbox, trees)
│   ├── services/              # Domain helpers (fetch, qgis_http, area_poller, geometry, buildings)
│   ├── models/                # Analysis, vegetation, time-frame parsers
│   ├── visualization/         # Raster rendering helpers
│   ├── utils/                 # Shared utilities
│   ├── icons/                 # Toolbar icons (PNG/SVG)
│   ├── i18n/                  # Translations
│   ├── metadata.txt           # QGIS plugin metadata (version, deps, tags)
│   ├── pb_tool.cfg            # Build config for pb_tool
│   └── requirements.txt       # Python deps (installed at runtime)
└── .github/workflows/         # CI: release builds the ZIP on tag push
```

The plugin **must** ship as a single folder (`infrared_city_gis/`) zipped at the root — that's what QGIS expects when users install from ZIP.

## Common Commands

```bash
# Build a plugin ZIP locally (mirrors what CI does on tag push)
# Excludes caches, hidden files, and the dev-only tests/ dir so the package
# stays clean for plugins.qgis.org (no hidden-file warnings).
zip -r infrared-city-qgis.zip infrared_city_gis/ \
  -x "*__pycache__*" "*.pyc" "*.pyo" "*.DS_Store" "*/.*" \
     "infrared_city_gis/tests/*" "infrared_city_gis/test/*"

# Lint — these two are the CI gates (NOT pylint; pylintrc is a leftover)
ruff check infrared_city_gis/
flake8 infrared_city_gis/

# Security — the same two scanners plugins.qgis.org runs on every upload.
# Run bandit with NO config: it auto-discovers any .bandit inside the tree it
# scans and silently applies its skips. The upload waiver is kept outside the
# scanned tree for exactly that reason.
bandit -r infrared_city_gis/ -x infrared_city_gis/tests,infrared_city_gis/test
git ls-files 'infrared_city_gis/*' | xargs detect-secrets-hook

# Tests — real QGIS runtime; see the marker gates in infrared_city_gis/tests/
./scripts/run_qgis_tests.sh -q                       # free, no network
INFRARED_API_KEY=… ./scripts/run_qgis_tests.sh -m e2e -s   # hits prod, costs tokens

# Qt5/Qt6 name resolution (what a linter cannot catch — see Stack above).
# Needs the binding installed: `pip install PyQt6`, or run it under the QGIS
# bundled interpreter to cover Qgs* classes too (docs/battle-scars.md 2026-08-03).
python scripts/check_qt6_names.py infrared_city_gis
python scripts/check_qt6_names.py infrared_city_gis --binding pyqt5
```

Plugin uploads to `plugins.qgis.org` are **manual via the web UI** — see [`docs/deployment.md`](docs/deployment.md).

**Note:** `infrared_city_gis/pb_tool.cfg` exists but is currently stale — it references files that have been moved or renamed. Don't run `pb_tool zip` until the config is updated to match the current layout.

## Release Process

Triggered by pushing a `v*` tag (see `.github/workflows/release.yml`):

```bash
# Bump version in infrared_city_gis/metadata.txt first, commit, then:
git tag v0.2.2 && git push --tags
```

CI builds the ZIP and creates a GitHub Release. Upload to `plugins.qgis.org` is still **manual** — review the release on plugins.qgis.org before promoting to non-experimental.

See [`docs/deployment.md`](docs/deployment.md) for full deploy details.

## Conventions

Team-wide rules are **not duplicated here** — they live in the plugins enabled by
[`.claude/settings.json`](.claude/settings.json) and are the default for this repo:

| Skill | Use it for |
|---|---|
| `ir-dev:conventions` | Size limits, naming, imports, error handling, testing tiers, git workflow. Its `python-conventions.md` and `logging-conventions.md` are the baseline for every file here. |
| `infrared:use-infrared` + `ir-sdk:infrared-sdk-consumers` | The simulation domain this plugin drives — analysis types, weather data, area vs. single-tile, result handling. Read before changing anything under `services/`. |
| `ir-research:simulation-context` | What the results *mean* — the Lawson and UTCI standards this plugin renders, model accuracy, the serving pipeline. |
| `ir-dev:codebase-overview` | Where this repo sits among the org's services, and how the SDK is released. |
| `/ir-dev:audit-branch` | Run before opening a PR — checks conventions, reinvention, and doc sync. |
| `/security-review`, `/code-review` | Before a release. plugins.qgis.org scans every upload and **blocks on Critical findings**. |

Two deviations from those conventions are deliberate and documented under
**Reviewer Corrections** in [`docs/battle-scars.md`](docs/battle-scars.md):
snake_case module names, and prose logging. Read that section before "fixing"
either — both are load-bearing.

## Doc Map

- [`README.md`](README.md) — user-facing overview, install, and usage.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — contributing guide (canonical filename; there is no `CONTRIBUTION.md`).
- [`docs/architecture.md`](docs/architecture.md) — component overview, dialog flow, API contract.
- [`docs/vegetation-input.md`](docs/vegetation-input.md) — tree-layer input contract (OSM-native: `species`/`genus`/`leaf_type`, optional size; two-tier resolution — precise registry species or archetype; catalog override). Only the point geometry is mandatory.
- [`docs/ground-materials.md`](docs/ground-materials.md) — ground-material (surface) layers: fetch dialog, `ground-*` layer convention, simulation usage.
- [`docs/manual-testing.md`](docs/manual-testing.md) — manual/exploratory test checklist for every dialog function (API key, fetch, ground materials, simulation matrix, trees).
- [`docs/battle-scars.md`](docs/battle-scars.md) — non-obvious gotchas and workarounds (PyQGIS, pb_tool, plugin distribution).
- [`docs/deployment.md`](docs/deployment.md) — how to cut a release, plugins.qgis.org review process.
- [`docs/release-process.md`](docs/release-process.md) — Release Please flow (staging → main → tag).

## License & Distribution

- **GPL-2.0-or-later** — required because plugins link against PyQGIS (also GPL).
- Distributed via plugins.qgis.org (preferred — gets discoverability) and GitHub Releases (fallback).
- The repo is `qgis-plugin`, the plugin display name is **Infrared City GIS**, and the inner package folder is `infrared_city_gis/` (must stay underscore-named — Python module requirement).
