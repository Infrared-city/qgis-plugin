"""Single-tile mode is a toolbar toggle, and the button never lies about it.

The pending tile used to be invisible module state whose only exits were
running a simulation or picking an empty tile. A tile picked and forgotten
silently narrowed the next ground-material fetch to one 512 m tile while the
user was looking at a far larger building selection — the bug that started
this. The toggle is the fix, so what these tests protect is the one property
that makes it work: the button shows exactly what
``services.single_tile_selection`` holds, at every exit.

The plugin object is built with ``__new__`` and given only the two attributes
these methods touch. Its real ``__init__`` builds translators, fetches
registries and reads QSettings, none of which this behaviour depends on, and a
headless QGIS has no ``iface`` to hand it anyway.
"""

import pytest

from infrared_city_gis.infrared_city_gis import InfraredCityGIS
from infrared_city_gis.services import single_tile_selection

TILE = {
    "type": "Polygon",
    "coordinates": [[[16.37, 48.21], [16.38, 48.21],
                     [16.38, 48.22], [16.37, 48.22], [16.37, 48.21]]],
}


class _FakeAction:
    """Just the slice of QAction the toggle uses."""

    def __init__(self):
        self.checked = False

    def setChecked(self, value):
        self.checked = bool(value)


class _FakeMessageBar:
    def __init__(self):
        self.messages = []

    def pushMessage(self, title, text, level=None, duration=None):
        self.messages.append(text)


class _FakeIface:
    def __init__(self):
        self._bar = _FakeMessageBar()

    def messageBar(self):
        return self._bar


@pytest.fixture
def plugin():
    """A plugin object carrying only what the toggle touches."""
    obj = InfraredCityGIS.__new__(InfraredCityGIS)
    obj.select_tile_action = _FakeAction()
    obj.iface = _FakeIface()
    yield obj
    single_tile_selection.clear()


@pytest.fixture(autouse=True)
def _no_pending_selection():
    """The selection is module-global, so neighbouring tests must not see it."""
    single_tile_selection.clear()
    yield
    single_tile_selection.clear()


def _arm():
    single_tile_selection.set_selection(
        polygon=TILE, center_lon=16.375, center_lat=48.215,
        bbox=(16.37, 48.21, 16.38, 48.22), building_count=42,
    )


def test_the_button_follows_the_selection(plugin):
    """Checked state is derived, never tracked — the two cannot drift."""
    plugin._sync_single_tile_action()
    assert plugin.select_tile_action.checked is False

    _arm()
    plugin._sync_single_tile_action()
    assert plugin.select_tile_action.checked is True

    single_tile_selection.clear()
    plugin._sync_single_tile_action()
    assert plugin.select_tile_action.checked is False


def test_pressing_an_armed_button_releases_the_mode(plugin):
    """The way out that did not exist before: click it again."""
    _arm()
    plugin._sync_single_tile_action()

    plugin.select_bbox()

    assert single_tile_selection.peek() is None
    assert plugin.select_tile_action.checked is False
    assert plugin.iface.messageBar().messages, "the user was not told the mode ended"


def test_releasing_does_not_open_the_pick_dialog(plugin, monkeypatch):
    """Releasing is not a re-pick. Opening a dialog here would be a trap.

    Guarded by making the dialog constructor fail: if the release path ever
    reaches it, this test says so instead of a QGIS runtime hanging on a modal.
    """
    def _explode(*args, **kwargs):
        raise AssertionError("release must not open the tile-pick dialog")

    monkeypatch.setattr(
        "infrared_city_gis.infrared_city_gis.InfraredCitySelectBBoxDialog",
        _explode,
    )
    _arm()

    plugin.select_bbox()

    assert single_tile_selection.peek() is None


def test_saving_an_api_key_disarms(plugin):
    """A tile picked against one account must not carry into another."""
    _arm()
    plugin._sync_single_tile_action()

    plugin._disarm_single_tile()

    assert single_tile_selection.peek() is None
    assert plugin.select_tile_action.checked is False


def test_sync_survives_a_missing_action(plugin):
    """unload() tears the QAction down; syncing after that must not raise."""
    del plugin.select_tile_action
    plugin._sync_single_tile_action()  # must not raise
