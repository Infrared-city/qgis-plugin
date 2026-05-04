"""End-to-end orchestration of an Infrared SDK area analysis from QGIS.

This is the SDK-based counterpart to ``multi_sim_runner.run_tiles``: where
``run_tiles`` walks tile centers and calls the legacy per-tile API, this
runner builds a typed SDK payload, hands the entire polygon to
``InfraredClient.run_area_and_wait`` (which handles tiling, parallel job
submission, polling, and merging server-side), then renders the merged
result grid as a single GeoTIFF + raster layer in QGIS.
"""

from __future__ import annotations

import math
import os
import tempfile
from typing import Optional, Tuple

import numpy as np
from qgis.core import Qgis
from qgis.PyQt.QtWidgets import QApplication, QMessageBox
from qgis.utils import iface

from infrared_sdk import InfraredClient

from ..infrared_logger import logger
from ..models.analysis import AnalysisType
from ..services.geotiff import generate_geotiff
from ..services.sdk_payloads import build_sdk_payload
from ..visualization.display import add_geojson_then_raster


def _status(msg: str, level=Qgis.Info, duration: int = 0) -> None:
    iface.messageBar().clearWidgets()
    iface.messageBar().pushMessage("InfraredCity", msg, level=level, duration=duration)
    QApplication.processEvents()


_METERS_PER_DEG_LAT = 111_320.0
"""Same constant the SDK's tile/merger code uses (see tiling/merger.py)."""


def _polygon_wgs84_bbox(polygon: dict) -> Tuple[float, float, float, float]:
    """Return (west, south, east, north) of a GeoJSON polygon in WGS84."""
    ring = polygon["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


def _merged_grid_wgs84_bbox(
    polygon: dict, grid_shape: Tuple[int, int]
) -> Tuple[float, float, float, float]:
    """Return the WGS84 (west, south, east, north) bbox of an AreaResult merged_grid.

    The SDK's merge step (`tiling/merger.py:_polygon_to_meters`) projects to a
    local tangent plane whose origin is the polygon's WGS84 bbox SW corner,
    using ``meters_per_deg_lng = 111320 * cos(center_lat_rad)`` and
    ``meters_per_deg_lat = 111320``. The merged grid is sized in cells, with
    1 cell = 1 m. To geo-reference it for QGIS we invert that projection:

      east  = origin_lon + width_cells  / meters_per_deg_lng
      north = origin_lat + height_cells / meters_per_deg_lat

    The grid extent is the *tile-grid* extent (which fully encloses the
    polygon), not the polygon's WGS84 bbox — that's the bug the previous
    implementation had.
    """
    height_cells, width_cells = grid_shape
    west, south, east, north_poly = _polygon_wgs84_bbox(polygon)
    center_lat = (south + north_poly) / 2.0
    meters_per_deg_lng = _METERS_PER_DEG_LAT * math.cos(math.radians(center_lat))
    if meters_per_deg_lng <= 0:
        # Polar latitudes — fall back to polygon-bbox-relative scaling so we
        # at least produce a finite raster instead of crashing.
        logger.warning("Polar latitude — meters_per_deg_lng=%s; using polygon bbox.",
                       meters_per_deg_lng)
        return west, south, east, north_poly
    east_grid = west + (width_cells / meters_per_deg_lng)
    north_grid = south + (height_cells / _METERS_PER_DEG_LAT)
    return west, south, east_grid, north_grid


def _make_progress_cb(total_hint: Optional[int] = None):
    """Return an on_progress callback for run_area_and_wait that updates the QGIS bar."""

    def _cb(state) -> None:
        total = state.total or total_hint or 0
        _status(
            f"InfraredCity: {state.succeeded}/{total} tiles done "
            f"({state.running} running, {state.pending} pending, {state.failed} failed)",
            level=Qgis.Info,
        )
        logger.info(
            "Area progress: status=%s succeeded=%d running=%d pending=%d failed=%d total=%d",
            state.status, state.succeeded, state.running, state.pending,
            state.failed, state.total,
        )

    return _cb


def _write_buildings_outline_geojson(area_buildings, out_path: str) -> Optional[str]:
    """Write a minimal GeoJSON FeatureCollection with one polygon per building.

    The visualization helper ``add_geojson_then_raster`` always overlays a
    GeoJSON outline on top of the raster. For the SDK area path we don't
    have per-tile outlines but we do have the merged ``AreaBuildings`` from
    ``client.buildings.get_area``. We project each building's footprint
    (xy of every vertex, ignoring z) to a flat polygon for display.
    Returns ``out_path`` if it managed to write at least one feature, else None.
    """
    import json

    features = []
    for bid, mesh in (area_buildings.buildings or {}).items():
        coords = list(getattr(mesh, "coordinates", []) or [])
        if len(coords) < 9:  # need >= 3 vertices (x,y,z each)
            continue
        # Project vertices to ground plane and take the convex hull-ish ring
        # via simple xy dedup. For visualisation only, so this can be coarse.
        xy = []
        for i in range(0, len(coords), 3):
            xy.append((float(coords[i]), float(coords[i + 1])))
        if len(xy) < 3:
            continue
        # Close the ring
        if xy[0] != xy[-1]:
            xy.append(xy[0])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[x, y] for (x, y) in xy]]},
            "properties": {"building_id": str(bid)},
        })
    if not features:
        return None
    fc = {"type": "FeatureCollection", "features": features}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(fc, f)
    return out_path


def run_sdk_area(dlg, polygon: dict, area) -> None:
    """Build payload, run the area analysis, render the result.

    Parameters
    ----------
    dlg
        The dialog instance. Must have ``api_key``, ``analysis_type``, and
        the analysis-type-specific input widgets populated.
    polygon : dict
        A GeoJSON Polygon (WGS84) — the area to analyse.
    area
        The ``AreaBuildings`` returned by ``client.buildings.get_area(polygon)``;
        its ``buildings`` map is forwarded to the SDK so the API does not need
        to refetch them.
    """
    # 1. Capture analysis type + sub-type from the dialog. Default sub-type
    #    starts None and may be overwritten by build_sdk_payload (PWC, TCS).
    dlg.analysis_type = dlg.analysis_type_dropdown.currentData()
    dlg.sub_analysis_type = None

    # 2. Build the typed SDK payload from the dialog UI.
    payload = build_sdk_payload(dlg)
    if payload is None:
        return  # build_sdk_payload already showed a QMessageBox.
    logger.info(
        "SDK payload built: analysis_type=%s sub=%s",
        dlg.analysis_type,
        dlg.sub_analysis_type,
    )

    # 3. Run the area analysis end-to-end. Forward the buildings we already
    #    fetched so the SDK doesn't refetch them tile-by-tile.
    client = InfraredClient(api_key=dlg.api_key)
    _status("InfraredCity: submitting area jobs…")
    try:
        result = client.run_area_and_wait(
            payload,
            polygon,
            buildings=area.buildings,
            on_progress=_make_progress_cb(total_hint=area.total_buildings),
        )
    except Exception as e:
        logger.error("run_area_and_wait failed: %s", e, exc_info=True)
        _status(f"InfraredCity: area run failed — {str(e)[:120]}", level=Qgis.Critical, duration=15)
        QMessageBox.critical(
            dlg, "Simulation Error",
            f"Area run failed.\n\n{e}\n\nCheck the plugin log for details.",
        )
        return

    logger.info(
        "Area complete: analysis_type=%s succeeded=%d failed=%d skipped=%d total=%d",
        result.analysis_type, result.succeeded_jobs,
        len(result.failed_jobs), len(result.skipped_jobs), result.total_jobs,
    )

    # 4. Render the merged grid as a GeoTIFF over the polygon's WGS84 bbox.
    grid = result.merged_grid
    if grid is None or grid.size == 0:
        _status("InfraredCity: empty result grid", level=Qgis.Warning, duration=10)
        return
    if not isinstance(grid, np.ndarray):
        grid = np.asarray(grid, dtype=np.float32)
    else:
        grid = grid.astype(np.float32)

    bbox = _merged_grid_wgs84_bbox(polygon, grid.shape)
    logger.info(
        "Merged grid: shape=%s polygon-bbox-SW=%s -> WGS84 bbox=%s",
        grid.shape, _polygon_wgs84_bbox(polygon), bbox,
    )
    tmp_dir = tempfile.mkdtemp(prefix="ic_area_")
    geotiff_path = os.path.join(tmp_dir, f"area_{result.analysis_type}.tif")
    crs_authid = "EPSG:4326"

    # NOTE: AreaResult.merged_grid is row-major with row 0 = southernmost row
    # (tile-grid order with origin at polygon-bbox SW). generate_geotiff
    # writes top-to-bottom, so we flip vertically to match north-up GeoTIFF
    # convention.
    grid_for_tiff = np.flipud(grid)

    sub = dlg.sub_analysis_type.value if dlg.sub_analysis_type else None
    generate_geotiff(
        grid_for_tiff,
        bbox,
        crs_authid,
        geotiff_path,
        simulation_type=str(dlg.analysis_type),
        criteria=sub,
    )

    # 5. Optional buildings outline overlay (purely visual).
    geojson_path = os.path.join(tmp_dir, "buildings_outline.geojson")
    if _write_buildings_outline_geojson(area, geojson_path) is None:
        # add_geojson_then_raster requires a vector layer; fall back to a
        # one-feature collection holding the polygon itself.
        import json
        with open(geojson_path, "w", encoding="utf-8") as f:
            json.dump({
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": polygon,
                    "properties": {"role": "area"},
                }],
            }, f)

    # 6. Resolve legend bounds. Per SDK README: prefer result.min_legend /
    #    max_legend when the API supplies them; otherwise fall back to
    #    np.nanmin / np.nanmax. Deriving from data alone produces washed-out
    #    plots for analyses like Direct Sun Hours / Daylight Availability
    #    where most cell values cluster near the maximum.
    grid_min = float(np.nanmin(grid)) if np.any(~np.isnan(grid)) else None
    grid_max = float(np.nanmax(grid)) if np.any(~np.isnan(grid)) else None
    leg_min: Optional[float] = (
        result.min_legend if result.min_legend is not None else grid_min
    )
    leg_max: Optional[float] = (
        result.max_legend if result.max_legend is not None else grid_max
    )
    # UTCI honours the dialog's manual overrides on top of that.
    if dlg.analysis_type == AnalysisType.THERMAL_COMFORT_INDEX:
        if (getattr(dlg, "legend_min_enable_tci", None)
                and dlg.legend_min_enable_tci.isChecked()):
            leg_min = dlg.min_legend_value
        if (getattr(dlg, "legend_max_enable_tci", None)
                and dlg.legend_max_enable_tci.isChecked()):
            leg_max = dlg.max_legend_value
    logger.info(
        "Legend bounds: api=(%s, %s) grid=(%s, %s) -> applied=(%s, %s)",
        result.min_legend, result.max_legend, grid_min, grid_max, leg_min, leg_max,
    )

    add_geojson_then_raster(
        geojson_path=geojson_path,
        geotiff_path=geotiff_path,
        analysis_type=str(dlg.analysis_type),
        sub_analysis_type=sub,
        min_legend_value=leg_min,
        max_legend_value=leg_max,
        tile_id=None,
    )
    iface.mapCanvas().refresh()
    QApplication.processEvents()

    summary = (
        f"InfraredCity: ✅ area done — {result.succeeded_jobs}/{result.total_jobs} tiles, "
        f"{len(result.failed_jobs)} failed, {len(result.skipped_jobs)} skipped. "
        f"Saved: {geotiff_path}"
    )
    _status(summary, level=Qgis.Success, duration=20)
    logger.info(summary)
