"""Driving one single-tile simulation the way the plugin drives it.

Split out of ``test_e2e_workflow.py`` so that file is about what we assert, and
this one about how a run is produced. The reference site and the pinned inputs
live here too, because they are part of "how the run was made" — a baseline is
only meaningful next to them.
"""

import math
import time

from _baseline import record
from _fake_dialog import NEEDS_WEATHER, FakeRunDialog

# Vienna, the reference tile the baselines were recorded on. A simulation result
# only means anything next to the inputs that produced it, so the site, the tile
# size and the weather file are pinned here, the analysis inputs in
# _fake_dialog.DEFAULTS, and all of them are written into the baseline file and
# compared exactly.
LON, LAT = 16.373, 48.215
AREA_SIZE_M = 1024.0
TILE_SIZE_M = 512.0   # a single-tile run covers one 512 m tile
BASELINE = "vienna_16.373_48.215"
WEATHER_FILE = "AUT_NO_Gros.Enzersdorf.110370_TMYx.2009-2023"
SITE = f"lon {LON} lat {LAT} tile {TILE_SIZE_M:g}m area {AREA_SIZE_M:g}m"

#: How long to wait for one job before calling it stuck.
JOB_TIMEOUT_S = 600


def polygon(lon, lat, size_m):
    """Square GeoJSON polygon of ``size_m`` centred on (lon, lat)."""
    half_lat = (size_m / 2) / 111_320.0
    half_lon = half_lat / max(math.cos(math.radians(lat)), 1e-6)
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon - half_lon, lat - half_lat], [lon + half_lon, lat - half_lat],
            [lon + half_lon, lat + half_lat], [lon - half_lon, lat + half_lat],
            [lon - half_lon, lat - half_lat],
        ]],
    }


def _await_result(client, job):
    """Poll one job to completion and return its decompressed result."""
    from infrared_sdk.analyses.jobs import JobsServiceClient, JobStatus

    deadline = time.monotonic() + JOB_TIMEOUT_S
    while True:
        job = client.jobs.get_status(job.job_id)
        if job.status == JobStatus.succeeded:
            break
        if job.status == JobStatus.failed:
            raise AssertionError(f"job failed: {job.error or '(no message)'}")
        if time.monotonic() > deadline:
            raise AssertionError(f"job timed out (last status {job.status})")
        time.sleep(5)

    download = client.jobs.download_results(job.job_id, _job=job)
    return JobsServiceClient.decompress(download.content)


def run_single_tile(api_key, analysis_type, *, weather_file, ground):
    """Submit one single-tile run of ``analysis_type`` and return its grid stats.

    ``ground`` selects how surfaces are supplied, and is part of the run's
    recorded configuration because it changes the result:

    * ``None``   — none at all
    * ``"auto"`` — fetched from the platform at submit time (the auto path)
    * a ``{material: [QgsVectorLayer]}`` mapping — collected from those layers

    Goes through the plugin's own builders — ``build_sdk_payload``,
    ``collect_qgis_area_buildings``, the two ground-material paths and
    ``grid_from_result`` — so a regression in any of them shows up here. Only
    the submit/poll loop is this module's own, because the plugin drives that
    from a QTimer.
    """
    import numpy as np
    from infrared_sdk import InfraredClient

    from infrared_city_gis.services.ground_materials import (
        collect_ground_materials,
        stamp_material_properties,
    )
    from infrared_city_gis.services.qgis_area_buildings import (
        collect_qgis_area_buildings,
    )
    from infrared_city_gis.services.sdk_payloads import build_sdk_payload
    from infrared_city_gis.services.sdk_single_tile import (
        _single_tile_geometries,
        grid_from_result,
    )

    tile = polygon(LON, LAT, TILE_SIZE_M)
    ring = tile["coordinates"][0]
    auto = ground == "auto"

    dlg = FakeRunDialog(
        analysis_type,
        api_key=api_key,
        bbox=(ring[0][0], ring[0][1], ring[2][0], ring[2][1]),
        crs="EPSG:4326",
        weather_file=weather_file if analysis_type in NEEDS_WEATHER else "",
        use_infrared_ground_materials=auto,
        ground_material_layers=None if auto or ground is None else ground,
    )

    payload = build_sdk_payload(dlg)
    assert payload is not None, "the payload builder rejected the fake dialog's inputs"

    area = collect_qgis_area_buildings(tile)
    geometries = _single_tile_geometries(area)
    assert geometries, "no building meshes collected for the tile"
    payload = payload.model_copy(update={"geometries": geometries}, deep=True)

    if auto:
        with InfraredClient(api_key=api_key) as client:
            area_gm = client.ground_materials.get_area(tile)
        materials = stamp_material_properties(area_gm.layers)
        ground_label = "auto-fetch"
    elif ground is None:
        materials = {}
        ground_label = "none"
    else:
        materials = collect_ground_materials(tile, ground)
        ground_label = "+".join(sorted(ground))
        assert materials, f"no ground materials collected from {sorted(ground)}"

    if materials:
        payload = payload.model_copy(update={"ground_materials": materials}, deep=True)

    record(
        f"payload.{analysis_type}.{ground_label}",
        "ok",
        f"{len(geometries)} meshes, materials {list(materials) or 'none'}",
    )

    client = InfraredClient(api_key=api_key)
    result = _await_result(client, client.analyses.execute(payload=payload))

    # Same normalisation the poller renders — matters for PWC, whose grid comes
    # back as Lawson letter classes rather than numbers.
    grid = np.asarray(
        grid_from_result(result, analysis_type, dlg.sub_analysis_type), dtype=float,
    )
    assert grid.size, f"{analysis_type}: empty result grid"
    assert grid[~np.isnan(grid)].size, f"{analysis_type}: result grid is entirely NaN"

    return {
        "min": float(np.nanmin(grid)),
        "max": float(np.nanmax(grid)),
        "mean": float(np.nanmean(grid)),
        "config": f"{dlg.input_signature()}, ground={ground_label}",
    }
