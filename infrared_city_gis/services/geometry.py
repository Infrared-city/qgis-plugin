import os
import json
import math
from typing import List, Tuple
import numpy as np
from shapely.geometry import Polygon
from ..infrared_logger import logger
from pyproj import Geod
import uuid


def convert_to_local_coords(coords: List[Tuple[float, float]], center_lat: float, center_lon: float) -> List[List[float]]:
    """
    Convert lon/lat list -> local meters (x east, y north) relative to center (center_lon, center_lat).
    Uses spherical approx: 1 deg lat ~111320 m, lon scaled by cos(lat).
    Returns list of [x, y] pairs.
    """
    local_coords = []
    k_lat = 111320.0
    k_lon = 111320.0 * math.cos(math.radians(center_lat))
    for lon, lat in coords:
        x = (lon - center_lon) * k_lon
        y = (lat - center_lat) * k_lat
        local_coords.append([float(x), float(y)])
    return local_coords

def create_building_extrusion(polygon_coords_local: List[List[float]], height: float):
    """
    Given polygon coords in local meters (list of [x,y]), create vertices and triangle indices:
    returns (vertices_list, indices_list) or (None, None) on failure.
    vertices_list is flat [x,y,z, x,y,z, ...] bottom then top.
    """
    if len(polygon_coords_local) < 3:
        return None, None
    try:
        poly = Polygon(polygon_coords_local)
        if not poly.is_valid:
            poly = poly.buffer(0)
            if not poly.is_valid:
                return None, None
        coords = list(poly.exterior.coords[:-1])  # drop closing duplicate
        n = len(coords)
        if n < 3:
            return None, None

        vertices = []
        # bottom vertices (z=0)
        for x, y in coords:
            vertices.extend([float(x), float(y), 0.0])
        # top vertices (z=height)
        for x, y in coords:
            vertices.extend([float(x), float(y), float(height)])

        indices = []
        # bottom face (fan)
        for i in range(1, n - 1):
            indices.extend([0, i, i + 1])
        # top face (fan reversed winding)
        for i in range(1, n - 1):
            indices.extend([n, n + i + 1, n + i])
        # sides (two triangles per edge)
        for i in range(n):
            ni = (i + 1) % n
            indices.extend([i, ni, i + n])
            indices.extend([ni, ni + n, i + n])

        # basic sanity
        if len(vertices) == 0 or len(indices) == 0:
            return None, None

        return vertices, indices
    except Exception as e:
        print(f"[create_building_extrusion] error: {e}")
        return None, None

def update_dotbim(data,bbox_size_meters=512):
    shiftSize = bbox_size_meters / 2
    logger.info("Updating geometry...")

    fixed_data = {}

    for key, mesh in data.items():
        coords = np.array(mesh.get("coordinates", [])).reshape(-1, 3)
        indices = mesh.get("indices", [])

        if coords.size == 0 or len(indices) == 0:
            continue

        #  skip if all Z <=1
        if np.max(coords[:, 2]) <= 1.0:
            continue


        # X,Y shift
        coords[:, 0] += shiftSize
        coords[:, 1] += shiftSize

        mesh["coordinates"] = coords.flatten().tolist()
        fixed_data[key] = mesh

    # if there are valid buildings left, save them
    return fixed_data

def get_bbox(center_lon, center_lat, box_size_meters=512):
    """Return bounding box [minLon, minLat, maxLon, maxLat] around center point."""
    logger.info("Getting bounding box...")
    
    half = box_size_meters / 2  # meters
    geod = Geod(ellps="WGS84")
    
    # North
    _, lat_n, _ = geod.fwd(center_lon, center_lat, 0, half)
    # South
    _, lat_s, _ = geod.fwd(center_lon, center_lat, 180, half)
    # East
    lon_e, _, _ = geod.fwd(center_lon, center_lat, 90, half)
    # West
    lon_w, _, _ = geod.fwd(center_lon, center_lat, 270, half)  # use 270 instead of -90
    
    return [lon_w, lat_s, lon_e, lat_n]


def sanitize_name(s: str) -> str:
    return ''.join(c if (c.isalnum() or c in "-_") else "-" for c in s)

def geojson_to_dotbim(in_path: str, center_lon: float, center_lat: float, bbox_size_meters=512):
    """
    Read GeoJSON (features polygons) and produce dotbim-like JSON.
    Returns (buildings_2d_list, dotbim_dict)
    """
    with open(in_path, "r", encoding="utf-8") as f:
        gj = json.load(f)

    features = gj.get("features", [])
    buildings_2d = []
    dotbim_data = {}

    for idx, feat in enumerate(features):
        geom = feat.get("geometry")
        props = feat.get("properties", {}) or {}
        if geom is None:
            continue
        gtype = geom.get("type")
        coords = geom.get("coordinates")

        # handle Polygon or MultiPolygon (take first polygon of MultiPolygon)
        poly_coords_ll = None
        if gtype == "Polygon":
            # coordinates: [ [ [lon,lat], ... ] , ... ]
            if len(coords) > 0 and len(coords[0]) >= 3:
                poly_coords_ll = coords[0]
        elif gtype == "MultiPolygon":
            if len(coords) > 0 and len(coords[0]) > 0 and len(coords[0][0]) >= 3:
                poly_coords_ll = coords[0][0]
        else:
            # skip points/lines etc.
            continue

        if not poly_coords_ll:
            continue

        # ensure polygon is closed and valid list of lon,lat
        if poly_coords_ll[0] != poly_coords_ll[-1]:
            poly_coords_ll = poly_coords_ll + [poly_coords_ll[0]]

        # read height from properties; fallback to 3.0
        height = props.get("height", props.get("building:height", None))
        try:
            if height is None:
                h_val = 3.0
            else:
                # try numeric parsing (strip units)
                if isinstance(height, (int, float)):
                    h_val = float(height)
                else:
                    hs = str(height)
                    if "m" in hs:
                        h_val = float(hs.replace("m","").strip())
                    elif "ft" in hs or "'" in hs:
                        h_f = float(hs.replace("ft","").replace("'","").strip())
                        h_val = h_f * 0.3048
                    else:
                        h_val = float(hs)
        except Exception:
            h_val = 3.0

        # convert to local meters relative to center
        # poly_coords_ll is list of [lon,lat]
        poly_lonlat = [(float(p[0]), float(p[1])) for p in poly_coords_ll]
        local_coords = convert_to_local_coords(poly_lonlat, center_lat, center_lon)

        # store 2D for plotting
        buildings_2d.append({
            "coords": local_coords,
            "height": float(h_val),
            "source_idx": idx
        })

        # create extrusion mesh (in local meters)
        verts, inds = create_building_extrusion(local_coords, h_val)
        if verts and inds:
            # name = props.get("id") or props.get("osm_id")
            # ame = sanitize_name(str(name))
            # if name == "" or name is None:
            name = str(uuid.uuid4())

            dotbim_data[name] = {
                "mesh_id": len(dotbim_data),
                "coordinates": verts,
                "indices": inds
            }

    sifted_dotbim_data = update_dotbim(dotbim_data, bbox_size_meters)
    logger.info("DotBIM updated")

    return sifted_dotbim_data

def crop_matrix(matrix: np.ndarray, core_size=256):
    h, w = matrix.shape
    start_row = (h - core_size) // 2
    start_col = (w - core_size) // 2
    return matrix[start_row:start_row + core_size, start_col:start_col + core_size]


def generate_geotiff(matrix: np.ndarray, bbox: tuple, crs: str, output_path: str, simulation_type: str = "unknown", criteria: str = "unknown"):
    try:
        import rasterio
        from rasterio.transform import from_bounds
    except Exception as e:
        raise ImportError("rasterio is required to write GeoTIFF. Please ensure dependencies are installed and restart QGIS.") from e

    height, width = matrix.shape
    west, south, east, north = bbox
    logger.info(f"Height: {height}, Width: {width}")
    logger.info(f"West: {west}, South: {south}, East: {east}, North: {north}")
    transform = from_bounds(west, south, east, north, width, height)     

    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,  # 1 band
        dtype=np.float32,
        crs=crs,
        transform=transform,
        nodata=np.nan,
        compress="LZW",    
        tiled=True,              
        blockxsize=width,          
        blockysize=height
    ) as dst:
        if simulation_type == "pedestrian-wind-comfort":
            mapped_matrix, mapping_dict = map_categories(matrix)  
            dst.write(mapped_matrix[np.newaxis, :, :])  
            dst.update_tags(
                simulation_type=simulation_type,
                category_mapping=str(mapping_dict),
                criteria=criteria,
                no_data=np.nan,
                AREA_OR_POINT="Point"
        )
        else:
            dst.write(matrix[np.newaxis, :, :])  
            dst.update_tags(
                simulation_type=simulation_type,
                no_data=np.nan,
                AREA_OR_POINT="Point")  