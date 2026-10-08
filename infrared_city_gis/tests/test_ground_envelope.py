"""A run sends every ground surface the download brought, not just bbox + 100 m.

The SDK's ground read keeps a circle around the selection (half its bbox
diagonal, at least 544 m), so on a 1 km square it reaches ~210 m past the
edges: that band is context the edge tiles' thermal model uses. The collector
used to keep only what touched the selection's bbox + 100 m, so a fresh
download lost part of that band before the run — the user saw the water stop
short of where it was downloaded — and the model ran it as default asphalt.
"""

import json
import math
from pathlib import Path

import pytest

pytestmark = pytest.mark.qgis

FIXTURE = Path(__file__).parent / "fixtures" / "vienna_16.373_48.215_ground_materials.json"
LON, LAT, SIZE_M = 16.373, 48.215, 1024.0


def _square(lon, lat, size_m):
    half_lat = (size_m / 2) / 111_320.0
    half_lon = half_lat / math.cos(math.radians(lat))
    return {"type": "Polygon", "coordinates": [[
        [lon - half_lon, lat - half_lat], [lon + half_lon, lat - half_lat],
        [lon + half_lon, lat + half_lat], [lon - half_lon, lat + half_lat],
        [lon - half_lon, lat - half_lat],
    ]]}


def test_the_envelope_reaches_as_far_as_the_sdk_read(qgis_app):
    from infrared_city_gis.services.ground_materials import ground_envelope

    polygon = _square(LON, LAT, SIZE_M)
    west, south, east, north = ground_envelope(polygon)
    sel_w = polygon["coordinates"][0][0][0]
    past_west_m = (sel_w - west) * 111_195 * math.cos(math.radians(LAT))

    # half diagonal 724 m - half side 512 m = ~212 m; the old envelope was 100 m
    assert 205 < past_west_m < 220
    assert south < LAT - 0.004 and north > LAT + 0.004 and east > LON + 0.006


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
