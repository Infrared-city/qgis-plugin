# Contributing

## Setup

1. **Clone**:
   ```bash
   git clone https://github.com/Infrared-city/qgis-plugin.git
   cd qgis-plugin
   ```

2. **Install Python deps** (used by the plugin at runtime):
   ```bash
   pip install -r infrared_city_gis/requirements.txt
   ```

3. **Install dev tools**:
   ```bash
   pip install pylint
   ```

   Linting config: `infrared_city_gis/pylintrc`.

4. **Symlink into your QGIS plugin folder** so QGIS picks up live changes:
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`

   ```bash
   ln -s "$(pwd)/infrared_city_gis" <plugin-folder>/infrared_city_gis
   ```

5. **Reload the plugin** in QGIS via the *Plugin Reloader* extension (recommended) or by toggling it off/on in the Plugin Manager.

## Workflow

1. **Branch**: `feat/<short-description>`, `fix/<short-description>`, `chore/<description>`. Target `main` (or `staging` if it exists).
2. **Commit** following [Conventional Commits](https://www.conventionalcommits.org): `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `ci:`. Lowercase type, imperative mood. Append `!` for breaking changes.
3. **Open a PR** with a short summary and a test plan. One concern per PR.
4. **Reviewer checks** pylint passes (`pylint --rcfile=infrared_city_gis/pylintrc infrared_city_gis/`), plugin loads in QGIS, no regressions on the dialogs you touched.

## Testing

The plugin is QGIS-runtime-only — there's no headless test harness yet. Manual checks:

- Plugin loads (no exceptions in QGIS Python console)
- Auth dialog accepts a valid API key
- Fetch geometry pulls buildings (1 km × 1 km) for a known area (e.g. Hamburg city center)
- Run a simulation end-to-end (try **wind speed** as the smoke test)
- Result raster appears as a layer with a sensible style

Adding pytest-based unit tests for non-PyQGIS code (`models/`, `services/timeframes_parser.py`, etc.) is encouraged.

## Releasing

See [`docs/deployment.md`](docs/deployment.md). Short version:

1. Bump `version=` in `infrared_city_gis/metadata.txt`
2. Update `changelog=` in `metadata.txt`
3. Commit on `main`
4. `git tag v0.X.Y && git push --tags`
5. CI builds the ZIP and creates a GitHub Release
6. **Manually** upload the ZIP to `plugins.qgis.org` via the web UI

## Project Conventions

This repo follows Infrared coding conventions, loaded via the `ir-dev` plugin. Highlights:

- Files ≤ 400 lines (split into modules above that)
- Functions ≤ 100 lines
- snake_case for Python, PascalCase for classes, UPPER_SNAKE for constants
- Mark shortcuts with `# TODO:` / `# FIXME:` / `# HACK:` / `# NOTE:`
- See `ir-dev:conventions` skill for full rules

## QGIS Plugin Specifics

- **Don't add Node-style dependencies.** Everything must run in QGIS's bundled Python (no compiled C extensions beyond what QGIS ships).
- **Stay GPL-2.0+ compatible.** Linking against PyQGIS forces this — see [LICENSE](LICENSE).
- **Test on multiple OSes.** QGIS plugins must work on macOS, Linux, and Windows. Avoid path-style assumptions and OS-specific shell calls.
