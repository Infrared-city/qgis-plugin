import base64
import gzip
import json
import os
import time
from datetime import datetime
from enum import Enum

import numpy as np
from qgis.core import QgsApplication

from .constants import RUN_ANALYSIS_ENDPOINT, RUN_ANALYSIS_HTTP_TIMEOUT
from .exceptions import InfraredAPIError
from .infrared_logger import logger
from .models.timeframes_parser import makeTimeFrameObjWithMonth
from .services import qgis_http as requests
from .services.geometry import crop_matrix, generate_geotiff
from .utils.helper import decode


class Headers(Enum):
    """Headers for Infrared API"""
    X_API_KEY = 'X-Api-Key'
    CONTENT_TYPE = 'Content-Type'
    X_INFRARED_ENCODING = 'X-Infrared-Encoding'
    X_INFRARED_CLIENT = "X-Infrared-Client"
    X_INFRARED_PROXY = "infrared-ogc-proxy"
    APPLICATION_JSON = 'application/json'
    TEXT_PLAIN = 'text/plain'
    GZIP = 'gzip'
    UTF_8 = 'utf-8'


def load_dotbim(dotbim_path: str) -> dict:
    with open(dotbim_path, "r", encoding="utf-8") as f:
        dotbim_data = json.load(f)
    logger.info(f"Dotbim loaded, {dotbim_path}")
    return dotbim_data


def process_run_analysis(
    payload: dict,
    geometry_path: str,
    bbox: list,
    crs: str,
    api_key: str,
    analysis_type: str,
    do_crop: bool = False,
    criteria = None
) -> str:
    logger.info("Processing run analysis endpoint...")

    json_data = json.dumps(payload)

    compressed_data = gzip.compress(json_data.encode(Headers.UTF_8.value))
    base64_encoded = base64.b64encode(compressed_data).decode(Headers.UTF_8.value)

    try:
        headers = {
            Headers.X_API_KEY.value.lower(): api_key,
            Headers.CONTENT_TYPE.value: Headers.TEXT_PLAIN.value,
            Headers.X_INFRARED_ENCODING.value: Headers.GZIP.value,
            Headers.X_INFRARED_CLIENT.value: Headers.X_INFRARED_PROXY.value,
        }

        retries = 3
        response = None
        for attempt in range(1, retries + 1):
            try:
                # Explicit (connect, read) timeout — without this the QGIS UI
                # can hang indefinitely if upstream stalls on the socket.
                response = requests.post(
                    RUN_ANALYSIS_ENDPOINT,
                    data=base64_encoded,
                    headers=headers,
                    timeout=RUN_ANALYSIS_HTTP_TIMEOUT,
                )
                response.raise_for_status()
                break
            except requests.Timeout as e:
                logger.warning(
                    "Attempt %d timed out (connect=%ss, read=%ss): %s",
                    attempt, RUN_ANALYSIS_HTTP_TIMEOUT[0],
                    RUN_ANALYSIS_HTTP_TIMEOUT[1], e,
                )
                if attempt >= retries:
                    raise InfraredAPIError(
                        status_code=None,
                        server_message=(
                            f"The simulation request timed out after "
                            f"{RUN_ANALYSIS_HTTP_TIMEOUT[1]}s. The server may be "
                            f"under heavy load; please try again."
                        ),
                    ) from e
                logger.info("Retrying in 5 seconds...")
                time.sleep(5)
            except requests.RequestException as e:
                logger.info(f"Attempt {attempt} failed: {e}")
                if attempt < retries:
                    logger.info("Retrying in 5 seconds...")
                    time.sleep(5)
                else:
                    logger.error(f"All {retries} attempts failed. Aborting request.")
                    status = e.response.status_code if e.response is not None else None
                    body_text = e.response.text if e.response is not None else ""
                    logger.error(f"Status: {status}")
                    logger.error(f"Body: {body_text}")
                    logger.error(f"Response: {e.response}")

                    parsed_message = None
                    if e.response is not None:
                        try:
                            body_json = e.response.json()
                            parsed_message = body_json.get("message")
                            logger.error(f"Parsed message: {parsed_message}")
                        except ValueError:
                            parsed_message = None

                    raise InfraredAPIError(
                        status_code=status,
                        server_message=parsed_message,
                    ) from e

        logger.info(f"Response received from client, status code: {response.status_code}")
        logger.info(f"Response headers: {dict(response.headers)}")

        # --- Log & dump raw response JSON; extract minLegend/maxLegend ---
        min_legend = None
        max_legend = None
        try:
            raw_json = response.json()
            top_level_keys = list(raw_json.keys())
            logger.info(f"Response top-level keys: {top_level_keys}")
            for key in top_level_keys:
                if key != "result":
                    logger.info(f"  Response field '{key}': {raw_json[key]}")

            min_legend = raw_json.get("minLegend")
            max_legend = raw_json.get("maxLegend")
            logger.info(f"Legend range from API: minLegend={min_legend}, maxLegend={max_legend}")

        except Exception as e:
            logger.warning(f"Could not parse/dump response JSON for debug: {e}")

        decoded_result = decode(response.content)

        plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "data")
        os.makedirs(plugin_data_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(geometry_path))[0]

        # Per-simulation-run timestamp so that re-running on the same selection
        # produces a fresh .tif instead of overwriting the previous result.
        # The QGIS layer name stays clean — only the file path carries the run id.
        sim_run_id = datetime.now().strftime('%H-%M-%S')

        if analysis_type == "pedestrian-wind-comfort":
            matrix = np.array(decoded_result, dtype=str)
        else:
            matrix = np.array(decoded_result, dtype=np.float32)
            logger.info(f"Matrix stats — min: {float(np.nanmin(matrix)):.4f}, max: {float(np.nanmax(matrix)):.4f}, shape: {matrix.shape}")

        file_path = os.path.join(plugin_data_dir, f"{base_name}_run_{sim_run_id}.tif")

        bbox_tuple = (bbox[0], bbox[1], bbox[2], bbox[3])
        logger.info(f"Bbox: {bbox}")
        logger.info(f"Bbox tuple: {bbox_tuple}")
        matrix = np.flipud(matrix)

        if do_crop:
            matrix = crop_matrix(matrix)

        generate_geotiff(matrix, bbox_tuple, crs, file_path, simulation_type=analysis_type,criteria=criteria)

        logger.info(f"Matrix shape: {matrix.shape}")

        return file_path, min_legend, max_legend

    except Exception as e:
        logger.error(f"Error processing tile : {e}")
        raise


def get_windspeed_payload(wind_direction: float, wind_speed: float) -> dict:
    return {
        "analysis-type": "wind-speed",
        "geometries": None,
        "wind-direction": wind_direction,
        "wind-speed": wind_speed,
    }


def get_utci_payload(weather_data: dict, lon: float, lat: float, time_frame: dict) -> dict:
    start = time_frame["startTime"]
    end = time_frame["endTime"]
    month_stamp_array = [start["month"], end["month"]]
    hour_stamp_array = [start["hour"], end["hour"]]

    return {
        "analysis-type": "thermal-comfort-index",
        "geometries": None,
        "latitude": lat,
        "longitude": lon,
        "month-stamp": month_stamp_array,
        "hour-stamp": hour_stamp_array,
        "horizontal-infrared-radiation-intensity": weather_data["horizontalInfraredRadiationIntensity"],
        "diffuse-horizontal-radiation": weather_data["diffuseHorizontalRadiation"],
        "direct-normal-radiation": weather_data["directNormalRadiation"],
        "global-horizontal-radiation": weather_data["globalHorizontalRadiation"],
        "dry-bulb-temperature": weather_data["dryBulbTemperature"],
        "wind-speed": weather_data["windSpeed"],
        "relative-humidity": weather_data["relativeHumidity"],
    }


def get_tcs_payload(weather_data: dict, lon: float, lat: float, time_frame: dict, subtype: str) -> dict:
    start = time_frame["startTime"]
    end = time_frame["endTime"]
    month_stamp_array = [start["month"], end["month"]]
    hour_stamp_array = [start["hour"], end["hour"]]

    return {
        "analysis-type": "thermal-comfort-statistics",
        "geometries": None,
        "latitude": lat,
        "longitude": lon,
        "month-stamp": month_stamp_array,
        "hour-stamp": hour_stamp_array,
        "horizontal-infrared-radiation-intensity": weather_data["horizontalInfraredRadiationIntensity"],
        "diffuse-horizontal-radiation": weather_data["diffuseHorizontalRadiation"],
        "direct-normal-radiation": weather_data["directNormalRadiation"],
        "global-horizontal-radiation": weather_data["globalHorizontalRadiation"],
        "dry-bulb-temperature": weather_data["dryBulbTemperature"],
        "wind-speed": weather_data["windSpeed"],
        "relative-humidity": weather_data["relativeHumidity"],
        "subtype": subtype,
    }


def get_solar_radiation_payload(weather_data: dict, lon: float, lat: float, time_frame: dict) -> dict:
    start = time_frame["startTime"]
    end = time_frame["endTime"]
    month_stamp_array = [start["month"], end["month"]]
    hour_stamp_array = [start["hour"], end["hour"]]

    return {
        "analysis-type": "solar-radiation",
        "geometries": None,
        "latitude": lat,
        "longitude": lon,
        "month-stamp": month_stamp_array,
        "hour-stamp": hour_stamp_array,
        "diffuse-horizontal-radiation": weather_data["diffuseHorizontalRadiation"],
        "direct-normal-radiation": weather_data["directNormalRadiation"],
    }


def get_daylight_availability_payload(month: int, hourly: str, lon: float, lat: float) -> dict:
    return get_solar_analyses_payload("daylight-availability", month, hourly, lon, lat)


def get_solar_analyses_payload(analysis_type: str, month: int, hourly: str, lon: float, lat: float) -> dict:
    time_frame = makeTimeFrameObjWithMonth(month=month, hourly=hourly)

    start = time_frame["startTime"]
    end = time_frame["endTime"]
    month_stamp_array = [start["month"], end["month"]]
    hour_stamp_array = [start["hour"], end["hour"]]

    return {
        "analysis-type": analysis_type,
        "geometries": None,
        "latitude": lat,
        "longitude": lon,
        "month-stamp": month_stamp_array,
        "hour-stamp": hour_stamp_array,
    }

def get_pwc_payload(wind_data: dict, criteria: str) -> dict:

    return {
        "analysis-type": "pedestrian-wind-comfort",
        "geometries": None,
        "wind-data": wind_data,
        "criteria": criteria,
    }


def get_direct_sun_hours_payload(month: int, hourly: str, lon: float, lat: float) -> dict:
    return get_solar_analyses_payload("direct-sun-hours", month, hourly, lon, lat)


