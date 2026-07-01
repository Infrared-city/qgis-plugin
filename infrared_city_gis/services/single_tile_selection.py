"""One-shot holder for an ArcGIS-style single-tile selection.

Mirrors the .NET plugin's ``SingleTileSelection`` static state. The
"Select tile" map tool stores the picked 512×512 m tile here; the Run
Simulation dialog :func:`consume`s it on open to enter *single-tile mode*.

Consuming clears the state, so re-opening the dialog without re-selecting
falls back to *area mode* — exactly like the ArcGIS plugin. Single-tile
mode submits **one** tile via ``client.analyses.execute`` (≈10 tokens)
instead of routing the 512 m box through the area tiler, which would split
it into multiple overlapping 256 m-step tiles and multiply the token cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class SingleTileSelection:
    """An immutable snapshot of a picked 512×512 m tile (WGS84)."""

    polygon: dict          # GeoJSON Polygon for the 512×512 m tile (WGS84)
    center_lon: float      # clicked centre longitude (WGS84)
    center_lat: float      # clicked centre latitude (WGS84)
    bbox: Tuple[float, float, float, float]  # (west, south, east, north) WGS84
    crs: str               # always "EPSG:4326"
    building_count: int    # how many features were highlighted at pick time


_PENDING: Optional[SingleTileSelection] = None


def set_selection(
    *,
    polygon: dict,
    center_lon: float,
    center_lat: float,
    bbox,
    crs: str = "EPSG:4326",
    building_count: int = 0,
) -> None:
    """Store a freshly picked tile, replacing any previous pending selection."""
    global _PENDING
    _PENDING = SingleTileSelection(
        polygon=polygon,
        center_lon=float(center_lon),
        center_lat=float(center_lat),
        bbox=tuple(bbox),  # type: ignore[arg-type]
        crs=crs,
        building_count=int(building_count),
    )


def consume() -> Optional[SingleTileSelection]:
    """Return the pending selection and clear it (one-shot)."""
    global _PENDING
    sel = _PENDING
    _PENDING = None
    return sel


def peek() -> Optional[SingleTileSelection]:
    """Return the pending selection without clearing it."""
    return _PENDING


def clear() -> None:
    """Discard any pending selection."""
    global _PENDING
    _PENDING = None
