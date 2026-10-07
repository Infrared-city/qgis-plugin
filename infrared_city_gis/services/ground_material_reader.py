"""Read ground materials off the UI thread.

``ground_materials.get_area`` is one blocking call that pulls Overture parquet
and composes it in the bundled kernel. It moves far more data than it returns —
around 149 MB for a 0.4 km2 site — so on a constrained link it runs for minutes,
and it used to run on the QGIS main thread with the window frozen behind it.

There is nothing to poll: unlike an area simulation, which the SDK splits into
submit / check / merge, this is a single call with no steps. So it goes to a
worker thread and reports back through signals.

Two things it deliberately does NOT do:

* **It cannot be cancelled.** The SDK offers no way to stop a read in flight —
  its own timeout stops WAITING while the reader thread keeps downloading. So
  ``detach()`` gives up on the result rather than pretending to abort, and the
  thread is kept alive in :data:`_ACTIVE_READERS` until it finishes on its own.
  Dropping the last reference to a running QThread crashes QGIS.
* **It does not measure progress.** ``on_progress`` fires once per read CHUNK,
  and a site under 4 km2 is ONE chunk — so a progress bar built on it would sit
  at 0% for the whole run and then jump to 100%. The chunk counter is still
  forwarded for large sites, but a caller that wants something moving should
  show elapsed time, which is the honest signal here.
"""

from __future__ import annotations

from typing import List, Optional

from qgis.PyQt.QtCore import QObject, QThread, pyqtSignal

from ..constants import GROUND_FETCH_TIMEOUT_S, GROUND_FETCH_TOTAL_TIMEOUT_S
from ..infrared_logger import logger
from ..utils.client_identity import make_client
from .user_errors import UserError, describe_error

#: Readers still running. A QThread that is garbage collected while running
#: takes QGIS down with it, so a reader whose caller has gone away stays here
#: until it finishes. Mirrors ``sdk_runner._ACTIVE_POLLERS``.
_ACTIVE_READERS: List["GroundMaterialReader"] = []


#: Class names that only ever mean "ran out of time". NOT
#: ``AreaOvertureReadError``: the SDK raises that for EVERY failed Overture
#: read — a timeout ("did not finish within …") and any other failure ("the
#: area Overture read failed: …") alike. Matching it by name reported a pyarrow
#: build without S3 support as a slow connection.
_TIMEOUT_CLASSES = frozenset({
    "SiteReadTimeout", "TimeoutError", "Timeout", "ReadTimeout", "ConnectTimeout",
})
#: The SDK's own wording for its two read deadlines (overture_area.py,
#: ground_materials/_site_chunks.py).
_TIMEOUT_TEXT = ("did not finish within", "passed its total_timeout")


def timed_out(exc: BaseException) -> bool:
    """Did this failure come from the read running out of time?

    Worth telling apart, because the advice differs completely: the Overture
    read sends NO API key, so the generic "check your key and subscription"
    is not merely unhelpful there, it points at the wrong thing. A timeout is
    about the connection.

    Matched by class NAME and the SDK's message text rather than an import —
    the classes live in ``infrared_sdk._internal``, and pinning a private path
    here would make the message quality depend on an SDK layout that is free to
    move. Walks the whole chain because the SDK wraps the cause in a
    ``TiledRunError``.
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if type(exc).__name__ in _TIMEOUT_CLASSES:
            return True
        if any(text in str(exc) for text in _TIMEOUT_TEXT):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def describe_read_failure(exc: BaseException) -> UserError:
    """What a failed ground-material read means to the user."""
    if timed_out(exc):
        return UserError(
            "Fetch Timed Out",
            "Reading ground materials took longer than allowed.",
            "This read downloads a large amount of map data. Please check "
            "your internet connection and try again — on a slow or congested "
            "connection it can take longer than the read allows. A smaller "
            "area downloads less, and your own ground-* layers need no "
            "download at all.",
            detail=str(exc),
        )
    return describe_error(exc)


def read_in_progress() -> bool:
    """Is an earlier read still running, possibly behind a closed dialog?

    A read cannot be stopped (see the module docstring), so a second one
    started now would run NEXT to it — two downloads on the same constrained
    line, each slower than one, and a user retrying a slow fetch three times
    had three running (#47). The dialog refuses to start while this is true.
    """
    return bool(_ACTIVE_READERS)


class _Worker(QObject):
    """The blocking call, on the worker thread. Touches no widgets."""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str, object)
    chunk_done = pyqtSignal(int, int)

    def __init__(self, api_key: str, polygon: dict, analysis_type=None):
        super().__init__()
        self._api_key = api_key
        self._polygon = polygon
        self._analysis_type = analysis_type

    def run(self) -> None:
        def on_progress(progress):
            # Signals are queued across threads, so this hands the numbers to
            # the main thread rather than touching anything itself.
            try:
                self.chunk_done.emit(
                    int(progress.completed_count), int(progress.total_count),
                )
            except Exception as e:  # a progress tick never fails a read
                logger.debug("ground-material progress tick skipped: %s", e)

        try:
            with make_client(self._api_key) as client:
                area = client.ground_materials.get_area(
                    self._polygon,
                    on_progress=on_progress,
                    # None is the SDK default: the widest read margin.
                    analysis_type=self._analysis_type,
                    # Explicit budgets: the SDK's 60 s per-read default is
                    # shorter than a single tile can take (#47). This runs off
                    # the UI thread, so a long budget costs only waiting.
                    timeout=GROUND_FETCH_TIMEOUT_S,
                    total_timeout=GROUND_FETCH_TOTAL_TIMEOUT_S,
                )
        except Exception as e:
            logger.error("Ground materials read failed: %s", e, exc_info=True)
            self.failed.emit(str(e), describe_read_failure(e))
            return
        self.finished.emit(area)


class GroundMaterialReader(QObject):
    """Owns the worker thread and re-emits its outcome on the main thread.

    ``finished`` carries the SDK's ``AreaGroundMaterials``; ``failed`` carries
    the raw message (already logged with its traceback) and the
    :class:`~.user_errors.UserError` to show for it.
    """

    finished = pyqtSignal(object)
    #: ``(message, user_error)`` — see :func:`describe_read_failure`.
    failed = pyqtSignal(str, object)
    chunk_done = pyqtSignal(int, int)

    def __init__(self, api_key: str, polygon: dict, analysis_type=None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._thread = QThread()
        self._worker = _Worker(api_key, polygon, analysis_type)
        self._worker.moveToThread(self._thread)
        self._detached = False

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.chunk_done.connect(self.chunk_done)

    def start(self) -> None:
        _ACTIVE_READERS.append(self)
        self._thread.start()

    def detach(self) -> None:
        """Stop caring about the result; let the thread run itself out.

        For a caller that is going away (a dialog the user closed). The read
        cannot be interrupted, so this is honest about what it does: the
        download continues, and its result is dropped.
        """
        self._detached = True
        logger.info("Ground-material read detached; it will finish in the background")

    def _retire(self) -> None:
        self._thread.quit()
        self._thread.wait()
        if self in _ACTIVE_READERS:
            _ACTIVE_READERS.remove(self)
        self.deleteLater()

    def _on_finished(self, area) -> None:
        if not self._detached:
            self.finished.emit(area)
        self._retire()

    def _on_failed(self, message: str, user_error: UserError) -> None:
        if not self._detached:
            self.failed.emit(message, user_error)
        self._retire()
