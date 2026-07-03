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
  missing we fall back to the canonical six.

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
# them, so the collector re-stamps Z using the server's MaterialCategory
# order (asphalt = the bbox-covering background, lowest; water on top).
# Without this, overlays would tie with the asphalt background at z=0 and
# sensors could read asphalt everywhere.
MATERIAL_Z_ORDER: Tuple[str, ...] = (
    "asphalt", "building", "concrete", "vegetation", "soil", "water",
)
_Z_STEP_M = 0.00001  # clean-v3's _Z_STEP_M


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


# Canonical fallback catalog — matches the server's MaterialCategory enum and
# the SDK's _KNOWN_MATERIAL_NAMES. Colors mirror the SDK demo palette.
# NOTE: the material is "vegetation" (green surfaces), NOT "grass" — the old
# display helper used "grass", a key the server never returns.
DEFAULT_MATERIALS: Dict[str, dict] = {
    "asphalt": {"displayName": "Asphalt", "color": (119, 119, 119)},
    "building": {"displayName": "Building", "color": (232, 168, 90)},
    "concrete": {"displayName": "Concrete", "color": (189, 189, 189)},
    "vegetation": {"displayName": "Vegetation (green surfaces)", "color": (155, 208, 163)},
    "soil": {"displayName": "Soil", "color": (202, 164, 114)},
    "water": {"displayName": "Water", "color": (126, 182, 232)},
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
    :data:`DEFAULT_MATERIALS` so materials the registry doesn't carry (e.g.
    ``building``) keep their default styling instead of falling to gray.
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
    """
    found: Dict[str, List[QgsVectorLayer]] = {}
    for layer in QgsProject.instance().mapLayers().values():
        if not isinstance(layer, QgsVectorLayer):
            continue
        material = _material_from_layer_name(layer.name())
        if material is None:
            continue
        found.setdefault(material, []).append(layer)
    for layers in found.values():
        layers.sort(key=lambda ly: ly.name().lower())
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
                        continue
                if geom.intersects(area_geom):
                    n += 1
        if n:
            counts[material] = n
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

    Returns ``{material_name: FeatureCollection}``; materials with no
    features in the envelope are omitted entirely — sending an empty
    FeatureCollection would tell the server "this surface type is absent"
    rather than "unspecified".
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

    # Stacking order among the materials actually present (server semantics:
    # index within the input layer list drives Z). Materials beyond the known
    # canon go on top, alphabetically, so a hand-drawn future material still
    # beats the asphalt background.
    known = [m for m in MATERIAL_Z_ORDER if m in layers]
    unknown = sorted(m for m in layers if m not in MATERIAL_Z_ORDER)
    z_of = {m: (i + 1) * _Z_STEP_M for i, m in enumerate(known + unknown)}

    out: Dict[str, dict] = {}
    skipped_non_polygon = 0
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
                        continue
                if not geom.intersects(bbox_geom):
                    continue
                try:
                    geometry = json.loads(geom.asJson())
                    geometry["coordinates"] = _with_z(
                        geometry["coordinates"], z_of[material],
                    )
                except Exception:
                    continue
                # Unwrap QVariants — the SDK deep-copies features per tile
                # and a raw QVariant in properties breaks pickling (same
                # failure mode the vegetation collector hit).
                props = {}
                for name in field_names:
                    try:
                        props[name] = _to_json_primitive(feat[name])
                    except Exception:
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

    logger.info(
        "collect_ground_materials: %d material layer(s) in, %d with features "
        "(%s); %d non-polygon feature(s) skipped",
        len(layers), len(out),
        ", ".join(f"{k}={len(v['features'])}" for k, v in out.items()) or "none",
        skipped_non_polygon,
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

    Per the SDK guidance (README "Vegetation & Ground Materials" + the
    analysis-tour demo): thermal analyses (UTCI, TCS) and the solar/daylight
    family use surface materials (emissivity / reflectance); wind-based
    analyses and SVF are pure geometry and ignore them — for those the
    dialog hides the section entirely and the runners skip collection.
    ``None`` (unknown) errs on showing the option.
    """
    if analysis_type is None:
        return True
    from ..models.analysis import AnalysisType

    return analysis_type not in {
        AnalysisType.WIND_SPEED,
        AnalysisType.PEDESTRIAN_WIND_COMFORT,
        AnalysisType.SKY_VIEW_FACTORS,
    }


def stamp_material_properties(layers: Dict[str, dict]) -> Dict[str, dict]:
    """Return ``layers`` with ``properties.material`` stamped per feature.

    ``run_area``'s tile assignment stamps the material itself, but the
    single-tile path embeds the payload as-is — auto-fetched layers (which
    come straight from the SDK, unstamped) need this before embedding or
    the Lambda's emissivity lookup falls back to the 0.97 default.
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
    return stamped
