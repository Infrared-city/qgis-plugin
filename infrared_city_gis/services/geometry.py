"""Geometry collection for Infrared City simulation tiles.

Public entry points:
  - ``collect_buildings(center_x, center_y, idx, permissive=True)``
      Building-only flow with tiered height resolution and per-tier summary.
  - ``collect_trees(center_x, center_y, idx)``
      Tree-only flow — collects point positions for the registry mesh.
  - ``collect_geometries(center_x, center_y, idx, geometry_type=...)``
      Backward-compatible dispatcher; new code should call the type-specific
      function directly.

Other helpers have been split into dedicated modules:
  - geotiff.py  : crop_matrix, map_categories, generate_geotiff, _to_json_primitive
  - tiles.py    : get_bbox, tile center generation, CRS/bbox helpers
"""

import json
from datetime import datetime

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..infrared_logger import logger
from ..models.analysis import GeometryTypes
from ._geometry_io import (
    bbox_512_in_ref_crs,
    compute_bbox_256,
    ensure_data_dir,
    notify_user,
    pick_layers_by_keyword,
    select_features_in_bbox,
    write_dotbim,
    write_geojson,
)
from .converter import Converter
from .feature_height import (
    SOURCE_BUILDING_TYPE,
    SOURCE_GENERIC_DEFAULT,
    SOURCE_HEIGHT_FIELD,
    SOURCE_LEVELS,
    SOURCE_MISSING,
    SOURCE_OVERRIDE,
    SOURCE_TOP_BASE,
    SOURCE_Z_RANGE,
    resolve_feature_height_with_source,
)
from .geojson2dotbim import convert_tree_to_dotbim, process_geojson_file  # noqa: F401 — kept for backward compat
from .geotiff import (  # noqa: F401 — re-exported for backward compat
    _to_json_primitive,
    crop_matrix,
    generate_geotiff,
    map_categories,
)
from .tiles import (  # noqa: F401 — re-exported for backward compat
    collect_tile_centers_from_selection,
    create_polygon_from_selection,
    create_wgs84_geojson_polygon_from_selection,
    generate_tile_centers,
    get_bbox,
    get_center_from_bbox,
    get_center_lon_lat_from_bbox,
    get_selected_bbox,
    get_selected_crs,
    plot_selected_polygon,
    plot_tile_centers,
)




# ---------------------------------------------------------------------------
# Buildings — height-resolution, schema check, per-tier accounting
# ---------------------------------------------------------------------------

# Human-readable labels for the per-tier counter shown to the user.
_HEIGHT_SOURCE_LABELS = {
    SOURCE_OVERRIDE: "user override field",
    SOURCE_TOP_BASE: "top–base elevation pair",
    SOURCE_HEIGHT_FIELD: "height field",
    SOURCE_LEVELS: "levels × floor height",
    SOURCE_Z_RANGE: "3D geometry Z-range",
    SOURCE_BUILDING_TYPE: "OSM building-type estimate",
    SOURCE_GENERIC_DEFAULT: "generic 6 m fallback",
    SOURCE_MISSING: "no resolvable height (skipped)",
}


def collect_buildings(center_x, center_y, idx, permissive: bool = True):
    """Collect building features for a tile, resolving heights with a tiered
    strategy.

    Args:
        center_x, center_y: tile centre in the reference layer's CRS (or in
            project CRS if no buildings layer is found).
        idx: tile index (used for filenames and log lines).
        permissive: when True (default), features that fall through every
            tier get a generic 6 m default rather than being dropped — keeps
            simulations runnable on sparse OSM data. When False, missing
            heights cause the feature to be skipped.

    Returns:
        ``(geojson_path, dotbim_path, bbox_512, crs_authid, bbox_256)`` or
        ``None`` if no features could be collected.
    """
    date_now = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    keywords = ["building", "buildings"]
    target_layers = pick_layers_by_keyword(keywords, prefer_active=True)
    if not target_layers:
        logger.warning(
            "collect_buildings: no buildings layer found by name; "
            "falling back to all polygon vector layers in the project."
        )
        target_layers = [
            lyr for lyr in QgsProject.instance().mapLayers().values()
            if isinstance(lyr, QgsVectorLayer)
        ]
    if not target_layers:
        logger.error("collect_buildings: no vector layers in project.")
        return None

    ref_layer = target_layers[0]
    ref_crs = ref_layer.crs()

    bbox = bbox_512_in_ref_crs(center_x, center_y, ref_crs)
    if bbox is None:
        return None
    bbox_rect_wgs84, bbox_rect_ref, center_lon, center_lat = bbox
    logger.info(
        "[collect_buildings] Tile %d centre WGS84 (%.6f, %.6f), ref CRS %s, permissive=%s",
        idx, center_lon, center_lat, ref_crs.authid(), permissive,
    )

    geojson_dict = {"type": "FeatureCollection", "features": []}
    total_features = 0
    # Per-tier counter: which strategy resolved each feature's height.
    source_counts = {key: 0 for key in _HEIGHT_SOURCE_LABELS}

    for layer in target_layers:
        selected_features = select_features_in_bbox(layer, bbox_rect_wgs84)
        if not selected_features:
            continue

        layer_crs = layer.crs()
        fields = [f.name() for f in layer.fields()]
        layer_to_wgs84 = (
            QgsCoordinateTransform(layer_crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
            if layer_crs.authid() != "EPSG:4326" else None
        )

        for feat in selected_features:
            geom = feat.geometry()
            geom_wgs84 = QgsGeometry(geom)
            if layer_to_wgs84 is not None:
                geom_wgs84.transform(layer_to_wgs84)

            attr_values = feat.attributes()
            raw_props = {fields[i]: attr_values[i] for i in range(len(fields))}
            props = {k: _to_json_primitive(v) for k, v in raw_props.items()}
            props["source_layer"] = layer.name()
            props["geometry_type"] = GeometryTypes.BUILDINGS.value

            height, source = resolve_feature_height_with_source(
                feat, fields, geom=geom, permissive=permissive,
            )
            source_counts[source] = source_counts.get(source, 0) + 1

            if height is None or height <= 0:
                logger.warning(
                    "[collect_buildings] No height resolved for fid=%s in layer "
                    "'%s'; skipping (permissive=%s).",
                    feat.id(), layer.name(), permissive,
                )
                continue

            props["height"] = round(float(height), 2)
            props["height_source"] = source
            geojson_dict["features"].append({
                "type": "Feature",
                "geometry": json.loads(geom_wgs84.asJson()),
                "properties": props,
            })
            total_features += 1

    if total_features == 0:
        skipped = source_counts.get(SOURCE_MISSING, 0)
        if skipped:
            logger.error(
                "[collect_buildings] Tile %d: ALL %d features dropped — no height "
                "could be resolved for any of them.", idx, skipped,
            )
            notify_user(
                f"Tile {idx}: all {skipped} buildings dropped because their height "
                f"could not be determined. Add a 'height' / 'building_levels' "
                f"attribute or use 3D geometry.",
                level=Qgis.Warning, duration=12,
            )
        else:
            logger.info("[collect_buildings] Tile %d: no features found.", idx)
        return None

    # Summary log + user message — show only the tiers that actually contributed.
    summary_parts = []
    for src, label in _HEIGHT_SOURCE_LABELS.items():
        n = source_counts.get(src, 0)
        if n > 0:
            summary_parts.append(f"  {n:>5} from {label}")
    summary_text = "\n".join(summary_parts)
    logger.info(
        "[collect_buildings] Tile %d: %d buildings included.\n%s",
        idx, total_features, summary_text,
    )

    # Push a single concise summary to the QGIS bar so the user sees it without
    # having to open the log. Only mention "estimated" tiers if any were used.
    estimated = (
        source_counts.get(SOURCE_BUILDING_TYPE, 0)
        + source_counts.get(SOURCE_GENERIC_DEFAULT, 0)
    )
    skipped = source_counts.get(SOURCE_MISSING, 0)
    if estimated > 0 or skipped > 0:
        msg = f"Tile {idx}: {total_features} buildings included"
        bits = []
        if estimated > 0:
            bits.append(f"{estimated} estimated")
        if skipped > 0:
            bits.append(f"{skipped} skipped (no data)")
        if bits:
            msg += " (" + ", ".join(bits) + ")"
        notify_user(msg, level=Qgis.Info, duration=10)

    plugin_data_dir = ensure_data_dir()
    geojson_path = write_geojson(
        geojson_dict, plugin_data_dir, GeometryTypes.BUILDINGS.value, date_now, idx,
    )
    dotbim_data = Converter.GeojsonToDotbimMesh(
        geojson_dict, center_lon, center_lat, "EPSG:4326",
    )
    dotbim_path = write_dotbim(
        dotbim_data, plugin_data_dir, GeometryTypes.BUILDINGS.value, date_now, idx,
    )

    bbox_512 = (
        bbox_rect_ref.xMinimum(), bbox_rect_ref.yMinimum(),
        bbox_rect_ref.xMaximum(), bbox_rect_ref.yMaximum(),
    )
    bbox_256 = compute_bbox_256(bbox_rect_ref, center_x, center_y, ref_crs)
    crs_authid = ref_crs.authid()
    logger.info(
        "[collect_buildings] Tile %d: CRS=%s bbox_512=%s bbox_256=%s",
        idx, crs_authid, bbox_512, bbox_256,
    )
    return geojson_path, dotbim_path, bbox_512, crs_authid, bbox_256


# ---------------------------------------------------------------------------
# Trees — point positions only; mesh + size come from vegetation_registry
# ---------------------------------------------------------------------------

def collect_trees(center_x, center_y, idx):
    """Collect tree point positions for a tile.

    Trees are simpler than buildings: per-feature attributes don't matter (no
    height resolution, no schema check), and the actual visual mesh comes from
    ``vegetation_registry.json`` plus the user's QSettings (tree_type +
    tree_size). This function just collects the point coordinates within the
    tile bbox, writes a minimal geojson, and hands it to
    ``convert_tree_to_dotbim`` which places one (scaled) registry mesh per
    point.

    Returns:
        ``(geojson_path, dotbim_path, bbox_512, crs_authid, bbox_256)`` or
        ``None`` if no tree features were found in the bbox (trees are
        optional — the caller treats this as "no vegetation").
    """
    date_now = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    keywords = ["tree", "trees", "vegetation"]
    target_layers = pick_layers_by_keyword(keywords, prefer_active=False)
    if not target_layers:
        logger.info("collect_trees: no tree/vegetation layer in project.")
        return None

    ref_layer = target_layers[0]
    ref_crs = ref_layer.crs()

    bbox = bbox_512_in_ref_crs(center_x, center_y, ref_crs)
    if bbox is None:
        return None
    bbox_rect_wgs84, bbox_rect_ref, center_lon, center_lat = bbox
    logger.info(
        "[collect_trees] Tile %d centre WGS84 (%.6f, %.6f), ref CRS %s",
        idx, center_lon, center_lat, ref_crs.authid(),
    )

    geojson_dict = {"type": "FeatureCollection", "features": []}
    total_points = 0

    for layer in target_layers:
        selected_features = select_features_in_bbox(layer, bbox_rect_wgs84)
        if not selected_features:
            continue

        layer_crs = layer.crs()
        layer_to_wgs84 = (
            QgsCoordinateTransform(layer_crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
            if layer_crs.authid() != "EPSG:4326" else None
        )

        for feat in selected_features:
            geom = feat.geometry()
            wkb = geom.wkbType() if geom is not None else None
            geom_type = QgsWkbTypes.geometryType(wkb) if wkb is not None else None
            # Trees are placed at point positions. Skip non-point geometries.
            if geom_type != QgsWkbTypes.PointGeometry:
                continue

            geom_wgs84 = QgsGeometry(geom)
            if layer_to_wgs84 is not None:
                geom_wgs84.transform(layer_to_wgs84)

            geojson_dict["features"].append({
                "type": "Feature",
                "geometry": json.loads(geom_wgs84.asJson()),
                "properties": {
                    "source_layer": layer.name(),
                    "geometry_type": GeometryTypes.TREES.value,
                },
            })
            total_points += 1

    if total_points == 0:
        logger.info("[collect_trees] Tile %d: no tree points in bbox.", idx)
        return None

    plugin_data_dir = ensure_data_dir()
    geojson_path = write_geojson(
        geojson_dict, plugin_data_dir, GeometryTypes.TREES.value, date_now, idx,
    )
    dotbim_data = convert_tree_to_dotbim(
        geojson_dict, center_lon, center_lat, "EPSG:4326",
    )
    dotbim_path = write_dotbim(
        dotbim_data, plugin_data_dir, GeometryTypes.TREES.value, date_now, idx,
    )
    logger.info(
        "[collect_trees] Tile %d: %d tree positions → %s",
        idx, total_points, geojson_path,
    )

    bbox_512 = (
        bbox_rect_ref.xMinimum(), bbox_rect_ref.yMinimum(),
        bbox_rect_ref.xMaximum(), bbox_rect_ref.yMaximum(),
    )
    bbox_256 = compute_bbox_256(bbox_rect_ref, center_x, center_y, ref_crs)
    crs_authid = ref_crs.authid()
    return geojson_path, dotbim_path, bbox_512, crs_authid, bbox_256


# ---------------------------------------------------------------------------
# Backward-compat dispatcher — existing callers still use this signature.
# ---------------------------------------------------------------------------

def collect_geometries(center_x, center_y, idx, geometry_type: GeometryTypes = GeometryTypes.BUILDINGS):
    """Backward-compatible wrapper. New code should call ``collect_buildings``
    or ``collect_trees`` directly so the type-specific options (e.g.
    ``permissive``) are surfaced explicitly.
    """
    if geometry_type == GeometryTypes.TREES:
        return collect_trees(center_x, center_y, idx)
    return collect_buildings(center_x, center_y, idx)
