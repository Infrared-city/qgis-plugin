"""Fetched ground materials become file-backed layers, not QGIS scratch layers.

They used to be memory layers: QGIS flags those "Temporary scratch layer only!"
and drops their contents when it closes, so a several-minute download was lost
with the session. They are now saved to a GeoPackage and loaded from it. The
same change fixed a quieter bug: the old builder kept only each polygon's outer
ring, so a courtyard or a pond inside a polygon took that polygon's material.
"""

import pytest

pytestmark = pytest.mark.qgis

OUTER = [[16.370, 48.210, 0.0], [16.374, 48.210, 0.0], [16.374, 48.214, 0.0],
         [16.370, 48.214, 0.0], [16.370, 48.210, 0.0]]
HOLE = [[16.371, 48.211, 0.0], [16.372, 48.211, 0.0], [16.372, 48.212, 0.0],
        [16.371, 48.212, 0.0], [16.371, 48.211, 0.0]]

#: As the SDK returns it: 2.5D coordinates, one polygon with a hole, plus a
#: MultiPolygon, and a property the plugin must not carry into the payload.
FETCHED = {
    "concrete": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"z": 0.00002},
         "geometry": {"type": "Polygon", "coordinates": [OUTER, HOLE]}},
        {"type": "Feature", "properties": {},
         "geometry": {"type": "MultiPolygon", "coordinates": [[[
             [16.375, 48.210], [16.376, 48.210], [16.376, 48.211], [16.375, 48.210],
         ]]]}},
    ]},
}

AREA = {"type": "Polygon", "coordinates": [[
    [16.369, 48.209], [16.377, 48.209], [16.377, 48.215], [16.369, 48.215], [16.369, 48.209],
]]}


@pytest.fixture
def saved(qgis_app, tmp_path, monkeypatch):
    """Run display_ground_materials into a temporary GeoPackage; clean the project."""
    from qgis.core import QgsProject

    from infrared_city_gis.visualization import layers

    gpkg = tmp_path / "ground.gpkg"
    monkeypatch.setattr(layers, "ground_package_path", lambda: str(gpkg))
    created = layers.display_ground_materials(FETCHED)
    project_layers = {ly.name(): ly for ly in QgsProject.instance().mapLayers().values()}
    yield gpkg, created, project_layers
    QgsProject.instance().removeAllMapLayers()


def test_the_layer_is_a_file_not_a_scratch_layer(saved):
    from qgis.core import Qgis

    gpkg, created, project_layers = saved
    layer = project_layers["ground-concrete"]

    assert created == {"ground-concrete": 2}
    assert layer.providerType() == "ogr", "a memory layer is lost when QGIS closes"
    assert str(gpkg) in layer.source() and gpkg.exists()
    caps = layer.dataProvider().capabilities()
    assert caps & Qgis.VectorProviderCapability.ChangeGeometries, "must stay editable"


def test_polygon_holes_survive(saved):
    _gpkg, _created, project_layers = saved
    layer = project_layers["ground-concrete"]

    rings = sorted(
        len(part)
        for feature in layer.getFeatures()
        for part in feature.geometry().asMultiPolygon()
    )
    assert rings == [1, 2], "the hole was filled with the surrounding material"


def test_saved_layers_are_two_dimensional_geometry_only(saved):
    from qgis.core import QgsWkbTypes

    _gpkg, _created, project_layers = saved
    layer = project_layers["ground-concrete"]

    assert not QgsWkbTypes.hasZ(layer.wkbType()), "the collector stamps z itself"
    assert [f.name() for f in layer.fields()] == ["fid"], "fetched attributes rode along"


def test_the_payload_carries_no_storage_key(saved):
    """A GeoPackage exposes its `fid` as a field; the model must not receive it."""
    from infrared_city_gis.services.ground_materials import collect_ground_materials

    _gpkg, _created, project_layers = saved
    collected = collect_ground_materials(AREA, {"concrete": [project_layers["ground-concrete"]]})

    props = [f["properties"] for f in collected["concrete"]["features"]]
    assert props and all("fid" not in p for p in props)
    assert all(p["material"] == "concrete" for p in props)


def test_a_second_fetch_is_numbered_in_its_own_file(saved, tmp_path, monkeypatch):
    from infrared_city_gis.visualization import layers

    second = tmp_path / "ground-2.gpkg"
    monkeypatch.setattr(layers, "ground_package_path", lambda: str(second))

    created = layers.display_ground_materials(FETCHED)

    assert created == {"ground-concrete-2": 2}
    assert second.exists()


# -- a download that cannot save every table (PR #51 review) -----------------

TWO = {
    "concrete": FETCHED["concrete"],
    "water": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Polygon", "coordinates": [OUTER]}},
    ]},
    "soil": {"type": "FeatureCollection", "features": []},
}


def test_a_table_that_fails_to_save_makes_the_download_incomplete(qgis_app, tmp_path, monkeypatch):
    """A disk-full on one table used to be reported as 'Ground Materials Added'."""
    from qgis.core import QgsProject, QgsVectorFileWriter

    from infrared_city_gis.exceptions import IncompleteGroundDownload
    from infrared_city_gis.visualization import layers

    real_write = layers._write_table

    def flaky(source, path, options):
        if options.layerName == "ground-water":
            return QgsVectorFileWriter.WriterError.ErrCreateDataSource, "disk full"
        return real_write(source, path, options)

    monkeypatch.setattr(layers, "ground_package_path", lambda: str(tmp_path / "g.gpkg"))
    monkeypatch.setattr(layers, "_write_table", flaky)
    try:
        with pytest.raises(IncompleteGroundDownload) as caught:
            layers.display_ground_materials(TWO)
        names = {ly.name() for ly in QgsProject.instance().mapLayers().values()}
    finally:
        QgsProject.instance().removeAllMapLayers()

    assert caught.value.created == {"ground-concrete": 2}
    assert set(caught.value.failed) == {"ground-water"}
    assert "disk full" in caught.value.failed["ground-water"]
    assert names == {"ground-concrete"}, "the saved layer is kept, the failed one is not added"


def test_an_empty_material_is_absent_not_a_failure(qgis_app, tmp_path, monkeypatch):
    from qgis.core import QgsProject

    from infrared_city_gis.visualization import layers

    monkeypatch.setattr(layers, "ground_package_path", lambda: str(tmp_path / "g.gpkg"))
    try:
        created = layers.display_ground_materials({"soil": TWO["soil"], "water": TWO["water"]})
    finally:
        QgsProject.instance().removeAllMapLayers()

    assert created == {"ground-water": 1}


def test_startup_cleanup_prunes_logs_but_never_downloaded_data(qgis_app, tmp_path, monkeypatch):
    """A month-old GeoPackage may still back a saved project (PR #51 review)."""
    import os
    import time

    from infrared_city_gis.utils import helper

    root = tmp_path / "infrared_city_gis"
    (root / "data").mkdir(parents=True)
    (root / "logs").mkdir()
    old = time.time() - 40 * 86400
    kept = root / "data" / "infrared_city_ground_materials_old.gpkg"
    pruned = root / "logs" / "infrared_city_old.log"
    for f in (kept, pruned):
        f.write_text("x")
        os.utime(f, (old, old))

    monkeypatch.setattr(helper.QgsApplication, "qgisSettingsDirPath", staticmethod(lambda: str(tmp_path)))
    helper.cleanup_old_logs()

    assert kept.exists(), "downloaded data was deleted"
    assert not pruned.exists(), "an old log was kept"
