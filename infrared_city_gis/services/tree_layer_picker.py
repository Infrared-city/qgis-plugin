"""Tree-layer dropdown helpers shared by the run-simulation dialogs.

The two ``Run …`` dialogs both load
``infrared_city_run_simulation_dialog.ui`` which now includes a
``tree_layer_dropdown`` QComboBox. This module owns the small amount of
behaviour that belongs to it:

* :func:`populate_tree_layer_dropdown` — fills the combo with project
  layers whose name contains ``"tree-"``, in the order they appear in the
  layer panel. Always inserts a leading "(none)" item, so the user can
  explicitly opt out of vegetation even when matching layers exist. If
  no layers match, the dropdown ends up with just the "(none)" entry.

* :func:`update_tree_layer_enabled` — disables the dropdown when either
  of these is true:
    1. There are no candidate layers in the project.
    2. The current analysis type is in
       :data:`_ANALYSES_WITHOUT_TREE_SUPPORT` (currently empty — the
       Infrared models all accept a ``vegetation`` payload, so the
       dropdown is enabled for every analysis type by default; tighten
       this set if a future analysis genuinely cannot use vegetation).

* :func:`selected_tree_layer` — read accessor returning the currently
  selected ``QgsVectorLayer`` or ``None``. The caller decides whether to
  fetch vegetation from it; this module never touches the actual
  geometry.
"""

from __future__ import annotations

from typing import Optional

from qgis.core import QgsProject, QgsVectorLayer

from ..infrared_logger import logger
from ..models.analysis import AnalysisType

# Substring match on the (case-insensitive) layer name. Matches anything
# containing "tree" — including "trees", "TREES", "DBGT_TREES",
# "tree-blacksburg", "street_trees_2024" etc. We deliberately don't
# require a hyphen: real-world layer names from cadastre dumps often
# use suffixes like "_TREES" rather than the "tree-…" convention.
_NAME_FILTER = "tree"

# Display label for the "no vegetation" option — kept as a constant so the
# accessor can compare reliably without depending on the .ui file's text.
_NONE_LABEL = "(none)"

# Analyses for which the dropdown should be greyed out even when matching
# layers exist. Empty by default — every analysis type supported by the
# plugin currently accepts a ``vegetation`` payload via the SDK. Add an
# AnalysisType member here to grey out the dropdown for that page.
_ANALYSES_WITHOUT_TREE_SUPPORT: set = {
    AnalysisType.WIND_SPEED,
    AnalysisType.PEDESTRIAN_WIND_COMFORT,
}


def _candidate_layers() -> list[QgsVectorLayer]:
    """Return all project layers whose name contains ``tree-`` (case-insensitive)."""
    out: list[QgsVectorLayer] = []
    for lyr in QgsProject.instance().mapLayers().values():
        if not isinstance(lyr, QgsVectorLayer):
            continue
        if _NAME_FILTER in lyr.name().lower():
            out.append(lyr)
    return out


def populate_tree_layer_dropdown(combo) -> int:
    """Fill ``combo`` with the project's tree-* layers and a leading "(none)".

    Idempotent — clears the combo first, so it's safe to call repeatedly
    (e.g. from the dialog ``__init__`` and again after the user adds a
    new tree layer to the project).

    Returns the count of *real* tree layers found (excluding the "(none)"
    sentinel). Useful for the caller to decide whether to grey out the
    dropdown right away.
    """
    combo.clear()
    # Sentinel first — its userData is None, used as the "no vegetation"
    # signal by ``selected_tree_layer``.
    combo.addItem(_NONE_LABEL, None)

    layers = _candidate_layers()
    for lyr in layers:
        combo.addItem(lyr.name(), lyr)

    logger.info(
        "populate_tree_layer_dropdown: %d tree-* layer(s) found in project",
        len(layers),
    )
    return len(layers)


def update_tree_layer_enabled(combo, analysis_type: Optional[AnalysisType]) -> None:
    """Grey out ``combo`` when there are no candidates or the analysis
    doesn't support vegetation.

    Triggered from the dialog's ``on_analysis_changed`` (and once at the
    end of ``__init__``). The visible state is:

      * disabled, "(none)" only — analysis supports trees but the project
        has no tree-* layer.
      * disabled, "(none)" plus real entries — analysis doesn't support
        trees, even though layers exist.
      * enabled — at least one real layer AND analysis supports trees.
    """
    has_real_layer = combo.count() > 1  # > 1 because "(none)" is always there
    analysis_supports = analysis_type is None or analysis_type not in _ANALYSES_WITHOUT_TREE_SUPPORT

    enabled = has_real_layer and analysis_supports
    combo.setEnabled(enabled)

    if not enabled:
        # If we're forcing it disabled, snap selection back to "(none)" so
        # the dialog never silently passes a user choice that the analysis
        # would ignore.
        none_idx = combo.findText(_NONE_LABEL)
        if none_idx >= 0 and combo.currentIndex() != none_idx:
            combo.setCurrentIndex(none_idx)


def selected_tree_layer(combo) -> Optional[QgsVectorLayer]:
    """Return the currently selected tree layer, or ``None`` if "(none)".

    Pulls from the combo's userData (set by
    :func:`populate_tree_layer_dropdown`) rather than parsing display
    text — robust against translation / re-labelling.
    """
    data = combo.currentData()
    if isinstance(data, QgsVectorLayer):
        return data
    return None


def has_tree_support(analysis_type: Optional[AnalysisType]) -> bool:
    """True if ``analysis_type`` accepts a vegetation payload.

    Exposed so the caller can decide whether to even attempt vegetation
    collection — useful when the dropdown selection is restored from
    persisted state and the analysis type might have been changed.
    """
    if analysis_type is None:
        return True
    return analysis_type not in _ANALYSES_WITHOUT_TREE_SUPPORT


def add_tree_support_exclusions(*types: AnalysisType) -> None:
    """Mark one or more analysis types as not supporting vegetation.

    Convenience hook for future tightening — call this from a plugin
    init hook or test setup to grey out the dropdown for specific
    analyses without editing this module.
    """
    for t in types:
        _ANALYSES_WITHOUT_TREE_SUPPORT.add(t)
