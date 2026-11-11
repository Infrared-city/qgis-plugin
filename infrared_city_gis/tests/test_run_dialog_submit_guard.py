"""One click, one paid submit.

Submitting a run is not atomic: ``_revalidate_trees``,
``collect_qgis_area_buildings`` and the submit helpers all reach ``_status()``,
which calls ``QApplication.processEvents()``. That delivers any click queued in
the meantime — and the dialog's own button stays enabled, because modality
stops other windows, not this one. So an impatient double-click re-entered
``accept()`` and submitted the same jobs twice. Tokens are charged per job, so
the user paid twice (INF-665).

The real dialog cannot be constructed here — it needs ``iface``, which is
``None`` outside QGIS (see ``test_fake_dialog_matches_real``) — so these drive
the unbound ``accept`` against a stand-in that re-enters exactly the way a
queued click does.
"""

import pytest

from infrared_city_gis.infrared_city_run_multiple_simulation_dialog import (
    InfraredCityRunMultipleSimulationDialog as _Dialog,
)

pytestmark = pytest.mark.qgis


class _ReentrantStub:
    """Re-enters ``accept`` from inside the submit, as ``processEvents`` does."""

    def __init__(self, depth=1):
        self._submitting = False
        self.submits = 0
        self._depth = depth

    def _accept(self):
        self.submits += 1
        for _ in range(self._depth):
            _Dialog.accept(self)


def test_a_click_queued_during_the_submit_does_not_submit_again():
    stub = _ReentrantStub()

    _Dialog.accept(stub)

    assert stub.submits == 1, (
        f"the submit ran {stub.submits} times — a queued click re-entered "
        "accept() and charged the user twice"
    )


def test_the_guard_survives_several_queued_clicks():
    """An impatient user clicks more than twice."""
    stub = _ReentrantStub(depth=5)

    _Dialog.accept(stub)

    assert stub.submits == 1


def test_the_guard_is_released_so_a_retry_still_works():
    """A refused submit keeps the dialog open on purpose; it must stay usable."""
    stub = _ReentrantStub(depth=0)

    _Dialog.accept(stub)
    assert stub._submitting is False, "the flag outlived the submit"

    _Dialog.accept(stub)
    assert stub.submits == 2, "a deliberate second run was refused"


def test_the_guard_is_released_when_the_submit_raises():
    """`accept` catches its own errors, but a crash must not lock the dialog."""

    class _Boom(_ReentrantStub):
        def _accept(self):
            raise RuntimeError("submission blew up")

    stub = _Boom()
    with pytest.raises(RuntimeError):
        _Dialog.accept(stub)

    assert stub._submitting is False, (
        "a failed submit left the flag set — every later click is now ignored "
        "and the dialog is dead until it is reopened"
    )
