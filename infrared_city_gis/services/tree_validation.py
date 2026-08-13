"""Validate a tree point layer against the Infrared vegetation input contract.

Counts, strictly INSIDE the selected simulation area polygon (unlike the
collector, which also ships nearby context trees within a ~100 m margin —
those affect the result but would be misleading in the dialog's counts):

* ``detected`` — tree point features inside the area
* ``with_type_props`` — points resolving to a PRECISE registry species
  (matched via their OSM ``species``/``genus`` tag against the registry)
* ``with_archetype_signal`` — points that don't match a precise species but
  carry an OSM tag (``species``/``genus``/``leaf_type``/``tree-type``) the
  backend resolves to an archetype (broadleaf/conifer/columnar/palm)
* ``default_count`` — points with no type signal at all → server default
  (broadleaf)
* ``with_osm_id`` — points carrying a dedup id (``osmid``/``@id``/…)
* ``type_counts`` — per resolved precise species (display name), how many points

No tree is ever "unsupported": a point that resolves to a precise registry
species is submitted with that ``modelId`` (precise mesh); everything else is
submitted WITHOUT a modelId and the backend resolves it to an archetype from
its OSM tags, falling back to broadleaf. The dialog surfaces this breakdown so
the user knows what will render before submitting. Matching is case-insensitive
on both attribute name and value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from qgis.core import (
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
    load_tree_type_resolver,
    resolve_tree_model,
)

# Dedup id keys — the ONLY property the SDK itself reads for a tree. Lower-cased
# for case-insensitive attribute matching.
OSM_ID_KEYS: Tuple[str, ...] = ("osmid", "osm_id", "@id", "id")

# OSM-native tags the backend's archetype resolver reads for a tree whose type
# doesn't resolve to a precise registry species. Presence of ANY of these means
# the tree gets a real archetype (broadleaf/conifer/columnar/palm) server-side
# instead of the plain broadleaf default.
ARCHETYPE_SIGNAL_KEYS: Tuple[str, ...] = (
    "species", "genus", "leaf_type", "tree-type", "tree_type",
)


def _is_present(value) -> bool:
    """True if a QGIS attribute value is a real, non-empty value.

    ``_to_json_primitive`` unwraps QVariant NULLs to ``None``.
    """
    prim = _to_json_primitive(value)
    if prim is None:
        return False
    if isinstance(prim, str) and not prim.strip():
        return False
    return True


def _present_keys(props_lower: Dict[str, object], keys: Tuple[str, ...]) -> List[str]:
    """Return the subset of ``keys`` present (non-empty) in ``props_lower``."""
    return [k for k in keys if k in props_lower and _is_present(props_lower[k])]


@dataclass
class TreeValidationResult:
    """Outcome of scanning a tree layer over the selected area."""

    detected: int = 0             # point features inside the area
    with_type_props: int = 0      # points resolving to a precise registry species
    with_archetype_signal: int = 0  # not-precise, but carry an OSM archetype signal
    with_osm_id: int = 0          # points with a dedup id
    non_point_skipped: int = 0    # non-point features skipped
    type_counts: Dict[str, int] = field(default_factory=dict)  # display name -> count

    @property
    def default_count(self) -> int:
        """Trees with no type signal at all → server default (broadleaf)."""
        return max(0, self.detected - self.with_type_props - self.with_archetype_signal)


def validate_tree_layer(
    polygon: dict,
    layer: QgsVectorLayer,
) -> TreeValidationResult:
    """Count tree points + those resolving to a supported type inside the area.

    Uses the same CRS transform as
    :func:`qgis_area_vegetation.collect_qgis_area_vegetation`, but filters by
    true point-in-polygon on the selected area — NOT the collector's
    bbox + 100 m context margin. The margin trees are still submitted (they
    shade / disturb airflow into the area) but counting them here would make
    the dialog report trees the user visibly placed outside their selection.
    """
    result = TreeValidationResult()

    layer_crs = layer.crs()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    transform: Optional[QgsCoordinateTransform] = (
        QgsCoordinateTransform(layer_crs, wgs84, QgsProject.instance())
        if layer_crs.isValid() and layer_crs != wgs84
        else None
    )
    area_geom = QgsGeometry.fromPolygonXY([
        [QgsPointXY(float(x), float(y)) for x, y in ring]
        for ring in polygon["coordinates"]
    ])
    area_bbox = area_geom.boundingBox()

    field_names = [f.name() for f in layer.fields()]
    resolver = load_tree_type_resolver()
    counts: Dict[str, int] = {}
    # Counted, not logged in the loop: a field that fails to convert fails for
    # every feature, and one line per tree would swamp the log.
    skipped_attrs = 0

    for feat in layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        if QgsWkbTypes.geometryType(geom.wkbType()) != QgsWkbTypes.PointGeometry:
            result.non_point_skipped += 1
            continue

        pt = geom.asPoint()
        if transform is not None:
            lon, lat = transform.transform(pt.x(), pt.y())
        else:
            lon, lat = pt.x(), pt.y()

        # Cheap bbox reject first; exact polygon containment for the rest.
        pt_xy = QgsPointXY(lon, lat)
        if not area_bbox.contains(pt_xy) or not area_geom.contains(pt_xy):
            continue

        result.detected += 1

        props_lower: Dict[str, object] = {}
        for name in field_names:
            try:
                props_lower[name.lower()] = _to_json_primitive(feat[name])
            except Exception:
                skipped_attrs += 1
                continue

        if _present_keys(props_lower, OSM_ID_KEYS):
            result.with_osm_id += 1

        resolved = resolve_tree_model(props_lower, resolver)
        if resolved is not None:
            _model_id, entry = resolved
            result.with_type_props += 1
            name = entry.get("displayName") or entry.get("latinName") or "?"
            counts[name] = counts.get(name, 0) + 1
        elif _present_keys(props_lower, ARCHETYPE_SIGNAL_KEYS):
            # No precise registry match, but the tree carries an OSM tag the
            # backend resolves to an archetype (broadleaf/conifer/…) — so it
            # still renders as its shape class, not a bare default.
            result.with_archetype_signal += 1

    result.type_counts = counts
    logger.info(
        "Tree validation: detected=%d precise=%d archetype_signal=%d default=%d "
        "with_osm_id=%d non_point_skipped=%d unreadable_attrs=%d types=%s",
        result.detected, result.with_type_props, result.with_archetype_signal,
        result.default_count, result.with_osm_id, result.non_point_skipped,
        skipped_attrs, result.type_counts,
    )
    return result
