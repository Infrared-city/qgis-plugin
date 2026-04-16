"""GeoTIFF generation utilities for Infrared City simulation results."""

import os
import math

import numpy as np
from osgeo import gdal, osr

from ..infrared_logger import logger


def crop_matrix(matrix: np.ndarray, core_size=256):
    h, w = matrix.shape
    start_row = (h - core_size) // 2
    start_col = (w - core_size) // 2
    return matrix[start_row:start_row + core_size, start_col:start_col + core_size]


def map_categories(matrix: np.ndarray):
    """Map PWC string category matrix to float32 GeoTIFF values (1-based).

    Handles two API formats transparently:

    Numeric strings ("0"–"4"):
        Preserves the original value + 1 so no 0 is stored in the GeoTIFF
        (0 could be confused with nodata). A tile that only contains "3"
        and "4" maps them to 4.0 and 5.0 (D and E), not 1.0 and 2.0.
            "0"→1.0 (A), "1"→2.0 (B), "2"→3.0 (C), "3"→4.0 (D), "4"→5.0 (E)

    Letter strings ("A"–"E"):
        Sorted alphabetically and assigned positions 1, 2, 3, …
            "A"→1.0, "B"→2.0, "C"→3.0, "D"→4.0, "E"→5.0

    Both produce GeoTIFF values 1–5, matching the color ramp in color_ramp.py.
    null / "None" / "nan" → NaN  (buildings / outside area)
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

    mapping: dict = {}
    for cat in unique_cats:
        if cat in nodata_strings:
            mapping[cat] = np.nan

    if all_numeric:
        # Integer format: preserve original value, shift to 1-based
        for cat, val in numeric_pairs:
            mapping[cat] = val + 1.0
        logger.info("map_categories: numeric mode — %s", {c: v for c, v in numeric_pairs})
    else:
        # Letter format: sorted alphabetical → positions 1, 2, 3, …
        for i, cat in enumerate(sorted(non_nodata), start=1):
            mapping[cat] = float(i)
        logger.info("map_categories: string mode — %s", {c: mapping[c] for c in sorted(non_nodata)})

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
    criteria: str = "unknown",
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

    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except Exception as e:
            logger.warning("Failed to remove existing GeoTIFF %s: %s", output_path, e)

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
            mapped_matrix, mapping_dict = map_categories(matrix)
            logger.info("PWC generate_geotiff: string matrix, category mapping done")
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
