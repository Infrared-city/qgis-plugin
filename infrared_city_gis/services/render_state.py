"""What a run was, captured from the dialog before it closes.

``describe_run`` turns the dialog's inputs into the result layer's label, and
``AreaRenderState`` snapshots everything the render needs as plain values, so
the area poller and the single-tile poller can render after the dialog has
been destroyed. Split out of ``area_poller`` (both pollers use it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from ..infrared_logger import logger
from ..models.analysis import AnalysisType

#: The dropdowns that describe a run, per analysis. Wind is absent because its
#: two inputs are spin boxes, not dropdowns, and reads differently ("5 m/s,
#: 270°"); sky-view-factors is absent because it has no inputs to describe.
#:
#: These names mirror what `services.sdk_payloads.build_sdk_payload` reads. A
#: typo here cannot crash a run — the label just loses a piece — so
#: `tests/test_result_layer_label.py` drives them through the fake dialog,
#: which is modelled on the real .ui.
_DESCRIBING_DROPDOWNS = {
    AnalysisType.PEDESTRIAN_WIND_COMFORT: (
        "pwc_type_dropdown", "season_dropdown_pwc", "hours_dropdown_pwc",
    ),
    AnalysisType.THERMAL_COMFORT_INDEX: (
        "month_dropdown_tci", "hours_dropdown_tci",
    ),
    AnalysisType.THERMAL_COMFORT_STATISTICS: (
        "tcs_type_dropdown", "season_dropdown_tcs", "hours_dropdown_tcs",
    ),
    AnalysisType.SOLAR_RADIATION: ("month_dropdown_sr", "hours_dropdown_sr"),
    AnalysisType.DAYLIGHT_AVAILABILITY: ("month_dropdown_da", "hours_dropdown_da"),
    AnalysisType.DIRECT_SUN_HOURS: ("month_dropdown_dsh", "hours_dropdown_dsh"),
}


def _pretty(member) -> str:
    """A dropdown value as a person would write it.

    Enum NAMES rather than values, because the values are wire tokens
    (``lawson-2001``, ``heat-stress``) while the names already read as English
    once the two spellings in use are normalised: ``LAWSON_2001`` and
    ``HEAT_STRESS`` are screaming snake, ``FullDay`` and ``Afternoon`` are
    camel.
    """
    name = getattr(member, "name", None) or str(member)
    if name.replace("_", "").isupper():
        return name.replace("_", " ").capitalize()
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def describe_run(dlg) -> str:
    """A short human description of what this run was configured with.

    Two UTCI runs a month apart used to produce two layers both called
    "IC result - thermal-comfort-index", which is unreadable the moment a
    project holds more than one result. Read from the dialog while it is still
    alive, and carried on :class:`AreaRenderState` because the renderer runs
    after the dialog is gone.

    Never raises: a label is a convenience, and losing one must not take a
    finished simulation down with it.
    """
    try:
        at = dlg.analysis_type
        if at == AnalysisType.WIND_SPEED:
            return (
                f"{int(dlg.wind_speed_input.value())} m/s, "
                f"{int(dlg.wind_direction_input.value())}°"
            )
        parts = []
        for widget_name in _DESCRIBING_DROPDOWNS.get(at, ()):
            widget = getattr(dlg, widget_name, None)
            data = widget.currentData() if widget is not None else None
            if data is not None:
                parts.append(_pretty(data))
        return ", ".join(parts)
    except Exception as e:
        logger.debug("could not describe the run for the layer name: %s", e)
        return ""


@dataclass(frozen=True)
class AreaRenderState:
    """Snapshot of dialog state needed to render an AreaResult.

    Captured *before* the dialog closes so the renderer is decoupled from
    the QWidget lifecycle. Holds primitive Python values only — no QWidget
    references — to be safe across the dialog's destruction.
    """

    analysis_type: AnalysisType
    sub_analysis_type: Any  # an Enum or None — kept generic to avoid a circular import
    legend_min_override: Optional[float]
    legend_max_override: Optional[float]
    #: e.g. "July, Afternoon" — what the result layer is named after.
    label: str = ""

    @classmethod
    def from_dialog(cls, dlg) -> "AreaRenderState":
        """Build a snapshot from the run-multiple-simulation dialog.

        Reads ``analysis_type`` and ``sub_analysis_type`` directly. For UTCI
        / TCI also captures the dialog's manual legend overrides if the
        user enabled them.
        """
        leg_min: Optional[float] = None
        leg_max: Optional[float] = None
        if dlg.analysis_type == AnalysisType.THERMAL_COMFORT_INDEX:
            min_w = getattr(dlg, "legend_min_enable_tci", None)
            if min_w is not None and min_w.isChecked():
                leg_min = float(dlg.min_legend_value)
            max_w = getattr(dlg, "legend_max_enable_tci", None)
            if max_w is not None and max_w.isChecked():
                leg_max = float(dlg.max_legend_value)
        return cls(
            analysis_type=dlg.analysis_type,
            sub_analysis_type=getattr(dlg, "sub_analysis_type", None),
            legend_min_override=leg_min,
            legend_max_override=leg_max,
            label=describe_run(dlg),
        )
