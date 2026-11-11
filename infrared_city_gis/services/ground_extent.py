"""How far past the user's SELECTION a ground-material download has to reach.

Three numbers decide it, and none of them is the selection:

* a simulation tile reads ground material ``ground_read_margin_m()`` beyond
  its own CENTRE (544 m on the widest preset);
* the tile grid does not stop at the selection — a centre can sit
  ``ground_grid_overhang_m()`` outside it (256 m, half a step);
* where that ground is missing the server composes its DEFAULT ASPHALT, so a
  band that is merely short is not empty, it is wrong.

:func:`fetch_polygon_with_context` is what we ask the SDK to download, and
:func:`ground_envelope` is what the collector is allowed to keep. They have to
agree, or the collector clips the context straight back off before the run.

Split out of ``ground_materials`` (2026-11-11): that module is part of the
size-limit debt in #50, and this is the one coherent piece of it with its own
subject. Import these names from HERE — ``ground_materials`` re-exports only
``ground_envelope``, which its own collector needs.
"""

from __future__ import annotations

import math
from typing import Tuple

from ..infrared_logger import logger
from .qgis_area_vegetation import (
    _DEFAULT_CONTEXT_MARGIN_M,
    _polygon_wgs84_bbox_with_margin,
)

#: Metres per degree of latitude on the SDK's spherical earth (radius 6 371 000 m,
#: ``cleaning_extent``), so the envelope converts the SDK's metres the same way.
_M_PER_DEG = 6_371_000.0 * math.pi / 180.0


def ground_read_margin_m() -> float:
    """How far beyond itself one simulation tile reads ground material.

    ``ground_read_distance_m(None)`` — the widest preset, 544 m today (363 m on
    the wind one). Ground that far outside a tile still decides that tile's
    result, and where it is missing the server falls back to its default
    asphalt.
    """
    from infrared_sdk.tiling.read_margin import ground_read_distance_m

    return float(ground_read_distance_m(None))


def ground_grid_overhang_m() -> float:
    """How far a tile CENTRE can sit outside the selection's bounding box.

    The read margin above is measured from the tile centre, and the grid does
    not stop at the selection. The SDK steps it from the bbox's south-west
    corner, so centres land at ``origin + (i + 0.5) * step``; with
    ``n = ceil(width / step)`` columns the last one overshoots the east edge by
    ``n * step - width - step / 2``, which is bounded by **half a step** (the
    west and south edges always start half a step INSIDE). Measured on the
    1113 x 1185 m Vienna site, UTCI grid: +168 m east, +94 m north, -256 m west
    and south.

    The step is the widest preset's, the same one :func:`ground_read_margin_m`
    answers for — ground materials feed UTCI/TCS, which steps 512 m, while the
    wind preset steps 256 m. ``get_tiling_config(None)`` is NOT that preset: it
    answers with the WIND config, so the analysis type has to be named.
    """
    from infrared_sdk.tiling.config import get_tiling_config
    from infrared_sdk.tiling.read_margin import WIDEST_READ_ANALYSIS_TYPE

    step_m = get_tiling_config(str(WIDEST_READ_ANALYSIS_TYPE)).step_m
    return float(step_m) / 2.0


def ground_context_margin_m() -> float:
    """How far past the SELECTION a ground download has to reach: 800 m today.

    :func:`ground_read_margin_m` (544 m) is measured from a tile centre, and
    :func:`ground_grid_overhang_m` (256 m) is how far outside the selection
    that centre can be — so the download needs their SUM. Growing the bbox by
    the read margin alone leaves the outermost tiles short by up to half a
    step, and that band composes as the default asphalt again, which is the
    whole bug this margin exists to close.
    """
    return ground_read_margin_m() + ground_grid_overhang_m()


def fetch_polygon_with_context(polygon: dict) -> dict:
    """The area to DOWNLOAD for a run over *polygon*: its bbox plus the margin.

    ``get_area`` crops its read to a circle around the polygon's bbox centre,
    radius ``max(half the bbox diagonal, the read margin)``
    (``cleaning_extent``). That circle circumscribes the bbox, so it touches
    the corners: on the 1113 x 1185 m site measured on 2026-10-16 the download
    reached 257 m past the east and west edges, 220 m past north and south,
    and **0 m past the corners** — while every tile reads ground 544 m around
    itself. The border band of the run was therefore composed with up to 544 m
    of context missing, and ran as default asphalt: "at the edges it is as if
    the ground materials were not there".

    Asking for the bbox grown by :func:`ground_context_margin_m` makes the
    circle cover the selection plus the full read distance everywhere, corners
    included — and the grid's own overhang with it, because the tile whose
    read has to land inside this box is not bounded by the selection either.
    It downloads more than the user selected — on that site 1.32 km2 ->
    7.6 km2 — which is why the dialog says so.

    The right long-term home for this is the SDK, where the same formula is
    duplicated for every client (``cleaning_extent`` in Python,
    ``cleaningExtent`` in ``public/typescript/src/internal/ground-merge.ts``,
    ``CleaningExtent`` in the .NET SDK): ``half_diagonal + fetch_distance``
    instead of ``max`` of the two. Tracked as **infrared-core#1652** — delete
    this margin and go back to handing ``get_area`` the plain selection once
    that lands.
    """
    ring = (polygon or {}).get("coordinates") or [[]]
    if not ring[0]:
        # Nothing to grow. The SDK's own validation is the right place to
        # refuse this, so pass it through rather than raising here.
        logger.warning(
            "ground context margin skipped: the polygon has no outer ring"
        )
        return polygon
    margin_m = ground_context_margin_m()
    west, south, east, north = _polygon_wgs84_bbox_with_margin(polygon, margin_m)
    logger.info(
        "Ground-material download area: selection + %.0f m of context "
        "(bbox %.6f, %.6f -> %.6f, %.6f)",
        margin_m, west, south, east, north,
    )
    return {
        "type": "Polygon",
        "coordinates": [[
            [west, south], [east, south], [east, north], [west, north],
            [west, south],
        ]],
    }


def ground_envelope(polygon: dict) -> Tuple[float, float, float, float]:
    """``(west, south, east, north)`` that holds everything a run can use.

    Two reaches, whichever is wider:

    * The SDK's ground read crops to a CIRCLE around the polygon's bbox centre,
      radius half the bbox diagonal and never less than the widest read
      distance (``cleaning_extent``, 544 m today; ground materials only feed
      UTCI/TCS, the widest preset). On a 1 km square that reaches ~210 m past
      the selection. Filtering to anything narrower dropped context the SDK had
      fetched for the edge tiles: those surfaces ran as the default asphalt
      instead.
    * The selection's bbox plus :func:`ground_context_margin_m` — what
      :func:`fetch_polygon_with_context` now downloads, and what the edge tiles
      actually read, the grid's overhang included. Without this the collector
      would clip the extra context straight back off before the run.

    Plus 1 m, so a downloaded layer passes whole and run_area still crops per
    tile.
    """
    from infrared_sdk.ground_materials.cleaning import cleaning_extent

    margin_box = _polygon_wgs84_bbox_with_margin(
        polygon, ground_context_margin_m() + 1.0
    )
    extent = cleaning_extent(polygon, fetch_distance=ground_read_margin_m())
    if extent is None:
        # Empty outer ring: fall back to the old envelope rather than nothing.
        return _polygon_wgs84_bbox_with_margin(polygon, _DEFAULT_CONTEXT_MARGIN_M)
    lat, lon, distance = extent
    reach = distance + 1.0
    dlat = reach / _M_PER_DEG
    dlon = reach / (_M_PER_DEG * max(math.cos(math.radians(lat)), 1e-6))
    circle_box = (lon - dlon, lat - dlat, lon + dlon, lat + dlat)
    return (
        min(circle_box[0], margin_box[0]), min(circle_box[1], margin_box[1]),
        max(circle_box[2], margin_box[2]), max(circle_box[3], margin_box[3]),
    )
