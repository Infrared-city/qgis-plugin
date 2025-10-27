import json
import gzip
import base64
import requests
import numpy as np
from .utils.helper import decode
from enum import Enum
import os
from qgis.core import QgsApplication
import time
from .infrared_logger import logger
import numpy as np
from .services.geometry import generate_geotiff
    
API_KEY = "Rs1MoXnOUn4PPPQ3LT7JZ8LN5J5SvV615rik1JgI"

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

    



def process_run_analysis(dotbim_path,wind_direction, wind_speed, bbox):
    INFRARED_URL = "https://fbiw2nq5ac.execute-api.eu-central-1.amazonaws.com/development-v1"

    with open(dotbim_path, "r", encoding="utf-8") as f:
        geometry_data = json.load(f)
    logger.info("Geometry loaded")

    payload = get_windspeed_payload(geometry_data, wind_direction, wind_speed,bbox)
    logger.info("Payload created")

    
    logger.info("Processing run analysis endpoint...")

    json_data = json.dumps(payload)
    compressed_data = gzip.compress(json_data.encode(HEADERS.UTF_8.value))
    base64_encoded = base64.b64encode(compressed_data).decode(HEADERS.UTF_8.value)

    try:
        headers = {
            HEADERS.X_API_KEY.value.lower(): API_KEY,
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
                    raise  
        
        logger.info(f"Response received from client, status code: {response.status_code}")

        decoded_result = decode(response.content)

        matrix = np.array(decoded_result, dtype=np.float32)

        plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "data")
        os.makedirs(plugin_data_dir, exist_ok=True)

        file_path = os.path.join(plugin_data_dir, "infrared_result.tif")


        bbox_tuple = (bbox[0], bbox[1], bbox[2], bbox[3])
        logger.info(f"Bbox: {bbox}")
        logger.info(f"Bbox tuple: {bbox_tuple}")
        matrix = np.flipud(matrix)

        generate_geotiff(matrix, bbox_tuple, "EPSG:4326",file_path, simulation_type="wind-speed")
        
        
        logger.info(f"Matrix shape: {matrix.shape}")

        return file_path

    except Exception as e:
        logger.error(f"Error processing tile : {e}")
        raise


def process_ogc(geometry_path,wind_direction, wind_speed, bbox):
    logger.info("Processing OGC request...")
    INFRARED_URL = "https://khg02ntlki.execute-api.eu-central-1.amazonaws.com/"

    with open(geometry_path, "r", encoding="utf-8") as f:
        geometry_data = json.load(f)
    logger.info(f"Geometry loaded from : {geometry_path}")

    

    payload_data = get_windspeed_payload_ogc(geometry_data,wind_direction, wind_speed, bbox)
    logger.info("Payload created")

    try:
        logger.info("Try processing OGC request...")
        retries = 3
        delay = 5  # másodperc

        for attempt in range(1, retries + 1):
            try:
                response = requests.post(
                    f"{INFRARED_URL}/processes/wind-speed/execution",
                    json=payload_data,
                    headers={
                        "X-Api-Key": API_KEY,
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
                logger.info(f"Attempt {attempt} failed: {e}")
                if attempt < retries:
                    logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.error(f"All {retries} attempts failed. Aborting request.")
                    raise 
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

        file_path = os.path.join(plugin_data_dir, "infrared_result.tif")
        with open(file_path, "wb") as f:
            f.write(binary_data)
            
        logger.info(f"GeoTIFF saved to {file_path}")

        return file_path

    else:
        logger.error("GeoTIFF was not found in resposne.")
        raise

def get_windspeed_payload(geometry, wind_direction, wind_speed,bbox):
    return {
                "analysis-type": "wind-speed",
                "geometries": geometry,
                "wind-direction": wind_direction,
                "wind-speed": wind_speed,
            }
    
def get_windspeed_payload_ogc(geometry, wind_direction, wind_speed, bbox):
    return      {
        "inputs": {
            "bbox": bbox,
            "crs": "EPSG:4326",
            "type": "wind-speed",
            "geometries": geometry,
            "geometry-type": "dotbim",
            "reduction-factor": 1,
            "skip-compression": True,
            "wind-direction": wind_direction,
            "wind-speed": wind_speed,
        },
        "response": "document"
    }