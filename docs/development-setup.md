# Development setup

What a new machine needs before the plugin runs or the tests pass. None of this
is in the repo — it is QGIS profile state and a hand-seeded dependency tree — so
it is written down here rather than left in someone's shell history.

Read [`CLAUDE.md`](../CLAUDE.md) for the commands; this is the environment they
assume.

## 1. The installed plugin is a COPY, not a symlink

QGIS loads the plugin from its profile, not from your clone:

```
~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/infrared_city_gis
```

Copying means **a change you have not re-synced is not the change you are
testing** — a silent false pass, and the most common way to waste an hour.
Re-sync after every edit:

```bash
R=infrared_city_gis
P=~/Library/Application\ Support/QGIS/QGIS3/profiles/default/python/plugins/infrared_city_gis
rsync -a --delete \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  --exclude 'logs/' --exclude 'settings/' --exclude 'tests/results/' \
  --exclude 'venv/' --exclude '.venv/' \
  "$R/" "$P/"
```

A symlink avoids the re-sync entirely and is worth it if you iterate a lot. It
also drops `tests/` into the plugin folder, which QGIS ignores and the release
ZIP excludes:

```bash
# quit QGIS first
rm -rf "$P" && ln -s "$PWD/infrared_city_gis" "$P"
```

## 2. Seeding the dependency directory by hand

`utils/deps_bootstrap.py` pip-installs `requirements.txt` into

```
<profile>/infrared_city_gis/deps-<hash>/
```

where `<hash>` is a short SHA-1 of the sorted requirement specs — so **changing
`requirements.txt` moves the folder**, and the new one starts empty.

While the pinned `infrared-sdk` is not on PyPI, that install fails and the
plugin does not load at all. Seed the folder by hand instead. `_find_missing`
only checks importability, so a seeded folder is never touched by the
bootstrap.

```bash
# The interpreter matters. QGIS links the python.org framework build; the
# default python3 here is conda, and its cp313 wheels cannot be imported by
# QGIS's 3.12.
PY=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12

# The folder name is the hash of the CURRENT requirements.txt:
HASH=$(python3 -c "
import hashlib
specs = sorted(l.strip() for l in open('infrared_city_gis/requirements.txt')
               if l.strip() and not l.startswith('#'))
print(hashlib.sha1('\n'.join(specs).encode(), usedforsecurity=False).hexdigest()[:12])")

for PROFILE in QGIS3 QGIS4; do
  T=~/Library/Application\ Support/QGIS/$PROFILE/profiles/default/infrared_city_gis/deps-$HASH
  rm -rf "$T"
  # TWO passes. A single `--pre --index-url testpypi` install pulls
  # PRERELEASES OF EVERY PACKAGE — observed: pydantic 2.14.0b2, shapely
  # 2.2.0rc1, urllib3 dev. Only the SDK comes from TestPyPI.
  $PY -m pip install --target "$T" --no-deps \
      --index-url https://test.pypi.org/simple "infrared-sdk==<version>"
  $PY -m pip install --target "$T" \
      "requests>=2.0" "pydantic>=2.0" validators \
      "numpy>=1.23.0,<2.5" "mapbox_earcut>=1.0.0" "structlog>=24.0.0" \
      pyarrow shapely
done
```

`numpy<2.5` is not optional: the deps folder goes to `sys.path[0]` and shadows
QGIS's own numpy, and QGIS's scipy 1.15.3 requires `numpy<2.5`.

`pyarrow` and `shapely` are the `[geodata]` extra. Without them
`ground_materials.get_area` raises `GeodataDependencyError`.

**A dependency swap needs a QGIS restart** — the SDK ships a compiled
`infrared_core.abi3.so` and native extensions cannot be hot-swapped.

Verify the way the bootstrap does, by importability:

```bash
PYTHONPATH="$T" $PY -c "
import numpy, mapbox_earcut, structlog, infrared_sdk, pyarrow, shapely
from infrared_sdk._internal.kernel_contract import require_kernel
require_kernel()
print('ok', infrared_sdk.__version__)"
```

## 3. Running the checks

```bash
scripts/preflight.sh            # every CI gate, plus the QGIS suite
scripts/preflight.sh --e2e      # also the paid prod round (needs a key)
```

It mirrors `.github/workflows/lint.yml` command for command. If you change one,
change both — a local command that drifts from CI is how a scan ends up looking
broken for reasons that have nothing to do with the code.

The test harness prefers the **QGIS4** profile and takes the first `deps-*`
folder it finds, which is why the loop above seeds both.

### The two cost gates

17 tests are skipped by default, deliberately:

| gate | tests | what they would do |
|---|---|---|
| `INFRARED_API_KEY` | 7 | real prod reads — consume tokens |
| `INFRARED_RUN_SIMULATIONS=1` | 10 | **submit paid runs** |

```bash
INFRARED_API_KEY=… INFRARED_RUN_SIMULATIONS=1 ./scripts/run_qgis_tests.sh -m e2e -s
```

## 4. Before a PR

1. `scripts/preflight.sh` — and read its "Still yours to do" tail.
2. `/ir-dev:audit-branch` — conventions, reinvention, doc sync on the diff.
3. The manual round in [`manual-testing.md`](manual-testing.md). The suite
   covers logic, not what a dialog looks like.
4. Check the merge gate the script reports: the pinned `infrared-sdk` must be
   installable **from PyPI**. TestPyPI does not count — `deps_bootstrap` has no
   index-URL support, so a fresh install cannot bootstrap without it.

[`battle-scars.md`](battle-scars.md) is worth skimming before touching
packaging, the legend, or anything that runs at QGIS shutdown.
