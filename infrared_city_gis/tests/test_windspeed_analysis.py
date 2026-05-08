"""End-to-end windspeed analysis integration test.

Pipeline:
    sample_buildings.geojson
        → build_dotbim_from_geojson()   (QGIS-free, local)
        → wind-speed payload
        → Infrared run-analysis API     (live HTTP call)
        → decoded float matrix
        → comparison against stored reference matrix

Required fixture (place in tests/fixtures/):
    sample_buildings.geojson     — building footprints in any projected CRS

Optional fixture (auto-generated on first run):
    windspeed_reference_matrix.npy  — stored numpy array for regression testing.
    If this file is absent the first test run saves the API result as the new
    reference and skips the comparison. Re-run to compare against it.

Run:
    INFRARED_API_KEY=<your-key> pytest tests/test_windspeed_analysis.py -v
    INFRARED_API_KEY=<your-key> pytest tests/test_windspeed_analysis.py -v -m integration
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

# _dotbim_writer and run_test are in the sibling test/ folder;
# conftest.py adds it to sys.path.
from _dotbim_writer import build_dotbim_from_geojson
from run_test import call_run_analysis, decode_result

FIXTURES = Path(__file__).parent / "fixtures"
GEOJSON_FILE = FIXTURES / "sample_buildings.geojson"
REFERENCE_MATRIX_FILE = FIXTURES / "windspeed_reference_matrix.npy"

# Endpoint — matches the constant in constants.py
API_URL = "https://fbiw2nq5ac.execute-api.eu-central-1.amazonaws.com/v2/api/run-analysis"

# Fixed analysis parameters used across all tests in this file so that the
# reference matrix can be reproduced deterministically.
WIND_DIRECTION = 270   # degrees (westerly wind)
WIND_SPEED = 5         # m/s


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sample_geojson() -> dict:
    if not GEOJSON_FILE.exists():
        pytest.skip(f"Fixture missing: {GEOJSON_FILE}")
    return json.loads(GEOJSON_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dotbim(sample_geojson) -> dict:
    """Convert the sample GeoJSON to dotbim once for the whole module."""
    return build_dotbim_from_geojson(sample_geojson)


@pytest.fixture(scope="module")
def windspeed_payload(dotbim) -> dict:
    """Build the wind-speed analysis payload with the fixed parameters."""
    return {
        "analysis-type": "wind-speed",
        "geometries": dotbim,
        "wind-direction": WIND_DIRECTION,
        "wind-speed": WIND_SPEED,
    }


@pytest.fixture(scope="module")
def api_result(windspeed_payload, api_key) -> np.ndarray:
    """Call the API once and decode the matrix; shared across all tests."""
    response_json = call_run_analysis(windspeed_payload, API_URL, api_key)
    raw = decode_result(response_json)
    return np.array(raw, dtype=np.float32)


# ---------------------------------------------------------------------------
# Local / structural tests  (no API call)
# ---------------------------------------------------------------------------

def test_dotbim_is_built_from_geojson(sample_geojson):
    """Conversion must produce at least one mesh before even calling the API."""
    bim = build_dotbim_from_geojson(sample_geojson)
    assert len(bim["meshes"]) > 0, "No meshes produced from sample GeoJSON"


def test_windspeed_payload_structure(windspeed_payload):
    """Payload must have all required keys before being sent to the API."""
    assert windspeed_payload["analysis-type"] == "wind-speed"
    assert windspeed_payload["wind-direction"] == WIND_DIRECTION
    assert windspeed_payload["wind-speed"] == WIND_SPEED
    assert windspeed_payload["geometries"] is not None


# ---------------------------------------------------------------------------
# Integration tests  (call the live Infrared API)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_api_returns_2d_matrix(api_result):
    """The decoded API response must be a 2-D float32 matrix."""
    assert api_result.ndim == 2, (
        f"Expected a 2-D matrix, got shape {api_result.shape}"
    )
    assert api_result.shape[0] > 1 and api_result.shape[1] > 1, (
        f"Matrix is too small: {api_result.shape}"
    )


@pytest.mark.integration
def test_matrix_contains_valid_wind_speeds(api_result):
    """Non-NaN cells must contain non-negative wind speeds below 100 m/s."""
    non_nan = api_result[~np.isnan(api_result)]
    assert len(non_nan) > 0, "Matrix contains only NaN — no valid result cells"
    assert float(non_nan.min()) >= 0.0, (
        f"Negative wind speed in result: {float(non_nan.min()):.3f} m/s"
    )
    assert float(non_nan.max()) < 100.0, (
        f"Implausibly large wind speed: {float(non_nan.max()):.3f} m/s"
    )


@pytest.mark.integration
def test_matrix_has_reasonable_data_coverage(api_result):
    """At least 10 % of cells must contain valid (non-NaN) data.

    A completely NaN matrix usually indicates a geometry or payload problem.
    """
    total = api_result.size
    valid = int(np.sum(~np.isnan(api_result)))
    coverage = valid / total
    assert coverage >= 0.10, (
        f"Only {coverage:.1%} of cells are valid — expected at least 10%"
    )


@pytest.mark.integration
def test_matrix_matches_reference(api_result):
    """Matrix values must match the stored reference within numerical tolerance.

    First run behaviour: if no reference file exists the current result is
    saved as the new baseline and the test is skipped with an informational
    message. Re-run the suite to compare against it.

    Tolerance: 1e-3 absolute (≈ 1 mm/s), 1e-4 relative — accounts for
    minor floating-point differences between API server deployments.
    """
    if not REFERENCE_MATRIX_FILE.exists():
        np.save(str(REFERENCE_MATRIX_FILE), api_result)
        pytest.skip(
            f"No reference matrix found — saved current result to "
            f"{REFERENCE_MATRIX_FILE}. Re-run the test to compare."
        )

    reference = np.load(str(REFERENCE_MATRIX_FILE))

    assert api_result.shape == reference.shape, (
        f"Matrix shape changed: got {api_result.shape}, "
        f"reference is {reference.shape}"
    )

    np.testing.assert_allclose(
        api_result,
        reference,
        rtol=1e-4,
        atol=1e-3,
        equal_nan=True,
        err_msg=(
            "Wind-speed matrix differs from the stored reference. "
            "If the API output legitimately changed, delete "
            f"{REFERENCE_MATRIX_FILE} and re-run to capture a new baseline."
        ),
    )
