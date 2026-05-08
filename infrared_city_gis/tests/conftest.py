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
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path: make the plugin root and test/ helper folder importable.
# ---------------------------------------------------------------------------

PLUGIN_ROOT = Path(__file__).parent.parent
TEST_HELPERS = PLUGIN_ROOT / "test"   # existing test/ folder with _dotbim_writer.py

# Only add the test/ helper folder — NOT the plugin root, because the plugin
# root contains a thirdparty/ directory with numpy built for a specific Python
# version that would shadow the system numpy in a plain pytest environment.
if str(TEST_HELPERS) not in sys.path:
    sys.path.insert(0, str(TEST_HELPERS))

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
