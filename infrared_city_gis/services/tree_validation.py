"""Validate a tree point layer against the Infrared vegetation input contract.

Counts, strictly INSIDE the selected simulation area polygon (unlike the
collector, which also ships nearby context trees within a ~100 m margin —
those affect the result but would be misleading in the dialog's counts):

* ``detected`` — tree point features inside the area
* ``with_type_props`` — points whose tree-type attribute (``genusCode`` & co.)
  resolves to a supported type in the vegetation registry
* ``with_osm_id`` — points carrying a dedup id (``osmid``/``@id``/…)
* ``type_counts`` — per resolved type (display name), how many points

The backend contract (utilities-service ``/convert/geojson-to-mesh``): the
tree mesh is picked ONLY by ``properties.modelId`` — a vegetation-registry
key. The user tags layers with the human-friendly ``genusCode`` (documented in
docs/vegetation-input.md); ``qgis_area_vegetation`` translates it to
``modelId`` at collection time using the same resolver used here. A point
whose type attribute doesn't resolve gets the backend's default model, so the
run dialog surfaces how many points resolved (and to what) before submitting.
Matching is case-insensitive on both attribute name and value.
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
    with_type_props: int = 0      # points resolving to a supported tree type
    with_osm_id: int = 0          # points with a dedup id
    non_point_skipped: int = 0    # non-point features skipped
    type_counts: Dict[str, int] = field(default_factory=dict)  # display name -> count

    @property
    def has_any_type(self) -> bool:
        """Whether any tree in the area resolves to a supported tree type."""
        return self.with_type_props > 0


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
                continue

        if _present_keys(props_lower, OSM_ID_KEYS):
            result.with_osm_id += 1

        resolved = resolve_tree_model(props_lower, resolver)
        if resolved is not None:
            _model_id, entry = resolved
            result.with_type_props += 1
            name = entry.get("displayName") or entry.get("genusCode") or "?"
            counts[name] = counts.get(name, 0) + 1

    result.type_counts = counts
    logger.info(
        "Tree validation: detected=%d with_type_props=%d with_osm_id=%d "
        "non_point_skipped=%d types=%s",
        result.detected, result.with_type_props, result.with_osm_id,
        result.non_point_skipped, result.type_counts,
    )
    return result
