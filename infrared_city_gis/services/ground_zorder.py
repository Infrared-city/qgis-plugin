"""The canonical Z stacking of ground-material surfaces.

The UTCI Lambda raycasts DOWNWARD with ``multiple_hits=False``, so where two
material layers overlap the HIGHEST surface wins. The order is therefore not a
display choice — it is the platform's own, and a run on hand-made layers has to
stack the way the server's own fetch does.

Split out of ``ground_materials`` (2026-11-11), which is part of the
size-limit debt in #50. These names are private to the collector; nothing
outside this package reads them.
"""

from __future__ import annotations

from typing import Dict, Tuple

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
