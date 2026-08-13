"""Ground-material catalog, layer discovery, collection, and validation.

The SDK/backend contract (see docs/ground-materials.md):

* Simulation input is ONE mapping ``{material_name: GeoJSON FeatureCollection}``
  passed as ``run_area_and_wait(..., ground_materials=...)``. The dict KEY is
  the material identity — the server's emissivity table is looked up by name,
  so features are never merged across materials.
* Material names come from the materials registry
  (``GET /v2/utils/registry/materials`` → ``settings/materials_registry.json``);
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
from .qgis_area_vegetation import (
    _DEFAULT_CONTEXT_MARGIN_M,
    _polygon_wgs84_bbox_with_margin,
)

GROUND_LAYER_PREFIX = "ground-"

# Z-stacking, mirroring utilities-service clean-v3: each material layer sits
# at its own tiny Z offset and the UTCI Lambda raycasts DOWNWARD with
# multiple_hits=False — the highest surface wins where layers overlap. The
# fetched GeoJSON carries these Z values, but QGIS 2D memory layers drop
# them, so the collector re-stamps Z.
#
# Order is the server's own ``_CANONICAL_Z_ORDER`` (utilities-service
# ``ground_material/fgb_sources.py``), which ``merge_fgb_layers`` already
# applies when collecting: asphalt lowest (the bbox-covering gap-fill
# background), vegetation on top. Verified 2026-07-28 (arcgis-plugin
# ``GroundMaterialNaming.cs``) by diffing an auto-fetch payload straight
# from clean-v3 against a manual one on the same tile: asphalt=1e-5,
# concrete=2e-5, water=3e-5, soil=4e-5, vegetation=5e-5.
#
# These five are the whole set. The pre-fgb order this constant used to carry
# (asphalt, building, concrete, vegetation, soil, water) came from the Mapbox
# ``regroup_layers`` enum walk, which pre-created a key per ``MaterialCategory``
# — including ``building``, whose feature loop was already commented out, so it
# only ever shipped empty. No source emits it now, the live materials registry
# does not carry it, and the model has no props row for it. Buildings are 3D
# volumes in the ``buildings`` payload, not a ground surface.
#
# Z is assigned by FIXED index (not rank among the materials present) so a
# manual run with a subset — vegetation without soil, say — still puts
# vegetation at 5e-5 exactly as the server does, instead of collapsing it
# to a lower slot and letting water override it.
MATERIAL_Z_ORDER: Tuple[str, ...] = (
    "asphalt", "concrete", "water", "soil", "vegetation",
)
_Z_STEP_M = 0.00001  # clean-v3's _Z_STEP_M


def _material_z_offsets(materials) -> Dict[str, float]:
    """Fixed-slot Z offset per material — see :data:`MATERIAL_Z_ORDER`.

    Materials outside the known canon stack above all known ones,
    alphabetically, so a hand-drawn future material still beats the
    asphalt background.
    """
    unknown = sorted(m for m in materials if m not in MATERIAL_Z_ORDER)
    offsets: Dict[str, float] = {}
    for material in materials:
        if material in MATERIAL_Z_ORDER:
            idx = MATERIAL_Z_ORDER.index(material)
        else:
            idx = len(MATERIAL_Z_ORDER) + unknown.index(material)
        offsets[material] = (idx + 1) * _Z_STEP_M
    return offsets


def _in_z_order(layers: Dict[str, dict]) -> Dict[str, dict]:
    """Re-key ``layers`` into :data:`MATERIAL_Z_ORDER` stacking order.

    The stamped Z is not the only thing that carries the stack: both
    clean-v3 and infrared-core's ``ground_clean`` (which the TCI/TCS
    scheduler runs in-process on the per-tile payload) re-stamp
    ``z = (i + 1) * z_step`` by dict INSERTION order, and insert the
    full-bbox default backdrop at asphalt's index. So the key order of the
    dict we send decides the stack — asphalt must come first or the
    backdrop lands on top of everything. Emitting in canonical order also
    makes a manual payload byte-order-identical to an auto-fetched one.
    """
    known = [m for m in MATERIAL_Z_ORDER if m in layers]
    unknown = sorted(m for m in layers if m not in MATERIAL_Z_ORDER)
    return {m: layers[m] for m in known + unknown}


def _with_z(coords, z):
    """Return ``coords`` with every position rewritten to ``[x, y, z]``.

    Works for any GeoJSON nesting depth; an existing third element is
    replaced.
    """
    if not isinstance(coords, (list, tuple)) or not coords:
        return coords
    if isinstance(coords[0], (int, float)):
        return [float(coords[0]), float(coords[1]), z]
    return [_with_z(c, z) for c in coords]


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
    # Counted rather than logged per feature: a layer in the wrong CRS fails on
    # every one of its features, and a per-feature log line would bury the run.
    skipped_transform = 0
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
                        skipped_transform += 1
                        continue
                if geom.intersects(area_geom):
                    n += 1
        if n:
            counts[material] = n
    if skipped_transform:
        logger.warning(
            "ground_material_counts: %d feature(s) could not be reprojected to "
            "WGS84 and were not counted", skipped_transform,
        )
    return counts


def collect_ground_materials(
    polygon: dict,
    layers: Dict[str, List[QgsVectorLayer]],
    *,
    context_margin_m: float = _DEFAULT_CONTEXT_MARGIN_M,
) -> Dict[str, dict]:
    """Build the SDK ``ground_materials`` mapping from ``ground-*`` layers.

    Each MATERIAL is read into its own WGS84 FeatureCollection — features
    from several layers of the same material (numbered fetches over
    different areas) are merged under one key, but never across materials:
    the material identity is the dict key. Features are filtered to the
    polygon bbox + context margin (same envelope as the
    buildings/vegetation collectors: surfaces just outside the selection
    still influence the thermal result inside it). Non-polygon geometries
    are skipped.

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
    west, south, east, north = _polygon_wgs84_bbox_with_margin(
        polygon, context_margin_m,
    )
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
            field_names = [f.name() for f in layer.fields()]
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
                try:
                    geometry = json.loads(geom.asJson())
                    geometry["coordinates"] = _with_z(
                        geometry["coordinates"], z_of[material],
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


def stamp_material_properties(layers: Dict[str, dict]) -> Dict[str, dict]:
    """Return ``layers`` with ``properties.material`` stamped per feature.

    ``run_area``'s tile assignment stamps the material itself, but the
    single-tile path embeds the payload as-is — auto-fetched layers (which
    come straight from the SDK, unstamped) need this before embedding or
    the Lambda's emissivity lookup falls back to the 0.97 default.

    The result is also re-keyed into :data:`MATERIAL_Z_ORDER`. The SDK hands
    back whatever key order ``/ground-material/collect`` produced; that is
    canonical today (``merge_fgb_layers``), but the order decides the server's
    z re-stamp, so don't depend on it silently — an asphalt key that drifted
    last would put the full-bbox backdrop on top of the whole stack.
    """
    stamped: Dict[str, dict] = {}
    for material, fc in layers.items():
        features = []
        for feat in (fc or {}).get("features", []):
            features.append({
                **feat,
                "properties": {**(feat.get("properties") or {}), "material": material},
            })
        stamped[material] = {"type": "FeatureCollection", "features": features}
    return _in_z_order(stamped)
