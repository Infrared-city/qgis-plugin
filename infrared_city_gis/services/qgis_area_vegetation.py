"""Collect vegetation point features from a selected QGIS layer for the SDK
area path.

The SDK's vegetation contract is much lighter than the buildings one:
``client.run_area_and_wait(..., vegetation=...)`` accepts a
``Mapping[str, dict]`` keyed by feature ID, where each value is a
GeoJSON-like ``Feature`` dict with at least ``geometry.coordinates =
[lon, lat]`` (WGS84). The SDK handles the per-tile distribution and
the inference engine resolves the actual mesh + size from the
``vegetation_registry`` (species / size attached on the feature
properties or via QSettings).

This collector pulls the user's selected QGIS tree layer (returned by
``tree_layer_picker.selected_tree_layer``), filters point features whose
position lies inside the polygon's WGS84 bbox (plus a 100 m margin to
match the buildings collector's solar-context envelope), reprojects
each to WGS84 lat/lon, and packages them in the SDK-friendly shape.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QSettings

from ..infrared_logger import logger
from ._buildings_compare_helpers import projection_params
from .geotiff import _to_json_primitive


# Same default margin as the buildings collector — comfortably > the SDK's
# solar 77 m context. Trees just outside the polygon can still cast
# shadows / disturb airflow into it.
_DEFAULT_CONTEXT_MARGIN_M = 100.0


def _resolve_current_species() -> Tuple[Optional[str], Optional[str]]:
    """Read the user's tree species + size from QSettings.

    The Tree Catalog dialog persists the user's choice under the keys
    ``infrared_city/tree_type`` (display name from
    ``vegetation_registry.json``'s ``clientModels``) and
    ``infrared_city/tree_size`` (one of ``"small" | "medium" | "large"``).

    If the user hasn't visited the Tree Catalog yet, both come back as
    ``None`` and we fall back to the first model in the registry — same
    behaviour as the legacy ``convert_tree_to_dotbim`` resolver, so
    runs are never silently empty when species hasn't been configured.

    Returns ``(species_display_name, size_label)``.
    """
    settings = QSettings()
    tree_type = settings.value("infrared_city/tree_type", None)
    tree_size = settings.value("infrared_city/tree_size", None)

    if tree_type:
        return tree_type, tree_size

    # Fallback: first entry in the registry. Mirrors legacy fallback.
    plugin_data_dir = os.path.join(
        QgsApplication.qgisSettingsDirPath(),
        "infrared_city_gis", "settings",
    )
    veg_path = os.path.join(plugin_data_dir, "vegetation_registry.json")
    try:
        with open(veg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        client_models = data.get("clientModels") or {}
        if isinstance(client_models, dict):
            for model in client_models.values():
                fallback = model.get("displayName")
                if fallback:
                    logger.info(
                        "Vegetation: no QSettings tree_type configured; "
                        "falling back to first registry model %r",
                        fallback,
                    )
                    return fallback, tree_size
    except Exception as e:
        logger.warning(
            "Vegetation: could not read vegetation_registry.json (%s); "
            "feature properties will not carry species info",
            e,
        )

    return None, tree_size


def _polygon_wgs84_bbox_with_margin(
    polygon: dict, margin_m: float,
) -> Tuple[float, float, float, float]:
    """Return (west, south, east, north) of polygon's WGS84 bbox + margin (deg)."""
    origin_lon, origin_lat, mpd_lng = projection_params(polygon)
    ring = polygon["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    # Convert margin (m) → degrees. Latitude scale is constant; longitude
    # scale shrinks with cos(lat). Use the polygon-bbox-SW projection's
    # mpd_lng, which is already evaluated at the polygon centroid lat.
    margin_deg_lat = margin_m / 111_320.0
    margin_deg_lng = margin_m / max(mpd_lng, 1.0)
    return (
        min(lons) - margin_deg_lng, min(lats) - margin_deg_lat,
        max(lons) + margin_deg_lng, max(lats) + margin_deg_lat,
    )


def collect_qgis_area_vegetation(
    polygon: dict,
    layer: QgsVectorLayer,
    *,
    context_margin_m: float = _DEFAULT_CONTEXT_MARGIN_M,
) -> Dict[str, dict]:
    """Build the SDK ``vegetation`` mapping from a QGIS point layer.

    Parameters
    ----------
    polygon : dict
        GeoJSON Polygon (WGS84). Same one passed to the buildings collector
        and ``run_area_and_wait`` — drives the bbox+margin filter so we
        don't ship every tree in a city-wide dataset just to get the few
        that affect the analysis.
    layer : QgsVectorLayer
        The user-picked tree layer (typically with ``tree-`` in its name).
        Only point features are kept; non-point geometries (e.g. polygon
        canopies) are skipped — the SDK's vegetation contract is point-
        based, the mesh comes from the registry.
    context_margin_m : float
        Bbox-filter expansion in metres. Trees outside the polygon bbox +
        margin are skipped to bound payload size.

    Returns
    -------
    dict
        ``{feat_id_str: {"type": "Feature",
                         "geometry": {"type": "Point",
                                      "coordinates": [lon, lat]},
                         "properties": {...}}}``
        — exactly the shape consumed by
        ``infrared_sdk.vegetation.dedup.assign_vegetation_to_tiles``.
        Empty dict if no matching points were found.
    """
    t0 = time.monotonic()
    layer_crs = layer.crs()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    transform: Optional[QgsCoordinateTransform] = (
        QgsCoordinateTransform(layer_crs, wgs84, QgsProject.instance())
        if layer_crs.isValid() and layer_crs != wgs84
        else None
    )

    west, south, east, north = _polygon_wgs84_bbox_with_margin(
        polygon, context_margin_m,
    )
    species, size = _resolve_current_species()
    logger.info(
        "collect_qgis_area_vegetation: layer=%r CRS=%s bbox+margin "
        "(W=%.6f S=%.6f E=%.6f N=%.6f) margin=%.0fm species=%r size=%r",
        layer.name(), layer_crs.authid(), west, south, east, north,
        context_margin_m, species, size,
    )

    field_names = [f.name() for f in layer.fields()]
    out: Dict[str, dict] = {}
    skipped_geom = 0
    skipped_outside = 0
    skipped_non_point = 0

    for feat in layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            skipped_geom += 1
            continue
        wkb = geom.wkbType()
        if QgsWkbTypes.geometryType(wkb) != QgsWkbTypes.PointGeometry:
            skipped_non_point += 1
            continue

        pt = geom.asPoint()
        if transform is not None:
            lon, lat = transform.transform(pt.x(), pt.y())
        else:
            lon, lat = pt.x(), pt.y()

        if not (west <= lon <= east and south <= lat <= north):
            skipped_outside += 1
            continue

        # Mirror the legacy collect_trees properties; downstream consumers
        # (registry resolver, debugger views) can use them. Stamp the
        # globally-configured species + size from QSettings (or the
        # registry fallback) so the inference engine can resolve the
        # mesh per-feature without server-side guesswork.
        #
        # NOTE: every per-feature attribute is fed through
        # _to_json_primitive — QGIS attribute reads can hand back
        # QVariant-wrapped values (NULL fields, dates, list-typed
        # columns) that copy.deepcopy can't pickle. The SDK's
        # assign_vegetation_to_tiles deep-copies each feature per
        # overlapping tile, so a single un-unwrapped QVariant tanks the
        # whole submit (we hit "cannot pickle 'QVariant' object" on a
        # Milan run before this conversion was added).
        props: Dict[str, Any] = {
            "source_layer": layer.name(),
            "geometry_type": "trees",
        }
        if species is not None:
            props["tree_type"] = _to_json_primitive(species)
        if size is not None:
            props["tree_size"] = _to_json_primitive(size)
        # Pass per-feature attributes through too so any per-feature
        # species/size columns (when the layer carries them) override the
        # global QSettings defaults at the inference engine.
        for name in field_names:
            try:
                props[name] = _to_json_primitive(feat[name])
            except Exception:
                continue

        feat_id = str(int(feat.id()))
        out[feat_id] = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
            "properties": props,
        }

    elapsed = time.monotonic() - t0
    logger.info(
        "collect_qgis_area_vegetation: kept=%d; skipped (geom=%d outside=%d "
        "non_point=%d) in %.2fs",
        len(out), skipped_geom, skipped_outside, skipped_non_point, elapsed,
    )
    return out
