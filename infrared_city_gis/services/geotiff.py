"""GeoTIFF generation utilities for Infrared City simulation results."""

import os
import math
import time

import numpy as np
from osgeo import gdal, osr

from ..infrared_logger import logger


def _release_existing_layer(output_path: str) -> None:
    """Detach any QgsRasterLayer in the current project that points at
    ``output_path``, so the OS releases the file handle before we try to
    overwrite it.

    On Windows, GDAL/QGIS holds an exclusive read lock on a loaded raster's
    backing .tif. Without this, ``os.remove`` and ``gdal.Driver.Create`` will
    fail with WinError 32 / "Permission denied". POSIX systems unlink open
    files happily, so this is effectively a no-op there.
    """
    try:
        # Imported lazily so unit tests / non-QGIS contexts don't blow up.
        from qgis.core import QgsProject, QgsRasterLayer
        from qgis.PyQt.QtWidgets import QApplication
    except Exception as e:  # pragma: no cover — only hit outside QGIS
        logger.debug("Skipping layer detach (QGIS not available): %s", e)
        return

    try:
        target = os.path.normcase(os.path.abspath(output_path))
        proj = QgsProject.instance()
        ids_to_remove = []
        for lyr in proj.mapLayers().values():
            if not isinstance(lyr, QgsRasterLayer):
                continue
            try:
                src = os.path.normcase(os.path.abspath(lyr.source()))
            except Exception:
                continue
            if src == target:
                ids_to_remove.append(lyr.id())

        for lid in ids_to_remove:
            proj.removeMapLayer(lid)
            logger.info("Detached existing raster layer holding %s", output_path)

        if ids_to_remove:
            # Let Qt actually process the layer-removed signal so the GDAL
            # provider releases the file handle before we try to delete.
            QApplication.processEvents()
    except Exception as e:
        logger.warning("Could not detach existing raster layer for %s: %s", output_path, e)


def _force_remove(path: str, attempts: int = 5, delay: float = 0.1) -> None:
    """Best-effort delete with retry. On Windows the file lock can take a
    moment to release after the QGIS layer is removed."""
    if not os.path.exists(path):
        return
    last_err = None
    for i in range(attempts):
        try:
            os.remove(path)
            return
        except OSError as e:
            last_err = e
            time.sleep(delay)
    logger.warning("Failed to remove existing GeoTIFF %s after %d attempts: %s",
                   path, attempts, last_err)


def crop_matrix(matrix: np.ndarray, core_size=256):
    h, w = matrix.shape
    start_row = (h - core_size) // 2
    start_col = (w - core_size) // 2
    return matrix[start_row:start_row + core_size, start_col:start_col + core_size]


def map_categories(matrix: np.ndarray, analysis_type: str = None, criteria: str = None):
    """Map a PWC category matrix to 1-based float32 GeoTIFF values.

    Uses the ``steps`` order from ``model_registry.json`` for the given
    ``(analysis_type, criteria)`` (e.g. ``pedestrian-wind-comfort`` /
    ``lawson-2001``). Each step is assigned its 1-based position in the array:

        steps = ["A","B","C","D","E"]
            → A→1, B→2, C→3, D→4, E→5

        steps = ["A","B","C","D","E","S"]
            → A→1, B→2, C→3, D→4, E→5, S→6

        steps = ["A","B","C","D","E","S15","S20"]
            → A→1, B→2, C→3, D→4, E→5, S15→6, S20→7

    Handles two API response shapes transparently:
      - Letter strings (``"A"``, ``"S15"``, …): looked up in ``steps``.
      - Numeric strings (``"0"``–``"N-1"``): already 0-based indices into
        ``steps``. Shifted to 1-based (``"0"``→1, ``"1"``→2, …).

    null / ``"None"`` / ``"NaN"`` / ``"nan"`` / ``"null"`` / ``""`` → NaN
    (buildings / outside area — not written to the raster).

    If the registry is unavailable or the analysis_type/criteria is unknown,
    falls back to: numeric mode → original value + 1; string mode → sorted
    alphabetical positions 1, 2, 3, …
    """
    nodata_strings = {"None", "NaN", "nan", "null", ""}
    unique_cats = np.unique(matrix)
    non_nodata = [c for c in unique_cats if c not in nodata_strings]

    # Detect format: are all non-nodata values numeric?
    numeric_pairs = []
    all_numeric = True
    for cat in non_nodata:
        try:
            numeric_pairs.append((cat, float(cat)))
        except (ValueError, TypeError):
            all_numeric = False
            break

    # Try to load the canonical step order from the model registry.
    steps = None
    if analysis_type and criteria:
        try:
            from ..visualization.color_ramp import get_visual_config
            cfg = get_visual_config(analysis_type, criteria)
            if cfg and cfg.get("steps"):
                steps = [str(s) for s in cfg["steps"]]
                logger.info(
                    "map_categories: registry steps for %s/%s: %s",
                    analysis_type, criteria, steps,
                )
            else:
                logger.warning(
                    "map_categories: no steps in registry for %s/%s",
                    analysis_type, criteria,
                )
        except Exception as e:
            logger.warning(
                "map_categories: registry lookup failed (%s/%s): %s",
                analysis_type, criteria, e,
            )

    mapping: dict = {}
    for cat in unique_cats:
        if cat in nodata_strings:
            mapping[cat] = np.nan

    if all_numeric:
        # Numeric indices into steps → 1-based position (index + 1).
        for cat, val in numeric_pairs:
            mapping[cat] = val + 1.0
        logger.info(
            "map_categories: numeric mode — %s",
            {c: v for c, v in numeric_pairs},
        )
    elif steps:
        # Letter mode with registry-provided step order.
        step_index = {s: i + 1 for i, s in enumerate(steps)}
        missing = []
        for cat in non_nodata:
            if cat in step_index:
                mapping[cat] = float(step_index[cat])
            else:
                missing.append(cat)
        if missing:
            # Unknown categories: append after known steps so colors don't
            # collide. This is defensive — normally the API only emits
            # values listed in the registry's steps.
            base = len(steps)
            for i, cat in enumerate(sorted(missing), start=1):
                mapping[cat] = float(base + i)
            logger.warning(
                "map_categories: categories not in registry steps: %s",
                missing,
            )
        logger.info(
            "map_categories: registry mode — %s",
            {c: mapping[c] for c in non_nodata},
        )
    else:
        # Fallback: sorted alphabetical → positions 1, 2, 3, …
        for i, cat in enumerate(sorted(non_nodata), start=1):
            mapping[cat] = float(i)
        logger.info(
            "map_categories: alphabetical fallback — %s",
            {c: mapping[c] for c in sorted(non_nodata)},
        )

    mapped = np.full(matrix.shape, np.nan, dtype=np.float32)
    for cat, val in mapping.items():
        mapped[matrix == cat] = val

    return mapped, mapping


def _to_json_primitive(value):
    """Recursively convert QGIS/Qt types (e.g. QVariant) to JSON-serializable
    Python primitives. Handles nested lists/tuples/dicts.
    """
    from qgis.PyQt.QtCore import QVariant

    if isinstance(value, QVariant):
        try:
            if hasattr(value, "value"):
                value = value.value()
        except Exception:
            return str(value)

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_to_json_primitive(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_json_primitive(v) for k, v in value.items()}
    return str(value)


def generate_geotiff(
    matrix: np.ndarray,
    bbox: tuple,
    crs: str,
    output_path: str,
    simulation_type: str = "unknown",
    criteria: str = None,
):
    logger.info(
        "Generate_geotiff bbox: %s, crs: %s, output_path: %s, simulation_type: %s, criteria: %s",
        bbox, crs, output_path, simulation_type, criteria,
    )

    west, south, east, north = bbox
    height, width = matrix.shape
    pixel_width = (east - west) / width
    pixel_height = (north - south) / height
    geotransform = (west, pixel_width, 0, north, 0, -pixel_height)

    driver = gdal.GetDriverByName("GTiff")

    # Windows holds a file lock on any .tif that's loaded as a QgsRasterLayer
    # in the current project. Detach matching layers first, then delete the
    # file with a short retry loop so GDAL's Create() can re-create it cleanly.
    _release_existing_layer(output_path)
    _force_remove(output_path)

    ds = driver.Create(output_path, width, height, 1, gdal.GDT_Float32)
    if ds is None:
        raise RuntimeError(f"Failed to create GeoTIFF at {output_path}")

    ds.SetGeoTransform(geotransform)
    srs = osr.SpatialReference()
    srs.SetFromUserInput(crs)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)

    if simulation_type == "pedestrian-wind-comfort":
        if np.issubdtype(matrix.dtype, np.floating):
            # Already mapped float matrix (e.g. crop of a previously processed GeoTIFF).
            # Write directly — do NOT re-run map_categories or "nan" strings will
            # be treated as a new category instead of nodata.
            logger.info("PWC generate_geotiff: float matrix detected, skipping map_categories")
            band.WriteArray(matrix.astype(np.float32))
        else:
            # String matrix from API response — run category mapping.
            mapped_matrix, mapping_dict = map_categories(
                matrix, analysis_type=simulation_type, criteria=criteria
            )
            logger.info(
                "PWC generate_geotiff: string matrix, category mapping done (%s/%s): %s",
                simulation_type, criteria, mapping_dict,
            )

            # Per-category pixel counts. Sort by mapping_dict order (A, B, C, …)
            # so the log reads naturally; unmapped values appear at the end.
            unique_cats, cat_counts = np.unique(matrix, return_counts=True)
            counts = dict(zip(unique_cats.tolist(), cat_counts.tolist()))
            ordered_keys = list(mapping_dict.keys()) + [
                k for k in counts if k not in mapping_dict
            ]
            count_str = ", ".join(
                f"{k}: {counts[k]}" for k in ordered_keys if k in counts
            )
            logger.info("PWC generate_geotiff: category counts -> %s", count_str)

            band.WriteArray(mapped_matrix.astype(np.float32))
        band.SetDescription(simulation_type)
        band.SetNoDataValue(math.nan)
        md = {
            "simulation_type": simulation_type,
            "criteria": criteria,
            "no_data": str(math.nan),
            "AREA_OR_POINT": "Point",
        }
        ds.SetMetadata(md)
    else:
        band.WriteArray(matrix.astype(np.float32))
        band.SetDescription(simulation_type)
        band.SetNoDataValue(math.nan)
        md = {
            "simulation_type": simulation_type,
            "no_data": str(math.nan),
            "AREA_OR_POINT": "Point",
        }
        ds.SetMetadata(md)

    band.FlushCache()
    ds = None
