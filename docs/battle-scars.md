# Battle Scars
_Non-obvious bugs, hard-won lessons. Date each entry. Remove when root cause is fixed._

- **[2026-05-07]** [bootstrap] `infrared_city_gis/__init__.py` calls `_ensure_deps()` (in `utils/deps_bootstrap.py`) at plugin load. It pip-installs `requests`, `numpy`, `shapely`, `pyproj`, `mapbox_earcut`, `structlog`, `infrared-sdk` into `infrared_city_gis/thirdparty/` if missing, reading from `requirements.txt`. **Two implications:**
  - `requirements.txt` MUST stay inside the shipped ZIP — the bootstrap reads it at runtime. If you ever exclude it from `release.yml`, the bootstrap silently does nothing.
  - plugins.qgis.org reviewers sometimes flag plugins that pip-install at runtime as a security concern. Be ready to explain it (numpy isn't reliably bundled with QGIS on Windows, mapbox_earcut needs native wheels). Long-term consider vendoring deps into `thirdparty/` at CI build time instead.
- **[2026-05-07]** [metadata] `metadata.txt` `icon=icon.png` points at a file that no longer exists at the plugin root — actual location is `icons/icon.png`. Plugin loads icon-less in QGIS Plugin Manager. Fix in metadata, not by re-creating the root copy.
- **[2026-05-07]** [metadata] `tracker=` and `repository=` URLs in `metadata.txt` point at non-existent `Infrared-city/infrared-city-qgis`. plugins.qgis.org may reject submissions whose links 404.
- **[2026-05-07]** [metadata] `category=Plugins` in `metadata.txt` is not a valid QGIS plugin category. Valid set: `Raster`, `Vector`, `Database`, `Web`. This plugin should likely be `Raster`.

## Format

- **[YYYY-MM-DD]** [area] Short description — why it happens and how to work around it.

## Common QGIS Plugin Pitfalls (general — not yet hit here)

- **Don't `import requests` at module top-level if you might run inside QGIS server**: server profiles can have stripped Python. Wrap in a function-level import or guard with try/except.
- **`pb_tool zip` excludes files based on `.gitignore` only loosely**: always verify the ZIP contents before uploading to plugins.qgis.org.
- **`metadata.txt` `version=` must be SemVer** — plugins.qgis.org rejects non-semver tags.
- **`experimental=True`** keeps the plugin out of default search results until you flip it. Useful while iterating, but remember to flip when ready.
- **Plugin reload in QGIS doesn't always pick up `__init__.py` changes.** Use the *Plugin Reloader* community plugin or fully restart QGIS.

## Format

- **[YYYY-MM-DD]** [area] Short description — why it happens and how to work around it.

## Common QGIS Plugin Pitfalls (general — not yet hit here)

- **Don't `import requests` at module top-level if you might run inside QGIS server**: server profiles can have stripped Python. Wrap in a function-level import or guard with try/except.
- **`pb_tool zip` excludes files based on `.gitignore` only loosely**: always verify the ZIP contents before uploading to plugins.qgis.org.
- **`metadata.txt` `version=` must be SemVer** — plugins.qgis.org rejects non-semver tags.
- **`experimental=True`** keeps the plugin out of default search results until you flip it. Useful while iterating, but remember to flip when ready.
- **Plugin reload in QGIS doesn't always pick up `__init__.py` changes.** Use the *Plugin Reloader* community plugin or fully restart QGIS.
