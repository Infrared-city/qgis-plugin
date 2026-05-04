"""Polygon-from-selection helpers split out of ``services/tiles.py`` to keep
that module under the 400-line Infrared convention.

Public entry points (re-exported by ``services.tiles`` and
``services.geometry`` for backward compatibility):

* :func:`create_polygon_from_selection` — bounding polygon of the active
  layer's current selection in the layer CRS, with three modes (convex /
  bbox / concave) approximating the user's drawn region.
* :func:`create_wgs84_geojson_polygon_from_selection` — same in WGS84
  GeoJSON Polygon format ready for the Infrared SDK.
* :func:`plot_selected_polygon` — debug visualiser that drops the polygon
  back onto the canvas as a temporary memory layer.
"""

from __future__ import annotations

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


def create_polygon_from_selection(mode: str = "convex"):
    """Return a single closed polygon bounding all selected features.

    Note on what this can and cannot return: QGIS does *not* preserve the
    rubber-band geometry from selection tools (Select by Polygon / Circle /
    Freehand) — only the resulting feature selection. So we cannot recover
    the literal shape the user drew. The ``mode`` parameter picks among
    practical approximations of that drawing:

    * ``"convex"`` (default) — convex hull of the selected features. Smooth
      outer envelope, no inward indentations. For a roughly-convex
      rubberband (circle, polygon-by-3-clicks, box select) this is a very
      close approximation of what the user drew.
    * ``"bbox"`` — axis-aligned bounding rectangle of all selected
      features. Even looser; useful when you want a stable rectangular
      analysis area regardless of selection shape.
    * ``"concave"`` — concave hull (``target_percent=0.3``) that follows the
      shape of the selection precisely; tighter than ``"convex"`` and may
      have interior gaps for L-shaped or scattered selections. Falls back
      to convex if QGIS < 3.30 lacks ``concaveHull``.

    For pixel-perfect control, draw your analysis polygon as a feature on
    a separate layer and select that single feature — ``"convex"`` /
    ``"concave"`` of a single polygon both reduce to that polygon's outer
    ring.

    Coordinates are in the active layer's CRS:
      * geographic CRS → ``[[lon, lat], ...]``
      * projected CRS → ``[[x, y], ...]`` (metres)

    Returns a single closed ring (first == last). Returns ``[]`` if no
    selection or no valid geometry.
    """
    layer = iface.activeLayer()
    if not layer:
        logger.warning("create_polygon_from_selection: no active layer")
        return []

    selected = layer.selectedFeatures()
    geoms = [
        f.geometry() for f in selected
        if f.geometry() and not f.geometry().isEmpty()
    ]
    if not geoms:
        logger.warning("create_polygon_from_selection: no selected geometries")
        return []

    geom_union = QgsGeometry.unaryUnion(geoms)
    if geom_union is None or geom_union.isEmpty():
        logger.warning("create_polygon_from_selection: geom_union empty")
        return []

    # Build the bounding shape according to mode. "convex" / "bbox" are
    # cheap; "concave" calls into QGIS's geos wrapper which can fail on
    # older builds — fall back to convex when it does.
    hull = None
    if mode == "concave":
        try:
            hull = geom_union.concaveHull(0.3, False)
            logger.info("create_polygon_from_selection: concave hull computed")
        except Exception as e:
            logger.warning("concaveHull failed, falling back to convex hull: %s", e)
            hull = None
        if hull is None or hull.isEmpty():
            hull = geom_union.convexHull()
            logger.info("create_polygon_from_selection: using convex hull (fallback)")
    elif mode == "bbox":
        rect = geom_union.boundingBox()
        hull = QgsGeometry.fromRect(rect)
        logger.info("create_polygon_from_selection: using bbox rectangle")
    else:
        # Default and explicit "convex": smooth outer envelope, the closest
        # practical approximation of a typical user-drawn rubber band.
        if mode != "convex":
            logger.warning(
                "create_polygon_from_selection: unknown mode %r; using 'convex'", mode,
            )
        hull = geom_union.convexHull()
        logger.info("create_polygon_from_selection: using convex hull")

    if hull is None or hull.isEmpty():
        logger.warning("create_polygon_from_selection: hull empty")
        return []

    # Hulls can be multipart if the selection has disconnected clusters;
    # take the outer ring of the largest part in that case.
    if hull.isMultipart():
        parts = hull.asMultiPolygon()
        if not parts:
            logger.warning("create_polygon_from_selection: multipart hull has no parts")
            return []
        largest = max(parts, key=lambda p: len(p[0]) if p else 0)
        outer = largest[0]
    else:
        poly = hull.asPolygon()
        if not poly:
            logger.warning("create_polygon_from_selection: asPolygon returned empty")
            return []
        outer = poly[0]

    coords = [[p.x(), p.y()] for p in outer]
    logger.info(
        "create_polygon_from_selection: %d points in ring (mode=%s)",
        len(coords), mode,
    )
    return coords


def create_wgs84_geojson_polygon_from_selection(
    mode: str = "convex",
) -> "dict | None":
    """Return the current selection as a GeoJSON Polygon in EPSG:4326.

    Wraps :func:`create_polygon_from_selection`, reprojects the ring to
    WGS84 lon/lat (if needed), ensures the ring is closed, and returns a
    dict shaped for the Infrared SDK::

        {"type": "Polygon", "coordinates": [[[lon, lat], ..., [lon, lat]]]}

    The ``mode`` parameter is forwarded — see
    :func:`create_polygon_from_selection` for what each mode does. Returns
    ``None`` if there is no valid selection.
    """
    ring = create_polygon_from_selection(mode=mode)
    if not ring:
        return None

    layer = iface.activeLayer()
    layer_crs = layer.crs() if layer is not None else None
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")

    if layer_crs is not None and layer_crs.isValid() and layer_crs != wgs84:
        transform = QgsCoordinateTransform(layer_crs, wgs84, QgsProject.instance())
        wgs_ring = []
        for x, y in ring:
            lon, lat = transform.transform(x, y)
            wgs_ring.append([lon, lat])
    else:
        wgs_ring = [[float(x), float(y)] for x, y in ring]

    # Ensure ring is closed (first == last). The SDK validator requires this.
    if wgs_ring[0] != wgs_ring[-1]:
        wgs_ring.append(wgs_ring[0])

    return {"type": "Polygon", "coordinates": [wgs_ring]}


def plot_selected_polygon(polygon):
    """Create a temporary polygon layer from a nested coordinate array and
    add it to the project.

    Accepts either:
      * single ring: ``[[x, y], [x, y], ...]``
      * multiple rings: ``[[[x, y], ...], [[x, y], ...], ...]``

    Coordinates are interpreted in the active layer's CRS (or the project
    CRS if no active layer is set).
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
    logger.info(
        "plot_selected_polygon: %d ring(s), first ring has %d points",
        len(rings), len(rings[0]),
    )

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
        if qgs_ring and (
            qgs_ring[0].x() != qgs_ring[-1].x() or qgs_ring[0].y() != qgs_ring[-1].y()
        ):
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
    logger.info(
        "plot_selected_polygon: addFeatures ok=%s, added=%d/%d",
        ok, len(added), len(feats),
    )
    vlayer.updateExtents()
    QgsProject.instance().addMapLayer(vlayer)
    logger.info("plot_selected_polygon: layer added to project")

    # Restore the previously active layer so downstream code still sees the
    # user's source layer (with its selection) as active.
    if prev_active is not None:
        iface.setActiveLayer(prev_active)
