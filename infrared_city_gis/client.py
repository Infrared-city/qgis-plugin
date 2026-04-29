import base64
import gzip
import json
import os
import time
from datetime import datetime
from enum import Enum
from typing import Optional

import numpy as np
import requests
from qgis.core import QgsApplication

from .constants import RUN_ANALYSIS_ENDPOINT
from .exceptions import InfraredAPIError
from .infrared_logger import logger
from .models.timeframes_parser import adjustDatetime, makeTimeFrameObjWithMonth
from .services.geometry import generate_geotiff, crop_matrix
from .utils.helper import decode
from .visualization.display import add_geojson_then_raster


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
    
    # debug_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "debug")
    # os.makedirs(debug_dir, exist_ok=True)
    
    # debug_path = os.path.join(debug_dir, f"payload_{analysis_type}.json")
    # with open(debug_path, "w", encoding="utf-8") as f:
    #     json.dump(payload, f, indent=2)

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
                response = requests.post(
                    RUN_ANALYSIS_ENDPOINT,
                    data=base64_encoded,
                    headers=headers,
                )
                response.raise_for_status()
                break
            except requests.RequestException as e:
                logger.info(f"Attempt {attempt} failed: {e}")
                if attempt < retries:
                    logger.info(f"Retrying in 5 seconds...")
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

            # Write full response (without the large 'result' blob) to a debug file
            # debug_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "debug")
            # os.makedirs(debug_dir, exist_ok=True)
            # debug_path = os.path.join(debug_dir, f"last_response_{analysis_type}.json")
            # debug_data = {k: v for k, v in raw_json.items() if k != "result"}
            # with open(debug_path, "w", encoding="utf-8") as f:
            #     json.dump(debug_data, f, indent=2)
            #logger.info(f"Response debug dump: {debug_path}")
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

        # debug_path = os.path.join(plugin_data_dir, f"{base_name}_run_{sim_run_id}_matrix.json")
        # with open(debug_path, "w", encoding="utf-8") as f:
        #     json.dump(decoded_result, f, indent=2)

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


def get_payload_shadow_mask(
    geometry_path: Optional[str] = None,
    bbox: Optional[list] = None,
    crs: Optional[str] = None,
    datetime: Optional[str] = None,
    analysis_type: Optional[str] = None,
) -> dict:
    if geometry_path is not None:
        with open(geometry_path, "r", encoding="utf-8") as f:
            geometry_data = json.load(f)
        logger.info("Geometry loaded")
    else:
        geometry_data = {}

    return {
        "inputs": {
            "bbox": bbox,
            "crs": crs,
            "geometries": geometry_data,
            "geometry-type": "dotbim",
            "reduction-factor": 1,
            "skip-compression": False,
            "type": analysis_type,
            "datetime": datetime,
        },
        "response": "document",
        "outputs": {
            "stringOutput": {"transmissionMode": "value"},
            "imageOutput": {"transmissionMode": "value"},
        },
    }

def get_shadow_mask_payload(dt_str: str, lon: float, lat: float) -> dict:
    dt_stamps = adjustDatetime(dt_str)

    return {
        "analysis-type": "shadow-mask",
        "geometries": None,
        "latitude": lat,
        "longitude": lon,
        "month-stamp": dt_stamps["month-stamp"],
        "hour-stamp": dt_stamps["hour-stamp"],
        "day-stamp": dt_stamps["day-stamp"],
        "minute-stamp": dt_stamps["minute-stamp"],
    }
