"""Drop-in replacement for ``client.buildings.get_area(polygon)`` that builds
the same ``AreaBuildings`` payload from a QGIS buildings layer instead of
fetching from Mapbox.

Per the SDK's DotBim coordinate-system contract:

  * x-axis east, y-axis north, z is height (metres),
  * for the *area* call the origin is the polygon's WGS84 bbox SW corner —
    "all buildings share one frame regardless of which tile they came from",
  * when those buildings are passed to ``run_area_and_wait()``, the SDK
    auto-transforms them from the polygon-bbox-SW frame to each tile's
    local frame.

So we project every QGIS feature into the polygon-bbox-SW frame (using the
exact projection params from ``_buildings_compare_helpers.projection_params``,
which mirror ``infrared_sdk/tiling/merger.py:_polygon_to_meters``), extrude
the 2-D footprint into a 3-D mesh with the existing
:func:`geojson2dotbim.create_building_extrusion`, and wrap each as a
:class:`DotBimMesh`. Returning an :class:`AreaBuildings` makes this
plug-compatible — callers can swap in a single line.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)
from qgis.utils import iface

from infrared_sdk.buildings.types import AreaBuildings, DotBimMesh

from ..infrared_logger import logger
from ._buildings_compare_helpers import (
    make_xy_converters,
    projection_params,
)
from ._geometry_io import pick_layers_by_keyword
from .feature_height import resolve_feature_height
from .geojson2dotbim import create_building_extrusion, triangulate_volume


# Heights below this threshold (m) produce a degenerate flat mesh that the
# wind/solar API treats as no obstacle — surface them as a separate counter
# so users notice 0-height features that the resolver returned literally.
_MIN_USABLE_HEIGHT_M = 0.5

# How many sample feature IDs to log per skip reason (DEBUG).
_LOG_SAMPLE_LIMIT = 5


# Default context margin (m) — solar uses 77 m, wind uses 0 m. We pick 100 m
# so a single QGIS pass is enough material for any analysis. Buildings
# outside the polygon-bbox + margin are skipped to save extrusion cost.
DEFAULT_CONTEXT_MARGIN_M = 100.0


def _all_outer_rings(geom: QgsGeometry) -> List[List[QgsPointXY]]:
    """Return the outer ring of every part in a polygon/multipolygon.

    The companion ``_qgis_outer_ring`` in ``_buildings_compare_helpers``
    keeps only the *largest* part — fine for centroid-based comparison but
    silently drops the smaller parts of a multipart feature. For the area
    buildings collector we want every part as a separate mesh so the SDK
    sees every physical building footprint as an obstacle.
    """
    rings: List[List[QgsPointXY]] = []
    if geom.isMultipart():
        for part in geom.asMultiPolygon():
            if part:
                rings.append(list(part[0]))
    else:
        poly = geom.asPolygon()
        if poly:
            rings.append(list(poly[0]))
    return rings


def _resolve_layer(layer: Optional[QgsVectorLayer]) -> QgsVectorLayer:
    if layer is not None:
        return layer
    candidates = pick_layers_by_keyword(["building", "buildings"], prefer_active=True)
    if candidates:
        return candidates[0]
    if iface is not None and iface.activeLayer() is not None:
        return iface.activeLayer()
    raise RuntimeError(
        "collect_qgis_area_buildings: no QGIS buildings layer found. "
        "Pass `layer=...` explicitly or load a polygon layer first."
    )


def _polygon_meter_bbox(
    polygon: dict, margin_m: float
) -> Tuple[float, float, float, float]:
    """Return (xmin, ymin, xmax, ymax) of the polygon in the polygon-bbox-SW
    frame, expanded by ``margin_m`` on every side.

    In the polygon-bbox-SW frame the polygon's bbox is exactly:
    ``(0, 0, width_m, height_m)``. The polygon vertices may extend the
    rectangle slightly because of vertex projection, but for a bbox-style
    *filter* this rectangle is the right thing — anything outside it cannot
    intersect the polygon.
    """
    to_meters, _ = make_xy_converters(polygon)
    ring = polygon["coordinates"][0]
    xs = []
    ys = []
    for lon, lat in ring:
        x, y = to_meters(lon, lat)
        xs.append(x)
        ys.append(y)
    return (
        min(xs) - margin_m, min(ys) - margin_m,
        max(xs) + margin_m, max(ys) + margin_m,
    )


def _feature_bbox_intersects(
    feat_xy: List[Tuple[float, float]],
    poly_bbox: Tuple[float, float, float, float],
) -> bool:
    if not feat_xy:
        return False
    fxs = [p[0] for p in feat_xy]
    fys = [p[1] for p in feat_xy]
    fxmin, fymin, fxmax, fymax = min(fxs), min(fys), max(fxs), max(fys)
    pxmin, pymin, pxmax, pymax = poly_bbox
    return not (fxmax < pxmin or fxmin > pxmax or fymax < pymin or fymin > pymax)


def _extrude_one(
    fid: int, footprint_xy: List[Tuple[float, float]], height: float
) -> Optional[DotBimMesh]:
    """Build a DotBimMesh for one building from its 2-D footprint + height.

    Triangulation strategy mirrors the existing GeoJSON→dotbim pipeline
    (``geojson2dotbim.geojson_to_dotbim``): earcut first because the simple
    fan-triangulation in ``create_building_extrusion`` only produces correct
    surfaces for *convex* footprints — concave or L-shaped buildings get
    overshot triangles. ``triangulate_volume`` uses ``mapbox_earcut`` and
    handles concave polygons (and holes) properly. We fall back to the fan
    extruder only if earcut is unavailable or fails on a particular
    feature.

    Returns ``None`` if both extruders reject the polygon (degenerate ring,
    self-intersection that can't be buffer-fixed, etc.).
    """
    # triangulate_volume expects an *open* ring (no duplicate closing vertex)
    # of (x, y) tuples. QGIS rings come with a closing duplicate from
    # asPolygon(), drop it here so earcut doesn't produce a zero-area
    # triangle on the seam.
    open_ring = [(float(x), float(y)) for (x, y) in footprint_xy]
    if len(open_ring) >= 2 and open_ring[0] == open_ring[-1]:
        open_ring = open_ring[:-1]
    if len(open_ring) < 3:
        return None

    # 1) Earcut path — preferred, handles concave footprints correctly.
    verts, inds = triangulate_volume([open_ring], float(height))

    # 2) Fan-triangulation fallback — only correct for convex polygons,
    # but fine as a safety net when earcut isn't available.
    if not verts or not inds:
        local = [[x, y] for (x, y) in open_ring]
        verts, inds = create_building_extrusion(local, float(height))

    if verts is None or inds is None or not verts or not inds:
        return None

    try:
        return DotBimMesh(
            mesh_id=int(fid),
            coordinates=list(verts),
            indices=list(inds),
        )
    except Exception as e:
        # Pydantic validation can reject non-finite values — log and skip.
        logger.warning("DotBimMesh validation failed for fid=%s: %s", fid, e)
        return None


def collect_qgis_area_buildings(
    polygon: dict,
    *,
    layer: Optional[QgsVectorLayer] = None,
    default_height_m: float = 6.0,
    context_margin_m: float = DEFAULT_CONTEXT_MARGIN_M,
    permissive: bool = True,
) -> AreaBuildings:
    """Build an :class:`AreaBuildings` from a QGIS layer.

    Drop-in replacement for ``client.buildings.get_area(polygon)`` — same
    return type, same coordinate frame (polygon-bbox-SW, metres), same
    plug into ``run_area_and_wait(payload, polygon, buildings=...)``.

    Parameters
    ----------
    polygon : dict
        GeoJSON Polygon (WGS84). Same one you'd pass to ``get_area``.
    layer : QgsVectorLayer, optional
        Source layer. Defaults to the first layer matched by the keywords
        ``["building", "buildings"]``, falling back to the active layer.
    default_height_m : float
        Fallback height (m) when the per-feature resolver returns ``None``
        and ``permissive`` is ``True``. Matches the legacy ``collect_buildings``
        default of 6 m.
    context_margin_m : float
        Bbox-filter expansion in metres. Buildings outside the polygon
        bbox + margin are skipped. 100 m by default — comfortably covers
        the SDK's solar 77 m context, so the same payload works for any
        analysis type.
    permissive : bool
        If True, features without a resolvable height fall back to
        ``default_height_m`` rather than being dropped.

    Returns
    -------
    AreaBuildings
        ``buildings`` dict keyed by string feature ID, ``building_ids`` as
        ints, plus the bookkeeping fields the SDK populates.
    """
    t0 = time.monotonic()
    layer = _resolve_layer(layer)
    layer_crs = layer.crs()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    transform: Optional[QgsCoordinateTransform] = (
        QgsCoordinateTransform(layer_crs, wgs84, QgsProject.instance())
        if layer_crs.isValid() and layer_crs != wgs84
        else None
    )
    to_meters, _ = make_xy_converters(polygon)
    poly_bbox = _polygon_meter_bbox(polygon, context_margin_m)
    # resolve_feature_height expects a list of field NAMES, not a QgsFields
    # container — passing QgsFields trips an internal `.strip()` on a
    # QgsField object. Match the convention used in services/geometry.py.
    field_names = [f.name() for f in layer.fields()]

    buildings: Dict[str, DotBimMesh] = {}
    building_ids: List[int] = []
    skipped_no_height = 0
    skipped_low_height = 0
    skipped_geom = 0
    skipped_outside = 0
    skipped_extrusion = 0
    multipart_features = 0
    multipart_extra_meshes = 0
    sample_low_height: List[Tuple[int, float]] = []
    sample_outside: List[int] = []
    seq_mesh_id = 0

    origin_lon, origin_lat, mpd_lng = projection_params(polygon)
    logger.info(
        "collect_qgis_area_buildings: layer=%r CRS=%s polygon-bbox-SW frame "
        "(origin_lon=%.6f origin_lat=%.6f mpd_lng=%.2f) margin=%.0fm "
        "min_usable_height=%.1fm",
        layer.name(), layer_crs.authid(), origin_lon, origin_lat, mpd_lng,
        context_margin_m, _MIN_USABLE_HEIGHT_M,
    )

    for feat in layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            skipped_geom += 1
            continue
        rings = _all_outer_rings(geom)
        if not rings:
            skipped_geom += 1
            continue
        if len(rings) > 1:
            multipart_features += 1

        # Resolve height once per feature — the height attribute lives on
        # the feature, not on individual multipolygon parts.
        try:
            h = resolve_feature_height(feat, field_names, geom=geom, default=None)
        except Exception as e:
            logger.debug("resolve_feature_height failed on fid=%s: %s", feat.id(), e)
            h = None
        if h is None:
            if permissive:
                h = default_height_m
            else:
                skipped_no_height += 1
                continue
        if float(h) < _MIN_USABLE_HEIGHT_M:
            skipped_low_height += 1
            if len(sample_low_height) < _LOG_SAMPLE_LIMIT:
                sample_low_height.append((int(feat.id()), float(h)))
            continue

        # Process every part. Each becomes its own DotBimMesh so the API
        # sees disconnected building bodies as separate obstacles.
        feat_kept_count = 0
        for part_idx, ring in enumerate(rings):
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
            if not _feature_bbox_intersects(xy, poly_bbox):
                continue

            seq_mesh_id += 1
            mesh = _extrude_one(seq_mesh_id, xy, float(h))
            if mesh is None:
                skipped_extrusion += 1
                continue

            key = (
                f"{int(feat.id())}"
                if len(rings) == 1
                else f"{int(feat.id())}.{part_idx}"
            )
            buildings[key] = mesh
            building_ids.append(int(feat.id()))
            feat_kept_count += 1

        if feat_kept_count == 0:
            skipped_outside += 1
            if len(sample_outside) < _LOG_SAMPLE_LIMIT:
                sample_outside.append(int(feat.id()))
        elif len(rings) > 1 and feat_kept_count > 1:
            multipart_extra_meshes += feat_kept_count - 1

    elapsed = time.monotonic() - t0
    logger.info(
        "collect_qgis_area_buildings: kept=%d (multipart features=%d, extra "
        "meshes from multipart=%d); skipped (geom=%d outside=%d no_height=%d "
        "low_height=%d extrusion=%d) in %.2fs",
        len(buildings), multipart_features, multipart_extra_meshes,
        skipped_geom, skipped_outside, skipped_no_height,
        skipped_low_height, skipped_extrusion, elapsed,
    )
    if sample_low_height:
        logger.info(
            "collect_qgis_area_buildings: low-height samples (fid, h<%.1fm): %s",
            _MIN_USABLE_HEIGHT_M, sample_low_height,
        )
    if sample_outside:
        logger.debug(
            "collect_qgis_area_buildings: bbox-outside sample fids: %s",
            sample_outside,
        )

    return AreaBuildings(
        buildings=buildings,
        building_ids=building_ids,
        polygon=polygon,
        total_buildings=len(buildings),
        execution_time=float(elapsed),
        failed_tiles=[],
    )
