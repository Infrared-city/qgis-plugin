"""A result layer is named after the run that produced it.

Two UTCI runs a month apart were both "IC result - thermal-comfort-index", so
a project holding more than one result could not be read: the layer panel gave
no way to tell which was July and which was January.

What these tests actually guard is the widget names in
``_DESCRIBING_DROPDOWNS``. A typo there cannot crash anything — ``describe_run``
reads defensively and a wrong name simply drops that piece from the label — so
the failure is a quietly poorer name, which no one reports. Driving the real
``FakeRunDialog``, which is modelled on the shipped .ui, is what catches it.
"""

import pytest

from infrared_city_gis.models.analysis import AnalysisType
from infrared_city_gis.models.timeframes_parser import (
    DailyTimeFrameConfig,
    DailyTimeFrameConfigUTCI,
    MonthConfig,
    SeasonalTimeFrameConfig,
)
from infrared_city_gis.services.render_state import _pretty, describe_run
from infrared_city_gis.tests._fake_dialog import FakeRunDialog


def _dialog(analysis_type, **params):
    return FakeRunDialog(
        analysis_type,
        api_key="dummy",
        bbox=(16.370, 48.213, 16.376, 48.217),
        crs="EPSG:4326",
        weather_file="dummy.epw",
        **params,
    )


@pytest.mark.parametrize("analysis_type", [
    AnalysisType.PEDESTRIAN_WIND_COMFORT,
    AnalysisType.THERMAL_COMFORT_INDEX,
    AnalysisType.THERMAL_COMFORT_STATISTICS,
    AnalysisType.SOLAR_RADIATION,
    AnalysisType.DAYLIGHT_AVAILABILITY,
    AnalysisType.DIRECT_SUN_HOURS,
    AnalysisType.WIND_SPEED,
])
def test_every_analysis_with_inputs_describes_itself(analysis_type):
    """A wrong widget name shows up here as an empty or short label."""
    label = describe_run(_dialog(analysis_type))

    assert label, f"{analysis_type} produced no label at all"


def test_sky_view_factors_has_nothing_to_say():
    """It takes no inputs, so it gets the bare analysis name."""
    assert describe_run(_dialog(AnalysisType.SKY_VIEW_FACTORS)) == ""


def test_the_label_names_the_values_the_user_picked():
    """Not a wire token: the layer panel is read by a person."""
    label = describe_run(_dialog(
        AnalysisType.THERMAL_COMFORT_INDEX,
        month=MonthConfig.January,
        hours=DailyTimeFrameConfigUTCI.Morning,
    ))

    assert label == "January, Morning"


def test_wind_reads_as_a_measurement():
    """Its two inputs are spin boxes, so it is formatted, not listed."""
    label = describe_run(_dialog(
        AnalysisType.WIND_SPEED, wind_speed=7, wind_direction=180,
    ))

    assert label == "7 m/s, 180°"


def test_a_broken_dialog_costs_the_label_and_nothing_else():
    """The run has already finished by then; a name must not take it down."""
    class _Exploding:
        @property
        def analysis_type(self):
            raise RuntimeError("dialog is gone")

    assert describe_run(_Exploding()) == ""


@pytest.mark.parametrize("member, expected", [
    (SeasonalTimeFrameConfig.Summer, "Summer"),
    (DailyTimeFrameConfig.Afternoon, "Afternoon"),
    (DailyTimeFrameConfig.FullDay, "Full Day"),
    (MonthConfig.July, "July"),
])
def test_camel_case_names_get_their_spaces(member, expected):
    assert _pretty(member) == expected


def test_screaming_snake_names_read_as_a_sentence():
    """PWC criteria and TCS subtypes are the two spellings in play."""
    from infrared_city_gis.models.analysis import (
        PedestrianWindComfortType,
        ThermalComfortStatisticsType,
    )

    assert _pretty(PedestrianWindComfortType.LAWSON_2001) == "Lawson 2001"
    assert _pretty(ThermalComfortStatisticsType.HEAT_STRESS) == "Heat stress"
