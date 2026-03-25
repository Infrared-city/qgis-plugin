import json
import gzip
import base64
import requests
import numpy as np
from .utils.helper import decode, decode_gzip_base64_binary
from enum import Enum
import os
from qgis.core import QgsApplication
import time
from .infrared_logger import logger
import numpy as np
from .services.geometry import generate_geotiff, crop_matrix
from .visualization.display import add_geojson_then_raster
from .models.timeframes_parser import adjustDatetime, makeTimeFrameObjWithMonth
from .exceptions import InfraredAPIError
    

class HEADERS(Enum):
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

def load_dotbim(dotbim_path):
    with open(dotbim_path, "r", encoding="utf-8") as f:
        dotbim_data = json.load(f)
    logger.info(f"Dotbim loaded, {dotbim_path}")
    return dotbim_data


def process_run_analysis(payload, geometry_path, bbox,crs, api_key, analysis_type, do_crop = False):
    INFRARED_URL = "https://fbiw2nq5ac.execute-api.eu-central-1.amazonaws.com/development-v1"
    
    logger.info("Processing run analysis endpoint...")

    json_data = json.dumps(payload)
    
    compressed_data = gzip.compress(json_data.encode(HEADERS.UTF_8.value))
    base64_encoded = base64.b64encode(compressed_data).decode(HEADERS.UTF_8.value)

    try:
        headers = {
            HEADERS.X_API_KEY.value.lower(): api_key,
            HEADERS.CONTENT_TYPE.value: HEADERS.TEXT_PLAIN.value,
            HEADERS.X_INFRARED_ENCODING.value: HEADERS.GZIP.value,
            HEADERS.X_INFRARED_CLIENT.value: HEADERS.X_INFRARED_PROXY.value,
        }
        logger.info(f"Headers: {headers}")

        retries = 3
        response = None
        for attempt in range(1,retries + 1):
            try:
                response = requests.post(
                    f"{INFRARED_URL}/api/run-analysis",
                    data=base64_encoded,
                    headers=headers,
                )
                response.raise_for_status()
                break
            except requests.RequestException as e:
                logger.info(f"Attempt {attempt} failed: {e}")
                if attempt < retries:
                    logger.info(f"Retrying in {5} seconds...")
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
                        except Exception:
                            parsed_message = None

                    raise InfraredAPIError(
                        status_code=status,
                        server_message=parsed_message,
                    ) from e

        
        logger.info(f"Response received from client, status code: {response.status_code}")

        decoded_result = decode(response.content)

        #matrix = np.array(decoded_result, dtype=np.float32)
        
        if analysis_type == "pedestrian-wind-comfort":
            matrix = np.array(decoded_result, dtype=str)
        else:
            matrix = np.array(decoded_result, dtype=np.float32)

        plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "data")
        os.makedirs(plugin_data_dir, exist_ok=True)

        base_name = os.path.splitext(os.path.basename(geometry_path))[0]
        file_path = os.path.join(plugin_data_dir, f"{base_name}.tif")


        bbox_tuple = (bbox[0], bbox[1], bbox[2], bbox[3])
        logger.info(f"Bbox: {bbox}")
        logger.info(f"Bbox tuple: {bbox_tuple}")
        matrix = np.flipud(matrix)

        if do_crop:
            matrix = crop_matrix(matrix)

        generate_geotiff(matrix, bbox_tuple, crs,file_path, simulation_type=analysis_type)
        
        
        logger.info(f"Matrix shape: {matrix.shape}")

        return file_path

    except Exception as e:
        logger.error(f"Error processing tile : {e}")
        raise


def process_ogc(payload,geometry_path,analysis_type,api_key):
    INFRARED_URL = "https://ogc.infrared.city"
    INFRARED_URL = "http://127.0.0.1:8050/"


    try:
        logger.info("Try processing OGC request...")
        retries = 3
        delay = 5

        for attempt in range(1, retries + 1):
            try:
                response = requests.post(
                    f"{INFRARED_URL}/processes/{analysis_type}/execution",
                    json=payload,
                    headers={
                        "X-Api-Key": api_key,
                        "Content-Type": "application/json",
                        "X-Infrared-Encoding": "gzip+base64",
                        "X-Infrared-Client": "infrared-ogc-proxy",
                        "Accept": "*/*",
                        "Accept-Encoding": "gzip, deflate, br",
                    },
                )
                response.raise_for_status()
                logger.info(f"Response arrived successfully (status code: {response.status_code})")
                break

            except requests.RequestException as e:
                logger.error(f"Attempt {attempt} failed: {e}")
                status = e.response.status_code if e.response is not None else None
                body_text = e.response.text if e.response is not None else ""
                logger.error(f"Status: {status}")
                logger.error(f"Body: {body_text}")
                if attempt < retries:
                    logger.error(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.error(f"All {retries} attempts failed. Aborting request.")

                    parsed_message = None
                    if e.response is not None:
                        try:
                            parsed_message = e.response.json().get("message")
                        except Exception:
                            parsed_message = None

                    raise InfraredAPIError(
                        status_code=status,
                        server_message=parsed_message,
                    ) from e
    except Exception as e:
        logger.error(f"HTTP Error: {e}")
        raise

    try:
        parsed = response.json()
    except Exception as e:
        logger.error(f"No resposne from OGC. {e}")
        raise

    if "imageOutput" in parsed and "value" in parsed["imageOutput"]:
        logger.info("Image geotiff was received.")
        b64_value = parsed["imageOutput"]["value"]

        binary_data = decode_gzip_base64_binary(b64_value)
        logger.info("GeoTIFF decoded.")

        if not binary_data:
            logger.error("Decoding GeoTIFF failed.")
            raise
        
        plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "data")
        os.makedirs(plugin_data_dir, exist_ok=True)

        base_name = os.path.splitext(os.path.basename(geometry_path))[0]
        file_path = os.path.join(plugin_data_dir, f"{base_name}.tif")
        with open(file_path, "wb") as f:
            f.write(binary_data)
            
        logger.info(f"GeoTIFF saved to {file_path}")

        return file_path

    else:
        logger.error("GeoTIFF was not found in resposne.")
        raise

def get_windspeed_payload(wind_direction, wind_speed):

    return {
                "analysis-type": "wind-speed",
                "geometries": None,
                "wind-direction": wind_direction,
                "wind-speed": wind_speed,
            }

def get_windspeed_payload_ogc(geometry_path=None, bbox=None, crs=None, wind_direction=None, wind_speed=None):
    
    if geometry_path is not None:
        with open(geometry_path, "r", encoding="utf-8") as f:
            geometry_data = json.load(f)
        logger.info("Geometry loaded")
    else:
        geometry_data = {}

    return      {
        "inputs": {
            "bbox": bbox,
            "crs": crs,
            "geometries": geometry_data,
            "geometry-type": "dotbim",
            "reduction-factor": 1,
            "skip-compression": False,
            "wind-direction": wind_direction,
            "wind-speed": wind_speed,
        },
        "response": "document",
        "outputs": {
            "stringOutput": {
                "transmissionMode": "value"
            },
            "imageOutput": {
                "transmissionMode": "value"
            }
        }
   }
    
def get_pwc_payload_ogc(geometry_path=None, bbox=None, crs=None, subtype=None, season=None, hours=None, weather_file_name=None):
    
    if geometry_path is not None:
        with open(geometry_path, "r", encoding="utf-8") as f:
            geometry_data = json.load(f)
        logger.info("Geometry loaded")
    else:
        geometry_data = {}

    return      {
        "inputs": {
            "bbox": bbox,
            "crs": crs,
            "geometries": geometry_data,
            "geometry-type": "dotbim",
            "reduction-factor": 1,
            "skip-compression": False,
            "type": "pedestrian-wind-comfort",
            "criteria": subtype,
            "season":season,
            "hours": hours,
            "weather-file-name": weather_file_name
        },
        "response": "document",
        "outputs": {
            "stringOutput": {
                "transmissionMode": "value"
            },
            "imageOutput": {
                "transmissionMode": "value"
            }
        }
   }

def get_utci_payload(weather_data,lon,lat,time_frame):
    
    start = time_frame["startTime"]
    end = time_frame["endTime"]
    month_stamp_array = [start["month"], end["month"]]
    hour_stamp_array = [start["hour"], end["hour"]]
    
    hIRIntensity = weather_data["horizontalInfraredRadiationIntensity"]
    diffuseHR = weather_data["diffuseHorizontalRadiation"]
    directNormalRadiation = weather_data["directNormalRadiation"]
    globalHorizontalRadiation = weather_data["globalHorizontalRadiation"]
    dryBulbTemperature = weather_data["dryBulbTemperature"]
    windSpeed = weather_data["windSpeed"]
    relativeHumidity = weather_data["relativeHumidity"]
    
    return {
        "analysis-type" : "thermal-comfort-index",
        "geometries" : None,
        "latitude" : lat,
        "longitude" : lon,
        "month-stamp" : month_stamp_array,
        "hour-stamp" : hour_stamp_array,
        "horizontal-infrared-radiation-intensity" : hIRIntensity,
        "diffuse-horizontal-radiation" : diffuseHR,
        "direct-normal-radiation" : directNormalRadiation,
        "global-horizontal-radiation" : globalHorizontalRadiation,
        "dry-bulb-temperature" : dryBulbTemperature,
        "wind-speed" : windSpeed,
        "relative-humidity" : relativeHumidity
    }

def get_tcs_payload(weather_data,lon,lat,time_frame,subtype):
    start = time_frame["startTime"]
    end = time_frame["endTime"]
    month_stamp_array = [start["month"], end["month"]]
    hour_stamp_array = [start["hour"], end["hour"]]
    
    hIRIntensity = weather_data["horizontalInfraredRadiationIntensity"]
    diffuseHR = weather_data["diffuseHorizontalRadiation"]
    directNormalRadiation = weather_data["directNormalRadiation"]
    globalHorizontalRadiation = weather_data["globalHorizontalRadiation"]
    dryBulbTemperature = weather_data["dryBulbTemperature"]
    windSpeed = weather_data["windSpeed"]
    relativeHumidity = weather_data["relativeHumidity"]  
    
    return {
        "analysis-type" : "thermal-comfort-statistics",
        "geometries" : None,
        "latitude" : lat,
        "longitude" : lon,
        "month-stamp" : month_stamp_array,
        "hour-stamp" : hour_stamp_array,
        "horizontal-infrared-radiation-intensity" : hIRIntensity,
        "diffuse-horizontal-radiation" : diffuseHR,
        "direct-normal-radiation" : directNormalRadiation,
        "global-horizontal-radiation" : globalHorizontalRadiation,
        "dry-bulb-temperature" : dryBulbTemperature,
        "wind-speed" : windSpeed,
        "relative-humidity" : relativeHumidity,
        "subtype": subtype
    }

def get_solar_radiation_payload(weather_data,lon,lat,time_frame):
    start = time_frame["startTime"]
    end = time_frame["endTime"]
    month_stamp_array = [start["month"], end["month"]]
    hour_stamp_array = [start["hour"], end["hour"]]
    
    diffuseHR = weather_data["diffuseHorizontalRadiation"]
    directNormalRadiation = weather_data["directNormalRadiation"]
    
    return {
        "analysis-type" : "solar-radiation",
        "geometries" : None,
        "latitude" : lat,
        "longitude" : lon,
        "month-stamp" : month_stamp_array,
        "hour-stamp" : hour_stamp_array,
        "diffuse-horizontal-radiation" : diffuseHR,
        "direct-normal-radiation" : directNormalRadiation
    }

def get_utci_payload_ogc(geometry_path=None, bbox=None, crs=None, month=None, hours=None, weather_file_name=None):
    
    if geometry_path is not None:
        with open(geometry_path, "r", encoding="utf-8") as f:
            geometry_data = json.load(f)
        logger.info("Geometry loaded")
    else:
        geometry_data = {}

    return      {
        "inputs": {
            "bbox": bbox,
            "crs": crs,
            "geometries": geometry_data,
            "geometry-type": "dotbim",
            "reduction-factor": 1,
            "skip-compression": False,
            "type": "thermal-comfort-index",
            "month":month,
            "hours": hours,
            "weather-file-name": weather_file_name
        },
        "response": "document",
        "outputs": {
            "stringOutput": {
                "transmissionMode": "value"
            },
            "imageOutput": {
                "transmissionMode": "value"
            }
        }
   }

def get_tcs_payload_ogc(geometry_path=None, bbox=None, crs=None, season=None, hours=None, weather_file_name=None, subtype=None):
    
    if geometry_path is not None:
        with open(geometry_path, "r", encoding="utf-8") as f:
            geometry_data = json.load(f)
        logger.info("Geometry loaded")
    else:
        geometry_data = {}

    return      {
        "inputs": {
            "bbox": bbox,
            "crs": crs,
            "geometries": geometry_data,
            "geometry-type": "dotbim",
            "reduction-factor": 1,
            "skip-compression": False,
            "type": "thermal-comfort-statistics",
            "season":season,
            "hours": hours,
            "subtype": subtype,
            "weather-file-name": weather_file_name
        },
        "response": "document",
        "outputs": {
            "stringOutput": {
                "transmissionMode": "value"
            },
            "imageOutput": {
                "transmissionMode": "value"
            }
        }
   }

def get_solar_analyses_payload_ogc(geometry_path=None, bbox=None, crs=None, month=None, hours=None, weather_file_name=None, analysis_type=None):
    
    if geometry_path is not None:
        with open(geometry_path, "r", encoding="utf-8") as f:
            geometry_data = json.load(f)
        logger.info("Geometry loaded")
    else:
        geometry_data = {}

    return      {
        "inputs": {
            "bbox": bbox,
            "crs": crs,
            "geometries": geometry_data,
            "geometry-type": "dotbim",
            "reduction-factor": 1,
            "skip-compression": False,
            "type": analysis_type,
            "month":month,
            "hours": hours,
            "weather-file-name": weather_file_name
        },
        "response": "document",
        "outputs": {
            "stringOutput": {
                "transmissionMode": "value"
            },
            "imageOutput": {
                "transmissionMode": "value"
            }
        }
   }
    
def get_daylight_availability_payload(month, hourly, lon, lat):
    return get_solar_analyses_payload("daylight-availability", month, hourly, lon, lat)

def get_solar_analyses_payload(analysis_type,month, hourly, lon, lat):
    time_frame = makeTimeFrameObjWithMonth(month=month, hourly=hourly)
    
    start = time_frame["startTime"]
    end = time_frame["endTime"]
    month_stamp_array = [start["month"], end["month"]]
    hour_stamp_array = [start["hour"], end["hour"]]
    
    return {
        "analysis-type": analysis_type,
        "geometries": None,
        "latitude" : lat,
        "longitude" : lon,
        "month-stamp" : month_stamp_array,
        "hour-stamp" : hour_stamp_array,
    }

def get_pwc_payload(wind_data, criteria):
    return {
        "analysis-type": "pedestrian-wind-comfort",
        "geometries": None,
        "wind-data": wind_data,
        "criteria": criteria
    }
    


def get_direct_sun_hours_payload(month, hourly, lon, lat):
    return get_solar_analyses_payload("direct-sun-hours", month, hourly, lon, lat)
    
def get_payload_shadow_mask(geometry_path=None, bbox=None, crs=None, datetime=None, analysis_type=None):
    
    if geometry_path is not None:
        with open(geometry_path, "r", encoding="utf-8") as f:
            geometry_data = json.load(f)
        logger.info("Geometry loaded")
    else:
        geometry_data = {}

    return      {
        "inputs": {
            "bbox": bbox,
            "crs": crs,
            "geometries": geometry_data,
            "geometry-type": "dotbim",
            "reduction-factor": 1,
            "skip-compression": False,
            "type": analysis_type,
            "datetime":datetime
        },
        "response": "document",
        "outputs": {
            "stringOutput": {
                "transmissionMode": "value"
            },
            "imageOutput": {
                "transmissionMode": "value"
            }
        }
   }
    
def get_shadow_mask_payload(dt_str : str, lon : float, lat : float):
    dt_stamps = adjustDatetime(dt_str)

    return {
        "analysis-type": "shadow-mask",
        "geometries": None,
        "latitude" : lat,
        "longitude" : lon,
        "month-stamp" : dt_stamps["month-stamp"],
        "hour-stamp" : dt_stamps["hour-stamp"],
        "day-stamp" : dt_stamps["day-stamp"],
        "minute-stamp" : dt_stamps["minute-stamp"]
    }

def run_wind_speed(geojson_path, dotbim_path, bbox, crs, wind_direction, wind_speed, api_key, analysis_type):
        try:
            logger.info("Simulation started")
            payload = get_windspeed_payload_ogc(dotbim_path, bbox, crs, wind_direction, wind_speed)
            logger.info(f"Payload created.")

            geotiff_path = process_ogc(payload, dotbim_path, analysis_type, api_key)
            logger.info("Simulation finished")
            
            add_geojson_then_raster(geojson_path, geotiff_path, analysis_type=analysis_type, sub_analysis_type=None)
            logger.info("Geotiff visualized")
            return geotiff_path
        except Exception as e:
            logger.error(f"Error happened: {e}")
            return None

def run_pwc(geojson_path,dotbim_path, bbox, crs, sub_analysis_type, season, hours, weather_file_name, api_key, analysis_type):
        
        logger.info(
                f"Parameters checked for analysis: standard={getattr(sub_analysis_type,'value',sub_analysis_type)}, "
                f"season={getattr(season,'value',season)}, "
                f"hours={getattr(hours,'value',hours)}"
            )

        payload = get_pwc_payload_ogc(
            geometry_path=dotbim_path,
            bbox=bbox,
            crs=crs,
            subtype=sub_analysis_type,
            season=season,
            hours=hours,
            weather_file_name=weather_file_name)
        
        logger.info(f"Payload created.")

        try:
            logger.info("Simulation started")
            geotiff_path = process_ogc(payload, dotbim_path, analysis_type, api_key)
            logger.info("Simulation finished")
            
            add_geojson_then_raster(geojson_path, geotiff_path, analysis_type=analysis_type, sub_analysis_type=sub_analysis_type)
            logger.info("Geotiff visualized")
            return geotiff_path
        except Exception as e:
            logger.error(f"Error happened: {e}")
            return None

def run_utci(geojson_path,dotbim_path, bbox, crs, month, hours, weather_file_name, api_key, analysis_type,legend_min=None, legend_max=None):

        logger.info(
            f"Parameters checked for analysis:"
            f"month={getattr(month,'value',month)}, "
            f"hours={getattr(hours,'value',hours)}"
        )

        payload = get_utci_payload_ogc(
            geometry_path=dotbim_path,
            bbox=bbox,
            crs=crs,
            month=month,
            hours=hours,
            weather_file_name=weather_file_name)
        
        logger.info(f"Payload created")

        try:
            logger.info("Simulation started")
            geotiff_path = process_ogc(payload, dotbim_path, analysis_type, api_key)
            logger.info("Simulation finished")
            
            add_geojson_then_raster(
                geojson_path, 
                geotiff_path, 
                analysis_type=analysis_type, 
                sub_analysis_type=None,
                min_legend_value=legend_min,
                max_legend_value=legend_max
            )
            logger.info("Geotiff visualized")
            return geotiff_path
        except Exception as e:
            logger.error(f"Error happened: {e}")
            return None

def run_tcs(geojson_path, dotbim_path, bbox, crs, season, hours, weather_file_name, api_key, analysis_type, sub_analysis_type=None):
        logger.info(
            f"Parameters checked for analysis:"
            f"tcs_type={getattr(sub_analysis_type,'value',sub_analysis_type)}, "
            f"season={getattr(season,'value',season)}, "
            f"hours={getattr(hours,'value',hours)}"
        )

        payload = get_tcs_payload_ogc(
            geometry_path=dotbim_path,
            bbox=bbox,
            crs=crs,
            season=season,
            hours=hours,
            weather_file_name=weather_file_name,
            subtype=sub_analysis_type)
        
        logger.info(f"Payload created")


        try:
            logger.info("Simulation started")
            geotiff_path = process_ogc(payload, dotbim_path, analysis_type, api_key)
            logger.info("Simulation finished")
            
            add_geojson_then_raster(geojson_path, geotiff_path, analysis_type=analysis_type, sub_analysis_type=sub_analysis_type)
            logger.info("Geotiff visualized")
            return geotiff_path
        except Exception as e:
            logger.error(f"Error happened: {e}")
            return None

def run_solar_analyses(geojson_path,dotbim_path, bbox, crs, month, hours, weather_file_name, api_key, analysis_type):

        logger.info(
            f"Parameters checked for analysis:"
            f"month={getattr(month,'value',month)}, "
            f"hours={getattr(hours,'value',hours)}"
        )

        payload = get_solar_analyses_payload_ogc(
            geometry_path=dotbim_path,
            bbox=bbox,
            crs=crs,
            month=month,
            hours=hours,
            weather_file_name=weather_file_name,
            analysis_type=analysis_type)
        
        logger.info(f"Payload created")

        try:
            logger.info("Simulation started")
            geotiff_path = process_ogc(payload, dotbim_path, analysis_type, api_key)
            logger.info("Simulation finished")
            
            add_geojson_then_raster(
                geojson_path, 
                geotiff_path, 
                analysis_type=analysis_type, 
                sub_analysis_type=None,
                min_legend_value=None,
                max_legend_value=None
            )
            logger.info("Geotiff visualized")
            return geotiff_path
        except Exception as e:
            logger.error(f"Error happened: {e}")
            return None

def run_shadow_mask(geojson_path,dotbim_path, bbox, crs, datetime, api_key, analysis_type):

        logger.info(
            f"Parameters checked for analysis:"
            f"datetime={getattr(datetime,'value',datetime)}"
        )

        payload = get_payload_shadow_mask(
            geometry_path=dotbim_path,
            bbox=bbox,
            crs=crs,
            datetime=datetime,
            analysis_type=analysis_type)
        
        logger.info(f"Payload created")

        try:
            logger.info("Simulation started")
            geotiff_path = process_ogc(payload, dotbim_path, analysis_type, api_key)
            logger.info("Simulation finished")
            
            add_geojson_then_raster(geojson_path, geotiff_path, analysis_type=analysis_type, sub_analysis_type=None)
            
            logger.info("Geotiff visualized")
            return geotiff_path
        except Exception as e:
            logger.error(f"Error happened: {e}")
            return None