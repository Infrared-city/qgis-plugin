"""Shared pytest configuration and fixtures for the infrared_city_gis test suite.

These tests run outside QGIS (no qgis.* imports) so they can be executed in
a plain Python environment or CI pipeline:

    pip install pytest numpy requests
    INFRARED_API_KEY=<your-key> pytest tests/

Integration tests (those marked with @pytest.mark.integration) call the live
Infrared API and are skipped automatically when INFRARED_API_KEY is not set.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path: make the plugin root and test/ helper folder importable.
# ---------------------------------------------------------------------------

PLUGIN_ROOT = Path(__file__).parent.parent
TESTS_DIR = Path(__file__).parent
TEST_HELPERS = PLUGIN_ROOT / "test"   # existing test/ folder with _dotbim_writer.py

# Importing the plugin package runs ``_ensure_deps()``, which pip-installs over
# the network. Set the bootstrap's own re-entry guard here — at conftest import
# time, before any test module is imported — so test modules can import plugin
# code at module level (the analysis enums the simulation matrix parametrises
# over) without a collection-time pip run. The runner script exports the same
# variable; this covers a bare ``pytest`` invocation.
os.environ.setdefault("INFRARED_BOOTSTRAP_RUNNING", "1")

# Only add the test/ helper folder — NOT the plugin root, because the plugin
# root contains a thirdparty/ directory with numpy built for a specific Python
# version that would shadow the system numpy in a plain pytest environment.
if str(TEST_HELPERS) not in sys.path:
    sys.path.insert(0, str(TEST_HELPERS))

# This directory too, so test modules can `from _baseline import ...` —
# pytest does not put conftest.py itself on the import path.
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

# Remove thirdparty/ from sys.path if it was injected by the plugin bootstrap
# (deps_bootstrap.py adds it when the plugin package is imported). Tests must
# use the system numpy, not the bundled one built for a different Python/arch.
_thirdparty = str(PLUGIN_ROOT / "thirdparty")
while _thirdparty in sys.path:
    sys.path.remove(_thirdparty)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Custom markers
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: calls the live Infrared API — requires INFRARED_API_KEY env var",
    )
    config.addinivalue_line(
        "markers",
        "qgis: needs a real QGIS runtime (qgis.core) but no network — see the qgis_app fixture",
    )
    config.addinivalue_line(
        "markers",
        "e2e: full fetch -> fetch -> simulate chain against prod; costs tokens, run by hand",
    )


# ---------------------------------------------------------------------------
# End-of-run summary (helpers live in _baseline.py)
# ---------------------------------------------------------------------------

from _baseline import _SUMMARY, DRIFT_LIMIT  # noqa: E402  (needs sys.path above)

#: Where run summaries are kept. Gitignored — these are measurements of a
#: particular moment, not source. Each run gets its own file so two runs can be
#: diffed against each other, which the terminal scrollback does not allow.
RESULTS_DIR = Path(__file__).parent / "results"


def _summary_lines(exitstatus) -> list:
    """The domain summary as text — the same content the terminal shows."""
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    flags = " ".join(
        f"{name}={os.environ[name]}"
        for name in ("INFRARED_RUN_SIMULATIONS", "UPDATE_BASELINE", "UPDATE_FIXTURES")
        if os.environ.get(name, "").strip()
    ) or "(read-only run)"

    width = max(len(step) for step, _, _ in _SUMMARY)
    lines = [
        "=" * 72,
        "Infrared workflow summary",
        f"{stamp}   {flags}   exit={exitstatus}",
        "=" * 72,
    ]
    lines += [f"  {step:<{width}}  {status:<9} {detail}" for step, status, detail in _SUMMARY]
    lines.append("=" * 72)
    if any(status == "recorded" for _, status, _ in _SUMMARY):
        lines.append("  'recorded' = new baseline written to tests/baselines/")
    if any(status == "drift" for _, status, _ in _SUMMARY):
        lines.append(f"  'drift'    = changed but within {DRIFT_LIMIT:.0%}; re-record when expected")
    return lines


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print the domain summary — pass/fail dots do not say what was measured."""
    if not _SUMMARY:
        return

    lines = _summary_lines(exitstatus)

    terminalreporter.write_line("")
    for line in lines:
        terminalreporter.write_line(line)

    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = RESULTS_DIR / datetime.now().strftime("run-%Y%m%d-%H%M%S.txt")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        latest = RESULTS_DIR / "latest.txt"
        latest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        terminalreporter.write_line(f"  saved to tests/results/{path.name} (and latest.txt)")
    except OSError as exc:   # a read-only checkout should not fail the run
        terminalreporter.write_line(f"  could not write the summary file: {exc}")

    terminalreporter.write_line("")


# ---------------------------------------------------------------------------
# QGIS runtime
#
# Tests marked ``qgis`` run the plugin's own code against real QgsVectorLayer /
# QgsGeometry objects instead of mocks — that is where the geometry, CRS and
# payload-ordering rules actually live. Two things must be handled or the run
# either hangs or misbehaves:
#
# * ``infrared_city_gis/__init__.py`` calls ``_ensure_deps()`` on import, which
#   pip-installs over the network. ``INFRARED_BOOTSTRAP_RUNNING=1`` is the
#   bootstrap's own re-entry guard and makes it return immediately.
# * Without ``PROJ_LIB`` QGIS logs "Cannot find proj.db" and CRS transforms are
#   unreliable — the collectors do transform, so this matters.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qgis_app():
    """Initialise QGIS headless for the session, or skip if it is unavailable."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["INFRARED_BOOTSTRAP_RUNNING"] = "1"

    try:
        from qgis.core import QgsApplication
    except ImportError:  # plain-Python environment: the non-qgis tests still run
        pytest.skip("qgis.core is not importable — run these under a QGIS interpreter")

    prefix = os.environ.get("QGIS_PREFIX_PATH", "")
    if prefix:
        QgsApplication.setPrefixPath(prefix, True)
    app = QgsApplication([], False)
    app.initQgis()
    yield app
    app.exitQgis()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def api_key():
    """Return the Infrared API key, or skip the test if it is not set."""
    key = os.environ.get("INFRARED_API_KEY", "").strip()
    if not key:
        pytest.skip("INFRARED_API_KEY environment variable is not set — skipping integration test")
    return key


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Path to the tests/fixtures/ directory."""
    return FIXTURES_DIR
