"""The analysis type the plugin hands `get_area` resolves to the right margin.

The auto-fetch paths now call ``ground_materials.get_area(polygon,
analysis_type=payload.analysis_type)`` so a wind run stops reading the 544 m
margin it cannot use. That optimisation is silent when it breaks: a name the
SDK does not recognise raises nothing — it passes through
``resolve_read_analysis_type`` unchanged and the read falls back to a default.
So what is worth pinning is not that the call happens, but that the value still
resolves to the margin that analysis needs.

Two tests, because the two halves fail differently. The first builds a REAL
payload and is the end-to-end check, but only for the analyses whose payload
needs no weather arrays — the rest query the weather service, and a free test
must not reach the network. The second covers every name the plugin can emit,
including the weather-bearing ones, straight against the SDK.

Only the two wind analyses read less. For everything else this change is a
no-op by design, and the tests say so rather than leaving it to be
rediscovered.
"""

import pytest
from infrared_sdk.analyses.types import AnalysesName
from infrared_sdk.tiling.read_margin import ground_read_distance_m

from infrared_city_gis.models.analysis import AnalysisType
from infrared_city_gis.services.sdk_payloads import build_sdk_payload
from infrared_city_gis.tests._fake_dialog import FakeRunDialog

#: What `get_area` reads around each tile, in metres, per SDK tiling preset.
WIND_READ_M = 363.0
WIDEST_READ_M = 544.0


def _payload(analysis_type):
    return build_sdk_payload(FakeRunDialog(
        analysis_type,
        api_key="dummy",
        bbox=(16.370, 48.213, 16.376, 48.217),
        crs="EPSG:4326",
        weather_file="dummy.epw",
    ))


@pytest.mark.parametrize("analysis_type, expected_m", [
    (AnalysisType.WIND_SPEED, WIND_READ_M),
    (AnalysisType.SKY_VIEW_FACTORS, WIDEST_READ_M),
    (AnalysisType.DIRECT_SUN_HOURS, WIDEST_READ_M),
    (AnalysisType.DAYLIGHT_AVAILABILITY, WIDEST_READ_M),
])
def test_a_real_payload_resolves_to_the_margin_its_analysis_needs(
    analysis_type, expected_m,
):
    """End to end: what `build_sdk_payload` produces is what `get_area` takes."""
    payload = _payload(analysis_type)

    assert ground_read_distance_m(payload.analysis_type) == expected_m


@pytest.mark.parametrize("name, expected_m", [
    (AnalysesName.wind_speed, WIND_READ_M),
    (AnalysesName.pedestrian_wind_comfort, WIND_READ_M),
    (AnalysesName.solar_radiation, WIDEST_READ_M),
    (AnalysesName.thermal_comfort_index, WIDEST_READ_M),
    (AnalysesName.thermal_comfort_statistics, WIDEST_READ_M),
    (AnalysesName.sky_view_factors, WIDEST_READ_M),
    (AnalysesName.daylight_availability, WIDEST_READ_M),
    (AnalysesName.direct_sun_hours, WIDEST_READ_M),
])
def test_every_analysis_the_plugin_emits_is_a_key_the_sdk_knows(name, expected_m):
    """Covers the weather-bearing analyses the payload test cannot reach."""
    assert ground_read_distance_m(name) == expected_m


def test_passing_nothing_would_read_the_widest_margin():
    """The behaviour the change replaces, so the win is visible in the suite.

    Without an analysis type the SDK reads a margin valid for every analysis.
    Correct, but for wind it is 544 m of context the model never uses.
    """
    assert ground_read_distance_m(None) == WIDEST_READ_M
    assert ground_read_distance_m(None) > WIND_READ_M


def test_the_enum_survives_the_trip_as_a_string():
    """``get_area`` stringifies whatever it is given.

    ``AnalysesName`` is a ``StrEnum``, but on Python 3.9/3.10 that is the SDK's
    own compat class rather than the stdlib one. If its ``__str__`` ever stopped
    returning the value, every analysis would resolve to an unknown name and
    quietly read a default margin.
    """
    payload = _payload(AnalysisType.WIND_SPEED)

    assert str(payload.analysis_type) == "wind-speed"
