import os
import time
import requests
import json
from qgis.core import QgsApplication
from ..infrared_logger import logger
from .geometry import geojson_to_dotbim, get_bbox
from datetime import datetime

def extract_height_from_tags(tags):
    """Extract building height from OSM tags."""
    default_height = 3.0
    
    # Try different height tags
    height_tags = ['height', 'building:height']
    
    for tag in height_tags:
        if tag in tags:
            height_str = tags[tag]
            try:
                if 'm' in height_str:
                    height = float(height_str.replace('m', '').strip())
                elif 'ft' in height_str or "'" in height_str:
                    height_ft = float(height_str.replace('ft', '').replace("'", '').strip())
                    height = height_ft * 0.3048
                else:
                    height = float(height_str)
                
                return max(height, 0.5)
            except (ValueError, TypeError):
                continue
    
    # Try building levels
    if 'building:levels' in tags:
        try:
            levels = float(tags['building:levels'])
            return max(levels * 3.0, 1.0)
        except (ValueError, TypeError):
            pass
    
    # Building type estimates
    building_type = tags.get('building', '')
    height_estimates = {
        'house': 6.0, 'residential': 9.0, 'apartments': 15.0,
        'commercial': 4.0, 'retail': 4.0, 'office': 12.0,
        'industrial': 8.0, 'warehouse': 10.0, 'garage': 3.0,
        'shed': 3.0, 'roof': 1.0
    }
    
    return height_estimates.get(building_type, default_height)



def fetch_geometry_from_osm(lon: float, lat: float, bbox_size_m: float, retries: int = 3, delay: int = 3,tile_id: int = 0) -> str:
        logger.info("Fetching geometry with lon: {lon}, lat: {lat}, bbox_size_m: {bbox_size_m}")

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
                logger.info(f"Timeout, retrying in {delay}s...")
                time.sleep(delay)
            except requests.exceptions.RequestException as e:
                logger.info(f"Request error: {e}")
                time.sleep(delay)

        if not data:
            raise RuntimeError("Failed to fetch valid data from Overpass API.")

        logger.info(f"Fetched {len(data.get('elements', []))} elements")

        # --- GeoJSON creation ---
        features = []

        for elem in data.get("elements", []):
            if elem.get("type") == "way" and "geometry" in elem:
                coords = [(p["lon"], p["lat"]) for p in elem["geometry"]]
                # Zárjuk a polygont, ha nincs zárva
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

        # ---- Mentés ----
        with open(geojson_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, indent=2)

        logger.info(f"GeoJSON saved to {geojson_path}")

        logger.info("Converting GeoJSON to DotBIM...")
        dotbim_data = geojson_to_dotbim(geojson_path, lon, lat, bbox_size_m)
        logger.info("DotBIM created")

        with open(dotbim_path, "w", encoding="utf-8") as f:
            json.dump(dotbim_data, f, ensure_ascii=False, indent=2)

        logger.info(f"DotBIM saved to {dotbim_path}")

        return geojson_path, dotbim_path, bbox