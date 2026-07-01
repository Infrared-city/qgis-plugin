"""Parse a local EPW (EnergyPlus Weather) file into the weather-array dict the
SDK payload builders consume — the "Upload EPW" alternative to
``query_infrared_epw``.

Mirrors the ArcGIS plugin's ``EpwParser`` (column indices) **and** the Python
plugin's ``query_infrared_epw`` filtering contract: the same ``TimeFrame``
(``{startTime, endTime}`` with ``month`` + 1-based ``hour``, half-open
``[start, end)`` upper bounds — no day filter) is applied so the resulting
arrays have the SAME length and chronological ordering the weather endpoint
would return. This matters because the SDK rejects a run when a weather-array
length != the sun-vector count derived from the analysis ``TimePeriod`` (see
``sdk_payloads._hours_window``).
"""

from __future__ import annotations

import os
from typing import Dict, List

_HEADER_LINES = 8

# 0-based EPW data-row column indices (standard EPW layout).
_COL_MONTH = 1
_COL_HOUR = 3            # 1-based hour, 1..24
_COL_DRY_BULB = 6
_COL_REL_HUMIDITY = 8
_COL_HORIZ_INFRARED = 12
_COL_GLOBAL_HORIZ = 13
_COL_DIRECT_NORMAL = 14
_COL_DIFFUSE_HORIZ = 15
_COL_WIND_DIR = 20
_COL_WIND_SPEED = 21
_MIN_COLUMNS = 22

# Output key -> EPW column. camelCase keys match the weather-endpoint response
# consumed by ``sdk_payloads.build_sdk_payload`` (windSpeed / windDirection /
# dryBulbTemperature / relativeHumidity / *Radiation / horizontalInfrared...).
_FIELDS = {
    "dryBulbTemperature": _COL_DRY_BULB,
    "relativeHumidity": _COL_REL_HUMIDITY,
    "horizontalInfraredRadiationIntensity": _COL_HORIZ_INFRARED,
    "globalHorizontalRadiation": _COL_GLOBAL_HORIZ,
    "directNormalRadiation": _COL_DIRECT_NORMAL,
    "diffuseHorizontalRadiation": _COL_DIFFUSE_HORIZ,
    "windDirection": _COL_WIND_DIR,
    "windSpeed": _COL_WIND_SPEED,
}


class EpwParseError(Exception):
    """Raised when a file is not a usable EPW or no rows match the time frame."""


def _read_data_rows(path: str) -> List[List[str]]:
    rows: List[List[str]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for _ in range(_HEADER_LINES):
            f.readline()
        for line in f:
            line = line.strip()
            if line:
                rows.append(line.split(","))
    return rows


def validate_file(path: str) -> None:
    """Quick structural check. Raises :class:`EpwParseError` if not a valid EPW.

    Mirrors the ArcGIS ``EpwParser.ValidateFile``: first line starts with
    ``LOCATION``, at least 8 header lines, and the first data row has >= 22
    columns.
    """
    if not path or not os.path.isfile(path):
        raise EpwParseError(f"EPW file not found: {path!r}")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        first = f.readline()
    if not first.upper().startswith("LOCATION"):
        raise EpwParseError(
            "Not an EPW file: the first line must start with 'LOCATION'."
        )
    rows = _read_data_rows(path)
    if not rows:
        raise EpwParseError("EPW file has no data rows after the 8 header lines.")
    if len(rows[0]) < _MIN_COLUMNS:
        raise EpwParseError(
            f"EPW data row has {len(rows[0])} columns; expected at least "
            f"{_MIN_COLUMNS}."
        )


def _month_in_window(month: int, start_m: int, end_m: int) -> bool:
    """Half-open [start_m, end_m) with year-wrap support.

    ``makeTimeFrameObj`` uses exclusive upper bounds (full-year -> end 13,
    a single month June -> [6, 7)), and seasons may wrap the year (southern
    summer Dec->Mar -> start 12, end 3).
    """
    if end_m > start_m:
        return start_m <= month < end_m
    return month >= start_m or month < end_m


def _hour_in_window(hour: int, start_h: int, end_h: int) -> bool:
    """Half-open [start_h, end_h) with midnight-wrap support.

    Mirrors :func:`_month_in_window`. A wrapped window such as 18->6 (start_h
    >= end_h) means "18:00 through 05:59", so no single ``start_h <= hour <
    end_h`` test can match it — the wrapped branch keeps ``hour >= start_h`` OR
    ``hour < end_h`` instead.
    """
    if end_h > start_h:
        return start_h <= hour < end_h
    return hour >= start_h or hour < end_h


def parse(path: str, time_frame: dict) -> Dict[str, List[float]]:
    """Parse ``path``, filtered by the same ``TimeFrame`` ``query_infrared_epw`` uses.

    Parameters
    ----------
    path : str
        Local ``.epw`` file path (call :func:`validate_file` first).
    time_frame : dict
        ``{"startTime": {"month": M, "hour": H}, "endTime": {"month", "hour"}}``
        as produced by ``makeTimeFrameObj`` / ``makeTimeFrameObjWithMonth``.
        Hours are 1-based (matching the EPW hour column); upper bounds are
        exclusive.

    Returns
    -------
    dict[str, list[float]]
        All weather fields keyed as the endpoint returns them, in
        chronological (file) order.

    Raises
    ------
    EpwParseError
        If no rows match the selected time frame.
    """
    start_m = int(time_frame["startTime"]["month"])
    end_m = int(time_frame["endTime"]["month"])
    start_h = int(time_frame["startTime"]["hour"])
    end_h = int(time_frame["endTime"]["hour"])

    out: Dict[str, List[float]] = {k: [] for k in _FIELDS}
    kept = 0
    for cols in _read_data_rows(path):
        if len(cols) < _MIN_COLUMNS:
            continue
        try:
            month = int(float(cols[_COL_MONTH]))
            hour = int(float(cols[_COL_HOUR]))  # 1-based
        except ValueError:
            continue
        if not _month_in_window(month, start_m, end_m):
            continue
        if not _hour_in_window(hour, start_h, end_h):
            continue
        # Parse all needed values first so a malformed row is skipped whole.
        try:
            values = {key: float(cols[idx]) for key, idx in _FIELDS.items()}
        except (ValueError, IndexError):
            continue
        for key, val in values.items():
            out[key].append(val)
        kept += 1

    if kept == 0:
        raise EpwParseError(
            "No EPW rows matched the selected time frame "
            f"(months [{start_m}, {end_m}), hours [{start_h}, {end_h}))."
        )
    return out
