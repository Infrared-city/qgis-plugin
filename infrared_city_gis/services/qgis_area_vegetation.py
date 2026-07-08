"""Collect vegetation point features from a selected QGIS layer for the SDK
area path.

The SDK's vegetation contract is much lighter than the buildings one:
``client.run_area_and_wait(..., vegetation=...)`` accepts a
``Mapping[str, dict]`` keyed by feature ID, where each value is a
GeoJSON-like ``Feature`` dict with at least ``geometry.coordinates =
[lon, lat]`` (WGS84). The SDK handles the per-tile distribution; the
backend's geojson-to-mesh conversion picks the tree mesh by
``properties.modelId`` (a vegetation-registry key) and scales it by
``properties.height`` / ``crownDiameter``. This collector resolves each
feature's OSM type tags (``species``/``genus`` — see docs/vegetation-input.md)
to that ``modelId`` when they match a registry species; unmatched trees are
sent WITHOUT a modelId and the backend resolves them to an archetype.
Catalog-override mode stamps one selected type
on every tree instead.

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
import uuid
from typing import Any, Dict, Optional, Tuple

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

# Layer attribute names (lower-cased) whose VALUE identifies a precise registry
# species (docs/vegetation-input.md). OSM ``species``/``genus`` are the common
# ones; ``latinName``/``modelId`` let a layer name a registry entry directly.
# Checked in order — first attribute whose value resolves against the registry
# wins; anything unmatched is archetyped by the backend.
TREE_TYPE_ATTRIBUTE_KEYS: Tuple[str, ...] = (
    "genus", "species", "latinname", "modelid",
)


def _load_client_models() -> Dict[str, dict]:
    """Return ``vegetation_registry.json``'s ``clientModels`` (id → entry).

    The dict key is the registry model id — the value the backend's
    geojson-to-mesh conversion expects as ``properties.modelId``. Empty dict
    when the registry is missing (API key never saved / fetch failed).
    """
    veg_path = os.path.join(
        QgsApplication.qgisSettingsDirPath(),
        "infrared_city_gis", "settings", "vegetation_registry.json",
    )
    try:
        with open(veg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        models = data.get("clientModels") or {}
        return models if isinstance(models, dict) else {}
    except Exception as e:
        logger.warning(
            "Vegetation: could not read vegetation_registry.json (%s)", e,
        )
        return {}


def load_tree_type_resolver() -> Dict[str, Tuple[str, dict]]:
    """Build ``lower-cased alias -> (model_id, registry entry)``.

    Aliases per model: its ``latinName`` (matched against the OSM ``species``
    tag), ``displayName`` and the raw registry id, so a layer can also name a
    registry entry directly. Translates a tree's OSM type tag into the
    ``modelId`` the backend conversion needs for a PRECISE mesh — an UNKNOWN
    ``modelId`` there is a hard 404, so we only ever send resolved ids;
    unmatched trees are sent modelId-less and archetyped by the backend.
    """
    resolver: Dict[str, Tuple[str, dict]] = {}
    for model_id, entry in _load_client_models().items():
        if not isinstance(entry, dict):
            continue
        aliases = (
            model_id,
            entry.get("latinName"),
            entry.get("displayName"),
        )
        for alias in aliases:
            if isinstance(alias, str) and alias.strip():
                resolver[alias.strip().lower()] = (model_id, entry)
    return resolver


def resolve_tree_model(
    props: Dict[str, Any],
    resolver: Dict[str, Tuple[str, dict]],
) -> Optional[Tuple[str, dict]]:
    """Resolve a feature's tree type to ``(model_id, registry entry)``.

    Scans ``props`` (any key casing) for the first
    :data:`TREE_TYPE_ATTRIBUTE_KEYS` attribute whose value matches a registry
    alias (case-insensitive). ``None`` when nothing resolves.
    """
    lower = {str(k).lower(): v for k, v in props.items()}
    for key in TREE_TYPE_ATTRIBUTE_KEYS:
        value = lower.get(key)
        if isinstance(value, str) and value.strip():
            hit = resolver.get(value.strip().lower())
            if hit is not None:
                return hit
    return None


def _resolve_current_species() -> Tuple[Optional[str], Optional[dict], Optional[str]]:
    """Read the user's tree species + size from QSettings.

    The Tree Catalog dialog persists the user's choice under keys
    ``infrared_city/tree_type`` (display name from
    ``vegetation_registry.json``'s ``clientModels``) and
    ``infrared_city/tree_size`` (one of ``"small" | "medium" | "large"``).

    If the user hasn't visited the Tree Catalog yet, both come back as
    ``None`` and we fall back to the first model in the registry — same
    behaviour as the legacy ``convert_tree_to_dotbim`` resolver, so
    runs are never silently empty when the species hasn't been configured.

    Returns ``(model_id, client_model_dict, size_label)`` — ``model_id`` is
    the registry key, i.e. the backend conversion's ``modelId``.
    """
    settings = QSettings()
    tree_type = settings.value("infrared_city/tree_type", None)
    tree_size = settings.value("infrared_city/tree_size", None)

    client_models = _load_client_models()

    if tree_type:
        for model_id, model in client_models.items():
            if isinstance(model, dict) and model.get("displayName") == tree_type:
                logger.info(
                    "Vegetation: found configured tree_type %r in registry",
                    tree_type,
                )
                return model_id, model, tree_size

    # Fallback: first entry in registry. Mirrors legacy fallback.
    for model_id, model in client_models.items():
        if isinstance(model, dict) and model.get("displayName"):
            logger.info(
                "Vegetation: no QSettings tree_type configured; "
                "falling back to first registry model %r",
                model.get("displayName"),
            )
            return model_id, model, tree_size

    return None, None, tree_size


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
    use_catalog_type: bool = False,
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

    # Catalog-override mode resolves a single registry species (from the tree
    # catalog / QSettings) and stamps it onto every tree. Layer mode (default)
    # resolves each feature's OWN OSM type tags (species/genus & co.) against
    # the registry and stamps the matching ``modelId`` for a precise mesh
    # (unknown ids 404 there, so unresolved features are sent WITHOUT one and
    # the backend resolves them to an archetype — untagged → broadleaf).
    model_id = None
    model = None
    size = None
    height = 0
    crownDiameter = 0
    resolver: Dict[str, Tuple[str, dict]] = {}
    if use_catalog_type:
        model_id, model, size = _resolve_current_species()
        # No species model — vegetation_registry.json missing/empty (e.g. API
        # key never saved, or registry fetch failed). Degrade cleanly: run
        # without vegetation instead of crashing on model.get(...) below.
        if model is None:
            logger.warning(
                "collect_qgis_area_vegetation: tree-catalog mode but no vegetation "
                "model resolved (registry missing/empty) — running without trees."
            )
            return {}
        if size == "small":
            height = model.get("heightRange", [0, 0])[0] if model.get("heightRange") else model.get("height", 0)
            crownDiameter = model.get("crownDiameterRange", [0, 0])[0] if model.get("crownDiameterRange") else model.get("crownDiameter", 0)
        elif size == "large":
            height = model.get("heightRange", [0, 0])[-1] if model.get("heightRange") else model.get("height", 0)
            crownDiameter = model.get("crownDiameterRange", [0, 0])[-1] if model.get("crownDiameterRange") else model.get("crownDiameter", 0)
        else:  # medium / unset
            height = model.get("height", 0)
            crownDiameter = model.get("crownDiameter", 0)
    else:
        resolver = load_tree_type_resolver()

    logger.info(
        "collect_qgis_area_vegetation: layer=%r CRS=%s mode=%s bbox+margin "
        "(W=%.6f S=%.6f E=%.6f N=%.6f) margin=%.0fm species=%r size=%r "
        "height=%.2f crownDiameter=%.2f",
        layer.name(), layer_crs.authid(), "catalog" if use_catalog_type else "osm",
        west, south, east, north, context_margin_m,
        model.get("displayName") if model else None, size, height, crownDiameter,
    )

    field_names = [f.name() for f in layer.fields()]
    out: Dict[str, dict] = {}
    skipped_geom = 0
    skipped_outside = 0
    skipped_non_point = 0
    skipped_duplicate = 0
    id_collisions = 0
    resolved_count = 0

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

        # Pass every layer attribute through untouched — the type/size
        # resolution below only ADDS keys (modelId, defaults), never
        # rewrites what the user tagged.
        for name in field_names:
            try:
                props[name] = _to_json_primitive(feat[name])
            except Exception:
                continue

        if model is not None:
            # Catalog-override: modelId is authoritative (the backend picks the
            # mesh by it); the rest is metadata for traceability.
            props["modelId"] = model_id
            props["species"] = _to_json_primitive(model.get("latinName", 0))
            props["height"] = _to_json_primitive(height)
            props["crownDiameter"] = _to_json_primitive(crownDiameter)
            props["diameter_crown"] = _to_json_primitive(crownDiameter)
            props["leaf_cycle"] = _to_json_primitive(model.get("leafCycles", 0))
        elif resolver:
            resolved = resolve_tree_model(props, resolver)
            if resolved is not None:
                r_model_id, entry = resolved
                props["modelId"] = r_model_id
                resolved_count += 1
                # Size is optional in the layer: when absent, default to the
                # catalog dimensions of the RESOLVED type (documented in
                # docs/vegetation-input.md) instead of the backend's generic
                # 6 m / 4 m fallback.
                lower = {str(k).lower(): v for k, v in props.items()}
                h = lower.get("height")
                if h in (None, "") or (isinstance(h, str) and not h.strip()):
                    props["height"] = _to_json_primitive(entry.get("height", 0))
                c = lower.get("crowndiameter")
                if c in (None, "") or (isinstance(c, str) and not c.strip()):
                    c = lower.get("diameter_crown")
                if c in (None, "") or (isinstance(c, str) and not c.strip()):
                    crown = _to_json_primitive(entry.get("crownDiameter", 0))
                    props["crownDiameter"] = crown
                    props["diameter_crown"] = crown

        # Key each tree for the payload. The key's ONLY job is per-submit
        # uniqueness: the SDK's assign_vegetation_to_tiles distributes this dict
        # per tile as-is (its osmid dedup runs only on server-FETCHED tiles, not
        # this user layer), so a collision here would silently drop a tree — but
        # the key is NOT a simulation input. The result depends on each tree's
        # position + properties, not its key, so an unstable key across runs is
        # harmless (same trees → same result).
        # Order: a stable attribute id, else the QGIS feature id (best-effort —
        # for an OGR-loaded GeoJSON this IS the feature-level `id`, e.g. a raw
        # OSM export's `"id": 2016564072`; memory/edited layers may renumber it,
        # harmless per the above), else a fresh UUID.
        feat_id = props.get("osm_id") or props.get("osmid") or props.get("@id")
        if feat_id in (None, ""):
            qgis_fid = feat.id()
            if qgis_fid not in (None, "", -1):
                feat_id = qgis_fid
        if feat_id in (None, ""):
            feat_id = str(uuid.uuid4())
        feat_id = str(feat_id)

        coords = [round(float(lon), 7), round(float(lat), 7)]
        if feat_id in out:
            prev = out[feat_id]["geometry"]["coordinates"]
            if [round(c, 7) for c in prev] == coords:
                # Same id at the same position: a true duplicate of the same
                # tree (e.g. copied features) — keep one.
                skipped_duplicate += 1
                continue
            # Same id at a DIFFERENT position: the id column isn't unique
            # (common with imported GeoJSON / generic id fields). Keep the
            # tree under a disambiguated key; the original id stays in
            # properties.
            id_collisions += 1
            suffix = 2
            base_id = feat_id
            while feat_id in out:
                feat_id = f"{base_id}#{suffix}"
                suffix += 1

        out[feat_id] = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
            "properties": props,
        }

    if id_collisions:
        logger.warning(
            "collect_qgis_area_vegetation: %d id collision(s) in layer %r — "
            "id column is not unique; colliding trees kept under "
            "disambiguated keys",
            id_collisions, layer.name(),
        )

    elapsed = time.monotonic() - t0
    logger.info(
        "collect_qgis_area_vegetation: kept=%d (type-resolved=%d); skipped "
        "(geom=%d outside=%d non_point=%d duplicate=%d) in %.2fs",
        len(out), resolved_count,
        skipped_geom, skipped_outside, skipped_non_point, skipped_duplicate,
        elapsed,
    )

    return out
