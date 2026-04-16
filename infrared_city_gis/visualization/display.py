import math

from PyQt5.QtGui import QColor
from qgis.core import (
    QgsVectorLayer,
    QgsRasterLayer,
    QgsProject,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsFillSymbol,
    QgsRasterRange,
)

from ..infrared_logger import logger
from .color_ramp import get_visual_config, _build_color_ramp_items
from .layers import deselect_all, display_geojson, display_ground_materials  # noqa: F401 re-exported


def add_geojson_then_raster(
    geojson_path,
    geotiff_path,
    analysis_type,
    sub_analysis_type,
    raster_opacity=0.7,
    min_legend_value=None,
    max_legend_value=None,
    tile_id=None
):
    logger.info("Adding GeoJSON layer: %s", geojson_path)
    logger.info("Adding GeoTIFF layer: %s", geotiff_path)

    visual_config = get_visual_config(analysis_type, sub_analysis_type)
    if not visual_config:
        raise RuntimeError(
            f"Visual configuration not found for analysis type: {analysis_type}, "
            f"sub analysis type: {sub_analysis_type}"
        )

    logger.info(f"Visual configuration: {visual_config}")

    # --- GeoJSON layer ---
    vlayer = QgsVectorLayer(geojson_path, "Infrared Buildings", "ogr")
    if not vlayer.isValid():
        raise RuntimeError(f"GeoJSON layer loading failed: {geojson_path}")

    fill_sym = QgsFillSymbol.createSimple({
        "color": "0,0,0,0",
        "outline_color": "0,0,0",
        "outline_width": "0.8",
    })
    vlayer.renderer().setSymbol(fill_sym)
    QgsProject.instance().addMapLayer(vlayer)

    # --- GeoTIFF layer ---
    rlayer = QgsRasterLayer(geotiff_path, f"IC result - {analysis_type}{tile_id}", "gdal")
    if not rlayer.isValid():
        raise RuntimeError(f"GeoTIFF layer loading failed: {geotiff_path}")

    # Handle no-data values
    provider = rlayer.dataProvider()
    try:
        nodata = provider.sourceNoDataValue(1)
        if isinstance(nodata, (int, float)) and not math.isnan(nodata):
            provider.setUserNoDataValues(1, [QgsRasterRange(nodata, nodata)])
            logger.info("User no-data range set for band 1: %s", nodata)
    except Exception as e:
        logger.warning("Could not set no-data value: %s", e)

    # --- Min / Max values ---
    stats = rlayer.dataProvider().bandStatistics(1)
    vmin, vmax = stats.minimumValue, stats.maximumValue
    logger.info("Original raster stats: min=%s, max=%s", vmin, vmax)

    if min_legend_value is not None:
        vmin = min_legend_value
        logger.info("Using min_legend_value: %s", min_legend_value)
    if max_legend_value is not None:
        vmax = max_legend_value
        logger.info("Using max_legend_value: %s", max_legend_value)

    if vmin is None or vmax is None:
        logger.warning("Raster band statistics not available; using default 0..1")
        vmin, vmax = 0.0, 1.0

    # --- Build and apply color ramp ---
    shader, color_items, step_vmin, step_vmax = _build_color_ramp_items(
        visual_config, analysis_type, vmin=vmin, vmax=vmax
    )
    logger.info("Color items: %s", color_items)
    logger.info("Shader range: min=%s, max=%s", step_vmin, step_vmax)

    raster_shader = QgsRasterShader()
    raster_shader.setRasterShaderFunction(shader)
    raster_shader.setMinimumValue(step_vmin)
    raster_shader.setMaximumValue(step_vmax)

    renderer = QgsSingleBandPseudoColorRenderer(rlayer.dataProvider(), 1, raster_shader)
    renderer.setClassificationMin(step_vmin)
    renderer.setClassificationMax(step_vmax)
    rlayer.setRenderer(renderer)
    rlayer.setOpacity(float(raster_opacity))

    QgsProject.instance().addMapLayer(rlayer)

    # --- Vector on top of raster ---
    try:
        root = QgsProject.instance().layerTreeRoot()
        v_node = root.findLayer(vlayer.id())
        if v_node is not None:
            parent = v_node.parent()
            parent.removeChildNode(v_node)
            root.insertChildNode(0, v_node)
    except Exception as e:
        logger.warning("Could not reorder layers automatically: %s", e)

    # --- PWC mapping debug ---
    if analysis_type == "pedestrian-wind-comfort":
        try:
            import numpy as np
            from osgeo import gdal

            logger.info("=== PWC color ramp mapping ===")
            for item in color_items:
                logger.info("  value=%.0f  →  label='%s'  color=%s",
                            item.value, item.label, item.color.name())

            ds = gdal.Open(geotiff_path)
            if ds:
                band = ds.GetRasterBand(1)
                arr = band.ReadAsArray()
                nodata_val = band.GetNoDataValue()
                ds = None
                if arr is not None:
                    flat = arr.flatten().astype(float)
                    if nodata_val is not None:
                        flat = flat[flat != nodata_val]
                    # NaN = buildings/outside area — log once, then exclude
                    nan_count = int(np.sum(np.isnan(flat)))
                    if nan_count:
                        logger.info("=== PWC has %d NaN pixels (buildings/outside) — skipped", nan_count)
                    valid = flat[~np.isnan(flat)]
                    unique_vals = sorted(np.unique(valid).tolist())
                    logger.info("=== PWC unique valid values in raster: %s", unique_vals)
                    item_map = {int(round(it.value)): it.label for it in color_items}
                    logger.info("=== PWC raster value → label ===")
                    for v in unique_vals:
                        iv = int(round(v))
                        label = item_map.get(iv, f"<no match for {iv}>")
                        logger.info("  %d → '%s'", iv, label)
        except Exception as e:
            logger.warning("PWC mapping debug failed: %s", e)

    logger.info("✅ GeoJSON and colorized GeoTIFF added. Raster opacity=%s", raster_opacity)
