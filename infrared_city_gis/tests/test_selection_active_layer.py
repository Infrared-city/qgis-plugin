"""A selection is read off the ACTIVE layer, which is not always a vector one.

QGIS makes a newly added layer current, so the moment a simulation renders its
result the active layer is a raster — and the user can click any layer in the
Layers panel besides. The selection helpers called ``selectedFeatures()`` on
whatever they got, which a ``QgsRasterLayer`` does not have: the
``AttributeError`` escaped the fetch dialog's constructor and QGIS showed its
own Python error box, in the one place the dialog meant to say "select a
building area first".

These tests drive the real helpers with a raster, with no active layer at all,
and with a vector layer that does have a selection, so the happy path stays
covered by the same file.
"""

import pytest

pytestmark = pytest.mark.qgis

RASTER_NAME = "area_wind-speed"


class _Iface:
    """Just enough of ``iface`` for the selection helpers."""

    def __init__(self, layer):
        self._layer = layer

    def activeLayer(self):
        return self._layer


class _FakeMessageBox:
    """Records ``(title, text)`` instead of blocking on a modal box."""

    warning_calls = []
    information_calls = []
    critical_calls = []

    @classmethod
    def reset(cls):
        cls.warning_calls.clear()
        cls.information_calls.clear()
        cls.critical_calls.clear()

    @classmethod
    def warning(cls, parent, title, text, *args):
        cls.warning_calls.append((title, text))

    @classmethod
    def information(cls, parent, title, text, *args):
        cls.information_calls.append((title, text))

    @classmethod
    def critical(cls, parent, title, text, *args):
        cls.critical_calls.append((title, text))


def _raster():
    """A raster layer object. It need not resolve — only its type matters."""
    from qgis.core import QgsRasterLayer

    return QgsRasterLayer(f"/nonexistent/{RASTER_NAME}.tif", RASTER_NAME)


def _buildings_with_selection():
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer

    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "buildings", "memory")
    ring = [QgsPointXY(16.370, 48.210), QgsPointXY(16.374, 48.210),
            QgsPointXY(16.374, 48.214), QgsPointXY(16.370, 48.214),
            QgsPointXY(16.370, 48.210)]
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromPolygonXY([ring]))
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    layer.selectAll()
    return layer


def _use(monkeypatch, layer):
    """Point every selection reader at *layer*."""
    from infrared_city_gis.services import polygon_from_selection as pfs
    from infrared_city_gis.services import tiles

    monkeypatch.setattr(pfs, "iface", _Iface(layer))
    monkeypatch.setattr(tiles, "iface", _Iface(layer))


# ---------------------------------------------------------------------------
# The helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("layer_factory", [_raster, lambda: None],
                         ids=["raster-active", "no-active-layer"])
def test_a_layer_without_a_selection_yields_nothing_instead_of_raising(
    monkeypatch, layer_factory
):
    from infrared_city_gis.services import tiles
    from infrared_city_gis.services.polygon_from_selection import (
        create_polygon_from_selection,
        create_wgs84_geojson_polygon_from_selection,
    )

    _use(monkeypatch, layer_factory())

    assert create_polygon_from_selection() == []
    assert create_wgs84_geojson_polygon_from_selection() is None
    assert tiles.get_selected_bbox() is None
    assert tiles.collect_tile_centers_from_selection() == []


def test_a_vector_layer_with_a_selection_still_works(monkeypatch):
    """The guard must not cost the happy path."""
    from infrared_city_gis.services.polygon_from_selection import (
        create_wgs84_geojson_polygon_from_selection,
    )

    _use(monkeypatch, _buildings_with_selection())

    polygon = create_wgs84_geojson_polygon_from_selection()
    assert polygon["type"] == "Polygon"
    ring = polygon["coordinates"][0]
    assert ring[0] == ring[-1]          # closed, as the SDK validator wants
    assert len(ring) >= 4


def test_the_note_names_the_layer_that_cannot_hold_a_selection(monkeypatch):
    from infrared_city_gis.services.polygon_from_selection import active_layer_note

    _use(monkeypatch, _raster())
    assert RASTER_NAME in active_layer_note()

    _use(monkeypatch, None)
    assert "No layer is active" in active_layer_note()

    # A vector layer is the user's own business: they simply selected nothing.
    _use(monkeypatch, _buildings_with_selection())
    assert active_layer_note() == ""


# ---------------------------------------------------------------------------
# The dialog the user actually sees
# ---------------------------------------------------------------------------

class _Label:
    """Minimal QLabel stand-in: ``_prepare`` only ever sets text on it."""

    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _StubDialog:
    """The attributes ``_prepare`` touches, and nothing else.

    The real dialog is not constructed: a ``QDialog`` needs a GUI
    ``QApplication``, and the headless QGIS this suite runs in aborts the
    process on one. ``_prepare`` itself is the real method, called with this
    object as ``self``, so the branch under test is production code.
    """

    def __init__(self):
        self.api_key = None
        self.polygon = None
        self.tile_count = None
        self.is_single_tile = False
        self._init_ok = False
        self.fetch_enabled = None
        self.info_label = _Label()

    def _set_fetch_enabled(self, enabled):
        self.fetch_enabled = enabled


def test_the_fetch_dialog_explains_the_active_layer_instead_of_crashing(
    monkeypatch,
):
    """What the tester hit: a QGIS Python error box in place of the message."""
    from infrared_city_gis import infrared_city_fetch_ground_materials_dialog as mod
    from infrared_city_gis.services import single_tile_selection

    single_tile_selection.clear()
    _FakeMessageBox.reset()
    monkeypatch.setattr(mod, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(mod, "QMessageBox", _FakeMessageBox)
    _use(monkeypatch, _raster())

    dialog = _StubDialog()
    mod.InfraredCityFetchGroundMaterialsDialog._prepare(dialog)

    assert dialog._init_ok is False, "the dialog must not offer a Download"
    title, text = _FakeMessageBox.warning_calls[-1]
    assert title == "No selection"
    assert "Please select a building area first" in text
    assert RASTER_NAME in text, "the message must name the active layer"
    assert "Layers panel" in text


def test_nothing_selected_on_a_real_building_layer_says_only_that(monkeypatch):
    """No note when the active layer IS a vector layer: it would be noise."""
    from qgis.core import QgsVectorLayer

    from infrared_city_gis import infrared_city_fetch_ground_materials_dialog as mod
    from infrared_city_gis.services import single_tile_selection

    single_tile_selection.clear()
    _FakeMessageBox.reset()
    monkeypatch.setattr(mod, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(mod, "QMessageBox", _FakeMessageBox)
    _use(monkeypatch, QgsVectorLayer("Polygon?crs=EPSG:4326", "buildings", "memory"))

    dialog = _StubDialog()
    mod.InfraredCityFetchGroundMaterialsDialog._prepare(dialog)

    assert dialog._init_ok is False
    title, text = _FakeMessageBox.warning_calls[-1]
    assert title == "No selection"
    assert "Please select a building area first" in text
    assert "not a vector layer" not in text
