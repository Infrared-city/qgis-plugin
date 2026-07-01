"""Non-blocking poller for an Infrared SDK area analysis run.

The synchronous ``client.run_area_and_wait`` blocks the QGIS UI thread for
the entire duration of a simulation (submission, polling and merge).  This
module wraps the SDK's three composable steps — :meth:`run_area`,
:meth:`check_area_state`, :meth:`merge_area_jobs` — in a ``QObject`` that
drives polling from a ``QTimer`` so the UI stays responsive and the
dialog can close immediately after submission.

Usage::

    state = AreaRenderState.from_dialog(dlg)   # snapshot before closing
    poller = AreaPoller(
        client=InfraredClient(api_key=dlg.api_key),
        polygon=polygon,
        area=area,
        payload=payload,
        render_state=state,
        on_render=run_sdk_area_render,
        parent=iface.mainWindow(),  # outlives the dialog
    )
    poller.start()
    super().accept()  # close dialog right away

The poller emits ``finished`` (with the AreaResult) on success, ``failed``
(with a string message) on submission, polling, merge or render error.
``deleteLater`` is called on completion either way so the QObject is
properly cleaned up.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from infrared_sdk.analyses.jobs import JobStatus
from qgis.core import Qgis
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import QApplication
from qgis.utils import iface

from ..infrared_logger import logger
from ..models.analysis import AnalysisType

# Cap how many failed-job error messages we fetch per tick. ``check_area_state``
# only returns the status enum (no error string), so to log the cause of a
# failure we have to call ``client.jobs.get_status`` per-job — a synchronous
# HTTP round-trip on the UI thread. For a typical "all jobs failed" cascade
# the error string is identical across jobs, so sampling the first N is
# enough for diagnostics without freezing the UI.
_MAX_FAILURE_DETAIL_FETCHES = 5


# Polling cadence (ms) — fixed 2 s is plenty for typical 30-90 s wind/solar
# runs (15-45 polls), and matches the SDK's _POLL_BACKOFF_BASE of 2 s.
_DEFAULT_POLL_INTERVAL_MS = 2000

# Hard wall-clock cap. Mirrors the SDK's run_area_and_wait default
# (area_timeout=3600 s) so behaviour is consistent between sync and async
# paths.
_DEFAULT_AREA_TIMEOUT_S = 3600


def _status(msg: str, level=Qgis.Info, duration: int = 0) -> None:
    """Push a one-line message to the QGIS status bar."""
    bar = iface.messageBar() if iface else None
    if bar is None:
        return
    bar.clearWidgets()
    bar.pushMessage("InfraredCity", msg, level=level, duration=duration)
    QApplication.processEvents()


@dataclass(frozen=True)
class AreaRenderState:
    """Snapshot of dialog state needed to render an AreaResult.

    Captured *before* the dialog closes so the renderer is decoupled from
    the QWidget lifecycle. Holds primitive Python values only — no QWidget
    references — to be safe across the dialog's destruction.
    """

    analysis_type: AnalysisType
    sub_analysis_type: Any  # an Enum or None — kept generic to avoid a circular import
    legend_min_override: Optional[float]
    legend_max_override: Optional[float]

    @classmethod
    def from_dialog(cls, dlg) -> "AreaRenderState":
        """Build a snapshot from the run-multiple-simulation dialog.

        Reads ``analysis_type`` and ``sub_analysis_type`` directly. For UTCI
        / TCI also captures the dialog's manual legend overrides if the
        user enabled them.
        """
        leg_min: Optional[float] = None
        leg_max: Optional[float] = None
        if dlg.analysis_type == AnalysisType.THERMAL_COMFORT_INDEX:
            min_w = getattr(dlg, "legend_min_enable_tci", None)
            if min_w is not None and min_w.isChecked():
                leg_min = float(dlg.min_legend_value)
            max_w = getattr(dlg, "legend_max_enable_tci", None)
            if max_w is not None and max_w.isChecked():
                leg_max = float(dlg.max_legend_value)
        return cls(
            analysis_type=dlg.analysis_type,
            sub_analysis_type=getattr(dlg, "sub_analysis_type", None),
            legend_min_override=leg_min,
            legend_max_override=leg_max,
        )


class AreaPoller(QObject):
    """Background poller that drives an SDK area analysis to completion.

    Lifecycle:
      1. ``start()`` — submit jobs via ``client.run_area``, store schedule,
         kick off the QTimer.
      2. Each ``QTimer`` tick calls ``client.check_area_state(schedule)``
         on the main thread; this is mostly I/O-bound but parallel inside
         the SDK, so a per-tick cost of ~50-200 ms is fine for UI.
      3. When ``state.is_complete`` the timer stops, ``merge_area_jobs``
         downloads + merges, and ``on_render(state, polygon, area, result)``
         is invoked to push the layers onto the canvas.
      4. ``finished(result)`` signal fires; ``deleteLater()`` cleans up.

    On any error along the way, ``failed(msg)`` fires and the poller
    cleans itself up.
    """

    finished = pyqtSignal(object)  # AreaResult
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        client,
        polygon: dict,
        area,
        payload,
        render_state: AreaRenderState,
        on_render: Callable[[AreaRenderState, dict, Any, Any], None],
        vegetation: Optional[dict] = None,
        poll_interval_ms: int = _DEFAULT_POLL_INTERVAL_MS,
        area_timeout_s: int = _DEFAULT_AREA_TIMEOUT_S,
        parent: Optional[QObject] = None,
    ) -> None:
        # IMPORTANT: parent should outlive the dialog (e.g.
        # iface.mainWindow()), otherwise Qt GC's the poller when the
        # dialog is destroyed.
        super().__init__(parent)
        self._client = client
        self._polygon = polygon
        self._area = area
        self._payload = payload
        self._render_state = render_state
        self._on_render = on_render
        # Optional ``Mapping[str, dict]`` of GeoJSON-like Point features
        # ({"geometry": {"coordinates": [lon, lat]}, ...}) — the SDK does
        # the per-tile distribution. None means no vegetation.
        self._vegetation = vegetation
        self._area_timeout_s = area_timeout_s

        self._schedule = None
        self._deadline: Optional[float] = None
        # Job IDs whose error messages we've already fetched + logged. Stops
        # us from re-querying the same failure on every subsequent tick.
        self._reported_failures: set = set()

        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._on_tick)

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """Submit jobs and start the polling timer.

        Submission itself is synchronous and brief (parallel HTTP POSTs
        inside the SDK) — there's no benefit to backgrounding it, and
        callers want to know up front if the submission failed.
        """
        _status("InfraredCity: submitting area jobs…")
        try:
            self._schedule = self._client.run_area(
                self._payload,
                self._polygon,
                buildings=self._area.buildings,
                vegetation=self._vegetation,
            )
        except Exception as e:
            self._fail(f"submit failed: {e}", exc=e)
            return

        n_jobs = len(self._schedule.jobs) if self._schedule is not None else 0
        if n_jobs == 0:
            # Submission scheduled 0 jobs — e.g. the selected area contained
            # no buildings / no valid tiles. There is nothing to poll for, so
            # fail fast with a clear message instead of spinning the timer
            # until area_timeout_s elapses.
            self._fail(
                "submission scheduled 0 jobs — nothing to run "
                "(check that the selected area contains buildings)",
                exc=None,
            )
            return
        logger.info(
            "AreaPoller: submitted, %d jobs scheduled, polling every %d ms "
            "(timeout=%ds)",
            n_jobs, self._timer.interval(), self._area_timeout_s,
        )
        self._deadline = time.monotonic() + self._area_timeout_s
        _status(f"InfraredCity: {n_jobs} jobs submitted, running in background…")
        self._timer.start()

    def cancel(self) -> None:
        """Stop polling and clean up; submitted jobs continue server-side."""
        logger.info("AreaPoller: cancel requested")
        self._timer.stop()
        _status("InfraredCity: area run cancelled (jobs remain on server)",
                level=Qgis.Warning, duration=10)
        self.deleteLater()

    # -- internal ------------------------------------------------------

    def _on_tick(self) -> None:
        # Defensive: if the timer fires after we've finalised, do nothing.
        if self._schedule is None:
            return
        try:
            state = self._client.check_area_state(self._schedule)
        except Exception as e:
            # Transient errors are common (rate limits, brief network
            # blips). Don't fail the whole run on one bad tick — the SDK's
            # check_area_state already retries 429s internally.
            logger.warning("AreaPoller: check_area_state failed (continuing): %s", e)
            return

        _status(
            f"InfraredCity: {state.succeeded}/{state.total} tiles done "
            f"({state.running} running, {state.pending} pending, "
            f"{state.failed} failed)",
            level=Qgis.Info,
        )
        logger.info(
            "Area progress: status=%s succeeded=%d running=%d pending=%d "
            "failed=%d total=%d",
            state.status, state.succeeded, state.running, state.pending,
            state.failed, state.total,
        )

        # Surface server-side error messages for newly-failed jobs. The
        # bare status enum from check_area_state doesn't carry the error
        # string — that lives on the full Job object — so we fetch a bounded
        # sample directly via jobs.get_status.
        if state.failed > 0:
            self._log_new_failures(state)

        if state.is_complete:
            self._timer.stop()
            self._finalize()
            return

        if self._deadline is not None and time.monotonic() > self._deadline:
            self._timer.stop()
            self._fail(
                f"timed out after {self._area_timeout_s}s (last status={state.status})",
                exc=None,
            )
            return

    def _log_new_failures(self, state) -> None:
        """Fetch + log error messages for jobs that just transitioned to failed.

        ``check_area_state`` only returns ``Dict[str, JobStatus]``; to read
        the actual error message we fetch each failed job's full status via
        ``client.jobs.get_status``. Capped at
        :data:`_MAX_FAILURE_DETAIL_FETCHES` per tick so a wholesale failure
        of N jobs doesn't hang the UI on N sequential HTTP calls.
        """
        # Build the set of newly-failed job IDs (exclude ones we've reported).
        newly_failed = [
            job_id
            for job_id, st in state.job_states.items()
            if st == JobStatus.failed and job_id not in self._reported_failures
        ]
        if not newly_failed:
            return

        # Mark all of them as reported up-front, even the ones we don't
        # have budget to fetch — otherwise on every subsequent tick we'd
        # re-attempt to fetch them and never converge.
        self._reported_failures.update(newly_failed)

        sample = newly_failed[:_MAX_FAILURE_DETAIL_FETCHES]
        suppressed = len(newly_failed) - len(sample)
        for job_id in sample:
            try:
                job = self._client.jobs.get_status(job_id)
                err = job.error or "(no error message returned)"
                logger.error(
                    "AreaPoller: job %s failed — %s", job_id, err,
                )
            except Exception as e:
                logger.warning(
                    "AreaPoller: couldn't fetch error detail for failed job %s: %s",
                    job_id, e,
                )
        if suppressed > 0:
            logger.error(
                "AreaPoller: …%d additional failed jobs not detailed "
                "(capped at %d per run for UI responsiveness)",
                suppressed, _MAX_FAILURE_DETAIL_FETCHES,
            )

    def _finalize(self) -> None:
        try:
            result = self._client.merge_area_jobs(self._schedule)
        except Exception as e:
            self._fail(f"merge_area_jobs failed: {e}", exc=e)
            return

        logger.info(
            "AreaPoller: merge complete — analysis_type=%s succeeded=%d "
            "failed=%d skipped=%d total=%d",
            result.analysis_type, result.succeeded_jobs,
            len(result.failed_jobs), len(result.skipped_jobs), result.total_jobs,
        )

        try:
            self._on_render(self._render_state, self._polygon, self._area, result)
        except Exception as e:
            # Render failures shouldn't be silent, but the result is still
            # valid — surface the error and emit finished with the result
            # so callers can do something with it.
            logger.error("AreaPoller: render failed: %s", e, exc_info=True)
            _status(
                f"InfraredCity: render failed — {str(e)[:120]}",
                level=Qgis.Critical, duration=15,
            )

        self.finished.emit(result)
        self.deleteLater()

    def _fail(self, msg: str, *, exc: Optional[Exception]) -> None:
        if exc is not None:
            logger.error("AreaPoller: %s", msg, exc_info=True)
        else:
            logger.error("AreaPoller: %s", msg)
        _status(
            f"InfraredCity: area run failed — {msg[:120]}",
            level=Qgis.Critical, duration=15,
        )
        self._timer.stop()
        self.failed.emit(msg)
        self.deleteLater()
