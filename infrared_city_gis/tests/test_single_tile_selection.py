"""The armed single-tile box, and why it is the BOX that is stored.

Deriving the run polygon from the map selection instead of storing the picked
box was tried and reverted: ``selectByRect`` takes whole features, so a building
straddling an edge pulls the selection's convex hull past the box — measured at
617 x 586 m for a 512 m pick, which the area tiler charges NINE jobs for instead
of one. ``test_a_building_on_the_edge_pulls_the_hull_past_the_box`` reproduces
that with real QGIS geometry, and ``test_the_area_tiler_is_why_this_matters``
puts the price on it. Together they are why this module holds a box.

The rest is the lifecycle the toolbar toggle mirrors: arming and disarming both
notify, so nothing has to remember to keep the button in step.
"""

import math

import pytest

from infrared_city_gis.services import single_tile_selection as sts

CENTRE = (16.3732, 48.2146)
TILE_EDGE_M = 512.0


def _box(width_m, height_m, centre=CENTRE):
    dlon = (width_m / 2) / (111_320 * math.cos(math.radians(centre[1])))
    dlat = (height_m / 2) / 111_320
    return (centre[0] - dlon, centre[1] - dlat, centre[0] + dlon, centre[1] + dlat)


def _polygon(bbox):
    w, s, e, n = bbox
    return {"type": "Polygon",
            "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def _rect(w, s, e, n):
    from qgis.core import QgsRectangle

    return QgsRectangle(w, s, e, n)


def _extent_m(bbox):
    w, s, e, n = bbox
    per_lng = 111_320 * math.cos(math.radians((s + n) / 2))
    return (e - w) * per_lng, (n - s) * 111_320


@pytest.fixture(autouse=True)
def _disarmed():
    """The pick is module-global, so neighbouring tests must not inherit it."""
    sts.clear()
    yield
    sts.clear()
    sts._LISTENERS.clear()


def _arm(bbox=None):
    bbox = bbox or _box(TILE_EDGE_M, TILE_EDGE_M)
    sts.set_selection(
        polygon=_polygon(bbox), center_lon=CENTRE[0], center_lat=CENTRE[1],
        bbox=bbox, building_count=42,
    )
    return bbox


# -- the lifecycle the toggle mirrors -----------------------------------


def test_arming_stores_the_box_that_was_picked():
    bbox = _arm()

    armed = sts.peek()
    assert sts.is_armed()
    assert armed.polygon == _polygon(bbox)
    assert armed.building_count == 42


def test_clearing_disarms():
    _arm()
    sts.clear()

    assert not sts.is_armed()
    assert sts.peek() is None


def test_a_second_pick_replaces_the_first():
    _arm()
    second = _arm(_box(TILE_EDGE_M, TILE_EDGE_M, centre=(16.40, 48.20)))

    assert sts.peek().bbox == second


# -- notification, so nothing has to remember to sync -------------------


def test_arming_and_disarming_both_notify():
    """The toolbar toggle is a listener; it never polls."""
    seen = []
    sts.subscribe(lambda: seen.append(sts.is_armed()))

    _arm()
    sts.clear()

    assert seen == [True, False]


def test_clearing_nothing_notifies_nothing():
    """Otherwise every dialog close would churn the button."""
    seen = []
    sts.subscribe(lambda: seen.append(1))

    sts.clear()

    assert seen == []


def test_a_listener_that_raises_does_not_break_the_pick():
    """A dead QAction during teardown must not take an arming with it."""
    def _explode():
        raise RuntimeError("wrapped C/C++ object has been deleted")

    sts.subscribe(_explode)
    _arm()

    assert sts.is_armed()


def test_subscribing_twice_registers_once():
    seen = []

    def listener():
        seen.append(1)

    sts.subscribe(listener)
    sts.subscribe(listener)
    _arm()

    assert seen == [1]


# -- why the box is stored ----------------------------------------------


@pytest.mark.qgis
def test_a_building_on_the_edge_pulls_the_hull_past_the_box(qgis_app):
    """The regression that made this a stored box rather than a measurement.

    A 512 m pick selects whole features, so one straddling the edge puts the
    selection's convex hull well outside the tile. Measuring the selection
    instead of storing the box therefore reports an area run, at nine times
    the cost, for a pick the user made as one tile.
    """
    from qgis.core import QgsGeometry, QgsPointXY

    w, s, e, n = _box(TILE_EDGE_M, TILE_EDGE_M)
    # A long building sitting on the eastern edge, mostly outside.
    overhang_deg = 105 / (111_320 * math.cos(math.radians(CENTRE[1])))
    building = QgsGeometry.fromPolygonXY([[
        QgsPointXY(e - 0.00002, s + 0.001), QgsPointXY(e + overhang_deg, s + 0.001),
        QgsPointXY(e + overhang_deg, s + 0.002), QgsPointXY(e - 0.00002, s + 0.002),
        QgsPointXY(e - 0.00002, s + 0.001),
    ]])
    tile = QgsGeometry.fromRect(_rect(w, s, e, n))

    hull = QgsGeometry.unaryUnion([tile, building]).convexHull()
    box = hull.boundingBox()
    width, _height = _extent_m(
        (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())
    )

    assert width > TILE_EDGE_M + 100, (
        "the hull should overshoot the tile; if it no longer does, the "
        "stored box may be unnecessary"
    )


@pytest.mark.qgis
@pytest.mark.parametrize("width, height, tiles", [
    (512, 512, 4),
    (617, 586, 9),
    (1024, 1024, 16),
])
def test_the_area_tiler_is_why_this_matters(qgis_app, width, height, tiles):
    """The price of getting the polygon wrong, measured against the SDK.

    The tiler steps every 256 m, so it never answers 1 for a real selection —
    617 x 586 m, the overshoot measured above, costs nine jobs.
    """
    import logging

    from infrared_sdk.tiling.tiles import TileService

    grid = TileService(
        polygon=_polygon(_box(width, height)), logger=logging.getLogger(__name__),
    ).generate_tiles_for_polygon()

    assert sum(1 for row in grid for tile in row if not tile.empty) == tiles
