"""Ground-material catalog, layer discovery, collection, and validation.

The SDK/backend contract (see docs/ground-materials.md):

* Simulation input is ONE mapping ``{material_name: GeoJSON FeatureCollection}``
  passed as ``run_area_and_wait(..., ground_materials=...)``. The dict KEY is
  the material identity — the server's emissivity table is looked up by name,
  so features are never merged across materials.
* Material names come from the materials registry
  (``registry.infrared.city/materials/latest.json`` →
  ``settings/materials_registry.json``);
  the SDK only WARNS on names it doesn't know, so a registry-driven list stays
  forward-compatible when the backend adds materials. When the registry is
  missing we fall back to the canonical five.
* An unknown material name is NOT rejected by the server — the model's
  ``props_for`` table (lambda-models ``rust/solar-models/src/material_props.rs``)
  falls through to an ``__unknown__`` row (albedo 0.20, dt_max 5.0), i.e. a
  fabricated mid-range surface. So a typo (``ground-asphlat``) or an unrelated
  polygon layer (``ground-parcels``) would quietly change the thermal result
  instead of failing. Such layers are therefore not offered in the run dialog
  — see :func:`supported_materials` / :func:`ground_material_layers`.

In QGIS, each material lives in its own vector layer named
``ground-<material>`` (e.g. ``ground-asphalt``) — created by the fetch dialog
and freely editable by the user. Note: ``ground-vegetation`` is green SURFACE
polygons (grass, parks), NOT trees — trees are separate ``tree-*`` point
layers.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..infrared_logger import logger
from .geotiff import _to_json_primitive
from .ground_extent import ground_envelope
from .ground_zorder import _in_z_order, _material_z_offsets, _with_z

GROUND_LAYER_PREFIX = "ground-"

# Canonical fallback catalog — the five materials the live registry carries and
# the only five the model has props for. Colors mirror the SDK demo palette.
# NOTE: the material is "vegetation" (green surfaces), NOT "grass" — the old
# display helper used "grass", a key the server never returns.
DEFAULT_MATERIALS: Dict[str, dict] = {
    "asphalt": {"displayName": "Asphalt", "color": (119, 119, 119)},
    "concrete": {"displayName": "Concrete", "color": (189, 189, 189)},
    "water": {"displayName": "Water", "color": (126, 182, 232)},
    "soil": {"displayName": "Soil", "color": (202, 164, 114)},
    "vegetation": {"displayName": "Vegetation (green surfaces)", "color": (155, 208, 163)},
}


def _materials_registry_path() -> str:
    return os.path.join(
        QgsApplication.qgisSettingsDirPath(),
        "infrared_city_gis", "settings", "materials_registry.json",
    )


def load_material_catalog() -> Dict[str, dict]:
    """Return ``{name: {"displayName", "color" (r, g, b), "opacity" 0..1}}``.

    Registry-driven: reads ``materials_registry.json`` (``materials`` is keyed
    by uuid; each entry has ``name``/``displayName``/``diffuseColor`` with
    0..1 floats and ``opacity``). Registry entries are overlaid on
    :data:`DEFAULT_MATERIALS`, so a material the backend adds later gets its
    own styling while the canonical five keep theirs when the registry is
    unavailable.
    """
    catalog: Dict[str, dict] = {
        name: {"opacity": 1.0, **entry} for name, entry in DEFAULT_MATERIALS.items()
    }
    try:
        with open(_materials_registry_path(), "r", encoding="utf-8") as f:
            doc = json.load(f)
        materials = doc.get("materials") or {}
        for entry in materials.values():
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip().lower()
            diffuse = entry.get("diffuseColor")
            if isinstance(diffuse, (list, tuple)) and len(diffuse) >= 3:
                color = tuple(
                    max(0, min(255, int(round(float(c) * 255)))) for c in diffuse[:3]
                )
            else:
                color = catalog.get(name, {}).get("color", (128, 128, 128))
            try:
                opacity = max(0.0, min(1.0, float(entry.get("opacity", 1.0))))
            except (TypeError, ValueError):
                opacity = 1.0
            catalog[name] = {
                "displayName": entry.get("displayName") or name.capitalize(),
                "color": color,
                "opacity": opacity,
            }
    except Exception as e:
        logger.info("Materials registry not available (%s); using defaults", e)
    return catalog


def supported_materials() -> Tuple[str, ...]:
    """Material names it is safe to send as payload dict keys.

    The cached materials registry is the authority (so a material the backend
    adds shows up without a plugin release); the canonical five stand in until
    it has been fetched. An unknown key is not rejected — it silently gets the
    model's ``__unknown__`` surface props — so layers outside this set are not
    offered in the run dialog. See the module docstring.
    """
    return tuple(load_material_catalog().keys())


def _material_from_layer_name(layer_name: str) -> Optional[str]:
    """Material name for a ``ground-*`` layer, or ``None`` when not one.

    The fetch dialog numbers repeated downloads (``ground-asphalt``,
    ``ground-asphalt-2``, …) so users can tell areas apart — a trailing
    ``-<digits>`` is therefore NOT part of the material name.
    """
    lname = layer_name.strip().lower()
    if not lname.startswith(GROUND_LAYER_PREFIX):
        return None
    material = lname[len(GROUND_LAYER_PREFIX):].strip()
    material = re.sub(r"-\d+$", "", material)
    if not material or material.isdigit():
        return None
    return material


def ground_material_layers() -> Dict[str, List[QgsVectorLayer]]:
    """Find project vector layers named ``ground-<material>[-N]``.

    Returns ``{material_name: [layers]}`` — several layers may carry the
    same material (repeated fetches over different areas); all participate,
    and the simulation dialog lets the user tick them individually.
    User-created layers (e.g. a hand-drawn ``ground-water``) join
    automatically.

    Only materials in :func:`supported_materials` are returned. A
    ``ground-<something-else>`` layer — a typo, a stale ``ground-building``,
    an unrelated ``ground-parcels`` — would reach the server as a live
    material key and silently pick up the model's ``__unknown__`` surface
    props, so it is skipped (and logged) rather than offered.
    """
    supported = set(supported_materials())
    found: Dict[str, List[QgsVectorLayer]] = {}
    skipped: Dict[str, int] = {}
    for layer in QgsProject.instance().mapLayers().values():
        if not isinstance(layer, QgsVectorLayer):
            continue
        material = _material_from_layer_name(layer.name())
        if material is None:
            continue
        if material not in supported:
            skipped[material] = skipped.get(material, 0) + 1
            continue
        found.setdefault(material, []).append(layer)
    for layers in found.values():
        layers.sort(key=lambda ly: ly.name().lower())
    if skipped:
        logger.info(
            "ground_material_layers: skipped %s — not a known material (known: %s)",
            ", ".join(f"ground-{m} x{n}" for m, n in sorted(skipped.items())),
            ", ".join(sorted(supported)),
        )
    return found


def _wgs84_transform(layer: QgsVectorLayer) -> Optional[QgsCoordinateTransform]:
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    crs = layer.crs()
    if crs.isValid() and crs != wgs84:
        return QgsCoordinateTransform(crs, wgs84, QgsProject.instance())
    return None


def validate_ground_material_layers(
    polygon: dict,
    layers: Dict[str, List[QgsVectorLayer]],
) -> Dict[str, int]:
    """Count features per material intersecting the selected area polygon.

    Mirrors the tree validation's strictness: TRUE geometry intersection with
    the drawn polygon (not the bbox+margin the collector ships as context), so
    the dialog reports only what the user visibly placed in their selection.
    Counts sum across all layers of a material. Returns
    ``{material_name: count}`` with zero-count materials omitted.
    """
    area_geom = QgsGeometry.fromPolygonXY([
        [QgsPointXY(float(x), float(y)) for x, y in ring]
        for ring in polygon["coordinates"]
    ])
    counts: Dict[str, int] = {}
    # Counted per layer rather than logged per feature: a layer in the wrong CRS
    # fails on every one of its features, so the useful information is which
    # layer, not which feature.
    skipped_transform: Dict[str, int] = {}
    for material, material_layers in layers.items():
        n = 0
        for layer in material_layers:
            transform = _wgs84_transform(layer)
            for feat in layer.getFeatures():
                geom = feat.geometry()
                if geom is None or geom.isEmpty():
                    continue
                if transform is not None:
                    geom = QgsGeometry(geom)
                    try:
                        geom.transform(transform)
                    except Exception:
                        skipped_transform[layer.name()] = (
                            skipped_transform.get(layer.name(), 0) + 1
                        )
                        continue
                if geom.intersects(area_geom):
                    n += 1
        if n:
            counts[material] = n
    for layer_name, skipped in sorted(skipped_transform.items()):
        logger.warning(
            "validate_ground_material_layers: %d feature(s) in layer %r could not be "
            "reprojected to WGS84 and are missing from its count — check the "
            "layer's CRS", skipped, layer_name,
        )
    return counts


def collect_ground_materials(
    polygon: dict,
    layers: Dict[str, List[QgsVectorLayer]],
) -> Dict[str, dict]:
    """Build the SDK ``ground_materials`` mapping from ``ground-*`` layers.

    Each MATERIAL is read into its own WGS84 FeatureCollection — features
    from several layers of the same material (numbered fetches over
    different areas) are merged under one key, but never across materials:
    the material identity is the dict key. Features are kept when they touch
    :func:`ground_envelope` — the area the SDK's own ground read covers
    (wider than the 100 m the building and tree collectors use: surfaces that
    far out still feed the thermal result of the edge tiles). Non-polygon
    geometries are skipped.

    Every coordinate is re-stamped with the material's stacking Z (see
    :data:`MATERIAL_Z_ORDER`) — QGIS 2D layers drop the Z the server put on
    the fetched features, and the thermal raycast needs it to resolve
    overlaps.

    Returns ``{material_name: FeatureCollection}`` in :data:`MATERIAL_Z_ORDER`
    key order (see :func:`_in_z_order` — the key order carries the stack, not
    just the stamped Z); materials with no features in the envelope are
    omitted entirely — sending an empty FeatureCollection would tell the
    server "this surface type is absent" rather than "unspecified".
    """
    west, south, east, north = ground_envelope(polygon)
    bbox_ring = [
        [west, south], [east, south], [east, north], [west, north], [west, south],
    ]
    bbox_geom = QgsGeometry.fromPolygonXY(
        [[QgsPointXY(x, y) for x, y in bbox_ring]]
    )

    z_of = _material_z_offsets(layers)

    out: Dict[str, dict] = {}
    skipped_non_polygon = 0
    # Same reasoning as ground_material_counts: these fail per feature (or per
    # field), so they are counted and reported once instead of logged in a loop.
    skipped_transform = 0
    skipped_geometry = 0
    skipped_props = 0
    for material, material_layers in layers.items():
        features = []
        for layer in material_layers:
            transform = _wgs84_transform(layer)
            # Not the storage's own key: a GeoPackage exposes `fid` as a
            # field, and it means nothing to the model.
            keys = set(layer.primaryKeyAttributes())
            field_names = [
                f.name() for i, f in enumerate(layer.fields()) if i not in keys
            ]
            for feat in layer.getFeatures():
                geom = feat.geometry()
                if geom is None or geom.isEmpty():
                    continue
                if QgsWkbTypes.geometryType(geom.wkbType()) != QgsWkbTypes.PolygonGeometry:
                    skipped_non_polygon += 1
                    continue
                geom = QgsGeometry(geom)
                if transform is not None:
                    try:
                        geom.transform(transform)
                    except Exception:
                        skipped_transform += 1
                        continue
                if not geom.intersects(bbox_geom):
                    continue
                # z_of[material] is looked up OUTSIDE the try on purpose: it is
                # built from the same `layers` keys, so a KeyError here means a
                # caller mutated the mapping mid-run. That is a bug worth
                # raising, not a malformed feature to count and skip.
                material_z = z_of[material]
                try:
                    geometry = json.loads(geom.asJson())
                    geometry["coordinates"] = _with_z(
                        geometry["coordinates"], material_z,
                    )
                except Exception:
                    skipped_geometry += 1
                    continue
                # Unwrap QVariants — the SDK deep-copies features per tile
                # and a raw QVariant in properties breaks pickling (same
                # failure mode the vegetation collector hit).
                props = {}
                for name in field_names:
                    try:
                        props[name] = _to_json_primitive(feat[name])
                    except Exception:
                        skipped_props += 1
                        continue
                # Material stamp: run_area's tile assignment adds this
                # itself, but the single-tile path embeds the payload as-is
                # — without the stamp the Lambda falls back to emissivity
                # 0.97.
                props["material"] = material
                features.append({
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": props,
                })
        if features:
            out[material] = {"type": "FeatureCollection", "features": features}

    # The dict arrives in list-widget (alphabetical) order; the wire order is
    # what the server's z re-stamp reads, so canonicalize before returning.
    out = _in_z_order(out)

    logger.info(
        "collect_ground_materials: %d material layer(s) in, %d with features "
        "(%s); skipped %d non-polygon, %d unprojectable, %d unreadable "
        "geometry, %d unreadable attribute(s)",
        len(layers), len(out),
        ", ".join(f"{k}={len(v['features'])}" for k, v in out.items()) or "none",
        skipped_non_polygon, skipped_transform, skipped_geometry, skipped_props,
    )
    return out


def material_color(material: str) -> Tuple[int, int, int]:
    """RGB display color for a material (catalog-driven, gray fallback)."""
    catalog = load_material_catalog()
    return catalog.get(material, {}).get("color", (128, 128, 128))


def material_opacity(material: str) -> float:
    """Registry ``opacity`` for a material (1.0 fallback)."""
    catalog = load_material_catalog()
    return catalog.get(material, {}).get("opacity", 1.0)


def has_ground_material_support(analysis_type) -> bool:
    """True when ground materials influence this analysis type.

    Ground truth is the backend model code (lambda-models): only the two
    thermal analyses consume ``ground-materials`` — TCI and TCS use it for
    the per-material ground-longwave term. The solar/daylight family and
    SVF *accept* the input but explicitly ignore it
    (``warn_ground_materials_ignored`` in their schedulers), and the wind
    models don't reference it at all. Offering the option for those
    analyses would mislead users into thinking materials affect the
    result, so the dialog hides the section and the runners skip
    collection. ``None`` (unknown) errs on showing the option.
    """
    if analysis_type is None:
        return True
    from ..models.analysis import AnalysisType

    return analysis_type in {
        AnalysisType.THERMAL_COMFORT_INDEX,
        AnalysisType.THERMAL_COMFORT_STATISTICS,
    }
