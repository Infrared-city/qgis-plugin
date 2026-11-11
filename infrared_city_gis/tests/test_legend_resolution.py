"""One legend rule for both run paths, with wind speed on its fixed registry scale.

Wind speed reads on the registry's 0-20 m/s for every run, so runs compare on
one scale. The others follow the run (UTCI's registry -40..46 °C would paint a
23-31 °C run in one band). The dialog's manual min/max wins over everything,
bound by bound.
"""

import pytest

from infrared_city_gis.models.analysis import AnalysisType
from infrared_city_gis.visualization import color_ramp

REGISTRY = {
    "wind-speed": {"steps": [0, 20]},
    "thermal-comfort-index": {"steps": [-40, 46]},
}


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(
        color_ramp, "get_visual_config",
        lambda analysis_type, sub=None: REGISTRY.get(str(analysis_type)),
    )


def resolve(analysis_type, run=(None, None), grid=(None, None), overrides=(None, None)):
    return color_ramp.resolve_legend(analysis_type, None, run, grid, overrides)


def test_wind_speed_reads_on_its_fixed_registry_scale():
    """The run reached 35.9 m/s; the scale stays 0-20 (the top band opens upward)."""
    assert resolve(AnalysisType.WIND_SPEED, grid=(0.0, 35.9)) == (0.0, 20.0)


def test_the_fixed_scale_also_beats_a_reported_range():
    """An area run reports the SDK-measured range; wind still keeps 0-20."""
    assert resolve("wind-speed", run=(0.0, 27.6), grid=(0.0, 27.6)) == (0.0, 20.0)


def test_manual_values_win_bound_by_bound():
    assert resolve("wind-speed", grid=(0.0, 35.9), overrides=(2.0, None)) == (2.0, 20.0)
    assert resolve("wind-speed", grid=(0.0, 35.9), overrides=(2.0, 12.0)) == (2.0, 12.0)


def test_other_analyses_follow_the_run_not_the_registry_scale():
    """UTCI has numeric registry steps too, and must not use them."""
    assert resolve(AnalysisType.THERMAL_COMFORT_INDEX, run=(22.0, 30.0), grid=(24.3, 31.6)) == (22.0, 30.0)
    assert resolve("thermal-comfort-index", grid=(24.3, 31.6)) == (24.3, 31.6)


def test_wind_without_a_registry_scale_falls_back_to_the_run():
    REGISTRY_BACKUP = dict(REGISTRY)
    try:
        REGISTRY.pop("wind-speed")
        assert resolve("wind-speed", grid=(0.0, 6.6)) == (0.0, 6.6)
    finally:
        REGISTRY.update(REGISTRY_BACKUP)


def test_the_enum_is_matched_by_its_wire_name():
    assert str(AnalysisType.WIND_SPEED) == "wind-speed"
