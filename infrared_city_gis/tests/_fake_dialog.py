"""A stand-in for the Run Simulation dialog, for driving the payload builder.

``build_sdk_payload`` takes the dialog itself and reads ~35 attributes off it —
mostly Qt widgets, queried through ``currentData()`` / ``currentText()`` /
``isChecked()`` / ``value()``. Constructing the real dialog in a test would drag
in the whole UI (and ``iface``, which is ``None`` outside QGIS), so this module
supplies just those attributes.

The trade-off is worth naming: a fake encodes *what we believe the dialog
produces*. If a widget is renamed or wired to the wrong control, these tests
still pass while the real dialog breaks. ``test_fake_dialog_matches_real.py``
pins that down — it checks every widget name here against the dialog's .ui file
and against what ``build_sdk_payload`` reads. Keep the three in step.

Every analysis type is modelled. The defaults in ``DEFAULTS`` are what the e2e
matrix runs with; a test overrides individual fields via keyword arguments:

    FakeRunDialog(AnalysisType.WIND_SPEED, api_key=…, bbox=…, crs=…)
    FakeRunDialog(AnalysisType.THERMAL_COMFORT_INDEX, …, month=MonthConfig.January)

The dropdown *contents* mirror the real dialog's populate step
(``_populate_analysis_widgets``): PWC/solar/daylight/sun-hours hours come from
``DailyTimeFrameConfig``, TCI and TCS hours from ``DailyTimeFrameConfigUTCI``.
"""

from infrared_city_gis.models.analysis import (
    AnalysisType,
    PedestrianWindComfortType,
    ThermalComfortStatisticsType,
)
from infrared_city_gis.models.timeframes_parser import (
    DailyTimeFrameConfig,
    DailyTimeFrameConfigUTCI,
    MonthConfig,
    SeasonalTimeFrameConfig,
)


class _Combo:
    """Minimal QComboBox stand-in: ``currentData()`` and ``currentText()``."""

    def __init__(self, data=None, text=""):
        self._data = data
        self._text = text

    def currentData(self):
        return self._data

    def currentText(self):
        return self._text


class _Check:
    """Minimal QCheckBox stand-in."""

    def __init__(self, checked=False):
        self._checked = checked

    def isChecked(self):
        return self._checked


class _Spin:
    """Minimal QSpinBox / QDoubleSpinBox stand-in."""

    def __init__(self, value=0):
        self._value = value

    def value(self):
        return self._value


#: Per-analysis inputs the e2e matrix runs with. Deliberately boring choices —
#: July afternoon in Vienna — so results are comparable across analysis types.
DEFAULTS = {
    AnalysisType.WIND_SPEED: {
        "wind_speed": 5,
        "wind_direction": 270,
    },
    AnalysisType.PEDESTRIAN_WIND_COMFORT: {
        "pwc_type": PedestrianWindComfortType.LAWSON_2001,
        "season": SeasonalTimeFrameConfig.Summer,
        "hours": DailyTimeFrameConfig.Afternoon,
    },
    AnalysisType.THERMAL_COMFORT_INDEX: {
        "month": MonthConfig.July,
        "hours": DailyTimeFrameConfigUTCI.Afternoon,
    },
    AnalysisType.THERMAL_COMFORT_STATISTICS: {
        "season": SeasonalTimeFrameConfig.Summer,
        "hours": DailyTimeFrameConfigUTCI.Afternoon,
        "tcs_type": ThermalComfortStatisticsType.THERMAL_COMFORT,
    },
    AnalysisType.SOLAR_RADIATION: {
        "month": MonthConfig.July,
        "hours": DailyTimeFrameConfig.Afternoon,
    },
    AnalysisType.DAYLIGHT_AVAILABILITY: {
        "month": MonthConfig.July,
        "hours": DailyTimeFrameConfig.Afternoon,
    },
    AnalysisType.DIRECT_SUN_HOURS: {
        "month": MonthConfig.July,
        "hours": DailyTimeFrameConfig.Afternoon,
    },
    AnalysisType.SKY_VIEW_FACTORS: {},
}

#: Analyses whose payload needs a weather file (or an uploaded EPW).
NEEDS_WEATHER = (
    AnalysisType.PEDESTRIAN_WIND_COMFORT,
    AnalysisType.THERMAL_COMFORT_INDEX,
    AnalysisType.THERMAL_COMFORT_STATISTICS,
    AnalysisType.SOLAR_RADIATION,
)


class FakeRunDialog:
    """Enough of the Run Simulation dialog to build any analysis payload.

    ``build_sdk_payload`` also *writes back* to the dialog
    (``sub_analysis_type``, ``min_legend_value``, ``max_legend_value``), so those
    start as plain attributes rather than properties.

    Note it is NOT a QWidget. ``build_sdk_payload`` passes the dialog to
    ``QMessageBox.warning`` on the validation-failure paths, which would raise
    with this object as parent — so drive it with valid inputs, and patch
    ``QMessageBox`` if you want to test rejection.
    """

    def __init__(self, analysis_type, *, api_key, bbox, crs, weather_file="",
                 tree_layer=None, ground_material_layers=None,
                 use_infrared_ground_materials=False, **params):
        self.analysis_type = analysis_type
        self.sub_analysis_type = None
        self.api_key = api_key
        self.bbox = bbox
        self.crs = crs

        cfg = dict(DEFAULTS[analysis_type])
        unknown = set(params) - set(cfg)
        if unknown:
            raise TypeError(
                f"{analysis_type} takes no {sorted(unknown)} — known inputs are "
                f"{sorted(cfg) or '(none)'}"
            )
        cfg.update(params)

        # No uploaded EPW: the builder then queries the platform weather file.
        self._epw_paths = {}

        if analysis_type == AnalysisType.WIND_SPEED:
            self.wind_speed_input = _Spin(cfg["wind_speed"])
            self.wind_direction_input = _Spin(cfg["wind_direction"])

        elif analysis_type == AnalysisType.PEDESTRIAN_WIND_COMFORT:
            self.pwc_type_dropdown = _Combo(data=cfg["pwc_type"])
            self.season_dropdown_pwc = _Combo(data=cfg["season"])
            self.hours_dropdown_pwc = _Combo(data=cfg["hours"])
            self.weather_file_input_pwc = _Combo(text=weather_file)

        elif analysis_type == AnalysisType.THERMAL_COMFORT_INDEX:
            self.month_dropdown_tci = _Combo(data=cfg["month"])
            self.hours_dropdown_tci = _Combo(data=cfg["hours"])
            self.weather_file_input_tci = _Combo(text=weather_file)
            self.legend_min_enable_tci = _Check(False)
            self.legend_max_enable_tci = _Check(False)
            self.legend_min_input_tci = _Spin(0)
            self.legend_max_input_tci = _Spin(0)

        elif analysis_type == AnalysisType.THERMAL_COMFORT_STATISTICS:
            self.season_dropdown_tcs = _Combo(data=cfg["season"])
            self.hours_dropdown_tcs = _Combo(data=cfg["hours"])
            self.tcs_type_dropdown = _Combo(data=cfg["tcs_type"])
            self.weather_file_input_tcs = _Combo(text=weather_file)

        elif analysis_type == AnalysisType.SOLAR_RADIATION:
            self.month_dropdown_sr = _Combo(data=cfg["month"])
            self.hours_dropdown_sr = _Combo(data=cfg["hours"])
            self.weather_file_input_sr = _Combo(text=weather_file)

        elif analysis_type == AnalysisType.DAYLIGHT_AVAILABILITY:
            self.month_dropdown_da = _Combo(data=cfg["month"])
            self.hours_dropdown_da = _Combo(data=cfg["hours"])

        elif analysis_type == AnalysisType.DIRECT_SUN_HOURS:
            self.month_dropdown_dsh = _Combo(data=cfg["month"])
            self.hours_dropdown_dsh = _Combo(data=cfg["hours"])

        elif analysis_type != AnalysisType.SKY_VIEW_FACTORS:
            raise NotImplementedError(f"no fake dialog wiring for {analysis_type}")

        # Written back by build_sdk_payload on the TCI path.
        self.min_legend_value = None
        self.max_legend_value = None

        # What this run was configured with. A simulation baseline is only
        # meaningful for the settings it was recorded under, so the e2e matrix
        # stores this alongside the numbers and compares it exactly — changing
        # an input then fails loudly instead of quietly comparing against a
        # result produced with different settings.
        self.inputs = dict(cfg)
        if analysis_type in NEEDS_WEATHER:
            self.inputs["weather_file"] = weather_file

        # Vegetation + ground materials
        self.tree_layer_dropdown = _Combo(data=tree_layer)
        self.use_infrared_ground_materials = use_infrared_ground_materials
        self._ground_material_layers = ground_material_layers or {}

    def selected_ground_material_layers(self):
        """Mirrors the dialog method the collectors call."""
        return self._ground_material_layers

    def input_signature(self) -> str:
        """Stable one-line description of the inputs this run used."""
        if not self.inputs:
            return "(no inputs)"
        return ", ".join(
            f"{name}={getattr(value, 'value', value)}"
            for name, value in sorted(self.inputs.items())
        )
