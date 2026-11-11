"""The download covers the band the edge tiles read, and the run sends all of it.

Every simulation tile reads ground material up to ``ground_read_margin_m()``
(544 m on the widest preset) beyond its own CENTRE, and where that ground is
missing the server composes default asphalt. Three things have to hold for the
edge tiles to see real surfaces:

* the DOWNLOAD must reach that far past the selection —
  ``fetch_polygon_with_context``. ``get_area`` alone crops to a circle around
  the bbox centre with radius ``max(half the bbox diagonal, 544 m)``, which
  circumscribes the bbox and therefore reaches **0 m past the corners**
  (measured 2026-10-16: +257 m E/W, +220 m N/S, 0 m at the corners);
* it must also cover the GRID's own overhang — ``ground_grid_overhang_m()``.
  The tile grid does not stop at the selection: the SDK steps it from the
  bbox's south-west corner, so a centre can land half a step outside
  (measured on the UTCI grid, 512 m step: +168 m east, +94 m north). Growing
  the bbox by the read margin ALONE leaves those tiles short, which is the
  same default-asphalt band in a narrower form;
* the COLLECTOR must not clip it back off again — ``ground_envelope``. It used
  to keep only what touched the selection's bbox + 100 m, so a fresh download
  lost part of the band before the run and the user saw the water stop short of
  where it was downloaded.
"""

import json
import math
from pathlib import Path

import pytest

pytestmark = pytest.mark.qgis

FIXTURE = Path(__file__).parent / "fixtures" / "vienna_16.373_48.215_ground_materials.json"
LON, LAT, SIZE_M = 16.373, 48.215, 1024.0

#: A square the UTCI grid (512 m step) can NOT tile flush: just over two steps,
#: so the third row and column overhang the selection by nearly a half-step.
#: ``SIZE_M`` is exactly two steps and overhangs by nothing, which is why the
#: overhang test cannot use it.
OVERHANG_SIZE_M = 1030.0


def _square(lon, lat, size_m):
    half_lat = (size_m / 2) / 111_320.0
    half_lon = half_lat / math.cos(math.radians(lat))
    return {"type": "Polygon", "coordinates": [[
        [lon - half_lon, lat - half_lat], [lon + half_lon, lat - half_lat],
        [lon + half_lon, lat + half_lat], [lon - half_lon, lat + half_lat],
        [lon - half_lon, lat - half_lat],
    ]]}


def test_the_envelope_reaches_as_far_as_a_tile_reads(qgis_app):
    """Not just as far as the SDK's circle: as far as the tiles read.

    On this 1 km square the circle reaches ~212 m past the edge (and 0 m past
    the corners), while a tile reads 544 m — so the envelope follows the wider
    of the two, or it would clip the context the download now brings.
    """
    from infrared_city_gis.services.ground_extent import (
        ground_context_margin_m,
        ground_envelope,
    )

    polygon = _square(LON, LAT, SIZE_M)
    west, south, east, north = ground_envelope(polygon)
    sel_w = polygon["coordinates"][0][0][0]
    past_west_m = (sel_w - west) * 111_195 * math.cos(math.radians(LAT))

    assert past_west_m >= ground_context_margin_m()
    assert past_west_m < ground_context_margin_m() + 20
    assert south < LAT - 0.004 and north > LAT + 0.004 and east > LON + 0.006


def test_the_download_area_grows_by_the_read_margin(qgis_app):
    """The corners are what the SDK's own circle leaves at zero margin."""
    from infrared_city_gis.services.ground_extent import (
        fetch_polygon_with_context,
        ground_context_margin_m,
    )

    polygon = _square(LON, LAT, SIZE_M)
    grown = fetch_polygon_with_context(polygon)
    ring = grown["coordinates"][0]
    assert ring[0] == ring[-1], "the ring must be closed"

    sel = polygon["coordinates"][0]
    sel_w, sel_s = min(p[0] for p in sel), min(p[1] for p in sel)
    sel_e, sel_n = max(p[0] for p in sel), max(p[1] for p in sel)
    west, south = min(p[0] for p in ring), min(p[1] for p in ring)
    east, north = max(p[0] for p in ring), max(p[1] for p in ring)

    margin = ground_context_margin_m()
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(LAT))
    for grew_m in (
        (sel_w - west) * m_per_deg_lon, (east - sel_e) * m_per_deg_lon,
        (sel_s - south) * 111_320.0, (north - sel_n) * 111_320.0,
    ):
        assert abs(grew_m - margin) < 20, grew_m


def test_the_download_covers_the_grid_overhang_too(qgis_app):
    """The real grid, not the selection, decides how far the read reaches.

    Regression for the gap left by the first cut of this fix: the bbox was
    grown by the READ MARGIN alone, but the SDK's grid starts at the bbox's
    south-west corner and steps past the east and north edges, so the
    outermost tile centres sit OUTSIDE the selection and read 544 m from
    there. Measured on this square: the UTCI grid (512 m step) puts a centre
    up to a half-step out, and every tile's full read has to be inside what we
    downloaded.

    NOT the ``SIZE_M`` square the other tests use: 1024 m is exactly two 512 m
    steps, so that grid lands flush on the selection and overhangs by nothing
    — it cannot tell the two margins apart. ``OVERHANG_SIZE_M`` is just over
    two steps, so the third row and column each stick out by nearly a full
    half-step, which is the worst case this margin exists for.
    """
    import logging

    from infrared_sdk.tiling.read_margin import (
        WIDEST_READ_ANALYSIS_TYPE,
        ground_read_distance_m,
    )
    from infrared_sdk.tiling.tiles import TileService

    from infrared_city_gis.services.ground_extent import fetch_polygon_with_context

    polygon = _square(LON, LAT, OVERHANG_SIZE_M)
    ring = fetch_polygon_with_context(polygon)["coordinates"][0]
    dl_w, dl_s = min(p[0] for p in ring), min(p[1] for p in ring)
    dl_e, dl_n = max(p[0] for p in ring), max(p[1] for p in ring)

    grid = TileService(
        polygon=polygon,
        logger=logging.getLogger(__name__),
        analysis_type=str(WIDEST_READ_ANALYSIS_TYPE),
    ).generate_tiles_for_polygon()
    centres = [t.centroid for row in grid for t in row if not t.empty]
    assert centres, "the fixture square must produce a grid"

    read_m = ground_read_distance_m(None)
    d_lat = read_m / 111_320.0
    for c in centres:
        d_lon = d_lat / math.cos(math.radians(c.latitude))
        assert c.longitude - d_lon >= dl_w, f"tile reads west of the download: {c}"
        assert c.longitude + d_lon <= dl_e, f"tile reads east of the download: {c}"
        assert c.latitude - d_lat >= dl_s, f"tile reads south of the download: {c}"
        assert c.latitude + d_lat <= dl_n, f"tile reads north of the download: {c}"


def test_a_polygon_without_a_ring_is_passed_through(qgis_app):
    """The SDK's own validation refuses this; the margin must not raise first."""
    from infrared_city_gis.services.ground_extent import fetch_polygon_with_context

    assert fetch_polygon_with_context({"type": "Polygon"}) == {"type": "Polygon"}


def test_a_downloaded_set_reaches_the_run_whole(qgis_app, tmp_path, monkeypatch):
    """Every feature the (recorded) SDK read returned for this square is sent."""
    from qgis.core import QgsProject

    from infrared_city_gis.services.ground_materials import collect_ground_materials
    from infrared_city_gis.visualization import layers

    monkeypatch.setattr(layers, "ground_package_path", lambda: str(tmp_path / "g.gpkg"))
    downloaded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    try:
        layers.display_ground_materials(downloaded)
        by_material = {
            ly.name().removeprefix("ground-"): [ly]
            for ly in QgsProject.instance().mapLayers().values()
            if ly.name().startswith("ground-")
        }
        sent = collect_ground_materials(_square(LON, LAT, SIZE_M), by_material)
    finally:
        QgsProject.instance().removeAllMapLayers()

    expected = {m: len(fc["features"]) for m, fc in downloaded.items() if fc.get("features")}
    assert {m: len(fc["features"]) for m, fc in sent.items()} == expected
