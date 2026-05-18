"""GeoJSON → dotbim-mesh converter for the run-analysis payload.

Replaces ``process_geojson_file`` with a class-based API that groups
**all polygons of one feature** (Multi/Polygon) into a single output
mesh. Multipolygon buildings stay one mesh — same model as the
``batch_pwc.py`` reference pipeline.

Public entry point::

    Converter.GeojsonToDotbimMesh(geojson, lon, lat, crs)

Returns the same shape the payload expects::

    {mesh_id: {"mesh_id": str, "coordinates": [x,y,z,...], "indices": [...]}}

Coordinates are local metres relative to ``(lon, lat)`` (or, when crs is
geographic, an AEQD projection centred on that point), then shifted by
``+SHIFT_M`` so everything sits in the positive quadrant — same convention
``update_geometry`` enforced. Meshes whose top vertex is below
``MIN_TOP_Z_M`` are dropped (matches the previous filter).

The triangulation + extrusion primitives in ``geojson2dotbim`` are
reused — this module is just the orchestration layer.
"""

from __future__ import annotations

import uuid
from typing import Dict, List, Tuple

from ..infrared_logger import logger
from .geojson2dotbim import (
    convert_to_local_coords,
    create_building_extrusion,
    triangulate_volume,
)

# Field-name priority list, mirrors feature_height.py for consistency.
# Single-value height fields tried first; then top - base pairs.
_HEIGHT_FIELDS = (
    "height", "h", "bldg_height", "building_height", "measuredheight",
    "relhmax", "rel_hmax",
    "hoehe", "h_geb", "geb_hoehe",
    "h_dak_max", "pand_hoogte",
    "hauteur",
    "z_max", "max_height",
)
_TOP_FIELDS = (
    "buildingto", "buildtopelev",
    "abshmax", "abs_hmax",
    "abs_hoehe_oben", "h_top", "roof_height",
)
_BASE_FIELDS = (
    "buildingbo", "buildbotelev",
    "abshmin", "abs_hmin", "relhmin", "rel_hmin",
    "abs_hoehe_unten", "h_base", "ground_height", "h_maaiveld",
)


def _norm(s: str) -> str:
    return (s or "").strip().lower().replace(":", "_").replace(" ", "_")


def _to_float(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        if "m" in s and not s.lower().endswith("ft"):
            return float(s.replace("m", "").strip())
        if "ft" in s or "'" in s:
            return float(s.replace("ft", "").replace("'", "").strip()) * 0.3048
        return float(s.replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _first_match(props: dict, candidates: Tuple[str, ...]) -> float | None:
    """Return the first numeric prop whose normalised name matches a candidate."""
    if not props:
        return None
    norm_lookup = {_norm(k): k for k in props.keys()}
    for cand in candidates:
        orig = norm_lookup.get(cand)
        if orig is None:
            continue
        v = _to_float(props.get(orig))
        if v != 0.0 or props.get(orig) in (0, 0.0, "0"):
            return v
    return None


class Converter:
    """GeoJSON → dotbim-mesh converter.

    Stateless — kept as a class so callers can swap the backend behind
    a stable name without changing the call site.
    """

    DEFAULT_HEIGHT_M: float = 3.0
    SHIFT_M: float = 256.0
    MIN_TOP_Z_M: float = 1.0

    @staticmethod
    def _resolve_height(props: dict) -> float:
        """Building height (metres) using single-field, then top - base, fallback default."""
        h = _first_match(props, _HEIGHT_FIELDS)
        if h is not None and h > 0:
            return h
        top = _first_match(props, _TOP_FIELDS)
        if top is not None and top > 0:
            base = _first_match(props, _BASE_FIELDS) or 0.0
            diff = top - base
            if diff > 0:
                return diff
        return Converter.DEFAULT_HEIGHT_M

    @staticmethod
    def _polygons_of(geom: dict) -> List[List[List[List[float]]]]:
        """Yield each polygon (list of rings) from any (Multi)Polygon geometry."""
        if not geom:
            return []
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if gtype == "Polygon":
            return [coords]
        if gtype == "MultiPolygon":
            return list(coords)
        return []

    @staticmethod
    def _polygon_to_mesh(
        polygon_lonlat: List[List[List[float]]],
        height: float,
        center_x: float,
        center_y: float,
        crs: str,
    ) -> Tuple[List[float], List[int]]:
        """Triangulate + extrude one polygon (outer + holes); (flat xyz, flat
        indices) or ([], []).

        ``polygon_lonlat`` is the GeoJSON polygon shape: a list of rings
        where index 0 is the outer ring and indices 1..n are inner rings
        (holes — atriums, courtyards). All rings are projected to local
        meters via ``convert_to_local_coords`` and handed to
        ``triangulate_volume`` which natively handles holes through
        mapbox_earcut. Falls back to fan-triangulation of the outer ring
        only when earcut fails — that fallback can't represent holes, so
        we log a warning when a hole-bearing polygon hits it.
        """
        if not polygon_lonlat or not polygon_lonlat[0]:
            return [], []
        outer_lonlat = polygon_lonlat[0]
        if len(outer_lonlat) < 3:
            return [], []

        def _project(ring_lonlat: List[List[float]]) -> List[Tuple[float, float]]:
            ring = [(float(p[0]), float(p[1])) for p in ring_lonlat]
            if ring and ring[0] != ring[-1]:
                ring = ring + [ring[0]]
            local = convert_to_local_coords(ring, center_x, center_y, crs)
            if local and local[0] == local[-1]:
                local = local[:-1]
            return [(float(p[0]), float(p[1])) for p in local]

        local_outer = _project(outer_lonlat)
        if len(local_outer) < 3:
            return [], []

        # Holes: project each, drop ones that collapsed to <3 vertices.
        local_holes: List[List[Tuple[float, float]]] = []
        for hole_ll in polygon_lonlat[1:]:
            if len(hole_ll) < 4:
                continue
            local_hole = _project(hole_ll)
            if len(local_hole) >= 3:
                local_holes.append(local_hole)

        rings = [local_outer] + local_holes
        verts, inds = triangulate_volume(rings, height)

        # Fan fallback only handles the outer ring — losses any holes.
        # Warn so the user sees that the resulting mesh is approximate.
        if not verts or not inds:
            if local_holes:
                logger.warning(
                    "Converter._polygon_to_mesh: earcut failed; falling back "
                    "to fan triangulation, %d hole(s) will be filled in",
                    len(local_holes),
                )
            verts, inds = create_building_extrusion(local_outer, height)
        return verts or [], inds or []

    @staticmethod
    def _shift_and_filter(coords: List[float]) -> List[float] | None:
        """Apply +SHIFT to XY and drop the mesh if its top Z is too low."""
        if not coords:
            return None
        max_z = 0.0
        out: List[float] = []
        for i in range(0, len(coords), 3):
            x = coords[i] + Converter.SHIFT_M
            y = coords[i + 1] + Converter.SHIFT_M
            z = coords[i + 2]
            if z > max_z:
                max_z = z
            out.extend([x, y, z])
        if max_z <= Converter.MIN_TOP_Z_M:
            return None
        return out

    @staticmethod
    def GeojsonToDotbimMesh(
        geojson: dict,
        lon: float,
        lat: float,
        crs: str,
    ) -> Dict[str, dict]:
        """Build dotbim mesh dict ready for the run-analysis payload.

        Args:
            geojson: a GeoJSON FeatureCollection of building footprints.
            lon: tile-centre X (longitude when crs is geographic, else easting).
            lat: tile-centre Y (latitude or northing).
            crs: input CRS string accepted by ``pyproj`` (e.g. ``"EPSG:4326"``).

        Returns:
            ``{mesh_id: {mesh_id, coordinates, indices}}``. Empty dict when no
            valid mesh could be produced.
        """
        features = (geojson or {}).get("features") or []
        out: Dict[str, dict] = {}
        skipped_empty = 0
        skipped_low_z = 0

        for feat in features:
            geom = feat.get("geometry") or {}
            polygons = Converter._polygons_of(geom)
            if not polygons:
                continue

            height = Converter._resolve_height(feat.get("properties") or {})
            if height <= 0:
                height = Converter.DEFAULT_HEIGHT_M

            merged_coords: List[float] = []
            merged_indices: List[int] = []
            for poly in polygons:
                if not poly:
                    continue
                exterior = poly[0]
                if not exterior or len(exterior) < 3:
                    continue
                # Pass the full polygon (exterior + holes) so atriums /
                # courtyards survive triangulation. ``_polygon_to_mesh``
                # handles inner-ring projection internally.
                c, idx = Converter._polygon_to_mesh(poly, height, lon, lat, crs)
                if not c:
                    continue
                vert_offset = len(merged_coords) // 3
                merged_coords.extend(c)
                merged_indices.extend(i + vert_offset for i in idx)

            if not merged_coords or not merged_indices:
                skipped_empty += 1
                continue

            shifted = Converter._shift_and_filter(merged_coords)
            if shifted is None:
                skipped_low_z += 1
                continue

            mesh_id = str(uuid.uuid4())
            out[mesh_id] = {
                "mesh_id": mesh_id,
                "coordinates": shifted,
                "indices": merged_indices,
            }

        logger.info(
            "[Converter] Built %d meshes from %d features (skipped: %d empty, %d low-Z).",
            len(out), len(features), skipped_empty, skipped_low_z,
        )
        return out
