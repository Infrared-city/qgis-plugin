"""Query weather-file data through the Infrared SDK's weather client.

Historically this POSTed to the legacy public endpoint on
``app.infrared.city`` (``/api/public/weatherfiles/{fileName}/data``). That
backend keeps its own API-key registry, so keys issued for
``api.infrared.city`` were rejected there with HTTP 401 even though every
other plugin call worked. The SDK's ``WeatherServiceClient`` goes through
``api.infrared.city`` — the same gateway (and key) as the rest of the
plugin, including the weather-file *list* fetch in ``fetch.py``.

The output contract is unchanged: a dict of camelCase weather arrays in
EPW file (chronological) order, the same shape ``epw_parser.parse``
produces for uploaded EPW files.
"""

import json
from typing import Dict, List

from infrared_sdk import InfraredClient
from infrared_sdk.layers.service import WeatherServiceError
from infrared_sdk.models import TimePeriod

from ..exceptions import InfraredAPIError
from ..infrared_logger import logger

# camelCase keys consumed by ``sdk_payloads.build_sdk_payload`` — the same
# set ``epw_parser._FIELDS`` produces, so both weather sources stay
# interchangeable.
_FIELDS = (
    "dryBulbTemperature",
    "relativeHumidity",
    "horizontalInfraredRadiationIntensity",
    "globalHorizontalRadiation",
    "directNormalRadiation",
    "diffuseHorizontalRadiation",
    "windDirection",
    "windSpeed",
)

# Leap years are irrelevant: this only bounds an inclusive month-end filter
# (see sdk_payloads._DAYS_PER_MONTH for the same table on the analysis side).
_DAYS_PER_MONTH = {
    1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
    7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
}


def _time_periods_from_time_frame(time_frame: dict) -> List[TimePeriod]:
    """Translate the plugin's half-open ``TimeFrame`` into inclusive SDK TimePeriods.

    ``makeTimeFrameObj``/``makeTimeFrameObjWithMonth`` produce half-open
    ``[start, end)`` windows with year-wrap (full-year is months 1→13,
    December alone is 12→1, northern winter is 12→3) and 1-based EPW hours
    (all-hours is 1→25). The utilities service instead filters with a
    both-ends-inclusive :class:`TimePeriod` over 0-based hours, so:

    - hour:  EPW hour ``h`` in ``[s, e)``  ⇔  0-based ``h-1`` in ``[s-1, e-2]``
    - month: inclusive end month is ``end - 1`` (underflow 0 → December)

    Two backend quirks force splitting a window into several periods:

    - The service applies the day filter to EVERY month in the range
      independently, so a multi-month period ending in a sub-31-day month
      (e.g. Sep→Nov with ``end_day=30``) would silently drop day-31 rows
      of the earlier months. Such windows split the short final month into
      its own period. (No two consecutive months are both shorter than 31
      days, so the leading period always ends on a 31-day month.)
    - A year-wrapping window (e.g. northern winter Dec→Feb) cannot be
      expressed at all — the validator requires start ≤ end — so it splits
      at the year boundary, Jan side first.

    Periods are emitted in ascending month order, so concatenating their
    results preserves EPW file order — exactly what the legacy endpoint
    returned for wrapped windows.
    """
    start_m = int(time_frame["startTime"]["month"])
    end_m = int(time_frame["endTime"]["month"]) - 1
    if end_m < 1:
        end_m = 12
    start_h = int(time_frame["startTime"]["hour"]) - 1
    end_h = int(time_frame["endTime"]["hour"]) - 2

    def _tp(s_month: int, e_month: int) -> TimePeriod:
        return TimePeriod(
            start_month=s_month, start_day=1, start_hour=start_h,
            end_month=e_month, end_day=_DAYS_PER_MONTH[e_month], end_hour=end_h,
        )

    def _ascending(s_month: int, e_month: int) -> List[TimePeriod]:
        if s_month < e_month and _DAYS_PER_MONTH[e_month] < 31:
            return [_tp(s_month, e_month - 1), _tp(e_month, e_month)]
        return [_tp(s_month, e_month)]

    if start_m <= end_m:
        return _ascending(start_m, end_m)
    return _ascending(1, end_m) + _ascending(start_m, 12)


def _server_message_from(err: WeatherServiceError):
    """Best-effort human message out of the (JSON) error body."""
    try:
        body = json.loads(err.response_body)
        # utilities-service errors use FastAPI's "detail"; the gateway and
        # older services use "message".
        return body.get("message") or body.get("detail")
    except Exception:
        return None


def query_infrared_epw(file_name: str, time_frame: dict, api_key: str) -> Dict[str, List[float]]:
    """Query weather data for ``file_name`` from the infrared.city database.

    Returns
    -------
    dict[str, list[float]]
        Weather fields keyed as ``build_sdk_payload`` consumes them
        (camelCase), filtered to ``time_frame``, in EPW file order.

    Raises
    ------
    InfraredAPIError
        On any HTTP failure from the weather service.
    """
    logger.info(
        f"Querying epw data for {file_name} with time frame: \n{time_frame}"
    )
    try:
        with InfraredClient(api_key=api_key) as client:
            points = []
            for tp in _time_periods_from_time_frame(time_frame):
                points.extend(
                    client.weather.filter_weather_data(
                        identifier=file_name, time_period=tp,
                    )
                )
    except WeatherServiceError as e:
        logger.error(f"Epw query request failed: {e}")
        raise InfraredAPIError(
            status_code=e.status_code or None,
            server_message=_server_message_from(e),
        ) from e

    if not points:
        logger.warning(
            f"Epw query for {file_name} returned no data points for {time_frame}"
        )
    return {field: [getattr(p, field) for p in points] for field in _FIELDS}
