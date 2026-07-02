"""End-to-end orchestration of an Infrared SDK area analysis from QGIS.

Two run flavours, sharing the same payload-build + result-render code:

* :func:`run_sdk_area` — synchronous, blocks until the area completes.
  Convenient for scripting / batch use; freezes the QGIS UI in the
  meantime.
* :func:`run_sdk_area_async` — submits jobs immediately, returns an
  :class:`AreaPoller` that drives polling + render via a ``QTimer`` so
  the dialog can close right away and the UI stays responsive.

Both paths use :class:`InfraredClient` from the bundled SDK; the async
path uses ``client.run_area`` + ``check_area_state`` + ``merge_area_jobs``
composed by :class:`AreaPoller`, the sync path is the SDK's own
``run_area_and_wait`` which composes the same three steps internally.
"""

from __future__ import annotations

import math
import os
import tempfile
from typing import Any, Optional, Tuple

import numpy as np
from infrared_sdk import InfraredClient
from qgis.core import Qgis
from qgis.PyQt.QtWidgets import QApplication, QMessageBox
from qgis.utils import iface

from ..infrared_logger import logger
from ..services.area_poller import AreaPoller, AreaRenderState
from ..services.geotiff import generate_geotiff
from ..services.ground_materials import (
    collect_ground_materials,
    has_ground_material_support,
)
from ..services.qgis_area_vegetation import collect_qgis_area_vegetation
from ..services.sdk_payloads import build_sdk_payload
from ..services.tree_layer_picker import has_tree_support, selected_tree_layer
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


def _prepare_run(dlg) -> "Optional[Any]":
    """Snapshot dialog state and build the SDK payload.

    Sets ``dlg.analysis_type`` and resets ``dlg.sub_analysis_type`` (which
    ``build_sdk_payload`` may overwrite for PWC/TCS). Returns the typed
    SDK payload, or ``None`` if validation failed (a QMessageBox was
    already shown by ``build_sdk_payload``).
    """
    dlg.analysis_type = dlg.analysis_type_dropdown.currentData()
    dlg.sub_analysis_type = None

    payload = build_sdk_payload(dlg)
    if payload is None:
        return None
    logger.info(
        "SDK payload built: analysis_type=%s sub=%s",
        dlg.analysis_type, dlg.sub_analysis_type,
    )
    return payload


def clear_layer_selections() -> None:
    """Remove feature-selection highlights from all vector layers.

    Called after a result renders so the picked single tile / selected area
    buildings stop being highlighted on the canvas — the result raster is the
    thing to look at now.
    """
    try:
        from qgis.core import QgsProject, QgsVectorLayer
        cleared = 0
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.selectedFeatureCount() > 0:
                lyr.removeSelection()
                cleared += 1
        if cleared and iface is not None:
            iface.mapCanvas().refresh()
        logger.info("Cleared feature selection on %d layer(s) after render", cleared)
    except Exception as e:
        logger.debug("clear_layer_selections failed: %s", e)


def render_area_result(
    render_state: AreaRenderState, polygon: dict, area, result,
) -> None:
    """Render an :class:`AreaResult` as a GeoTIFF + raster layer in QGIS.

    Decoupled from the dialog so it can be invoked both from the
    synchronous :func:`run_sdk_area` path *and* from the async
    :class:`AreaPoller`'s completion handler — which fires after the
    dialog has been destroyed, so ``render_state`` carries primitive
    Python values only (no QWidget references).
    """
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

    # NOTE: AreaResult.merged_grid is row-major with row 0 = southernmost
    # row (tile-grid order with origin at polygon-bbox SW). generate_geotiff
    # writes top-to-bottom, so we flip vertically to match north-up GeoTIFF
    # convention.
    grid_for_tiff = np.flipud(grid)

    sub = (
        render_state.sub_analysis_type.value
        if render_state.sub_analysis_type is not None
        else None
    )
    generate_geotiff(
        grid_for_tiff, bbox, "EPSG:4326", geotiff_path,
        simulation_type=str(render_state.analysis_type), criteria=sub,
    )

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

    # Legend bounds — per SDK README: prefer result.min_legend / max_legend
    # when the API supplies them; otherwise fall back to np.nanmin/nanmax.
    grid_min = float(np.nanmin(grid)) if np.any(~np.isnan(grid)) else None
    grid_max = float(np.nanmax(grid)) if np.any(~np.isnan(grid)) else None
    leg_min: Optional[float] = (
        result.min_legend if result.min_legend is not None else grid_min
    )
    leg_max: Optional[float] = (
        result.max_legend if result.max_legend is not None else grid_max
    )
    if render_state.legend_min_override is not None:
        leg_min = render_state.legend_min_override
    if render_state.legend_max_override is not None:
        leg_max = render_state.legend_max_override
    logger.info(
        "Legend bounds: api=(%s, %s) grid=(%s, %s) override=(%s, %s) -> "
        "applied=(%s, %s)",
        result.min_legend, result.max_legend, grid_min, grid_max,
        render_state.legend_min_override, render_state.legend_max_override,
        leg_min, leg_max,
    )

    add_geojson_then_raster(
        geojson_path=geojson_path,
        geotiff_path=geotiff_path,
        analysis_type=str(render_state.analysis_type),
        sub_analysis_type=sub,
        min_legend_value=leg_min,
        max_legend_value=leg_max,
        tile_id=None,
    )
    # Drop the selection highlight now the result raster is on the canvas.
    clear_layer_selections()
    if iface is not None:
        iface.mapCanvas().refresh()
    QApplication.processEvents()

    summary = (
        f"InfraredCity: ✅ area done — {result.succeeded_jobs}/{result.total_jobs} tiles, "
        f"{len(result.failed_jobs)} failed, {len(result.skipped_jobs)} skipped. "
        f"Saved: {geotiff_path}"
    )
    _status(summary, level=Qgis.Success, duration=20)
    logger.info(summary)


def run_sdk_area(dlg, polygon: dict, area) -> None:
    """Build payload, run the area analysis synchronously, render the result.

    Blocks the QGIS UI thread for the entire run (submission, polling and
    merge inside ``client.run_area_and_wait``). Use :func:`run_sdk_area_async`
    instead if you want the dialog to close immediately and the UI to stay
    responsive while jobs run.
    """
    payload = _prepare_run(dlg)
    if payload is None:
        return

    client = InfraredClient(api_key=dlg.api_key)
    _status("InfraredCity: submitting area jobs…")
    try:
        result = client.run_area_and_wait(
            payload, polygon,
            buildings=area.buildings,
            on_progress=_make_progress_cb(total_hint=area.total_buildings),
        )
    except Exception as e:
        logger.error("run_area_and_wait failed: %s", e, exc_info=True)
        _status(f"InfraredCity: area run failed — {str(e)[:120]}",
                level=Qgis.Critical, duration=15)
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
    render_area_result(AreaRenderState.from_dialog(dlg), polygon, area, result)


# Module-level keep-alive list for active pollers.
#
# Why: parenting the AreaPoller to ``iface.mainWindow()`` keeps its *C++*
# QObject alive across the dialog being destroyed, but PyQt is free to
# garbage-collect the *Python* wrapper as soon as no Python reference
# remains. When that happens the wrapper's bound method (``_on_tick``)
# disappears from the QTimer's slot table, the timer fires into the void,
# and we silently get the bug we just hit: jobs submit, polling never
# updates, no result is rendered.
#
# Holding a Python reference in this list pins the wrapper for the
# poller's lifetime. The poller removes itself on ``finished`` /
# ``failed`` so the list doesn't grow unbounded across runs.
_ACTIVE_POLLERS: list = []


def _retire_poller(poller: AreaPoller) -> None:
    """Drop the keep-alive reference once a poller signals completion."""
    try:
        _ACTIVE_POLLERS.remove(poller)
        logger.debug("retired AreaPoller; %d still active", len(_ACTIVE_POLLERS))
    except ValueError:
        pass  # already retired (double-emit safety)


def run_sdk_area_async(dlg, polygon: dict, area) -> Optional[AreaPoller]:
    """Async counterpart of :func:`run_sdk_area`.

    Builds the payload, submits jobs, hands the schedule off to an
    :class:`AreaPoller` and returns the poller. The caller can then close
    the dialog (``super().accept()``) immediately — the poller is
    parented to ``iface.mainWindow()`` *and* pinned in
    :data:`_ACTIVE_POLLERS` so neither Qt nor Python's GC can prematurely
    drop it.

    Returns ``None`` if payload validation failed (a QMessageBox was
    already shown). On submission failure the poller's ``failed`` signal
    fires; the caller doesn't need to handle that synchronously.
    """
    payload = _prepare_run(dlg)
    if payload is None:
        return None

    render_state = AreaRenderState.from_dialog(dlg)
    parent = iface.mainWindow() if iface is not None else None

    # Vegetation: collect from the user-picked tree layer (if any). Skip
    # entirely when the dialog's combo is on "(none)" or the analysis
    # type doesn't support vegetation — keeps the payload small and
    # avoids sending features the inference engine would discard.
    vegetation: Optional[dict] = None
    tree_layer = None
    try:
        tree_layer = selected_tree_layer(dlg.tree_layer_dropdown)
    except AttributeError:
        # Older .ui without the field — leave vegetation as None.
        pass
    if tree_layer is not None and has_tree_support(dlg.analysis_type):
        try:
            vegetation = collect_qgis_area_vegetation(
                polygon, tree_layer,
                use_catalog_type=getattr(dlg, "use_tree_catalog_type", False),
            )
            if not vegetation:
                vegetation = None  # empty dict → no vegetation
        except Exception as e:
            logger.warning(
                "Failed to collect vegetation from layer %r: %s",
                tree_layer.name(), e, exc_info=True,
            )
            vegetation = None

    # Ground materials — only for analyses that use surface materials.
    # Auto-fetch mode pulls Infrared's own layers for the polygon at submit
    # time (ignoring ground-* layers); otherwise the ticked ground-* layers
    # are collected into the SDK's {material_name: FeatureCollection}
    # mapping. Empty/failed → None → the run carries no ground materials
    # (server default emissivity).
    ground_materials: Optional[dict] = None
    if has_ground_material_support(dlg.analysis_type):
        if getattr(dlg, "use_infrared_ground_materials", False):
            _status("InfraredCity: fetching ground materials for the area…")
            try:
                with InfraredClient(api_key=dlg.api_key) as gm_client:
                    area_gm = gm_client.ground_materials.get_area(polygon)
                ground_materials = area_gm.layers or None
                logger.info(
                    "Auto-fetched ground materials: %d features, %d layer(s)",
                    area_gm.total_features, len(area_gm.layers),
                )
            except Exception as e:
                logger.warning(
                    "Ground materials auto-fetch failed — running without: %s",
                    e, exc_info=True,
                )
                _status(
                    "InfraredCity: ground materials fetch failed — running "
                    "without them", level=Qgis.Warning, duration=10,
                )
        else:
            try:
                gm_layers = dlg.selected_ground_material_layers()
            except AttributeError:
                gm_layers = {}
            if gm_layers:
                try:
                    ground_materials = (
                        collect_ground_materials(polygon, gm_layers) or None
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to collect ground materials: %s", e,
                        exc_info=True,
                    )
                    ground_materials = None

    poller = AreaPoller(
        client=InfraredClient(api_key=dlg.api_key),
        polygon=polygon,
        area=area,
        payload=payload,
        render_state=render_state,
        on_render=render_area_result,
        vegetation=vegetation,
        ground_materials=ground_materials,
        parent=parent,
    )
    _ACTIVE_POLLERS.append(poller)
    # Use partial-style closures so the lambda captures `poller` by value.
    poller.finished.connect(lambda _result, p=poller: _retire_poller(p))
    poller.failed.connect(lambda _msg, p=poller: _retire_poller(p))
    poller.start()
    return poller
