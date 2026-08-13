#!/usr/bin/env bash
# Run the plugin's test suite inside a real QGIS runtime.
#
#   scripts/run_qgis_tests.sh -m qgis                      # free, no network
#   INFRARED_API_KEY=… scripts/run_qgis_tests.sh -m e2e -s # hits prod
#   UPDATE_BASELINE=1 INFRARED_API_KEY=… scripts/run_qgis_tests.sh -m e2e -s
#
# Everything after the script name is passed straight to pytest.
#
# On Linux (and in CI) QGIS's Python is on PATH and this is nearly a no-op. On
# macOS the bundled interpreter cannot load the plugin's pip-installed
# dependencies when invoked directly: it is signed with a hardened runtime, so
# library validation rejects any .so with a different Team ID
# ("mapping process and mapped file (non-platform) have different Team IDs").
# The real QGIS app is entitled to load them; a bare invocation is not. So we
# keep an ad-hoc re-signed copy of the interpreter in a mini-bundle whose
# symlinks satisfy the @loader_path rpaths, which drops the hardened runtime
# and with it library validation. Development convenience only — nothing here
# ships, and CI on Linux never needs it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# --- locate QGIS -------------------------------------------------------------

if [[ "$(uname)" != "Darwin" ]]; then
    command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }
    python3 -c "import qgis.core" 2>/dev/null || {
        echo "qgis.core is not importable — run this inside a QGIS container" >&2
        exit 1
    }
    exec python3 -m pytest infrared_city_gis/tests/ "$@"
fi

APP="${QGIS_APP:-}"
if [[ -z "$APP" ]]; then
    for candidate in /Applications/QGIS-final-4*.app /Applications/QGIS*.app; do
        [[ -x "$candidate/Contents/MacOS/python3.12" ]] && { APP="$candidate"; break; }
    done
fi
[[ -n "$APP" ]] || { echo "No QGIS app bundle found — set QGIS_APP=/Applications/QGIS…app" >&2; exit 1; }
CONTENTS="$APP/Contents"
echo "QGIS: $APP"

# --- ad-hoc signed runtime (built once, reused) ------------------------------

RT="${TMPDIR:-/tmp}/qgis-test-runtime-$(basename "$APP")"
if [[ ! -x "$RT/MacOS/python3.12" ]]; then
    echo "Building ad-hoc signed runtime in $RT"
    rm -rf "$RT"; mkdir -p "$RT/MacOS"
    cp "$CONTENTS/MacOS/python3.12" "$RT/MacOS/python3.12"
    codesign --force --sign - "$RT/MacOS/python3.12" >/dev/null 2>&1
    for dir in Frameworks Resources PlugIns; do ln -s "$CONTENTS/$dir" "$RT/$dir"; done
fi
PY="$RT/MacOS/python3.12"

# --- paths -------------------------------------------------------------------

PYDIR="$CONTENTS/Resources/python3.12"
PROFILE="$HOME/Library/Application Support/QGIS/QGIS4/profiles/default"
[[ -d "$PROFILE" ]] || PROFILE="$HOME/Library/Application Support/QGIS/QGIS3/profiles/default"

# The plugin's runtime deps must come FIRST: the bundle ships its own pydantic,
# and mixing the two halves gives "pydantic-core … is incompatible".
DEPS="$(find "$PROFILE/infrared_city_gis" -maxdepth 1 -type d -name 'deps-*' 2>/dev/null | head -1)"
[[ -n "$DEPS" ]] && echo "deps: $DEPS" || echo "deps: none found (SDK-dependent tests will fail)"

# pytest is not in the bundle; keep a private copy next to the runtime. Test for
# the entry point rather than the directory: this lives under $TMPDIR, which
# macOS prunes file-by-file, and a half-emptied pylibs/ still looks "installed"
# while failing with "'pytest' is a package and cannot be directly executed".
PYLIBS="$RT/pylibs"
if [[ ! -f "$PYLIBS/pytest/__main__.py" ]]; then
    echo "Installing pytest into $PYLIBS"
    # Wipe first: pip --target refuses to overwrite an existing directory, so a
    # pruned-but-present pylibs/ would be "reinstalled" without any files landing.
    rm -rf "$PYLIBS"
    PYTHONPATH="$PYDIR:$PYDIR/lib-dynload:$PYDIR/site-packages" \
        "$PY" -m pip install --quiet --target "$PYLIBS" pytest >/dev/null 2>&1 || {
            python3 -m pip install --quiet --target "$PYLIBS" pytest >/dev/null 2>&1; }
fi

export INFRARED_BOOTSTRAP_RUNNING=1          # stop the plugin pip-installing on import
export QT_QPA_PLATFORM=offscreen
export QT_QPA_PLATFORM_PLUGIN_PATH="$CONTENTS/PlugIns/platforms"
export QT_PLUGIN_PATH="$CONTENTS/PlugIns"
export QGIS_PREFIX_PATH="$CONTENTS/MacOS"
# PROJ moved between QGIS 3 and 4 (Resources/proj -> Resources/qgis/proj). Probe
# for proj.db rather than guessing: without it QGIS logs "Cannot find proj.db"
# and CRS transforms are unreliable — and the collectors do transform.
for _proj in "$CONTENTS/Resources/qgis/proj" "$CONTENTS/Resources/proj"; do
    [[ -f "$_proj/proj.db" ]] && { export PROJ_LIB="$_proj"; break; }
done
[[ -n "${PROJ_LIB:-}" ]] && echo "proj: $PROJ_LIB" \
    || echo "proj: proj.db NOT FOUND — CRS transforms will be unreliable" >&2
export PYTHONPATH="$REPO:$PYLIBS${DEPS:+:$DEPS}:$PYDIR:$PYDIR/lib-dynload:$PYDIR/site-packages:$CONTENTS/Resources/python"

exec "$PY" -m pytest infrared_city_gis/tests/ "$@"
