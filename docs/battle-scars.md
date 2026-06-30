# Battle Scars
_Non-obvious bugs, hard-won lessons. Date each entry. Remove when root cause is fixed._

- **[2026-05-07]** [bootstrap] `infrared_city_gis/__init__.py` calls `_ensure_deps()` (in `utils/deps_bootstrap.py`) at plugin load. It pip-installs `requests`, `numpy`, `shapely`, `pyproj`, `mapbox_earcut`, `structlog`, `infrared-sdk` if missing, reading from `requirements.txt`. **Two implications:**
  - `requirements.txt` MUST stay inside the shipped ZIP — the bootstrap reads it at runtime. If you ever exclude it from `release.yml`, the bootstrap silently does nothing.
  - plugins.qgis.org reviewers sometimes flag plugins that pip-install at runtime as a security concern. Be ready to explain it (numpy isn't reliably bundled with QGIS on Windows, mapbox_earcut needs native wheels). Long-term consider vendoring deps at CI build time instead.
- **[2026-06-30]** [bootstrap/uninstall] Deps used to install into `infrared_city_gis/thirdparty/` (inside the plugin folder). On **uninstall** QGIS deletes the whole plugin dir, but the loaded `numpy`/`mapbox_earcut` native extensions (`.pyd`/`.dll`) are **locked by the running process on Windows** → "folder in use", uninstall aborts. **Fix:** deps now install into the **QGIS profile dir**, outside the plugin folder, so uninstall never touches locked files. Layout: `<profile>/infrared_city_gis/deps-<hash>/`, where `<hash>` is a short order-independent SHA1 of the requirement specs (`_reqs_hash`). **Why the hash:** `_find_missing` only checks *importability*, not version — so a bumped pin (e.g. `infrared-sdk>=0.4.10` → `>=0.5.0`) reusing the same folder would never reinstall. A changed requirement set → new hash → fresh empty folder → correct versions installed; only the current `deps-<hash>` is on `sys.path`, so the stale set isn't even importable. `_cleanup_legacy_thirdparty()` removes the old in-plugin folder; `_cleanup_old_deps_dirs()` removes sibling `deps-*` from previous sets (both best-effort/`ignore_errors`, work on a fresh session because those paths aren't on `sys.path`). **Caveats:** (1) after uninstall the last `deps-<hash>` is orphaned in the profile dir — no QGIS uninstall hook + can't self-delete from `unload()` (runs on every shutdown/reload). (2) A dep version bump only takes effect after a QGIS **restart** — already-loaded C extensions can't be hot-swapped.

## Format

- **[YYYY-MM-DD]** [area] Short description — why it happens and how to work around it.

## Common QGIS Plugin Pitfalls (general — not yet hit here)

- **Don't `import requests` at module top-level if you might run inside QGIS server**: server profiles can have stripped Python. Wrap in a function-level import or guard with try/except.
- **`pb_tool zip` excludes files based on `.gitignore` only loosely**: always verify the ZIP contents before uploading to plugins.qgis.org.
- **`metadata.txt` `version=` must be SemVer** — plugins.qgis.org rejects non-semver tags.
- **`experimental=True`** keeps the plugin out of default search results until you flip it. Useful while iterating, but remember to flip when ready.
- **Plugin reload in QGIS doesn't always pick up `__init__.py` changes.** Use the *Plugin Reloader* community plugin or fully restart QGIS.
