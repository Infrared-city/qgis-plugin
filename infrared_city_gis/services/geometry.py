"""Geometry collection for Infrared City simulation tiles.

Other helpers have been split into dedicated modules:
  - geotiff.py  : crop_matrix, map_categories, generate_geotiff, _to_json_primitive
  - tiles.py    : get_bbox, tile center generation, CRS/bbox helpers
"""

import json
import os
from datetime import datetime

import numpy as np
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsUnitTypes,
    QgsVectorLayer,
)

from ..infrared_logger import logger
from ..models.analysis import GeometryTypes
from .geojson2dotbim import convert_tree_to_dotbim, process_geojson_file
from .geotiff import (  # noqa: F401 — re-exported for backward compat
    _to_json_primitive,
    crop_matrix,
    generate_geotiff,
    map_categories,
)
from .tiles import (  # noqa: F401 — re-exported for backward compat
    collect_tile_centers_from_selection,
    generate_tile_centers,
    get_bbox,
    get_center_from_bbox,
    get_center_lon_lat_from_bbox,
    get_selected_bbox,
    get_selected_crs,
    plot_tile_centers,
)


def collect_geometries(center_x, center_y, idx, geometry_type: GeometryTypes = GeometryTypes.BUILDINGS):
    """Collect geometries for a tile center from all matching vector layers.

    geometry_type:
        - BUILDINGS: prefers layers with 'building'/'buildings' in name.
        - TREES:     prefers layers with 'tree'/'trees'/'vegetation' in name.

    Returns (geojson_path, dotbim_path, bbox_512, crs_authid, bbox_256) or None.
    """
    date_now = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    project = QgsProject.instance()

    all_vector_layers = [
        lyr for lyr in project.mapLayers().values()
        if isinstance(lyr, QgsVectorLayer)
    ]
    if not all_vector_layers:
        logger.error("No vector layers found in project.")
        return None

    if geometry_type == GeometryTypes.TREES:
        keywords = ["tree", "trees", "vegetation"]
    else:
        keywords = ["building", "buildings"]

    target_layers = [
        lyr for lyr in all_vector_layers
        if any(k in lyr.name().lower() for k in keywords)
    ]
    if not target_layers:
        logger.warning(
            "No layers matched geometry_type '%s'; falling back to all vector layers.",
            geometry_type,
        )
        target_layers = all_vector_layers

    ref_layer = target_layers[0]
    ref_crs = ref_layer.crs()

    try:
        transform_to_wgs84 = QgsCoordinateTransform(ref_crs, wgs84, project)
        pt_wgs84 = transform_to_wgs84.transform(QgsPointXY(center_x, center_y))
    except Exception as e:
        logger.error("Transform tile center to WGS84 failed: %s", e)
        return None

    center_lon, center_lat = pt_wgs84.x(), pt_wgs84.y()
    logger.info("[collect_geometries] Tile center (WGS84): %.6f, %.6f", center_lon, center_lat)

    try:
        xmin, ymin, xmax, ymax = get_bbox(center_lon, center_lat, 512)
        bbox_rect_wgs84 = QgsRectangle(xmin, ymin, xmax, ymax)
    except Exception as e:
        logger.error("get_bbox failed: %s", e)
        return None

    if ref_crs.authid() != "EPSG:4326":
        transform_bbox_ref = QgsCoordinateTransform(wgs84, ref_crs, project)
        bbox_rect_ref = transform_bbox_ref.transformBoundingBox(bbox_rect_wgs84)
    else:
        bbox_rect_ref = bbox_rect_wgs84

    geojson_dict = {"type": "FeatureCollection", "features": []}
    total_features = 0

    for layer in target_layers:
        layer_crs = layer.crs()

        if layer_crs.authid() != "EPSG:4326":
            try:
                transform_bbox = QgsCoordinateTransform(wgs84, layer_crs, project)
                bbox_rect_layer = transform_bbox.transformBoundingBox(bbox_rect_wgs84)
            except Exception as e:
                logger.error("BBox transform failed for layer %s: %s", layer.name(), e)
                continue
        else:
            bbox_rect_layer = bbox_rect_wgs84

        try:
            # setFilterRect casts a wide net; then filter with exact intersects.
            # (selectByRect uses GEOS ExactIntersect and can miss large buildings.)
            bbox_geom = QgsGeometry.fromRect(bbox_rect_layer)
            candidates = list(layer.getFeatures(QgsFeatureRequest().setFilterRect(bbox_rect_layer)))
            selected_features = []
            for feat in candidates:
                try:
                    if feat.geometry().intersects(bbox_geom):
                        selected_features.append(feat)
                except Exception:
                    selected_features.append(feat)  # accept on invalid geometry

            if not selected_features:
                logger.info("[collect_geometries] Layer '%s': no features in tile bbox.", layer.name())
                continue

            logger.info(
                "[collect_geometries] Layer '%s': %d candidates, %d intersecting.",
                layer.name(), len(candidates), len(selected_features),
            )
        except Exception as e:
            logger.error("Selection failed for layer %s tile %d: %s", layer.name(), idx, e)
            continue

        fields = [field.name() for field in layer.fields()]

        for feat in selected_features:
            geom = feat.geometry()
            geom_wgs84 = QgsGeometry(geom)
            if layer_crs.authid() != "EPSG:4326":
                t = QgsCoordinateTransform(layer_crs, wgs84, project)
                geom_wgs84.transform(t)

            attr_values = feat.attributes()
            raw_props = {fields[i]: attr_values[i] for i in range(len(fields))}
            props = {k: _to_json_primitive(v) for k, v in raw_props.items()}
            props["source_layer"] = layer.name()
            props["geometry_type"] = geometry_type.value

            h = geom.boundingBox().height()
            if layer_crs.mapUnits() == QgsUnitTypes.DistanceDegrees:
                h *= 111_000
            props["height"] = round(h, 2)

            geojson_dict["features"].append({
                "type": "Feature",
                "geometry": json.loads(geom_wgs84.asJson()),
                "properties": props,
            })
            total_features += 1

    if total_features == 0:
        logger.info("[collect_geometries] Tile %d: no features found.", idx)
        return None

    plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "data")
    os.makedirs(plugin_data_dir, exist_ok=True)

    geojson_path = os.path.join(
        plugin_data_dir,
        f"infrared_city_geometries_{geometry_type.value}_{date_now}_tile_{idx}.geojson",
    )
    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(geojson_dict, f, ensure_ascii=False, indent=2)

    if geometry_type == GeometryTypes.BUILDINGS:
        dotbim_data = process_geojson_file(geojson_dict, center_lon, center_lat, "EPSG:4326")
    else:
        dotbim_data = convert_tree_to_dotbim(geojson_dict, center_lon, center_lat, "EPSG:4326")
        logger.info("Trees converted to dotbim for tile %d", idx)

    dotbim_path = os.path.join(
        plugin_data_dir,
        f"infrared_city_geometries_{geometry_type.value}_{date_now}_tile_{idx}.bim",
    )
    with open(dotbim_path, "w", encoding="utf-8") as f:
        json.dump(dotbim_data, f, ensure_ascii=False, indent=2)

    logger.info(
        "[collect_geometries] Tile %d: %d features → %s",
        idx, total_features, geojson_path,
    )

    bbox_512 = (
        bbox_rect_ref.xMinimum(), bbox_rect_ref.yMinimum(),
        bbox_rect_ref.xMaximum(), bbox_rect_ref.yMaximum(),
    )

    half = 128.0  # 256m tile → 128m half
    if ref_crs.isGeographic():
        cx = (bbox_rect_ref.xMinimum() + bbox_rect_ref.xMaximum()) / 2.0
        cy = (bbox_rect_ref.yMinimum() + bbox_rect_ref.yMaximum()) / 2.0
        local_crs = QgsCoordinateReferenceSystem.fromProj4(
            f"+proj=aeqd +lat_0={cy} +lon_0={cx} +ellps=WGS84 +units=m +type=crs"
        )
        to_local = QgsCoordinateTransform(ref_crs, local_crs, project)
        cx_m, cy_m = to_local.transform(center_x, center_y)
        to_ref = QgsCoordinateTransform(local_crs, ref_crs, project)
        sw = to_ref.transform(cx_m - half, cy_m - half)
        ne = to_ref.transform(cx_m + half, cy_m + half)
        bbox_256 = (sw.x(), sw.y(), ne.x(), ne.y())
    else:
        bbox_256 = (center_x - half, center_y - half, center_x + half, center_y + half)

    crs_authid = ref_crs.authid()
    logger.info("[collect_geometries] Tile %d: CRS=%s bbox_512=%s bbox_256=%s", idx, crs_authid, bbox_512, bbox_256)

    return geojson_path, dotbim_path, bbox_512, crs_authid, bbox_256
