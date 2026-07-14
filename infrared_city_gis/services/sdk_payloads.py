"""Build typed Infrared SDK analysis payloads from the dialog's UI state.

All helpers read fields directly from the dialog (``dlg``) so this stays
de-coupled from the dialog class itself. Each ``build_sdk_payload`` exit
either returns a typed payload or returns ``None`` after showing a
QMessageBox describing the missing input.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

from infrared_sdk.analyses.types import (
    AnalysesName,
    AnalysesUnion,
    PwcCriteria,
    PwcModelRequest,
    SolarModelRequest,
    SolarRadiationModelRequest,
    SvfModelRequest,
    TcsModelRequest,
    TcsSubtype,
    UtciModelRequest,
    WindModelRequest,
)
from infrared_sdk.models import TimePeriod

from ..infrared_logger import logger
from ..models.analysis import (
    AnalysisType,
    PedestrianWindComfortType,
    ThermalComfortStatisticsType,
)
from ..models.timeframes_parser import (
    HOURS_LIMIT,
    SEASON_LIMITS,
    makeTimeFrameObj,
    makeTimeFrameObjWithMonth,
)
from ..services.epw_query import query_infrared_epw
from ..services.geometry import get_center_lon_lat_from_bbox

# Days per month — leap years are irrelevant since TimePeriod is year-less and
# SDK accepts day=29 for February.
_DAYS_PER_MONTH = {
    1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
    7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
}


# ---------------------------------------------------------------------------
# TimePeriod helpers
# ---------------------------------------------------------------------------


def _hours_window(hourly_value: str) -> Tuple[int, int]:
    """Return (start_hour, end_hour) within 0..23 for an SDK TimePeriod.

    The plugin's ``HOURS_LIMIT`` table is shared with the EPW weather
    filter, which interprets it as a *half-open* interval —
    ``[startTime, endTime)``. Morning's ``endTime: 10`` therefore covers
    hours 6, 7, 8, 9 → 4 hours per day, and the EPW endpoint returns 4
    weather samples per day for each ring tile.

    The SDK's :class:`TimePeriod` is *inclusive on both ends* — its
    sun-vector engine counts hours from ``start_hour`` through
    ``end_hour`` inclusive. If we passed ``endTime: 10`` straight through
    the engine would generate 5 sun vectors per day (6,7,8,9,10), and
    the API would reject the run with::

        DNI length 124 != sun_vectors 155

    We therefore subtract one from the ``HOURS_LIMIT`` end to translate
    half-open → inclusive. ``"all-hours"`` is a special case: there's no
    half-open boundary to translate, so we return the full inclusive
    range 0..23 directly (24 hours × N days, matching the EPW endpoint
    behaviour for full-day windows).
    """
    if hourly_value == "all-hours":
        return 0, 23
    limits = HOURS_LIMIT[hourly_value]
    start = max(0, min(23, int(limits["startTime"])))
    end = max(start, min(23, int(limits["endTime"]) - 1))
    return start, end


def _time_period_for_month(month: int, hourly_value: str) -> TimePeriod:
    """TimePeriod covering an entire calendar month, restricted to ``hourly``."""
    s_h, e_h = _hours_window(hourly_value)
    return TimePeriod(
        start_month=month, start_day=1, start_hour=s_h,
        end_month=month, end_day=_DAYS_PER_MONTH[month], end_hour=e_h,
    )


def _time_period_for_season(
    is_north: bool, season_value: str, hourly_value: str
) -> TimePeriod:
    """TimePeriod covering a season. Year-wrapping seasons fall back to full-year.

    SEASON_LIMITS encodes seasons as half-open month ranges. For non-wrap
    seasons we close at the last day of ``ends - 1``. Wrap-around seasons
    (e.g. northern winter Dec→Feb) cannot be represented in a single
    SDK TimePeriod, so we widen them to the full year — the API will
    happily run over a longer window.
    """
    s_h, e_h = _hours_window(hourly_value)
    if season_value == "full-year":
        return TimePeriod(
            start_month=1, start_day=1, start_hour=s_h,
            end_month=12, end_day=31, end_hour=e_h,
        )
    hemi = "north" if is_north else "south"
    s_lim = SEASON_LIMITS[hemi][season_value]
    start_m = int(s_lim["starts"])
    end_m_inclusive = int(s_lim["ends"]) - 1
    if end_m_inclusive < 1 or start_m > end_m_inclusive:
        # Year-wrap (e.g. northern winter Dec-Feb): widen to full year.
        logger.info(
            "sdk_payloads: season %r wraps the year; widening TimePeriod to full year",
            season_value,
        )
        return TimePeriod(
            start_month=1, start_day=1, start_hour=s_h,
            end_month=12, end_day=31, end_hour=e_h,
        )
    return TimePeriod(
        start_month=start_m, start_day=1, start_hour=s_h,
        end_month=end_m_inclusive, end_day=_DAYS_PER_MONTH[end_m_inclusive],
        end_hour=e_h,
    )


# ---------------------------------------------------------------------------
# Sub-type / criteria mapping
# ---------------------------------------------------------------------------


def _criteria_from_pwc(pwc: PedestrianWindComfortType) -> PwcCriteria:
    """Map plugin PWC enum → SDK PwcCriteria.

    The string values match the wire format the inference service expects
    1:1 (the actual ISO standard is VDI 3787, 4 digits). Older versions
    of this helper translated the plugin's ``vdi-3787`` to the SDK's
    legacy ``vdi-387`` enum member, which the API then rejected with
    ``ValueError: Invalid criteria value: vdi-387`` — that translation
    has been removed; the bundled SDK enum now exposes ``vdi_3787`` as
    the correct member.
    """
    return PwcCriteria(pwc.value)


def _subtype_from_tcs(tcs: ThermalComfortStatisticsType) -> TcsSubtype:
    return TcsSubtype(tcs.value)


def _weather_data(dlg, analysis_type, weather_file: str, tf) -> dict:
    """Return the weather-array dict for a thermal/wind analysis.

    If the dialog has a custom EPW uploaded for this analysis type
    (``dlg._epw_paths[analysis_type]``), parse it locally with the SAME time
    frame the endpoint uses; otherwise query the infrared.city weather file.
    Both produce the same camelCase keys, so callers are agnostic to source.
    """
    epw_path = getattr(dlg, "_epw_paths", {}).get(analysis_type)
    if epw_path:
        from .epw_parser import parse as parse_epw
        # Log only the basename — the full path leaks the user's home dir /
        # username (PII) into the plugin log.
        logger.info("Using uploaded EPW for %s: %s", analysis_type, os.path.basename(epw_path))
        return parse_epw(epw_path, tf)
    return query_infrared_epw(
        file_name=weather_file, time_frame=tf, api_key=dlg.api_key,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_sdk_payload(dlg) -> Optional[AnalysesUnion]:
    """Build a typed SDK analysis payload from the dialog's current UI state.

    The caller must have set ``dlg.analysis_type`` (an :class:`AnalysisType`).
    Sub-analysis criteria (PWC, TCS) are also written back onto the dialog
    via ``dlg.sub_analysis_type`` so downstream display code can reuse them.

    Returns ``None`` and shows a QMessageBox if validation fails.
    """
    from qgis.PyQt.QtWidgets import QMessageBox

    at = dlg.analysis_type

    if at == AnalysisType.WIND_SPEED:
        ws = int(dlg.wind_speed_input.value())
        wd = int(dlg.wind_direction_input.value())
        if ws <= 0 or wd < 0 or wd > 360:
            QMessageBox.warning(
                dlg, "Invalid Parameters",
                "Wind speed must be > 0 and direction 0–360°.",
            )
            return None
        return WindModelRequest(
            analysis_type=AnalysesName.wind_speed,
            wind_speed=ws,
            wind_direction=wd,
        )

    if at == AnalysisType.PEDESTRIAN_WIND_COMFORT:
        pwc_type = dlg.pwc_type_dropdown.currentData()
        season = dlg.season_dropdown_pwc.currentData()
        hours = dlg.hours_dropdown_pwc.currentData()
        weather_file = dlg.weather_file_input_pwc.currentText().strip()
        epw_path = getattr(dlg, "_epw_paths", {}).get(at)
        if not (pwc_type and season and hours and (weather_file or epw_path)):
            QMessageBox.warning(
                dlg, "Missing Input",
                "Please fill in all PWC fields (pick a weather file or upload an EPW).",
            )
            return None
        tf = makeTimeFrameObj(isNorthHem=True, season=season.value, hourly=hours.value)
        wind_data = _weather_data(dlg, at, weather_file, tf)
        dlg.sub_analysis_type = pwc_type
        return PwcModelRequest(
            analysis_type=AnalysesName.pedestrian_wind_comfort,
            criteria=_criteria_from_pwc(pwc_type),
            wind_speed=[float(x) for x in wind_data["windSpeed"]],
            wind_direction=[float(x) for x in wind_data["windDirection"]],
        )

    if at == AnalysisType.SKY_VIEW_FACTORS:
        # lat/lon are optional for SVF inference itself, but the backend
        # vegetation validator needs them as its conversion referencePoint —
        # without them an SVF run WITH a tree layer is rejected (HTTP 400,
        # "latitude and longitude are required for vegetation conversion").
        # The area orchestrator injects the tile centroid automatically; the
        # single-tile path sends the payload as-is, so stamp the tile centre
        # here (same convention as the thermal/solar builders below).
        lon, lat = get_center_lon_lat_from_bbox(dlg.bbox, dlg.crs)
        return SvfModelRequest(
            analysis_type=AnalysesName.sky_view_factors,
            latitude=lat, longitude=lon,
        )

    if at in (AnalysisType.DAYLIGHT_AVAILABILITY, AnalysisType.DIRECT_SUN_HOURS):
        if at == AnalysisType.DAYLIGHT_AVAILABILITY:
            month = dlg.month_dropdown_da.currentData().number
            hours = dlg.hours_dropdown_da.currentData()
            sdk_name = AnalysesName.daylight_availability
        else:
            month = dlg.month_dropdown_dsh.currentData().number
            hours = dlg.hours_dropdown_dsh.currentData()
            sdk_name = AnalysesName.direct_sun_hours
        lon, lat = get_center_lon_lat_from_bbox(dlg.bbox, dlg.crs)
        return SolarModelRequest(
            analysis_type=sdk_name,
            latitude=lat, longitude=lon,
            time_period=_time_period_for_month(month, hours.value),
        )

    if at == AnalysisType.SOLAR_RADIATION:
        month = dlg.month_dropdown_sr.currentData().number
        hours = dlg.hours_dropdown_sr.currentData()
        weather_file = dlg.weather_file_input_sr.currentText().strip()
        epw_path = getattr(dlg, "_epw_paths", {}).get(at)
        if not (weather_file or epw_path):
            QMessageBox.warning(dlg, "Missing Input", "Weather file or uploaded EPW is required.")
            return None
        tf = makeTimeFrameObjWithMonth(month=month, hourly=hours.value)
        wd = _weather_data(dlg, at, weather_file, tf)
        lon, lat = get_center_lon_lat_from_bbox(dlg.bbox, dlg.crs)
        return SolarRadiationModelRequest(
            analysis_type=AnalysesName.solar_radiation,
            latitude=lat, longitude=lon,
            time_period=_time_period_for_month(month, hours.value),
            diffuse_horizontal_radiation=[float(x) for x in wd["diffuseHorizontalRadiation"]],
            direct_normal_radiation=[float(x) for x in wd["directNormalRadiation"]],
        )

    if at == AnalysisType.THERMAL_COMFORT_INDEX:
        month = dlg.month_dropdown_tci.currentData().number
        hours = dlg.hours_dropdown_tci.currentData()
        weather_file = dlg.weather_file_input_tci.currentText().strip()
        epw_path = getattr(dlg, "_epw_paths", {}).get(at)
        if not (weather_file or epw_path):
            QMessageBox.warning(dlg, "Missing Input", "Weather file or uploaded EPW is required.")
            return None
        tf = makeTimeFrameObjWithMonth(month=month, hourly=hours.value)
        wd = _weather_data(dlg, at, weather_file, tf)
        lon, lat = get_center_lon_lat_from_bbox(dlg.bbox, dlg.crs)
        if dlg.legend_min_enable_tci.isChecked():
            dlg.min_legend_value = dlg.legend_min_input_tci.value()
        if dlg.legend_max_enable_tci.isChecked():
            dlg.max_legend_value = dlg.legend_max_input_tci.value()
        return UtciModelRequest(
            analysis_type=AnalysesName.thermal_comfort_index,
            latitude=lat, longitude=lon,
            time_period=_time_period_for_month(month, hours.value),
            horizontal_infrared_radiation_intensity=[float(x) for x in wd["horizontalInfraredRadiationIntensity"]],
            diffuse_horizontal_radiation=[float(x) for x in wd["diffuseHorizontalRadiation"]],
            direct_normal_radiation=[float(x) for x in wd["directNormalRadiation"]],
            global_horizontal_radiation=[float(x) for x in wd["globalHorizontalRadiation"]],
            dry_bulb_temperature=[float(x) for x in wd["dryBulbTemperature"]],
            wind_speed=[float(x) for x in wd["windSpeed"]],
            relative_humidity=[float(x) for x in wd["relativeHumidity"]],
        )

    if at == AnalysisType.THERMAL_COMFORT_STATISTICS:
        season = dlg.season_dropdown_tcs.currentData()
        hours = dlg.hours_dropdown_tcs.currentData()
        tcs_type = dlg.tcs_type_dropdown.currentData()
        weather_file = dlg.weather_file_input_tcs.currentText().strip()
        epw_path = getattr(dlg, "_epw_paths", {}).get(at)
        if not (season and hours and tcs_type and (weather_file or epw_path)):
            QMessageBox.warning(
                dlg, "Missing Input",
                "Please fill in all TCS fields (pick a weather file or upload an EPW).",
            )
            return None
        tf = makeTimeFrameObj(
            isNorthHem=True, season=season.value, hourly=hours.value, analysis_type=at,
        )
        wd = _weather_data(dlg, at, weather_file, tf)
        lon, lat = get_center_lon_lat_from_bbox(dlg.bbox, dlg.crs)
        dlg.sub_analysis_type = tcs_type
        return TcsModelRequest(
            analysis_type=AnalysesName.thermal_comfort_statistics,
            subtype=_subtype_from_tcs(tcs_type),
            latitude=lat, longitude=lon,
            time_period=_time_period_for_season(True, season.value, hours.value),
            horizontal_infrared_radiation_intensity=[float(x) for x in wd["horizontalInfraredRadiationIntensity"]],
            diffuse_horizontal_radiation=[float(x) for x in wd["diffuseHorizontalRadiation"]],
            direct_normal_radiation=[float(x) for x in wd["directNormalRadiation"]],
            global_horizontal_radiation=[float(x) for x in wd["globalHorizontalRadiation"]],
            dry_bulb_temperature=[float(x) for x in wd["dryBulbTemperature"]],
            wind_speed=[float(x) for x in wd["windSpeed"]],
            relative_humidity=[float(x) for x in wd["relativeHumidity"]],
        )

    logger.warning("build_sdk_payload: unknown analysis type %s", at)
    return None
