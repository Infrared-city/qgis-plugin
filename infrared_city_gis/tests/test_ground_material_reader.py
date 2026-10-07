"""The ground-material read happens off the UI thread and reports back on it.

The read is one blocking SDK call that moves ~149 MB to return ~1 MB, so on a
constrained link it runs for minutes. It used to run on the QGIS main thread,
which meant the whole window was frozen for the duration with nothing moving.

What is worth testing is the threading contract, because getting it wrong fails
in ways a reading of the code does not show: the work must actually leave the
main thread, the result must arrive back ON it, and a reader whose caller has
gone away must not be collected while its QThread still runs — that last one
takes QGIS down with it.
"""

import threading

import pytest
from qgis.PyQt.QtCore import QEventLoop, QTimer

from infrared_city_gis.services import ground_material_reader as gmr

pytestmark = pytest.mark.qgis


class _FakeArea:
    def __init__(self, layers):
        self.layers = layers
        self.total_features = sum(len(v) for v in layers.values())


class _FakeClient:
    """Stands in for InfraredClient, recording the thread it ran on."""

    def __init__(self, *, layers=None, error=None, chunks=0):
        self._layers = layers if layers is not None else {"asphalt": [1, 2]}
        self._error = error
        self._chunks = chunks
        self.ran_on = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def ground_materials(self):
        return self

    def get_area(self, polygon, on_progress=None, **kwargs):
        self.ran_on = threading.current_thread().name
        self.kwargs = kwargs
        for i in range(self._chunks):
            on_progress(type("P", (), {
                "completed_count": i + 1, "total_count": self._chunks,
            })())
        if self._error is not None:
            raise self._error
        return _FakeArea(self._layers)


@pytest.fixture
def fake_client(monkeypatch):
    """Swap the SDK client the worker builds, keeping the handle to inspect."""
    holder = {}

    def _factory(client):
        holder["client"] = client
        monkeypatch.setattr(gmr, "make_client", lambda _key: client)
        return client

    yield _factory, holder
    gmr._ACTIVE_READERS.clear()


def _run(reader, timeout_ms=10_000):
    """Drive a Qt event loop until the reader reports, or time out."""
    loop = QEventLoop()
    outcome = {}
    reader.finished.connect(lambda area: (outcome.update(area=area), loop.quit()))
    reader.failed.connect(
        lambda msg, user_error: (
            outcome.update(error=msg, user_error=user_error), loop.quit(),
        )
    )
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(timeout_ms)
    reader.start()
    loop.exec()
    return outcome


def test_the_read_leaves_the_main_thread(qgis_app, fake_client):
    """The whole point: the UI thread must be free while this runs."""
    factory, _holder = fake_client
    client = factory(_FakeClient())

    reader = gmr.GroundMaterialReader("key", {"type": "Polygon"})
    outcome = _run(reader)

    assert "area" in outcome, "the reader never reported back"
    assert client.ran_on is not None
    assert client.ran_on != threading.main_thread().name


def test_the_result_comes_back_intact(qgis_app, fake_client):
    factory, _holder = fake_client
    factory(_FakeClient(layers={"asphalt": [1, 2, 3], "water": [4]}))

    outcome = _run(gmr.GroundMaterialReader("key", {"type": "Polygon"}))

    assert set(outcome["area"].layers) == {"asphalt", "water"}
    assert outcome["area"].total_features == 4


def test_a_failure_arrives_as_a_message_not_an_exception(qgis_app, fake_client):
    """It is raised on the worker thread, where nothing could catch it."""
    factory, _holder = fake_client
    factory(_FakeClient(error=RuntimeError("something went wrong")))

    outcome = _run(gmr.GroundMaterialReader("key", {"type": "Polygon"}))

    assert "error" in outcome
    assert "something went wrong" in outcome["error"]
    assert outcome["user_error"].title != "Fetch Timed Out"
    assert "something went wrong" in outcome["user_error"].detail


def test_a_timeout_is_reported_as_one(qgis_app, fake_client):
    """The dialog tells the user to check their connection only for these."""
    factory, _holder = fake_client
    factory(_FakeClient(error=RuntimeError(
        "4 of 4 site read chunks failed: the area Overture read did not "
        "finish within 60.0s"
    )))

    outcome = _run(gmr.GroundMaterialReader("key", {"type": "Polygon"}))

    assert outcome["user_error"].title == "Fetch Timed Out"


def test_the_read_gets_an_explicit_time_budget(qgis_app, fake_client):
    """Without one the SDK's 60 s per-read default applies (#47)."""
    from infrared_city_gis.constants import (
        GROUND_FETCH_TIMEOUT_S,
        GROUND_FETCH_TOTAL_TIMEOUT_S,
    )
    factory, _holder = fake_client
    client = factory(_FakeClient())

    _run(gmr.GroundMaterialReader("key", {"type": "Polygon"}))

    assert client.kwargs["timeout"] == GROUND_FETCH_TIMEOUT_S > 60
    assert client.kwargs["total_timeout"] == GROUND_FETCH_TOTAL_TIMEOUT_S


def test_a_running_read_is_visible_until_it_retires(qgis_app, fake_client):
    """The dialog refuses a second fetch while one still runs (#47)."""
    factory, _holder = fake_client
    factory(_FakeClient())
    reader = gmr.GroundMaterialReader("key", {"type": "Polygon"})

    assert gmr.read_in_progress() is False
    reader.detach()  # a closed dialog: the read keeps going on its own
    reader.start()
    assert gmr.read_in_progress() is True

    loop = QEventLoop()
    QTimer.singleShot(2000, loop.quit)
    loop.exec()
    assert gmr.read_in_progress() is False


def test_chunk_progress_is_forwarded(qgis_app, fake_client):
    """Coarse by nature — one chunk for a small site — but not dropped."""
    factory, _holder = fake_client
    factory(_FakeClient(chunks=3))

    reader = gmr.GroundMaterialReader("key", {"type": "Polygon"})
    seen = []
    reader.chunk_done.connect(lambda done, total: seen.append((done, total)))
    _run(reader)

    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_a_finished_reader_stops_being_tracked(qgis_app, fake_client):
    """Otherwise every fetch in a session leaks a QThread."""
    factory, _holder = fake_client
    factory(_FakeClient())

    _run(gmr.GroundMaterialReader("key", {"type": "Polygon"}))

    assert gmr._ACTIVE_READERS == []


def test_a_detached_reader_stays_alive_but_stays_quiet(qgis_app, fake_client):
    """A closed dialog must not hear back, and must not drop a live QThread."""
    factory, _holder = fake_client
    factory(_FakeClient())

    reader = gmr.GroundMaterialReader("key", {"type": "Polygon"})
    heard = []
    reader.finished.connect(heard.append)
    reader.detach()
    reader.start()

    loop = QEventLoop()
    QTimer.singleShot(2000, loop.quit)
    loop.exec()

    assert heard == [], "a detached reader still reported to its caller"
    assert gmr._ACTIVE_READERS == [], "the thread was never retired"


# -- classifying the failure -------------------------------------------


class AreaOvertureReadError(Exception):
    """Deliberately named EXACTLY like the SDK's private class.

    That name is the match, so the double has to carry it — a test class called
    anything else would pass only through the message fallback and leave the
    class-name branch unproven.
    """


class SiteReadTimeout(Exception):
    """Named exactly like the SDK's private site-deadline class."""


def test_a_timeout_is_recognised_by_the_sdk_class_name():
    """The SDK's own type, which lives in a private module we do not import."""
    assert gmr.timed_out(SiteReadTimeout("deadline"))


def test_the_overture_read_class_alone_is_not_a_timeout():
    """The SDK raises it for EVERY failed read, not only a slow one.

    Seen on Windows QGIS 3.44.12, whose bundled pyarrow has no S3 support: the
    read failed in under ten seconds and the dialog said the connection was
    too slow. The real cause is the ImportError underneath.
    """
    try:
        try:
            try:
                raise ImportError(
                    "The pyarrow installation is not built with support for "
                    "'S3FileSystem'"
                )
            except ImportError as missing:
                raise AreaOvertureReadError(
                    f"the area Overture read failed: {missing}"
                ) from missing
        except AreaOvertureReadError as cause:
            raise RuntimeError("1 of 1 site read chunks failed") from cause
    except RuntimeError as wrapped:
        assert not gmr.timed_out(wrapped)
        assert gmr.describe_read_failure(wrapped).title == "Plugin Component Missing"


def test_a_timeout_is_recognised_through_the_wrapper():
    """The SDK raises TiledRunError `from` the real cause."""
    try:
        try:
            raise AreaOvertureReadError(
                "the area Overture read did not finish within 60.0s"
            )
        except AreaOvertureReadError as cause:
            raise RuntimeError("1 of 1 site read chunks failed") from cause
    except RuntimeError as wrapped:
        assert gmr.timed_out(wrapped)


def test_the_message_is_a_second_chance_at_the_same_answer():
    """If the SDK renames its class, the text still identifies a timeout."""
    assert gmr.timed_out(RuntimeError("the area Overture read did not finish within 60.0s"))


def test_an_ordinary_failure_is_not_a_timeout():
    assert not gmr.timed_out(RuntimeError("connection refused"))
    assert not gmr.timed_out(ValueError("unknown ground material 'building'"))


def test_a_self_referencing_chain_terminates():
    """A cause cycle must not hang the worker thread on its way to reporting."""
    first = RuntimeError("a")
    second = RuntimeError("b")
    first.__cause__ = second
    second.__cause__ = first

    assert gmr.timed_out(first) is False
