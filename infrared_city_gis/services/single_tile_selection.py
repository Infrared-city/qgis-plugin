"""The armed single-tile pick: the 512 m box a run will be submitted as.

The "Select tile" toolbar action is a TOGGLE, and this module is what it
reflects: pressed means a box is armed here. Both the simulation and the
ground-material dialogs read it, and a run submitted from an armed box goes
through ``analyses.execute`` as ONE job instead of the area tiler, which steps
every 256 m and would turn a 512 m box into four.

**The BOX is stored, not the buildings.** The pick also selects the buildings
inside it, but their convex hull is not the box: ``selectByRect`` takes whole
features, so a building straddling an edge pulls the hull past it — measured at
617 x 586 m for a 512 m pick, which the tiler charges nine jobs for. Deriving
the run polygon from the selection instead of storing the box is exactly that
bug, so the box is what lives here.

**The mode ends when a simulation is submitted**, releasing the toggle and the
map selection together. That pairing is the point: clearing one and not the
other is what made an armed tile invisible, and then a forgotten pick silently
shrank a later ground-material fetch. A fetch does NOT end it — a fetch is
preparation for a run on the same tile, and ending it there would make the user
re-pick to use what they just fetched.

Anything that changes the armed state notifies :func:`subscribe` listeners, so
the toolbar toggle stays in step without every caller having to know it exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple


@dataclass(frozen=True)
class SingleTileSelection:
    """An immutable snapshot of a picked 512x512 m tile (WGS84)."""

    polygon: dict          # GeoJSON Polygon for the 512x512 m tile (WGS84)
    center_lon: float      # clicked centre longitude (WGS84)
    center_lat: float      # clicked centre latitude (WGS84)
    bbox: Tuple[float, float, float, float]  # (west, south, east, north) WGS84
    crs: str               # always "EPSG:4326"
    building_count: int    # how many features were highlighted at pick time


_PENDING: Optional[SingleTileSelection] = None
_LISTENERS: List[Callable[[], None]] = []


def subscribe(listener: Callable[[], None]) -> None:
    """Call *listener* whenever the armed selection appears or disappears.

    Lets the toolbar toggle mirror this state without the dialogs that clear it
    needing a reference to the action — the two stayed out of step exactly
    because keeping them in step was every caller's job.
    """
    if listener not in _LISTENERS:
        _LISTENERS.append(listener)


def _notify() -> None:
    for listener in list(_LISTENERS):
        try:
            listener()
        except Exception:  # noqa: BLE001 - a stale listener must not break a pick
            pass


def set_selection(
    *,
    polygon: dict,
    center_lon: float,
    center_lat: float,
    bbox,
    crs: str = "EPSG:4326",
    building_count: int = 0,
) -> None:
    """Arm a freshly picked tile, replacing any previous one."""
    global _PENDING
    _PENDING = SingleTileSelection(
        polygon=polygon,
        center_lon=float(center_lon),
        center_lat=float(center_lat),
        bbox=tuple(bbox),  # type: ignore[arg-type]
        crs=crs,
        building_count=int(building_count),
    )
    _notify()


def peek() -> Optional[SingleTileSelection]:
    """The armed selection, or ``None``. Does not change anything."""
    return _PENDING


def is_armed() -> bool:
    return _PENDING is not None


def clear() -> None:
    """Disarm. Safe to call when nothing is armed."""
    global _PENDING
    was_armed = _PENDING is not None
    _PENDING = None
    if was_armed:
        _notify()
