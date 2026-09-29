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

from ..infrared_logger import logger
from ..utils.client_identity import make_client

#: Readers still running. A QThread that is garbage collected while running
#: takes QGIS down with it, so a reader whose caller has gone away stays here
#: until it finishes. Mirrors ``sdk_runner._ACTIVE_POLLERS``.
_ACTIVE_READERS: List["GroundMaterialReader"] = []


def timed_out(exc: BaseException) -> bool:
    """Did this failure come from the read running out of time?

    Worth telling apart, because the advice differs completely: the Overture
    read sends NO API key, so the generic "check your key and subscription"
    is not merely unhelpful there, it points at the wrong thing. A timeout is
    about the connection.

    Matched by class NAME rather than an import — ``AreaOvertureReadError``
    lives in ``infrared_sdk._internal``, and pinning a private path here would
    make the message quality depend on an SDK layout that is free to move. The
    message text is a second chance at the same answer. Walks the whole chain
    because the SDK wraps it in a ``TiledRunError``.
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if type(exc).__name__ == "AreaOvertureReadError":
            return True
        if "did not finish within" in str(exc):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


class _Worker(QObject):
    """The blocking call, on the worker thread. Touches no widgets."""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str, bool)
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
            kwargs = {"on_progress": on_progress}
            if self._analysis_type is not None:
                kwargs["analysis_type"] = self._analysis_type
            with make_client(self._api_key) as client:
                area = client.ground_materials.get_area(self._polygon, **kwargs)
        except Exception as e:
            logger.error("Ground materials read failed: %s", e, exc_info=True)
            self.failed.emit(str(e), timed_out(e))
            return
        self.finished.emit(area)


class GroundMaterialReader(QObject):
    """Owns the worker thread and re-emits its outcome on the main thread.

    ``finished`` carries the SDK's ``AreaGroundMaterials``; ``failed`` carries a
    message already logged with its traceback.
    """

    finished = pyqtSignal(object)
    #: ``(message, timed_out)`` — see :func:`timed_out`.
    failed = pyqtSignal(str, bool)
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

    def _on_failed(self, message: str, was_timeout: bool) -> None:
        if not self._detached:
            self.failed.emit(message, was_timeout)
        self._retire()
