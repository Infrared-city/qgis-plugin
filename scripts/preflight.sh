#!/usr/bin/env bash
# Everything CI will check, plus the tests CI cannot run — before you open a PR.
#
#   scripts/preflight.sh              # the free checks
#   scripts/preflight.sh --e2e        # also the paid prod round (needs a key)
#
# The lint and security commands here are COPIES of .github/workflows/lint.yml.
# Keeping them identical is the point: a local command that drifts from CI is
# how a scan ends up looking green (or, as happened on 2026-09-29, looking
# broken) for reasons that have nothing to do with the code. If you change one,
# change both.
#
# Runs every step even after a failure, so one pass tells you everything that
# is wrong rather than the first thing. Exits non-zero if any hard gate failed.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

RUN_E2E=0
[[ "${1:-}" == "--e2e" ]] && RUN_E2E=1

FAILED=()
SKIPPED=()

# A Python that actually runs. On Windows `python3` can be the Microsoft Store
# stub, which "exists" but runs nothing — every python3 check here used to
# skip silently on such a machine.
PY=$(for c in python3 python; do "$c" -c '' >/dev/null 2>&1 && { echo "$c"; break; }; done)

bold() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32mok\033[0m      %s\n' "$1"; }
bad()  { printf '  \033[31mFAILED\033[0m  %s\n' "$1"; FAILED+=("$1"); }
skip() { printf '  \033[33mskipped\033[0m %s — %s\n' "$1" "$2"; SKIPPED+=("$1: $2"); }

# Run a gate, capturing output and showing it only on failure.
gate() {
    local name="$1"; shift
    local out
    if out=$("$@" 2>&1); then
        ok "$name"
    else
        bad "$name"
        printf '%s\n' "$out" | sed 's/^/          /'
    fi
}

# ---------------------------------------------------------------- lint ----

bold "Lint (the CI gates — NOT pylint)"

if command -v ruff >/dev/null; then
    gate "ruff" ruff check infrared_city_gis/
else
    skip "ruff" "not installed (pip install ruff)"
fi

if command -v flake8 >/dev/null; then
    gate "flake8" flake8 infrared_city_gis/
else
    skip "flake8" "not installed (pip install flake8)"
fi

# ------------------------------------------------------------ security ----

bold "Security (what plugins.qgis.org runs on every upload)"

# The exclusions must name BOTH virtualenvs: bandit walks the filesystem, not
# git, so a local venv/ or .venv/ inside the plugin folder (gitignored, ~140 MB
# together) otherwise drowns the scan in third-party findings. A runner never
# has them; your machine does.
if command -v bandit >/dev/null; then
    gate "bandit" bandit -r infrared_city_gis/ \
        -x infrared_city_gis/tests,infrared_city_gis/test,infrared_city_gis/thirdparty,infrared_city_gis/venv,infrared_city_gis/.venv
else
    skip "bandit" "not installed (pip install bandit)"
fi

if command -v detect-secrets-hook >/dev/null; then
    detect_secrets() {
        git ls-files -z 'infrared_city_gis/*' \
            | grep -zEv '^infrared_city_gis/(tests|test)/' \
            | xargs -0 detect-secrets-hook
    }
    gate "detect-secrets" detect_secrets
else
    skip "detect-secrets" "not installed (pip install detect-secrets)"
fi

# The uploader rejects archives containing executables or scripts
# (FILE_SUSPICIOUS). Nothing in the shipped package should need one.
no_executables() {
    local found
    found=$(git ls-files 'infrared_city_gis/*' \
        | grep -Ev '^infrared_city_gis/(tests|test)/' \
        | grep -Ei '\.(exe|dll|so|dylib|sh|bat|cmd|ps1)$' || true)
    if [ -n "$found" ]; then
        echo "These would ship in the plugin ZIP:"
        echo "$found"
        return 1
    fi
}
gate "no executables in the package" no_executables

# ------------------------------------------------------------- Qt5/Qt6 ----

bold "Qt5/Qt6 name resolution (what a linter cannot catch)"

if [[ -n "$PY" ]] && "$PY" -c "import PyQt6" 2>/dev/null; then
    gate "qt6-names" "$PY" scripts/check_qt6_names.py infrared_city_gis
else
    skip "qt6-names" "PyQt6 not importable (pip install PyQt6) — CI covers it"
fi

# --------------------------------------------------------------- tests ----

bold "Tests (real QGIS runtime)"

if [[ -x scripts/run_qgis_tests.sh ]]; then
    gate "qgis suite" ./scripts/run_qgis_tests.sh -q
else
    skip "qgis suite" "scripts/run_qgis_tests.sh missing or not executable"
fi

if [[ "$RUN_E2E" == 1 ]]; then
    if [[ -n "${INFRARED_API_KEY:-}" ]]; then
        bold "E2E (hits prod — these cost tokens)"
        gate "e2e" ./scripts/run_qgis_tests.sh -m e2e -s
    else
        skip "e2e" "INFRARED_API_KEY is not set"
    fi
fi

# ---------------------------------------------------------- merge gate ----
# Not a check on the code: the branch cannot merge until the pinned SDK is
# installable from PyPI, because utils/deps_bootstrap.py installs from there
# and has no index-URL support. TestPyPI does not count.

bold "Merge gate"

SDK_LINE=$(grep -E '^infrared-sdk' infrared_city_gis/requirements.txt 2>/dev/null | head -1)
PINNED=$(printf '%s' "$SDK_LINE" | grep -oE '==[0-9][0-9A-Za-z.]*' | sed 's/^==//')
if [[ -z "$SDK_LINE" ]]; then
    skip "sdk on PyPI" "no infrared-sdk line in requirements.txt"
elif [[ -z "$PINNED" ]]; then
    # A floor (>=) lets the bootstrap hand fresh installs an SDK this plugin
    # was never tested with; v1.1.3 (>=0.4.11) broke the day 1.0.0 shipped.
    bad "infrared-sdk must be an exact pin (==X.Y.Z), got: $SDK_LINE"
elif ! command -v curl >/dev/null; then
    skip "sdk on PyPI" "curl not available"
else
    BODY=$(mktemp)
    CODE=$(curl -s --max-time 15 -o "$BODY" -w '%{http_code}' \
        "https://pypi.org/pypi/infrared-sdk/$PINNED/json" 2>/dev/null)
    case "$CODE" in
        200)
            # stdin, not a path: a Windows Python cannot open Git Bash's /tmp.
            if [[ -n "$PY" ]] && "$PY" -c '
import json, sys
files = json.load(sys.stdin)["urls"]
sys.exit(0 if files and not all(f.get("yanked") for f in files) else 1)
' < "$BODY" 2>/dev/null; then
                ok "infrared-sdk $PINNED is on PyPI"
            else
                bad "infrared-sdk $PINNED is yanked or has no files on PyPI — pin another version"
            fi ;;
        404) bad "infrared-sdk $PINNED is NOT on PyPI (TestPyPI does not count) — a fresh install cannot bootstrap" ;;
        *)   skip "sdk on PyPI" "could not reach PyPI (HTTP ${CODE:-none})" ;;
    esac
    rm -f "$BODY"
fi

# -------------------------------------------------------------- report ----

bold "Summary"

if [[ ${#SKIPPED[@]} -gt 0 ]]; then
    printf '  Not run:\n'
    printf '    - %s\n' "${SKIPPED[@]}"
fi

# Things no script can settle. Named every time, because they are exactly what
# gets forgotten between "the suite is green" and "the PR is ready".
cat <<'NOTE'

  Still yours to do:
    - The QGIS 4 manual round (docs/manual-testing.md). The suite covers logic,
      not what a dialog looks like or whether the toolbar behaves.
    - /ir-dev:audit-branch — conventions, reinvention and doc sync on the diff.
    - If you develop from a QGIS profile, remember the installed plugin is a
      COPY, not a symlink: re-sync it or you are testing stale code.
NOTE

if [[ ${#FAILED[@]} -gt 0 ]]; then
    printf '\n  \033[31m%d gate(s) failed:\033[0m\n' "${#FAILED[@]}"
    printf '    - %s\n' "${FAILED[@]}"
    exit 1
fi

printf '\n  \033[32mAll gates that could run passed.\033[0m\n'
