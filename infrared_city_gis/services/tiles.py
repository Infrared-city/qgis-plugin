"""Tile center generation and selection helpers for Infrared City."""

import math

from pyproj import Geod
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsRectangle,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
)
from qgis.utils import iface
from qgis.PyQt.QtCore import QVariant

from ..infrared_logger import logger


def get_bbox(center_lon, center_lat, box_size_meters=512):
    """Return bounding box [minLon, minLat, maxLon, maxLat] around center point."""
    logger.info("Getting bounding box...")
    half = box_size_meters / 2
    geod = Geod(ellps="WGS84")
    _, lat_n, _ = geod.fwd(center_lon, center_lat, 0, half)
    _, lat_s, _ = geod.fwd(center_lon, center_lat, 180, half)
    lon_e, _, _ = geod.fwd(center_lon, center_lat, 90, half)
    lon_w, _, _ = geod.fwd(center_lon, center_lat, 270, half)
    return [lon_w, lat_s, lon_e, lat_n]


def get_selected_crs():
    layer = iface.activeLayer()
    if layer is None:
        logger.warning("No active layer found")
        return None
    layer_crs = layer.crs()
    logger.info("Selected crs: %s", layer_crs.authid())
    return layer_crs.authid()


def get_selected_bbox():
    layer = iface.activeLayer()
    if layer is None:
        logger.warning("No active layer found")
        return None

    selected = layer.selectedFeatures()
    if not selected:
        logger.warning("No selected features found in the active layer")
        return None

    geom = selected[0].geometry()
    if geom is None or geom.isEmpty():
        logger.warning("First selected geometry is empty")
        return None
    bbox = geom.boundingBox()

    for feat in selected[1:]:
        g = feat.geometry()
        if g is None or g.isEmpty():
            continue
        bbox.combineExtentWith(g.boundingBox())

    west = bbox.xMinimum()
    south = bbox.yMinimum()
    east = bbox.xMaximum()
    north = bbox.yMaximum()
    logger.info("Selected bbox: %s, %s, %s, %s", west, south, east, north)
    return west, south, east, north


def generate_tile_centers(west, south, east, north, tile_size=256):
    width = east - west
    height = north - south

    if width <= 0 or height <= 0:
        logger.error("Wrong bbox: width=%s, height=%s for (%s, %s, %s, %s)", width, height, west, south, east, north)
        return []

    nx = math.ceil(width / tile_size)
    ny = math.ceil(height / tile_size)
    centers = []

    for j in range(ny):
        cy = north - (j + 0.5) * tile_size
        for i in range(nx):
            cx = west + (i + 0.5) * tile_size
            centers.append((cx, cy))

    return centers


def collect_tile_centers_from_selection():
    layer = iface.activeLayer()
    if not layer:
        return []

    bbox = get_selected_bbox()
    if bbox is None:
        logger.warning("collect_tile_centers_from_selection: no selection bbox")
        return []
    w, s, e, n = bbox

    selected = layer.selectedFeatures()
    geoms = [f.geometry() for f in selected if f.geometry() and not f.geometry().isEmpty()]
    if not geoms:
        return []
    geom_union = QgsGeometry.unaryUnion(geoms)
    if geom_union is None or geom_union.isEmpty():
        return []

    layer_crs = layer.crs()
    tile_size_m = 256.0
    half = tile_size_m / 2.0

    if layer_crs.isGeographic():
        cx_center = (w + e) / 2.0
        cy_center = (s + n) / 2.0
        target_crs = QgsCoordinateReferenceSystem.fromProj4(
            f"+proj=aeqd +lat_0={cy_center} +lon_0={cx_center} +ellps=WGS84 +units=m +type=crs"
        )
        transform_to_meters = QgsCoordinateTransform(layer_crs, target_crs, QgsProject.instance())
        w_m, s_m = transform_to_meters.transform(w, s)
        e_m, n_m = transform_to_meters.transform(e, n)
        tile_centers_m = generate_tile_centers(w_m, s_m, e_m, n_m)

        transform_geom = QgsCoordinateTransform(layer_crs, target_crs, QgsProject.instance())
        geom_union_m = QgsGeometry(geom_union)
        geom_union_m.transform(transform_geom)

        filtered_m = []
        for cx_m, cy_m in tile_centers_m:
            rect = QgsRectangle(cx_m - half, cy_m - half, cx_m + half, cy_m + half)
            if geom_union_m.intersects(QgsGeometry.fromRect(rect)):
                filtered_m.append((cx_m, cy_m))

        transform_back = QgsCoordinateTransform(target_crs, layer_crs, QgsProject.instance())
        filtered = []
        for cx_m, cy_m in filtered_m:
            cx_layer, cy_layer = transform_back.transform(cx_m, cy_m)
            filtered.append((cx_layer, cy_layer))
    else:
        tile_centers_layer_crs = generate_tile_centers(w, s, e, n)
        filtered = []
        for cx, cy in tile_centers_layer_crs:
            rect = QgsRectangle(cx - half, cy - half, cx + half, cy + half)
            if geom_union.intersects(QgsGeometry.fromRect(rect)):
                filtered.append((cx, cy))

    return filtered


def plot_tile_centers(tile_centers):
    """Create a temporary point layer with the given tile centers and add it to the project.

    The previously active layer is restored after adding the new layer so that
    subsequent selection-based operations still see the user's source layer.
    """
    if not tile_centers:
        logger.warning("plot_tile_centers called with empty tile_centers list")
        return

    tile_coords = [(x, y) for x, y in tile_centers]
    logger.info("Tile centers coordinates: %s", tile_coords)

    prev_active = iface.activeLayer()
    crs = prev_active.crs() if prev_active is not None else QgsProject.instance().crs()

    vlayer = QgsVectorLayer(f"Point?crs={crs.authid()}", f"Tile centers: {len(tile_centers)}", "memory")
    pr = vlayer.dataProvider()

    fields = QgsFields()
    fields.append(QgsField("id", QVariant.Int))
    pr.addAttributes(fields)
    vlayer.updateFields()

    feats = []
    for idx, (cx, cy) in enumerate(tile_centers):
        feat = QgsFeature()
        feat.setFields(fields)
        feat.setAttribute("id", idx)
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(cx, cy)))
        feats.append(feat)

    pr.addFeatures(feats)
    vlayer.updateExtents()
    QgsProject.instance().addMapLayer(vlayer)

    # Restore the previously active layer so downstream code still sees the
    # user's source layer (with its selection) as active.
    if prev_active is not None:
        iface.setActiveLayer(prev_active)


def create_polygon_from_selection():
    """Return a single closed polygon tightly bounding all selected features.

    Coordinates are in the active layer's CRS:
      - geographic CRS → [[lon, lat], ...]
      - projected CRS → [[x, y], ...] (meters)

    Uses a concave hull that follows the shape of the selection more precisely
    than a convex hull (e.g. does not fill interior gaps in L-shaped selections).
    Falls back to convex hull if concave hull is not available (QGIS < 3.30).
    Returns a single closed ring: [[x, y], ...] (first and last points identical).
    Returns [] if no selection or no valid geometry.
    """
    layer = iface.activeLayer()
    if not layer:
        logger.warning("create_polygon_from_selection: no active layer")
        return []

    selected = layer.selectedFeatures()
    geoms = [f.geometry() for f in selected if f.geometry() and not f.geometry().isEmpty()]
    if not geoms:
        logger.warning("create_polygon_from_selection: no selected geometries")
        return []

    geom_union = QgsGeometry.unaryUnion(geoms)
    if geom_union is None or geom_union.isEmpty():
        logger.warning("create_polygon_from_selection: geom_union empty")
        return []

    # Try concave hull first (tighter fit). target_percent=0.3 is a reasonable
    # balance between tightness and smoothness; lower = tighter, higher = smoother.
    hull = None
    try:
        hull = geom_union.concaveHull(0.3, False)
        logger.info("create_polygon_from_selection: concave hull computed")
    except Exception as e:
        logger.warning("concaveHull failed, falling back to convex hull: %s", e)
        hull = None

    if hull is None or hull.isEmpty():
        hull = geom_union.convexHull()
        logger.info("create_polygon_from_selection: using convex hull")

    if hull is None or hull.isEmpty():
        logger.warning("create_polygon_from_selection: hull empty")
        return []

    # Concave hull may be multipart if selection has disconnected clusters;
    # take the outer ring of the largest part in that case.
    if hull.isMultipart():
        parts = hull.asMultiPolygon()
        if not parts:
            logger.warning("create_polygon_from_selection: multipart hull has no parts")
            return []
        # Pick the part with the largest exterior ring (by point count as a proxy)
        largest = max(parts, key=lambda p: len(p[0]) if p else 0)
        outer = largest[0]
    else:
        poly = hull.asPolygon()
        if not poly:
            logger.warning("create_polygon_from_selection: asPolygon returned empty")
            return []
        outer = poly[0]

    coords = [[p.x(), p.y()] for p in outer]
    logger.info("create_polygon_from_selection: %d points in ring", len(coords))
    return coords


def plot_selected_polygon(polygon):
    """Create a temporary polygon layer from a nested coordinate array and add it to the project.

    Accepts either:
      - single ring: [[x, y], [x, y], ...]
      - multiple rings: [[[x, y], ...], [[x, y], ...], ...]

    Coordinates are interpreted in the active layer's CRS (or project CRS if no active layer).
    """
    if not polygon:
        logger.warning("plot_selected_polygon called with empty polygon")
        return

    prev_active = iface.activeLayer()
    crs = prev_active.crs() if prev_active is not None else QgsProject.instance().crs()
    logger.info("plot_selected_polygon: using CRS %s", crs.authid())

    if isinstance(polygon[0][0], (list, tuple)):
        rings = polygon
    else:
        rings = [polygon]
    logger.info("plot_selected_polygon: %d ring(s), first ring has %d points", len(rings), len(rings[0]))

    vlayer = QgsVectorLayer(
        f"Polygon?crs={crs.authid()}",
        f"Selected polygon ({len(rings)} part{'s' if len(rings) != 1 else ''})",
        "memory",
    )
    if not vlayer.isValid():
        logger.error("plot_selected_polygon: memory layer creation failed")
        return

    pr = vlayer.dataProvider()
    fields = QgsFields()
    fields.append(QgsField("id", QVariant.Int))
    pr.addAttributes(fields)
    vlayer.updateFields()

    feats = []
    for idx, ring in enumerate(rings):
        qgs_ring = [QgsPointXY(pt[0], pt[1]) for pt in ring]
        # Ensure ring is closed (first == last)
        if qgs_ring and (qgs_ring[0].x() != qgs_ring[-1].x() or qgs_ring[0].y() != qgs_ring[-1].y()):
            qgs_ring.append(QgsPointXY(qgs_ring[0].x(), qgs_ring[0].y()))
        geom = QgsGeometry.fromPolygonXY([qgs_ring])
        if geom is None or geom.isEmpty():
            logger.warning("plot_selected_polygon: feature %d has empty geometry", idx)
            continue
        feat = QgsFeature()
        feat.setFields(fields)
        feat.setAttribute("id", idx)
        feat.setGeometry(geom)
        feats.append(feat)

    ok, added = pr.addFeatures(feats)
    logger.info("plot_selected_polygon: addFeatures ok=%s, added=%d/%d", ok, len(added), len(feats))
    vlayer.updateExtents()
    QgsProject.instance().addMapLayer(vlayer)
    logger.info("plot_selected_polygon: layer added to project")

    # Restore the previously active layer so downstream code still sees the
    # user's source layer (with its selection) as active.
    if prev_active is not None:
        iface.setActiveLayer(prev_active)


def get_center_lon_lat_from_bbox(bbox, crs_authid: str):
    """Return bbox center as lon/lat in EPSG:4326."""
    w, s, e, n = bbox
    layer_rect = QgsRectangle(w, s, e, n)

    if crs_authid and crs_authid != "EPSG:4326":
        layer_crs = QgsCoordinateReferenceSystem(crs_authid)
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(layer_crs, wgs84, QgsProject.instance())
        bbox_rect_wgs84 = transform.transformBoundingBox(layer_rect)
    else:
        bbox_rect_wgs84 = layer_rect

    center_lon = (bbox_rect_wgs84.xMinimum() + bbox_rect_wgs84.xMaximum()) / 2
    center_lat = (bbox_rect_wgs84.yMinimum() + bbox_rect_wgs84.yMaximum()) / 2
    return center_lon, center_lat


def get_center_from_bbox(bbox):
    w, s, e, n = bbox
    return (w + e) / 2, (s + n) / 2
