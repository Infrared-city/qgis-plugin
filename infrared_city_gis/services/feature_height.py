"""Best-effort height extraction for vector features.

Format-agnostic: works on any QgsFeature regardless of source driver
(Shapefile, GeoJSON, GeoPackage, GML/CityGML via OGR, KML, …).
The format does not matter — what differs across datasets is the
attribute schema. This module encodes a prioritised lookup over the
most common naming conventions, plus a 3D-geometry Z fallback and a
type-based estimate for OSM-style data.

Resolution order (first hit wins):
  1. top-elevation field minus base-elevation field
     (e.g. BuildingTo - BuildingBo)
  2. a single height field
     (e.g. OSM `height`, CityJSON `measuredHeight`)
  3. building levels × floor height
     (e.g. OSM `building:levels`)
  4. Z range of a 3D geometry (LoD1/LoD2)
  5. OSM `building=*` type → typical height lookup
  6. caller-supplied generic default (only when permissive=True)

Tiers 1-4 are precise (use real numeric data). Tier 5 is an estimate based
on the building category. Tier 6 is the last resort — useful for OSM extracts
that are sparse on heights, where dropping all features without data would
remove most of the city. ``resolve_feature_height_with_source`` returns both
the height and the tier name, so callers can surface a per-tier summary to
the user.
"""

from qgis.core import QgsWkbTypes

from ..infrared_logger import logger

# ---------------------------------------------------------------------------
# Field-name candidates. All entries are pre-normalised (lowercase, no spaces,
# `:` -> `_`, no diacritics on the German entries that need it).
# ---------------------------------------------------------------------------

# A single field that already holds the height in metres.
_HEIGHT_FIELDS = (
    "height", "h", "bldg_height", "building_height", "measuredheight",
    "relhmax", "rel_hmax",                  # German 3D-LoD1/LoD2: relative max height (already a height)
    "hoehe", "h_geb", "geb_hoehe",          # German
    "h_dak_max", "pand_hoogte",             # Dutch 3D BAG
    "hauteur",                              # French
    "z_max", "max_height",
)

# Top elevation / roof height (absolute m a.s.l. or relative to ground).
_TOP_FIELDS = (
    "buildingto", "buildtopelev",
    "abshmax", "abs_hmax",                  # German 3D-LoD: absolute roof, m a.s.l.
    "abs_hoehe_oben", "h_top", "roof_height",
)

# Base elevation. Subtracted from the top field.
_BASE_FIELDS = (
    "buildingbo", "buildbotelev",
    "abshmin", "abs_hmin",                  # German 3D-LoD: absolute ground, m a.s.l.
    "relhmin", "rel_hmin",                  # relative ground (usually 0)
    "abs_hoehe_unten", "h_base", "ground_height", "h_maaiveld",
)

# Number of stories / levels — multiplied by floor_height_m.
_LEVELS_FIELDS = (
    "building_levels", "levels",
    "geschossza", "etagen",
    "floors", "stories",
)

DEFAULT_FLOOR_HEIGHT_M = 3.0

# Generic conservative default when nothing else is known. Roughly two
# residential storeys — biases low (under-shadowing) rather than overestimating
# a 30-storey tower. Used only when ``resolve_feature_height_with_source`` is
# called with permissive=True.
GENERIC_FALLBACK_HEIGHT_M = 6.0

# OSM ``building=*`` value → typical height in metres. Used only when the
# layer has a `building` field and the value is one of these well-known OSM
# categories. Numbers are conservative averages drawn from urban typology
# references; individual buildings vary widely. Use as a tier-5 fallback only.
OSM_BUILDING_HEIGHT_HINTS = {
    # Small / utility — single-storey or shorter
    "garage": 3.0, "garages": 3.0, "shed": 3.0, "hut": 3.0,
    "carport": 3.0, "cabin": 3.0, "roof": 3.0, "container": 3.0,
    "service": 4.0, "kiosk": 4.0,
    # Houses — ~2 storeys
    "house": 6.0, "detached": 6.0, "bungalow": 6.0,
    "semidetached_house": 6.0, "semi_detached": 6.0,
    "barn": 6.0, "farm": 6.0, "farm_auxiliary": 6.0, "stable": 6.0,
    "static_caravan": 3.0,
    # Low-rise residential / commercial — ~3 storeys
    "residential": 9.0, "terrace": 9.0,
    "retail": 9.0, "supermarket": 9.0,
    # Mid-rise — ~4 storeys
    "apartments": 12.0, "dormitory": 12.0,
    "commercial": 12.0, "office": 12.0,
    "hotel": 12.0, "motel": 12.0,
    # Public / institutional
    "school": 9.0, "university": 12.0, "college": 12.0, "kindergarten": 6.0,
    "hospital": 12.0, "clinic": 9.0,
    "public": 9.0, "civic": 9.0, "government": 12.0, "courthouse": 12.0,
    "library": 9.0, "fire_station": 6.0, "police": 9.0,
    # Industrial — one tall floor
    "industrial": 9.0, "warehouse": 9.0, "factory": 9.0, "manufacture": 9.0,
    "hangar": 12.0, "bunker": 4.0,
    # Religious — varies wildly, conservative averages
    "church": 12.0, "chapel": 9.0, "cathedral": 20.0,
    "temple": 12.0, "mosque": 12.0, "synagogue": 12.0,
    "religious": 12.0,
    # Sports
    "stadium": 15.0, "sports_hall": 9.0, "pavilion": 6.0, "grandstand": 9.0,
    # Generic fallbacks within the OSM scheme
    "yes": 6.0,
    "construction": 6.0,
}

# OSM-style fields that hold the building category. Lookup is normalised
# (lowercase, ":"->"_"); list both forms used in OSM exports.
_OSM_BUILDING_TYPE_FIELDS = ("building", "building_type", "building_use")


def _norm(s: str) -> str:
    """Normalise an attribute name for case/space/separator-insensitive lookup."""
    return (s or "").strip().lower().replace(":", "_").replace(" ", "_")


def _build_lookup(fields):
    """{normalised_name: original_name} — original is what feat[...] needs."""
    return {_norm(f): f for f in fields}


def _read_float(feat, key):
    try:
        v = feat[key]
    except (KeyError, IndexError):
        return None
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _first_match(feat, lookup, candidates):
    for cand in candidates:
        if cand in lookup:
            v = _read_float(feat, lookup[cand])
            if v is not None:
                return v
    return None


def _read_string(feat, key):
    """Read a string-ish attribute, returning None for null/empty/QVariant-null."""
    try:
        v = feat[key]
    except (KeyError, IndexError):
        return None
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("null", "none", "nan"):
        return None
    return s


def _resolve_from_building_type(feat, lookup):
    """Tier 5: derive a typical height from the OSM ``building=*`` category.

    Returns ``(height, building_type)`` if a recognized OSM value is present,
    or ``(None, None)`` otherwise.
    """
    for cand in _OSM_BUILDING_TYPE_FIELDS:
        if cand not in lookup:
            continue
        raw = _read_string(feat, lookup[cand])
        if not raw:
            continue
        # OSM allows multi-valued semicolons (rare): take the first.
        token = raw.split(";")[0].strip().lower()
        h = OSM_BUILDING_HEIGHT_HINTS.get(token)
        if h is not None:
            return h, token
    return None, None


def has_height_capable_schema(layer) -> bool:
    """Whether a vector layer can plausibly yield building heights at all.

    Returns True if the layer has at least one of:
      - a top-elevation field (e.g. ``BuildingTo``, ``abs_hmax``)
      - a single height field (e.g. ``height``, ``hoehe``, ``hauteur``)
      - a levels/floors field (e.g. ``building:levels``, ``geschossza``)
      - 3D geometry (Z coordinates, e.g. CityGML LoD1/LoD2)

    This is a *structural* check on the layer schema. It does NOT verify that
    actual feature values are populated — a feature with a `height` field
    explicitly set to NULL will still pass this check but fail at
    ``resolve_feature_height``. Use this as a pre-flight to fail fast when
    the layer plainly has no height information at all.
    """
    if layer is None:
        return False
    try:
        fields = [f.name() for f in layer.fields()]
    except Exception:
        fields = []
    lookup = _build_lookup(fields)

    has_top = any(c in lookup for c in _TOP_FIELDS)
    has_height = any(c in lookup for c in _HEIGHT_FIELDS)
    has_levels = any(c in lookup for c in _LEVELS_FIELDS)

    has_3d = False
    try:
        has_3d = QgsWkbTypes.hasZ(layer.wkbType())
    except Exception as e:
        # Not every layer exposes a wkbType (broken provider, no data source);
        # treat it as "no Z" and fall back to the attribute-based checks.
        logger.debug("could not read wkbType from %r: %s", layer, e)

    return has_top or has_height or has_levels or has_3d


# Tier names used in the (height, source) tuple returned by
# ``resolve_feature_height_with_source``. Stable, suitable for log keys / counters.
SOURCE_TOP_BASE = "top_base"
SOURCE_HEIGHT_FIELD = "height_field"
SOURCE_LEVELS = "levels"
SOURCE_Z_RANGE = "z_range"
SOURCE_BUILDING_TYPE = "building_type"
SOURCE_GENERIC_DEFAULT = "generic_default"
SOURCE_OVERRIDE = "override"
SOURCE_MISSING = "missing"


def resolve_feature_height_with_source(
    feat,
    fields,
    geom=None,
    floor_height_m: float = DEFAULT_FLOOR_HEIGHT_M,
    override_field: str = None,
    permissive: bool = False,
    generic_default_m: float = GENERIC_FALLBACK_HEIGHT_M,
):
    """Resolve a feature's height plus the tier name of the source.

    Args:
        feat: QgsFeature.
        fields: iterable of attribute names available on the feature.
        geom: optional QgsGeometry — used only as a 3D Z-range fallback.
        floor_height_m: assumed metres per floor when only level count is known.
        override_field: when set (and present on the feature), wins over every
                        heuristic. Useful to expose a UI dropdown letting the
                        user point at the correct attribute manually.
        permissive: when True, fall back to a generic conservative default
                    (``generic_default_m``) instead of returning ``None`` for
                    features with no resolvable data. Recommended for OSM data
                    where most buildings lack height attributes.
        generic_default_m: the height used as last-resort when ``permissive``.

    Returns:
        ``(height, source)`` tuple. ``height`` is a float in metres, or ``None``
        if no tier matched (and ``permissive=False``). ``source`` is one of the
        ``SOURCE_*`` constants — ``SOURCE_MISSING`` when ``height is None``.
    """
    lookup = _build_lookup(fields)

    if override_field:
        v = _read_float(feat, override_field)
        if v is not None and v > 0:
            return v, SOURCE_OVERRIDE

    # 1. top - base (precise)
    top = _first_match(feat, lookup, _TOP_FIELDS)
    if top is not None:
        base = _first_match(feat, lookup, _BASE_FIELDS) or 0.0
        h = top - base
        if h > 0:
            return h, SOURCE_TOP_BASE

    # 2. single height field (precise)
    h = _first_match(feat, lookup, _HEIGHT_FIELDS)
    if h is not None and h > 0:
        return h, SOURCE_HEIGHT_FIELD

    # 3. levels × floor height (good estimate)
    levels = _first_match(feat, lookup, _LEVELS_FIELDS)
    if levels and levels > 0:
        return levels * floor_height_m, SOURCE_LEVELS

    # 4. 3D geometry Z range (precise for LoD1/LoD2 data)
    if geom is not None:
        try:
            if QgsWkbTypes.hasZ(geom.wkbType()):
                zs = [v.z() for v in geom.vertices()]
                zs = [z for z in zs if z is not None]
                if zs:
                    z_range = max(zs) - min(zs)
                    if z_range > 0:
                        return z_range, SOURCE_Z_RANGE
        except Exception as e:
            # constructing vertices() can fail on malformed geoms — fall through
            # to the attribute-based sources below.
            logger.debug("Z-range extraction failed, trying attributes: %s", e)

    # 5. OSM building type → typical height (rough estimate)
    type_h, _osm_type = _resolve_from_building_type(feat, lookup)
    if type_h is not None and type_h > 0:
        return type_h, SOURCE_BUILDING_TYPE

    # 6. Generic conservative default — only when permissive
    if permissive:
        return generic_default_m, SOURCE_GENERIC_DEFAULT

    return None, SOURCE_MISSING


def resolve_feature_height(
    feat,
    fields,
    geom=None,
    default=None,
    floor_height_m: float = DEFAULT_FLOOR_HEIGHT_M,
    override_field: str = None,
):
    """Backward-compatible height resolver.

    Returns just the height in metres (or ``default`` when no tier matched).
    For new code prefer ``resolve_feature_height_with_source`` so you can
    surface per-tier statistics to the user.
    """
    h, _src = resolve_feature_height_with_source(
        feat, fields,
        geom=geom,
        floor_height_m=floor_height_m,
        override_field=override_field,
        permissive=False,  # preserve old behaviour: return None when missing
    )
    return h if h is not None else default
