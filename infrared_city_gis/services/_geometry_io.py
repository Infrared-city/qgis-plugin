"""Shared low-level helpers for ``collect_buildings`` and ``collect_trees``.

This module is private (underscore prefix). The public API lives in
``geometry.py``. Splitting these helpers out keeps each file under the 400-line
Infrared convention while letting both collectors share the same CRS / bbox /
feature-selection / file-writing primitives.
"""

import json
import os

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.utils import iface

from ..infrared_logger import logger
from .tiles import get_bbox


def pick_layers_by_keyword(keywords, prefer_active=False):
    """Return vector layers whose name contains any of ``keywords``.

    If ``prefer_active`` and the active layer matches one of the keywords,
    it's returned alone. Otherwise all matching layers are returned in
    project order.
    """
    project = QgsProject.instance()
    all_vlayers = [
        lyr for lyr in project.mapLayers().values()
        if isinstance(lyr, QgsVectorLayer)
    ]
    if not all_vlayers:
        return []

    if prefer_active:
        active = iface.activeLayer() if iface else None
        if isinstance(active, QgsVectorLayer) and any(k in active.name().lower() for k in keywords):
            return [active]

    return [
        lyr for lyr in all_vlayers
        if any(k in lyr.name().lower() for k in keywords)
    ]


def bbox_512_in_ref_crs(center_x, center_y, ref_crs):
    """Compute the 512×512 m bbox for a tile centre.

    Returns:
        ``(bbox_rect_wgs84, bbox_rect_ref, center_lon, center_lat)`` or
        ``None`` on transform failure.
    """
    project = QgsProject.instance()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")

    try:
        transform_to_wgs84 = QgsCoordinateTransform(ref_crs, wgs84, project)
        pt_wgs84 = transform_to_wgs84.transform(QgsPointXY(center_x, center_y))
    except Exception as e:
        logger.error("Transform tile center to WGS84 failed: %s", e)
        return None

    center_lon, center_lat = pt_wgs84.x(), pt_wgs84.y()

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

    return bbox_rect_wgs84, bbox_rect_ref, center_lon, center_lat


def select_features_in_bbox(layer, bbox_rect_wgs84):
    """Return features from ``layer`` whose geometry intersects the bbox.

    Handles CRS transformation between WGS84 (bbox) and the layer CRS. Uses a
    wide ``setFilterRect`` first (cheap), then filters with
    ``geometry.intersects`` (precise).
    """
    project = QgsProject.instance()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    layer_crs = layer.crs()

    if layer_crs.authid() != "EPSG:4326":
        try:
            transform_bbox = QgsCoordinateTransform(wgs84, layer_crs, project)
            bbox_rect_layer = transform_bbox.transformBoundingBox(bbox_rect_wgs84)
        except Exception as e:
            logger.error("BBox transform failed for layer %s: %s", layer.name(), e)
            return []
    else:
        bbox_rect_layer = bbox_rect_wgs84

    try:
        bbox_geom = QgsGeometry.fromRect(bbox_rect_layer)
        candidates = list(
            layer.getFeatures(QgsFeatureRequest().setFilterRect(bbox_rect_layer))
        )
        selected = []
        for feat in candidates:
            try:
                if feat.geometry().intersects(bbox_geom):
                    selected.append(feat)
            except Exception:
                # Malformed geometry — accept rather than silently drop.
                selected.append(feat)
        logger.info(
            "[select_features] Layer '%s': %d candidates → %d intersecting.",
            layer.name(), len(candidates), len(selected),
        )
        return selected
    except Exception as e:
        logger.error("Selection failed for layer %s: %s", layer.name(), e)
        return []


def compute_bbox_256(bbox_rect_ref, center_x, center_y, ref_crs):
    """Compute the inner 256×256 m bbox in the ref CRS.

    For projected CRSes the half-extents are exact metres; for geographic
    CRSes we detour through a local AEQD projection so the inner bbox is a
    square in metres rather than degrees.
    """
    project = QgsProject.instance()
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
        return (sw.x(), sw.y(), ne.x(), ne.y())

    return (center_x - half, center_y - half, center_x + half, center_y + half)


def ensure_data_dir():
    plugin_data_dir = os.path.join(
        QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "data"
    )
    os.makedirs(plugin_data_dir, exist_ok=True)
    return plugin_data_dir


def write_geojson(geojson_dict, plugin_data_dir, geometry_type_value, date_now, idx):
    path = os.path.join(
        plugin_data_dir,
        f"infrared_city_geometries_{geometry_type_value}_{date_now}_tile_{idx}.geojson",
    )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(geojson_dict, f, ensure_ascii=False, indent=2)
    return path


def write_dotbim(dotbim_data, plugin_data_dir, geometry_type_value, date_now, idx):
    path = os.path.join(
        plugin_data_dir,
        f"infrared_city_geometries_{geometry_type_value}_{date_now}_tile_{idx}.bim",
    )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dotbim_data, f, ensure_ascii=False, indent=2)
    return path


def notify_user(message, level=Qgis.Warning, duration=10):
    """Push a short message to the QGIS message bar (no-op outside QGIS)."""
    try:
        if iface is not None:
            iface.messageBar().pushMessage("InfraredCity", message, level=level, duration=duration)
    except Exception:
        pass
