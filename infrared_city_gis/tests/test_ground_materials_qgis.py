"""Ground-material collection against a real QGIS runtime.

``collect_ground_materials`` carries more implicit contract than any other
function in the plugin, and none of it is visible from the call site:

* The **key order** of the returned dict — not the Z it stamps — is what
  decides the surface stack, because both clean-v3 and infrared-core's
  ``ground_clean`` re-stamp ``z = (i + 1) * z_step`` by dict insertion order
  and insert a full-bbox default backdrop at asphalt's index.
* Z is assigned by **fixed slot**, so a subset selection keeps vegetation at
  5e-5 instead of collapsing it to a lower slot where water would override it.
* Every feature needs ``properties.material``; without it the Lambda's
  emissivity lookup falls back to a default surface.

Those rules were all violated at some point (see docs/battle-scars.md). These
tests pin them against real ``QgsVectorLayer`` objects — mocks would simply
agree with whatever the code does.
"""

import pytest

pytestmark = pytest.mark.qgis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ring(x0, y0, x1, y1):
    from qgis.core import QgsPointXY

    return [
        QgsPointXY(x0, y0), QgsPointXY(x1, y0), QgsPointXY(x1, y1),
        QgsPointXY(x0, y1), QgsPointXY(x0, y0),
    ]


def _layer(name, ring):
    """A one-feature in-memory polygon layer, as the fetch dialog creates."""
    from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer

    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", name, "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromPolygonXY([ring]))
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    return layer


AREA = {
    "type": "Polygon",
    "coordinates": [[
        [16.372, 48.208], [16.376, 48.208],
        [16.376, 48.212], [16.372, 48.212], [16.372, 48.208],
    ]],
}


@pytest.fixture
def collect(qgis_app):
    from infrared_city_gis.services.ground_materials import collect_ground_materials

    return collect_ground_materials


@pytest.fixture
def three_materials(qgis_app):
    """asphalt / vegetation / water, deliberately NOT in canonical order."""
    return {
        "vegetation": [_layer("ground-vegetation", _ring(16.373, 48.209, 16.374, 48.210))],
        "asphalt": [_layer("ground-asphalt", _ring(16.374, 48.209, 16.375, 48.210))],
        "water": [_layer("ground-water", _ring(16.373, 48.210, 16.374, 48.211))],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_key_order_is_canonical_not_input_order(collect, three_materials):
    """The wire order must be the platform canon, whatever order we were given.

    Input order here is vegetation, asphalt, water. Emitting that verbatim
    would put the full-bbox asphalt backdrop above vegetation and water, so
    every sensor would read asphalt.
    """
    out = collect(AREA, three_materials)

    assert list(out) == ["asphalt", "water", "vegetation"]
    assert list(out)[0] == "asphalt", "the gap-fill background must stack lowest"


def test_z_uses_fixed_slots_not_rank(collect, three_materials):
    """Vegetation stays at slot 5 even though only three materials are present."""
    out = collect(AREA, three_materials)

    def z_of(material):
        coords = out[material]["features"][0]["geometry"]["coordinates"]
        return coords[0][0][2]

    assert z_of("asphalt") == pytest.approx(1e-5)
    assert z_of("water") == pytest.approx(3e-5)
    assert z_of("vegetation") == pytest.approx(5e-5)


def test_every_feature_carries_its_material_stamp(collect, three_materials):
    """Without properties.material the Lambda falls back to a default surface."""
    out = collect(AREA, three_materials)

    for material, collection in out.items():
        assert collection["type"] == "FeatureCollection"
        for feature in collection["features"]:
            assert feature["properties"]["material"] == material


def test_coordinates_are_three_dimensional_wgs84(collect, three_materials):
    """The server projects lon/lat itself, so we must ship WGS84 with a Z."""
    out = collect(AREA, three_materials)

    for collection in out.values():
        for feature in collection["features"]:
            for position in feature["geometry"]["coordinates"][0]:
                assert len(position) == 3, "Z is dropped by QGIS 2D layers — re-stamp it"
                lon, lat, _z = position
                assert -180 <= lon <= 180 and -90 <= lat <= 90


def test_material_with_no_features_in_the_area_is_omitted(collect, qgis_app):
    """Omitted means "unspecified"; an empty FeatureCollection would mean "absent"."""
    far_away = _layer("ground-soil", _ring(16.9, 48.9, 16.91, 48.91))
    near = _layer("ground-asphalt", _ring(16.373, 48.209, 16.374, 48.210))

    out = collect(AREA, {"soil": [far_away], "asphalt": [near]})

    assert "soil" not in out
    assert "asphalt" in out


def test_non_polygon_geometry_is_skipped(collect, qgis_app):
    """A point layer named ground-* must not reach the payload as a surface."""
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer

    points = QgsVectorLayer("Point?crs=EPSG:4326", "ground-asphalt", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(16.374, 48.210)))
    points.dataProvider().addFeatures([feature])
    points.updateExtents()

    out = collect(AREA, {"asphalt": [points]})

    assert out == {}, "non-polygon features are not surfaces"
