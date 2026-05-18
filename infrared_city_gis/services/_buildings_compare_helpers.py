"""Internal helpers for ``buildings_compare`` — projection, footprint extraction,
greedy matching and visual diff layer construction.

Split out of ``buildings_compare.py`` to keep each module under the 400-line
Infrared convention. Public callers should import from
``buildings_compare`` only.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant
from qgis.utils import iface

from ..infrared_logger import logger
from .feature_height import resolve_feature_height

# Same constant the SDK's tile/merger code uses — keep these in sync if the
# SDK ever switches to a more accurate ellipsoidal model.
METERS_PER_DEG_LAT = 111_320.0


# ---------------------------------------------------------------------------
# Projection (mirrors infrared_sdk/tiling/merger.py:_polygon_to_meters)
# ---------------------------------------------------------------------------


def projection_params(polygon: dict) -> Tuple[float, float, float]:
    """Return (origin_lon, origin_lat, meters_per_deg_lng)."""
    ring = polygon["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    origin_lon = min(lons)
    origin_lat = min(lats)
    center_lat = (min(lats) + max(lats)) / 2.0
    meters_per_deg_lng = METERS_PER_DEG_LAT * math.cos(math.radians(center_lat))
    return origin_lon, origin_lat, meters_per_deg_lng


def make_xy_converters(polygon: dict):
    """Return (lonlat→meters, meters→lonlat) closures for this polygon."""
    origin_lon, origin_lat, mpd_lng = projection_params(polygon)

    def to_meters(lon: float, lat: float) -> Tuple[float, float]:
        return (
            (lon - origin_lon) * mpd_lng,
            (lat - origin_lat) * METERS_PER_DEG_LAT,
        )

    def to_lonlat(x: float, y: float) -> Tuple[float, float]:
        return (
            origin_lon + x / mpd_lng,
            origin_lat + y / METERS_PER_DEG_LAT,
        )

    return to_meters, to_lonlat


# ---------------------------------------------------------------------------
# Footprint extraction
# ---------------------------------------------------------------------------


def _qgis_outer_ring(geom: QgsGeometry) -> List[QgsPointXY]:
    """Return the outer ring of a polygon/multipolygon geometry."""
    if geom.isMultipart():
        parts = geom.asMultiPolygon()
        if not parts:
            return []
        largest = max(parts, key=lambda p: len(p[0]) if p else 0)
        return list(largest[0])
    poly = geom.asPolygon()
    return list(poly[0]) if poly else []


def project_qgis_buildings(
    layer: QgsVectorLayer, polygon: dict, *, sample_limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return per-feature footprints in the polygon-bbox-SW frame."""
    to_meters, _ = make_xy_converters(polygon)
    layer_crs = layer.crs()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    transform: Optional[QgsCoordinateTransform] = (
        QgsCoordinateTransform(layer_crs, wgs84, QgsProject.instance())
        if layer_crs.isValid() and layer_crs != wgs84
        else None
    )

    out: List[Dict[str, Any]] = []
    # resolve_feature_height expects field NAMES (list of strings), not a
    # QgsFields container — same convention as services/geometry.py:151.
    field_names = [f.name() for f in layer.fields()]
    for n, feat in enumerate(layer.getFeatures()):
        if sample_limit and n >= sample_limit:
            break
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        ring = _qgis_outer_ring(geom)
        if len(ring) < 3:
            continue
        xy: List[Tuple[float, float]] = []
        for p in ring:
            if transform is not None:
                lon, lat = transform.transform(p.x(), p.y())
            else:
                lon, lat = p.x(), p.y()
            xy.append(to_meters(lon, lat))
        if len(xy) < 3:
            continue
        try:
            h = resolve_feature_height(feat, field_names, geom=geom, default=None)
        except Exception as e:
            logger.debug("resolve_feature_height failed on fid=%s: %s", feat.id(), e)
            h = None
        xs = [p[0] for p in xy]
        ys = [p[1] for p in xy]
        out.append({
            "fid": int(feat.id()),
            "footprint_xy": xy,
            "centroid_xy": (sum(xs) / len(xs), sum(ys) / len(ys)),
            "bbox_xy": (min(xs), min(ys), max(xs), max(ys)),
            "height": float(h) if h is not None else None,
        })
    return out


def project_sdk_buildings(area_buildings) -> List[Dict[str, Any]]:
    """Return per-building footprints already in the polygon-bbox-SW frame.

    DotBimMesh ``coordinates`` is flat ``[x, y, z, ...]``. Base footprint
    vertices are picked at z == z_min (1 cm tolerance), falling back to all
    xy if a building's mesh has no flat base.
    """
    out: List[Dict[str, Any]] = []
    buildings = getattr(area_buildings, "buildings", None) or {}
    for bid, mesh in buildings.items():
        coords = list(getattr(mesh, "coordinates", []) or [])
        if len(coords) < 9:
            continue
        verts = [
            (coords[i], coords[i + 1], coords[i + 2])
            for i in range(0, len(coords) - 2, 3)
        ]
        if not verts:
            continue
        z_min = min(v[2] for v in verts)
        z_max = max(v[2] for v in verts)
        base_xy = list({
            (round(v[0], 3), round(v[1], 3))
            for v in verts
            if abs(v[2] - z_min) < 0.01
        })
        if len(base_xy) < 3:
            base_xy = [(v[0], v[1]) for v in verts]
        xs = [p[0] for p in base_xy]
        ys = [p[1] for p in base_xy]
        out.append({
            "bid": str(bid),
            "footprint_xy": base_xy,
            "centroid_xy": (sum(xs) / len(xs), sum(ys) / len(ys)),
            "bbox_xy": (min(xs), min(ys), max(xs), max(ys)),
            "height": float(z_max - z_min),
        })
    return out


# ---------------------------------------------------------------------------
# Greedy nearest-centroid matching
# ---------------------------------------------------------------------------


def greedy_centroid_match(
    qgis_buildings: List[Dict[str, Any]],
    sdk_buildings: List[Dict[str, Any]],
    *,
    threshold_m: float,
) -> Tuple[List[Dict[str, Any]], List[int], List[str]]:
    """Greedy nearest-centroid assignment from QGIS → SDK.

    Returns (matched, qgis_unmatched_fids, sdk_unmatched_ids). Each match
    consumes one SDK building (no SDK building is matched twice).
    """
    matched: List[Dict[str, Any]] = []
    sdk_used: set = set()

    for q in qgis_buildings:
        qcx, qcy = q["centroid_xy"]
        best: Optional[Dict[str, Any]] = None
        best_d = float("inf")
        for s in sdk_buildings:
            if s["bid"] in sdk_used:
                continue
            scx, scy = s["centroid_xy"]
            d = math.hypot(scx - qcx, scy - qcy)
            if d < best_d:
                best_d = d
                best = s
        if best is not None and best_d <= threshold_m:
            sdk_used.add(best["bid"])
            qbx = q["bbox_xy"]
            sbx = best["bbox_xy"]
            qa = (qbx[2] - qbx[0]) * (qbx[3] - qbx[1])
            sa = (sbx[2] - sbx[0]) * (sbx[3] - sbx[1])
            qh = q["height"]
            sh = best["height"]
            matched.append({
                "qgis_fid": q["fid"],
                "sdk_id": best["bid"],
                "centroid_distance_m": best_d,
                "area_diff_m2": qa - sa,
                "height_diff_m": (qh - sh) if qh is not None else None,
            })

    matched_qgis_fids = {m["qgis_fid"] for m in matched}
    qgis_unmatched = [q["fid"] for q in qgis_buildings if q["fid"] not in matched_qgis_fids]
    sdk_unmatched = [s["bid"] for s in sdk_buildings if s["bid"] not in sdk_used]
    return matched, qgis_unmatched, sdk_unmatched


# ---------------------------------------------------------------------------
# Visual diff (three memory layers added to the QGIS project)
# ---------------------------------------------------------------------------


def _make_polygon_layer(
    name: str, fields_def: List[Tuple[str, "QVariant.Type"]],
) -> QgsVectorLayer:
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", name, "memory")
    pr = layer.dataProvider()
    fields = QgsFields()
    for fname, ftype in fields_def:
        fields.append(QgsField(fname, ftype))
    pr.addAttributes(fields)
    layer.updateFields()
    return layer


def _xy_ring_to_geom(xy: List[Tuple[float, float]], to_lonlat) -> QgsGeometry:
    pts = [QgsPointXY(*to_lonlat(x, y)) for (x, y) in xy]
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return QgsGeometry.fromPolygonXY([pts])


def add_diff_layers(
    qgis_buildings: List[Dict[str, Any]],
    sdk_buildings: List[Dict[str, Any]],
    matched: List[Dict[str, Any]],
    polygon: dict,
) -> None:
    """Add three visual diff layers to the QGIS project."""
    _, to_lonlat = make_xy_converters(polygon)
    matched_qgis_fids = {m["qgis_fid"] for m in matched}
    matched_sdk_ids = {m["sdk_id"] for m in matched}

    matched_layer = _make_polygon_layer(
        "Buildings — matched (QGIS ∩ SDK)",
        [("qgis_fid", QVariant.Int), ("sdk_id", QVariant.String),
         ("centroid_dist_m", QVariant.Double)],
    )
    qgis_only_layer = _make_polygon_layer(
        "Buildings — QGIS only", [("fid", QVariant.Int)],
    )
    sdk_only_layer = _make_polygon_layer(
        "Buildings — SDK only (Mapbox)", [("sdk_id", QVariant.String)],
    )

    by_qgis_fid = {q["fid"]: q for q in qgis_buildings}

    feats_matched = []
    for m in matched:
        q = by_qgis_fid.get(m["qgis_fid"])
        if q is None:
            continue
        f = QgsFeature()
        f.setGeometry(_xy_ring_to_geom(q["footprint_xy"], to_lonlat))
        f.setAttributes([m["qgis_fid"], m["sdk_id"], float(m["centroid_distance_m"])])
        feats_matched.append(f)
    matched_layer.dataProvider().addFeatures(feats_matched)

    feats_qgis = []
    for q in qgis_buildings:
        if q["fid"] in matched_qgis_fids:
            continue
        f = QgsFeature()
        f.setGeometry(_xy_ring_to_geom(q["footprint_xy"], to_lonlat))
        f.setAttributes([q["fid"]])
        feats_qgis.append(f)
    qgis_only_layer.dataProvider().addFeatures(feats_qgis)

    feats_sdk = []
    for s in sdk_buildings:
        if s["bid"] in matched_sdk_ids:
            continue
        f = QgsFeature()
        f.setGeometry(_xy_ring_to_geom(s["footprint_xy"], to_lonlat))
        f.setAttributes([s["bid"]])
        feats_sdk.append(f)
    sdk_only_layer.dataProvider().addFeatures(feats_sdk)

    prev_active = iface.activeLayer() if iface else None
    project = QgsProject.instance()
    for lyr in (matched_layer, qgis_only_layer, sdk_only_layer):
        lyr.updateExtents()
        project.addMapLayer(lyr)
    if prev_active is not None:
        iface.setActiveLayer(prev_active)
