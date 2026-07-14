"""Dedicated single-tile analysis path (ArcGIS ``RunSingleTileInBackground``).

A single 512×512 m tile must run as **one** inference job, not through the
area tiler. ``run_area`` re-tiles its polygon with a 256 m step (50 % overlap,
see ``infrared_sdk/tiling/config.py``), so a 512 m box would fan out into
several overlapping tiles → several × the token cost. The single-tile path
instead submits exactly one job via ``client.analyses.execute`` (≈10 tokens)
and renders the resulting 512×512 grid over the tile's WGS84 bbox.

Building geometry: ``collect_qgis_area_buildings(polygon)`` already returns
meshes in the polygon-bbox-SW metre frame, which for a single tile is
identical to the tile-SW frame the inference engine expects (tile (0,0),
``tile_sw_offset == (0, 0)``). We therefore serialise each mesh with
``model_dump(by_alias=True)`` — exactly what ``_area/_layers.resolve_layers``
does — and attach them as ``payload.geometries`` with no coordinate
transform. Vegetation is passed as WGS84 ``Feature`` dicts; the server
localises them via the payload's ``latitude``/``longitude`` reference point
(same as the area path's per-tile validator).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Callable, Optional

import numpy as np
from infrared_sdk import InfraredClient
from infrared_sdk.analyses.jobs import JobsServiceClient, JobStatus
from infrared_sdk.tiling.orchestrator import _extract_grid
from qgis.core import Qgis
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import QApplication, QMessageBox
from qgis.utils import iface

from ..infrared_logger import logger
from ..visualization.display import add_geojson_then_raster
from .area_poller import AreaRenderState
from .geotiff import generate_geotiff, map_categories
from .ground_materials import (
    collect_ground_materials,
    has_ground_material_support,
    stamp_material_properties,
)
from .qgis_area_vegetation import collect_qgis_area_vegetation
from .sdk_runner import (
    _ACTIVE_POLLERS,
    _merged_grid_wgs84_bbox,
    _prepare_run,
    _retire_poller,
    _status,
    _write_buildings_outline_geojson,
    clear_layer_selections,
)
from .tree_layer_picker import has_tree_support, selected_tree_layer

# Single-tile jobs are quick; poll every 2 s with a 5-minute wall-clock cap.
_POLL_INTERVAL_MS = 2000
_JOB_TIMEOUT_S = 300


def render_single_tile_result(
    render_state: AreaRenderState, polygon: dict, area, grid,
) -> None:
    """Render a single-tile result grid as a GeoTIFF + raster layer in QGIS.

    Mirrors :func:`sdk_runner.render_area_result` but for a raw 512×512 grid
    straight from ``_extract_grid`` (no merge/clip — the tile *is* the
    polygon). Legend bounds come from the grid itself (the single-job path
    has no API-supplied ``min_legend``/``max_legend``), overridden by the
    dialog's manual legend values when set.
    """
    if grid is None or getattr(grid, "size", 0) == 0:
        _status("InfraredCity: empty single-tile result grid",
                level=Qgis.Warning, duration=10)
        return
    grid = np.asarray(grid, dtype=np.float32)

    bbox = _merged_grid_wgs84_bbox(polygon, grid.shape)
    logger.info("Single-tile grid: shape=%s -> WGS84 bbox=%s", grid.shape, bbox)

    tmp_dir = tempfile.mkdtemp(prefix="ic_single_")
    geotiff_path = os.path.join(tmp_dir, f"single_{render_state.analysis_type}.tif")

    # Inference grids follow the same row-0-is-south convention the area
    # merge uses; generate_geotiff writes north-up, so flip vertically.
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
        with open(geojson_path, "w", encoding="utf-8") as f:
            json.dump({
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": polygon,
                    "properties": {"role": "tile"},
                }],
            }, f)

    grid_min = float(np.nanmin(grid)) if np.any(~np.isnan(grid)) else None
    grid_max = float(np.nanmax(grid)) if np.any(~np.isnan(grid)) else None
    leg_min: Optional[float] = (
        render_state.legend_min_override
        if render_state.legend_min_override is not None else grid_min
    )
    leg_max: Optional[float] = (
        render_state.legend_max_override
        if render_state.legend_max_override is not None else grid_max
    )
    logger.info(
        "Single-tile legend: grid=(%s, %s) override=(%s, %s) -> applied=(%s, %s)",
        grid_min, grid_max,
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
    # Drop the picked-tile selection highlight now the result raster is shown.
    clear_layer_selections()
    if iface is not None:
        iface.mapCanvas().refresh()
    QApplication.processEvents()

    summary = f"InfraredCity: ✅ single tile done — saved: {geotiff_path}"
    _status(summary, level=Qgis.Success, duration=20)
    logger.info(summary)


class SingleTilePoller(QObject):
    """QTimer-driven poller for a single ``analyses.execute`` job.

    Mirrors :class:`area_poller.AreaPoller` but for one job: poll
    ``jobs.get_status`` until Succeeded/Failed, then download + decompress +
    extract the grid and hand it to ``on_render``. Parent should outlive the
    dialog (``iface.mainWindow()``) and the poller must be pinned in
    ``sdk_runner._ACTIVE_POLLERS`` so neither Qt nor Python GC drops it.
    """

    finished = pyqtSignal(object)  # numpy grid
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        client,
        job,
        polygon: dict,
        area,
        render_state: AreaRenderState,
        on_render: Callable[[AreaRenderState, dict, Any, Any], None],
        poll_interval_ms: int = _POLL_INTERVAL_MS,
        timeout_s: int = _JOB_TIMEOUT_S,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._job = job
        self._polygon = polygon
        self._area = area
        self._render_state = render_state
        self._on_render = on_render
        self._timeout_s = timeout_s
        self._deadline: Optional[float] = None

        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._on_tick)

    def start(self) -> None:
        self._deadline = time.monotonic() + self._timeout_s
        _status("InfraredCity: single-tile job submitted, running in background…")
        self._timer.start()

    def cancel(self) -> None:
        logger.info("SingleTilePoller: cancel requested")
        self._timer.stop()
        _status("InfraredCity: single-tile run cancelled (job remains on server)",
                level=Qgis.Warning, duration=10)
        self.deleteLater()

    def _on_tick(self) -> None:
        try:
            job = self._client.jobs.get_status(self._job.job_id)
        except Exception as e:
            # Transient errors (rate limits, blips) shouldn't kill the run.
            logger.warning("SingleTilePoller: get_status failed (continuing): %s", e)
            return

        if job.status == JobStatus.succeeded:
            self._timer.stop()
            self._finalize(job)
            return
        if job.status == JobStatus.failed:
            self._timer.stop()
            self._fail(f"job failed: {job.error or '(no error message)'}")
            return

        _status(f"InfraredCity: single tile {job.status}…", level=Qgis.Info)

        if self._deadline is not None and time.monotonic() > self._deadline:
            self._timer.stop()
            self._fail(f"timed out after {self._timeout_s}s (last status={job.status})")

    def _finalize(self, job) -> None:
        try:
            download = self._client.jobs.download_results(job.job_id, _job=job)
            result = JobsServiceClient.decompress(download.content)
            at_str = str(self._render_state.analysis_type)
            grid_list = _extract_grid(result, at_str)
            if at_str == "pedestrian-wind-comfort":
                # Categorical grid — PWC Lawson classes arrive as letter
                # strings ('A'…'E'/'S') or 0-based index strings, not
                # numbers. Map them to the registry's 1-based class indices,
                # same as the area path does inside generate_geotiff. JSON
                # nulls come through as Python None (object dtype) —
                # normalise to the "None" nodata string map_categories
                # expects. PWC ONLY: numeric grids of other analyses can
                # also arrive as object/string arrays (floats + None), and
                # map_categories' numeric mode would shift those by +1.
                raw = np.array(grid_list)
                if raw.dtype.kind in "fiu":
                    # Already-numeric PWC matrix — mirror generate_geotiff's
                    # float branch: treat as mapped, no re-mapping.
                    grid = raw.astype(np.float32)
                else:
                    if raw.dtype.kind == "O":
                        raw = np.where(
                            np.equal(raw, None), "None", raw
                        ).astype(str)
                    sub = (
                        self._render_state.sub_analysis_type.value
                        if self._render_state.sub_analysis_type is not None
                        else None
                    )
                    grid, _ = map_categories(
                        raw, analysis_type=at_str, criteria=sub,
                    )
            else:
                # Numeric analyses: force float32 directly — numpy converts
                # JSON-null Nones to NaN.
                grid = np.array(grid_list, dtype=np.float32)
        except Exception as e:
            self._fail(f"download/extract failed: {e}", exc=e)
            return

        logger.info("SingleTilePoller: job %s succeeded, grid shape=%s",
                    job.job_id, grid.shape)
        try:
            self._on_render(self._render_state, self._polygon, self._area, grid)
        except Exception as e:
            logger.error("SingleTilePoller: render failed: %s", e, exc_info=True)
            _status(f"InfraredCity: render failed — {str(e)[:120]}",
                    level=Qgis.Critical, duration=15)

        self.finished.emit(grid)
        self.deleteLater()

    def _fail(self, msg: str, *, exc: Optional[Exception] = None) -> None:
        if exc is not None:
            logger.error("SingleTilePoller: %s", msg, exc_info=True)
        else:
            logger.error("SingleTilePoller: %s", msg)
        _status(f"InfraredCity: single-tile run failed — {msg[:120]}",
                level=Qgis.Critical, duration=15)
        self._timer.stop()
        self.failed.emit(msg)
        self.deleteLater()


def _single_tile_geometries(area) -> dict:
    """Serialise area buildings into the ``geometries`` payload dict.

    For a single tile the polygon-bbox-SW metre frame produced by
    ``collect_qgis_area_buildings`` *is* the tile-SW frame, so no coordinate
    transform is needed — just the same ``model_dump(by_alias=True)``
    serialisation ``resolve_layers`` applies before submission.
    """
    geometries: dict = {}
    for key, mesh in area.buildings.items():
        geometries[key] = (
            mesh.model_dump(by_alias=True)
            if hasattr(mesh, "model_dump") else mesh
        )
    return geometries


def run_sdk_single_tile_async(dlg, polygon: dict, area) -> "Optional[SingleTilePoller]":
    """Submit a single-tile analysis via ``analyses.execute`` and poll async.

    Builds the payload from the dialog, attaches buildings (and vegetation
    when the analysis supports it), submits one job, and hands it to a
    :class:`SingleTilePoller`. Returns the poller, or ``None`` if payload
    validation or submission failed (a message was already shown) so the
    caller can keep the dialog open.
    """
    payload = _prepare_run(dlg)
    if payload is None:
        return None

    geometries = _single_tile_geometries(area)
    if not geometries:
        # No building geometry — don't submit. An empty single-tile run either
        # fails server-side or returns a meaningless result after consuming
        # time/tokens. (A 0-building tile should already be rejected at pick
        # time in the Select-tile dialog, but guard here too — it's the
        # authoritative check right before submission.)
        logger.warning("Single-tile: no building geometry — aborting before submission")
        _status(
            "InfraredCity: no buildings in the selected tile — nothing to simulate",
            level=Qgis.Warning, duration=10,
        )
        QMessageBox.warning(
            dlg, "No Buildings",
            "The selected tile contains no buildings, so there is nothing to "
            "simulate. Pick a tile with buildings (and make sure the buildings "
            "layer is active) before running.",
        )
        return None
    payload = payload.model_copy(update={"geometries": geometries}, deep=True)
    logger.info("Single-tile: attached %d building meshes", len(geometries))

    # Vegetation — only when a tree layer is picked and the analysis supports
    # it. Passed as WGS84 Feature dicts; the server localises via the
    # payload's lat/lon reference point.
    vegetation: Optional[dict] = None
    tree_layer = None
    try:
        tree_layer = selected_tree_layer(dlg.tree_layer_dropdown)
    except AttributeError:
        pass
    if tree_layer is not None and has_tree_support(dlg.analysis_type):
        try:
            vegetation = collect_qgis_area_vegetation(
                polygon, tree_layer,
                use_catalog_type=getattr(dlg, "use_tree_catalog_type", False),
            ) or None
        except Exception as e:
            logger.warning("Single-tile: vegetation collection failed: %s", e,
                           exc_info=True)
            vegetation = None
    if vegetation:
        payload = payload.model_copy(update={"vegetation": vegetation}, deep=True)
        logger.info("Single-tile: attached %d vegetation features", len(vegetation))

    # Ground materials — only for analyses that use surface materials,
    # embedded directly in the payload ({material_name: FeatureCollection}).
    # Every feature must carry a properties.material stamp: run_area's tile
    # assignment would add it, but analyses.execute sends the payload as-is
    # and the Lambda's emissivity lookup needs it. The collector stamps its
    # own output; auto-fetched layers are stamped here.
    ground_materials: Optional[dict] = None
    if has_ground_material_support(dlg.analysis_type):
        if getattr(dlg, "use_infrared_ground_materials", False):
            _status("InfraredCity: fetching ground materials for the tile…")
            try:
                with InfraredClient(api_key=dlg.api_key) as gm_client:
                    area_gm = gm_client.ground_materials.get_area(polygon)
                if area_gm.layers:
                    ground_materials = stamp_material_properties(area_gm.layers)
            except Exception as e:
                logger.warning(
                    "Single-tile: ground materials auto-fetch failed — "
                    "running without: %s", e, exc_info=True,
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
                        "Single-tile: ground material collection failed: %s",
                        e, exc_info=True,
                    )
                    ground_materials = None
    if ground_materials:
        payload = payload.model_copy(
            update={"ground_materials": ground_materials}, deep=True,
        )
        logger.info(
            "Single-tile: attached ground materials (%s)",
            ", ".join(
                f"{k}={len(v.get('features', []))}"
                for k, v in ground_materials.items()
            ),
        )

    render_state = AreaRenderState.from_dialog(dlg)
    parent = iface.mainWindow() if iface is not None else None

    client = InfraredClient(api_key=dlg.api_key)
    _status("InfraredCity: submitting single-tile job…")
    try:
        job = client.analyses.execute(payload=payload)
    except Exception as e:
        logger.error("Single-tile execute() failed: %s", e, exc_info=True)
        _status(f"InfraredCity: single-tile submit failed — {str(e)[:120]}",
                level=Qgis.Critical, duration=15)
        QMessageBox.critical(
            dlg, "Simulation Error",
            f"Single-tile submission failed.\n\n{e}\n\n"
            "Check the plugin log for details.",
        )
        return None

    logger.info("Single-tile job submitted: %s", job.job_id)
    poller = SingleTilePoller(
        client=client,
        job=job,
        polygon=polygon,
        area=area,
        render_state=render_state,
        on_render=render_single_tile_result,
        parent=parent,
    )
    poller.finished.connect(lambda *_: _retire_poller(poller))
    poller.failed.connect(lambda *_: _retire_poller(poller))
    _ACTIVE_POLLERS.append(poller)
    poller.start()
    return poller
