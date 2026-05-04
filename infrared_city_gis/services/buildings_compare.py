"""Compare QGIS buildings against the SDK's auto-fetched buildings.

The Infrared SDK's :meth:`InfraredClient.buildings.get_area` fetches building
footprints from Mapbox and returns them in the *polygon-bbox-SW frame* — a
local tangent plane whose origin is the polygon's WGS84 bounding-box SW
corner, x points east, y points north, units are metres. Footprints come as
triangulated 3-D meshes (``DotBimMesh``: flat ``[x, y, z, x, y, z, ...]``).

Users often have their own building dataset already loaded in QGIS (cadastre,
LiDAR-derived, custom-modelled). This helper projects those QGIS features
into the *same* frame and reports per-building matches by centroid distance,
so it's easy to spot:

  * buildings the user has but Mapbox missed,
  * buildings Mapbox added that aren't in the user's dataset,
  * positional drift between the two sources.

It also returns the SDK tile-grid centroids (in WGS84 lat/lon) — those come
from :class:`infrared_sdk.tiling.tiles.TileService`, the same code path
``run_area_and_wait`` uses internally, so the centroids match the simulation
tiles exactly.

The heavy lifting (projection, footprint extraction, matching, diff layers)
lives in ``_buildings_compare_helpers`` to keep this file under the 400-line
Infrared convention.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from qgis.core import QgsVectorLayer
from qgis.utils import iface

from infrared_sdk.tiling.tiles import TileService

from ..infrared_logger import logger
from ._buildings_compare_helpers import (
    METERS_PER_DEG_LAT,
    add_diff_layers,
    greedy_centroid_match,
    project_qgis_buildings,
    project_sdk_buildings,
    projection_params,
)
from ._geometry_io import pick_layers_by_keyword


def _resolve_layer(layer: Optional[QgsVectorLayer]) -> QgsVectorLayer:
    if layer is not None:
        return layer
    candidates = pick_layers_by_keyword(["building", "buildings"], prefer_active=True)
    if candidates:
        return candidates[0]
    if iface is not None and iface.activeLayer() is not None:
        return iface.activeLayer()
    raise RuntimeError(
        "compare_qgis_and_sdk_buildings: no QGIS buildings layer found. "
        "Pass `layer=...` explicitly or load a polygon layer first."
    )


def _collect_tile_centers(
    polygon: dict, analysis_type: Optional[str],
) -> List[Tuple[float, float, str]]:
    """Return non-empty SDK tile centroids as ``[(lon, lat, tile_id), ...]``.

    Uses ``TileService`` directly so the grid matches what
    ``run_area_and_wait`` will use internally (same analysis_type → same
    tiling config).
    """
    try:
        svc = TileService(
            polygon=polygon,
            logger=logging.getLogger(__name__),
            analysis_type=analysis_type,
        )
        out: List[Tuple[float, float, str]] = []
        for row in svc.generate_tiles_for_polygon():
            for tile in row:
                if tile.empty:
                    continue
                out.append(
                    (tile.centroid.longitude, tile.centroid.latitude, tile.tileId)
                )
        return out
    except Exception as e:
        logger.warning("TileService failed; tile centres unavailable: %s", e)
        return []


def compare_qgis_and_sdk_buildings(
    polygon: dict,
    area_buildings,
    *,
    layer: Optional[QgsVectorLayer] = None,
    match_distance_m: float = 5.0,
    plot_in_qgis: bool = True,
    sample_limit: Optional[int] = None,
    analysis_type_for_tiles: Optional[str] = "wind-speed",
) -> Dict[str, Any]:
    """Compare buildings the user defined in QGIS vs the SDK's auto-fetched ones.

    Both sets are projected into the SDK's polygon-bbox-SW frame (origin =
    polygon WGS84 bbox SW, x east, y north, units = metres). Matching is
    greedy nearest-centroid with a threshold; defaults to 5 m which catches
    real positional drift between cadastre datasets without joining
    obviously different buildings.

    Parameters
    ----------
    polygon : dict
        Same GeoJSON polygon you passed to ``client.buildings.get_area``.
    area_buildings : AreaBuildings
        Result of ``client.buildings.get_area(polygon)``.
    layer : QgsVectorLayer, optional
        Source QGIS layer for the user's buildings. Defaults to the first
        layer matched by the keywords ``["building", "buildings"]``, falling
        back to the active layer.
    match_distance_m : float
        Centroid distance below which a QGIS feature and an SDK building are
        considered the same physical structure.
    plot_in_qgis : bool
        Add three memory layers (matched / QGIS-only / SDK-only) for visual
        diff against the basemap.
    sample_limit : int, optional
        Cap the number of QGIS features processed (debug aid for huge layers).
    analysis_type_for_tiles : str, optional
        Tile config selector for centroid extraction; doesn't affect the
        comparison itself, only the returned ``tile_centers_wgs84`` list.
        Solar configs use 512 m step, wind configs 256 m step.

    Returns
    -------
    dict
        Summary report — counts, matched pairs, unmatched IDs, projection
        params, SDK tile centroids. Logged at INFO level for quick eyeballing.
    """
    layer = _resolve_layer(layer)

    qgis_buildings = project_qgis_buildings(layer, polygon, sample_limit=sample_limit)
    sdk_buildings = project_sdk_buildings(area_buildings)
    matched, qgis_unmatched, sdk_unmatched = greedy_centroid_match(
        qgis_buildings, sdk_buildings, threshold_m=match_distance_m,
    )

    if plot_in_qgis:
        try:
            add_diff_layers(qgis_buildings, sdk_buildings, matched, polygon)
        except Exception as e:
            logger.error("Failed to add diff layers: %s", e, exc_info=True)

    tile_centers_wgs84 = _collect_tile_centers(polygon, analysis_type_for_tiles)

    origin_lon, origin_lat, mpd_lng = projection_params(polygon)
    report: Dict[str, Any] = {
        "qgis_count": len(qgis_buildings),
        "sdk_count": len(sdk_buildings),
        "matched": matched,
        "qgis_unmatched": qgis_unmatched,
        "sdk_unmatched": sdk_unmatched,
        "match_rate": (
            len(matched) / max(1, max(len(qgis_buildings), len(sdk_buildings)))
        ),
        "origin_lon": origin_lon,
        "origin_lat": origin_lat,
        "meters_per_deg_lng": mpd_lng,
        "meters_per_deg_lat": METERS_PER_DEG_LAT,
        "tile_centers_wgs84": tile_centers_wgs84,
        "match_distance_m": match_distance_m,
        "qgis_layer": layer.name(),
    }

    logger.info(
        "Buildings compare: qgis=%d sdk=%d matched=%d (rate=%.1f%%); "
        "qgis_only=%d sdk_only=%d; tiles=%d",
        report["qgis_count"], report["sdk_count"], len(matched),
        100.0 * report["match_rate"], len(qgis_unmatched), len(sdk_unmatched),
        len(tile_centers_wgs84),
    )
    if matched:
        dists = [m["centroid_distance_m"] for m in matched]
        logger.info(
            "Centroid distance (m): min=%.2f median=%.2f max=%.2f",
            min(dists), sorted(dists)[len(dists) // 2], max(dists),
        )
    return report
