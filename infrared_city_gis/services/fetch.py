import json
import os
import time
from datetime import datetime

from qgis.core import QgsApplication

from ..constants import FETCH_GROUND_MATERIAL_URL, FETCH_WEATHER_FILES_URL
from ..exceptions import InfraredAPIError
from ..infrared_logger import logger
from . import qgis_http as requests
from .feature_height import DEFAULT_FLOOR_HEIGHT_M, OSM_BUILDING_HEIGHT_HINTS
from .geojson2dotbim import process_geojson_file
from .geometry import get_bbox


def extract_height_from_tags(tags: dict) -> float:
    """Extract building height in metres from a raw OSM tag dict.

    Mirrors the tier priority of ``feature_height.resolve_feature_height_with_source``
    but operates on plain string tag dicts (Overpass API responses) rather than
    QgsFeature objects. Unit strings like "10 m" and "33 ft" are parsed and
    converted. Shared constants (floor height, building-type hints) are imported
    from ``feature_height`` so both code paths stay in sync.
    """
    for tag in ("height", "building:height"):
        raw = tags.get(tag)
        if not raw:
            continue
        try:
            s = str(raw).strip()
            if "ft" in s or "'" in s:
                height = float(s.replace("ft", "").replace("'", "").strip()) * 0.3048
            else:
                height = float(s.replace("m", "").strip())
            if height > 0:
                return max(height, 0.5)
        except (ValueError, TypeError):
            continue

    raw_levels = tags.get("building:levels")
    if raw_levels:
        try:
            levels = float(raw_levels)
            if levels > 0:
                return max(levels * DEFAULT_FLOOR_HEIGHT_M, 1.0)
        except (ValueError, TypeError):
            pass

    building_type = str(tags.get("building", "")).strip().lower()
    return OSM_BUILDING_HEIGHT_HINTS.get(building_type, DEFAULT_FLOOR_HEIGHT_M)

def fetch_ground_materials(lon: float, lat: float, distance: float, api_key: str):
    base_url = FETCH_GROUND_MATERIAL_URL
    params = {
        "latitude": lat,
        "longitude": lon,
        "distance": distance,
    }

    logger.info(
        f"Fetching ground materials from {base_url} with params={params} "
        f"and api-key provided={bool(api_key)}"
    )

    headers = {"x-api-key": api_key} if api_key else {}

    try:
        response = requests.get(base_url, params=params, headers=headers, timeout=20)
        logger.info(f"Weather API status: {response.status_code}")
        logger.info(f"Weather API response text: {response.text}")

        # Raise if non-2xx
        response.raise_for_status()


        try:
            data = response.json()
            # --- Save response to settings/ground_materials.json ---
            dir = os.path.join(
                QgsApplication.qgisSettingsDirPath(),
                "infrared_city_gis",
                "data",
            )
            os.makedirs(dir, exist_ok=True)
            date_now = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
            ground_file = os.path.join(dir, f"ground_materials_{date_now}.json")

            try:
                with open(ground_file, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
                logger.info(f"Ground materials saved to {ground_file}")
            except Exception as write_err:
                logger.warning(
                    f"Failed to save ground materials JSON to {ground_file}: {write_err}"
                )

            return data

        except ValueError as e:
            logger.warning("Weather API response is not valid JSON ")
            raise e

    except requests.RequestException as e:
        logger.error(f"Weather API request failed: {e}")
        status = e.response.status_code if e.response is not None else None
        parsed_message = None
        if e.response is not None:
            try:
                parsed_message = e.response.json().get("message")
            except Exception:
                pass
        raise InfraredAPIError(status_code=status, server_message=parsed_message) from e


def fetch_weather_file_names(lon: float, lat: float, radius: float, api_key: str):
    """Call Infrared.city weather location endpoint and return response.

    Logs request URL, params, status code and body. Returns parsed JSON if
    possible. Raises RequestException on network errors
    and HTTPError on non-2xx status codes.
    """
    base_url = FETCH_WEATHER_FILES_URL
    params = {
        "latitude": lat,
        "longitude": lon,
        "radius": radius,
    }

    headers = {"x-api-key": api_key} if api_key else {}

    logger.info(
        f"Fetching weather file names from {base_url} with params={params} "
        f"and api-key provided={bool(api_key)}"
    )

    try:
        response = requests.get(base_url, params=params, headers=headers, timeout=20)
        logger.info(f"Weather API status: {response.status_code}")
        logger.info(f"Weather API response text: {response.text}")

        # Raise if non-2xx
        response.raise_for_status()

        try:
            data = response.json()
            logger.info("Weather API JSON parsed successfully")

            # Extract locations list
            locations = data.get("data", {}).get("locations", [])
            logger.info(f"Weather API returned {len(locations)} locations")

            # Collect only fileName values into a simple list
            file_names = [loc.get("fileName") for loc in locations if isinstance(loc, dict) and loc.get("fileName")]
            logger.info(f"Collected {len(file_names)} fileName entries from locations")
            return file_names

        except ValueError as e:
            logger.warning("Weather API response is not valid JSON, returning raw text")
            raise e

    except requests.RequestException as e:
        logger.error(f"Weather API request failed: {e}")
        status = e.response.status_code if e.response is not None else None
        parsed_message = None
        if e.response is not None:
            try:
                parsed_message = e.response.json().get("message")
            except Exception:
                pass
        raise InfraredAPIError(status_code=status, server_message=parsed_message) from e


def fetch_geometry_from_osm(lon: float, lat: float, bbox_size_m: float, retries: int = 3, delay: int = 3,tile_id: int = 0) -> str:
        logger.info(f"Fetching geometry with lon: {lon}, lat: {lat}, bbox_size_m: {bbox_size_m}")

        overpass_url = "https://overpass-api.de/api/interpreter"

        plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "data")
        os.makedirs(plugin_data_dir, exist_ok=True)

        date_now = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')

        geojson_path = os.path.join(plugin_data_dir,f"infrared_city_buildings_{date_now}.geojson")
        dotbim_path = os.path.join(plugin_data_dir,f"infrared_city_buildings_{date_now}.bim")


        bbox = get_bbox(lon, lat, bbox_size_m)
        logger.info(f"BBox: {bbox}")

        bbox_request = {
            "south": bbox[1],
            "west": bbox[0],
            "north": bbox[3],
            "east": bbox[2],
        }

        logger.info(f"BBox : {bbox_request}")

        query = f"""
        [out:json];
        (
            way["building"]({bbox_request['south']},{bbox_request['west']},{bbox_request['north']},{bbox_request['east']});
        );
        out geom;
        """

        # --- Overpass API query ---
        data = None
        for i in range(retries):
            try:
                logger.info(f"Overpass query attempt {i + 1}/{retries}...")
                response = requests.post(overpass_url, data={'data': query}, timeout=20)

                if response.status_code != 200 or not response.text.strip():
                    logger.info(f"HTTP {response.status_code}, retrying...")
                    time.sleep(delay)
                    continue

                try:
                    logger.info("Response received, parsing JSON...")
                    data = response.json()
                    break
                except ValueError:
                    logger.info("Invalid JSON response, retrying...")
                    time.sleep(delay)

            except requests.exceptions.Timeout:
                logger.error(f"Timeout, retrying in {delay}s...")
                time.sleep(delay)
            except requests.exceptions.RequestException as e:
                logger.info(f"Request error: {e}")
                status = e.response.status_code if e.response is not None else None
                body_text = e.response.text if e.response is not None else ""
                logger.error(f"Status: {status}")
                logger.error(f"Body: {body_text}")
                time.sleep(delay)

        if not data:
            logger.error("Failed to fetch valid data from Overpass API.")
            return None, None, None

        logger.info(f"Fetched {len(data.get('elements', []))} elements")

        # --- GeoJSON creation ---
        features = []

        elements = data.get("elements", [])

        if not elements:
            logger.warning("No elements found in response please try with different lon, lat values.")
            return None, None, None

        for elem in elements:
            if elem.get("type") == "way" and "geometry" in elem:
                coords = [(p["lon"], p["lat"]) for p in elem["geometry"]]
                # Close the polygon if not already closed
                if coords[0] != coords[-1]:
                    coords.append(coords[0])

                height = extract_height_from_tags(elem.get('tags', {}))

                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [coords]
                    },
                    "properties": {
                        "id": elem.get("id"),
                        "building_height": height,
                        **(elem.get("tags", {}) or {})
                    }
                }
                features.append(feature)

        geojson = {
            "type": "FeatureCollection",
            "features": features
        }

        logger.info("GeoJSON created")

        # Save to disk
        with open(geojson_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, indent=2)

        logger.info(f"GeoJSON saved to {geojson_path}")

        logger.info("Converting GeoJSON to DotBIM...")
        try:
            dotbim_data = process_geojson_file(geojson, lon, lat, "EPSG:4326")
        except Exception as e:
            logger.error(f"Failed to convert GeoJSON to DotBIM: {e}")
            return None, None, None

        logger.info("DotBIM created")

        with open(dotbim_path, "w", encoding="utf-8") as f:
            json.dump(dotbim_data, f, ensure_ascii=False, indent=2)

        logger.info(f"DotBIM saved to {dotbim_path}")

        return geojson_path, dotbim_path, bbox
