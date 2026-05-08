"""Tests for the GeoJSON → dotbim conversion pipeline.

These tests are pure unit tests — they do not call the network and do not
require QGIS to be running. They use the QGIS-free writer in test/_dotbim_writer.py.

Required fixtures (place in tests/fixtures/):
    sample_buildings.geojson         — building footprints in any projected CRS
    sample_buildings_reference.bim   — expected dotbim output for the same GeoJSON

Generate the reference file once:
    python -c "
    import json
    from pathlib import Path
    from _dotbim_writer import build_dotbim_from_geojson
    import sys; sys.path.insert(0, 'test')
    geojson = json.loads(Path('tests/fixtures/sample_buildings.geojson').read_text())
    bim = build_dotbim_from_geojson(geojson)
    Path('tests/fixtures/sample_buildings_reference.bim').write_text(json.dumps(bim, indent=2))
    "
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# _dotbim_writer is in the sibling test/ folder — conftest.py adds it to sys.path.
# Import numpy from the system, not from the plugin's thirdparty/ bundle.
from _dotbim_writer import build_dotbim_from_geojson, SCHEMA_VERSION

FIXTURES = Path(__file__).parent / "fixtures"
GEOJSON_FILE = FIXTURES / "sample_buildings.geojson"
REFERENCE_BIM_FILE = FIXTURES / "sample_buildings_reference.bim"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sample_geojson() -> dict:
    if not GEOJSON_FILE.exists():
        pytest.skip(f"Fixture missing: {GEOJSON_FILE}  (see module docstring)")
    return json.loads(GEOJSON_FILE.read_text(encoding="utf-8"))


def _parse_reference(raw: dict) -> list[dict]:
    """Normalise any reference .bim format to a list of {coordinates, indices} dicts.

    Two formats are supported:
      1. dotbim 1.1.0 — {"schema_version": ..., "meshes": [...], ...}
         Each mesh already has "coordinates" and "indices".
      2. QGIS internal format — {uuid: {"coordinates": [...], "indices": [...]}, ...}
         The plugin's geojson2dotbim service emits this flat dict keyed by UUID.
    """
    if "meshes" in raw:
        # dotbim 1.1.0 standard format
        return [{"coordinates": m["coordinates"], "indices": m["indices"]}
                for m in raw["meshes"]]
    # QGIS internal format: every value is a mesh dict keyed by UUID
    return [{"coordinates": v["coordinates"], "indices": v["indices"]}
            for v in raw.values() if isinstance(v, dict) and "coordinates" in v]


@pytest.fixture(scope="module")
def reference_dotbim() -> list[dict]:
    """Return a list of normalised mesh dicts from the reference file."""
    if not REFERENCE_BIM_FILE.exists():
        pytest.skip(f"Fixture missing: {REFERENCE_BIM_FILE}  (see module docstring)")
    raw = json.loads(REFERENCE_BIM_FILE.read_text(encoding="utf-8"))
    return _parse_reference(raw)


@pytest.fixture(scope="module")
def converted_dotbim(sample_geojson) -> dict:
    """Run the converter once and share the result across all tests in this module."""
    return build_dotbim_from_geojson(sample_geojson)


# ---------------------------------------------------------------------------
# Schema / structure tests  (no reference file needed)
# ---------------------------------------------------------------------------

def test_schema_version(converted_dotbim):
    """Output must declare the expected dotbim schema version."""
    assert converted_dotbim["schema_version"] == SCHEMA_VERSION


def test_top_level_keys(converted_dotbim):
    """Output must contain the four required dotbim top-level keys."""
    for key in ("schema_version", "meshes", "elements", "info"):
        assert key in converted_dotbim, f"Missing top-level key: {key!r}"


def test_mesh_ids_are_sequential(converted_dotbim):
    """mesh_id values must be 0-based consecutive integers."""
    ids = [m["mesh_id"] for m in converted_dotbim["meshes"]]
    assert ids == list(range(len(ids))), f"Non-sequential mesh IDs: {ids}"


def test_elements_reference_valid_mesh_ids(converted_dotbim):
    """Every element's mesh_id must point to an existing mesh."""
    valid_ids = {m["mesh_id"] for m in converted_dotbim["meshes"]}
    for elem in converted_dotbim["elements"]:
        assert elem["mesh_id"] in valid_ids, (
            f"Element references unknown mesh_id {elem['mesh_id']}"
        )


def test_meshes_and_elements_same_count(converted_dotbim):
    """Each mesh must have exactly one corresponding element."""
    assert len(converted_dotbim["meshes"]) == len(converted_dotbim["elements"])


def test_each_mesh_has_coordinates_and_indices(converted_dotbim):
    """Every mesh must have non-empty coordinate and index lists."""
    for i, mesh in enumerate(converted_dotbim["meshes"]):
        assert "coordinates" in mesh and len(mesh["coordinates"]) > 0, \
            f"Mesh {i} has no coordinates"
        assert "indices" in mesh and len(mesh["indices"]) > 0, \
            f"Mesh {i} has no indices"


def test_coordinates_are_multiples_of_three(converted_dotbim):
    """Coordinate list length must be a multiple of 3 (x, y, z triples)."""
    for i, mesh in enumerate(converted_dotbim["meshes"]):
        assert len(mesh["coordinates"]) % 3 == 0, \
            f"Mesh {i}: coordinate count {len(mesh['coordinates'])} is not a multiple of 3"


def test_indices_are_multiples_of_three(converted_dotbim):
    """Index list length must be a multiple of 3 (one triangle = 3 indices)."""
    for i, mesh in enumerate(converted_dotbim["meshes"]):
        assert len(mesh["indices"]) % 3 == 0, \
            f"Mesh {i}: index count {len(mesh['indices'])} is not a multiple of 3"


def test_indices_within_vertex_range(converted_dotbim):
    """All triangle indices must reference valid vertex positions."""
    for i, mesh in enumerate(converted_dotbim["meshes"]):
        vertex_count = len(mesh["coordinates"]) // 3
        for idx in mesh["indices"]:
            assert 0 <= idx < vertex_count, (
                f"Mesh {i}: index {idx} is out of range [0, {vertex_count})"
            )


def test_at_least_one_building_converted(converted_dotbim, sample_geojson):
    """A non-empty GeoJSON must produce at least one mesh."""
    feature_count = sum(
        1 for f in sample_geojson.get("features", []) if f.get("geometry")
    )
    if feature_count == 0:
        pytest.skip("Sample GeoJSON has no geometry features")
    assert len(converted_dotbim["meshes"]) > 0


# ---------------------------------------------------------------------------
# Reference comparison tests  (require sample_buildings_reference.bim)
#
# The reference fixture is a normalised list[dict] with "coordinates" and
# "indices" keys — see _parse_reference() above.  Both the QGIS internal
# format and the dotbim 1.1.0 format are accepted as reference files.
# ---------------------------------------------------------------------------

def test_mesh_count_matches_reference(converted_dotbim, reference_dotbim):
    """Converted mesh count must match the reference."""
    assert len(converted_dotbim["meshes"]) == len(reference_dotbim), (
        f"Got {len(converted_dotbim['meshes'])} meshes, "
        f"reference has {len(reference_dotbim)}"
    )


