import os
import json
import math
from typing import List, Tuple
import numpy as np
from shapely.geometry import Polygon
from ..infrared_logger import logger
from pyproj import Geod
import uuid
import os
import json
from qgis.PyQt import uic
from qgis.PyQt import QtWidgets
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsRectangle,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsApplication,
    QgsUnitTypes,
)
from qgis.utils import iface
from datetime import datetime
from qgis.PyQt.QtCore import QVariant
from .geojson2dotbim import process_geojson_file
from osgeo import gdal, osr
import numpy as np




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

def map_categories(matrix: np.ndarray):
    unique_cats = np.unique(matrix)
    filtered_cats = [cat for cat in unique_cats if cat != "None"]

    mapping = {cat: i for i, cat in enumerate(filtered_cats, start=1)}
    mapping["NaN"] = np.nan

    mapped = np.full(matrix.shape, np.nan, dtype=np.float32)

    for cat, idx in mapping.items():
        mapped[matrix == cat] = idx
        
    return mapped, mapping
    
def generate_geotiff(
    matrix: np.ndarray,
    bbox: tuple,
    crs: str,
    output_path: str,
    simulation_type: str = "unknown",
    criteria: str = "unknown",
):
    west, south, east, north = bbox
    height, width = matrix.shape
    pixel_width = (east - west) / width
    pixel_height = (north - south) / height
    geotransform = (west, pixel_width, 0, north, 0, -pixel_height)
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(output_path, width, height, 1, gdal.GDT_Float32)
    if ds is None:
        raise RuntimeError(f"Failed to create GeoTIFF at {output_path}")
    ds.SetGeoTransform(geotransform)
    srs = osr.SpatialReference()
    srs.SetFromUserInput(crs)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    if simulation_type == "pedestrian-wind-comfort":
        # index mapping
        mapped_matrix, mapping_dict = map_categories(matrix)
        band.WriteArray(mapped_matrix.astype(np.float32))
        band.SetDescription(simulation_type)
        band.SetNoDataValue(np.nan)
        # metadata
        md = {
            "simulation_type": simulation_type,
            "category_mapping": str(mapping_dict),
            "criteria": criteria,
            "no_data": str(np.nan),
            "AREA_OR_POINT": "Point",
        }
        ds.SetMetadata(md)
    else:
        band.WriteArray(matrix.astype(np.float32))
        band.SetDescription(simulation_type)
        band.SetNoDataValue(np.nan)
        md = {
            "simulation_type": simulation_type,
            "no_data": str(np.nan),
            "AREA_OR_POINT": "Point",
        }
        ds.SetMetadata(md)
    band.FlushCache()
    ds = None

def collect_geometry_data_by_tile(center_x, center_y, idx):
    date_now = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")

    layer = iface.activeLayer()
    if layer is None or not isinstance(layer, QgsVectorLayer):
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer):
                layer = lyr
                break

    if layer is None or not isinstance(layer, QgsVectorLayer):
        logger.error("No valid vector layer found.")
        return None

    layer_crs = layer.crs()

    try:
        transform_to_wgs84 = QgsCoordinateTransform(layer_crs, wgs84, QgsProject.instance())
        pt_wgs84 = transform_to_wgs84.transform(QgsPointXY(center_x, center_y))
    except Exception as e:
        logger.error("Transform tile center to WGS84 failed: %s", e)
        return None

    center_lon, center_lat = pt_wgs84.x(), pt_wgs84.y()
    logger.info("Tile center (WGS84): %.6f, %.6f", center_lon, center_lat)

    try:
        xmin, ymin, xmax, ymax = get_bbox(center_lon, center_lat, 512)
        bbox_rect_wgs84 = QgsRectangle(xmin, ymin, xmax, ymax)
    except Exception as e:
        logger.error("get_bbox failed: %s", e)
        return None

    if layer_crs.authid() != "EPSG:4326":
        transform_bbox = QgsCoordinateTransform(wgs84, layer_crs, QgsProject.instance())
        bbox_rect_layer = transform_bbox.transformBoundingBox(bbox_rect_wgs84)
    else:
        bbox_rect_layer = bbox_rect_wgs84

    try:
        layer.removeSelection()
        layer.selectByRect(bbox_rect_layer, QgsVectorLayer.SetSelection)
        count = layer.selectedFeatureCount()
        logger.info("Tile %d: selected %d features.", idx, count)
    except Exception as e:
        logger.error("Selection failed for tile %d: %s", idx, e)
        return None

    selected_features = layer.selectedFeatures()
    if not selected_features:
        logger.info("Tile %d: no features selected, skipping.", idx)
        return None
    
    logger.info("Tile %d: processing %d selected features.", idx, len(selected_features))
    
    geojson_dict = {
        "type": "FeatureCollection",
        "features": []
    }

    fields = [field.name() for field in layer.fields()]

    for feat in selected_features:
        geom = feat.geometry()
        geom_wgs84 = QgsGeometry(geom)
        if layer_crs.authid() != "EPSG:4326":
            transform_to_wgs84_back = QgsCoordinateTransform(layer_crs, wgs84, QgsProject.instance())
            geom_wgs84.transform(transform_to_wgs84_back)

        attr_values = feat.attributes()
        properties_dict = {fields[i]: attr_values[i] for i in range(len(fields))}

        geom_bbox = geom.boundingBox()
        height = geom_bbox.height()

        if layer_crs.mapUnits() == QgsUnitTypes.DistanceDegrees:
            height *= 111_000

        properties_dict["height"] = round(height, 2)

        geojson_dict["features"].append({
            "type": "Feature",
            "geometry": json.loads(geom_wgs84.asJson()),
            "properties": properties_dict
        })

    plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "data")
    os.makedirs(plugin_data_dir, exist_ok=True)
    geojson_path = os.path.join(plugin_data_dir, f"infrared_city_buildings_{date_now}_tile_{idx}.geojson")

    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(geojson_dict, f, ensure_ascii=False, indent=2)

    dotbim_data = process_geojson_file(geojson_dict, center_lon, center_lat, "EPSG:4326")
    dotbim_path = os.path.join(plugin_data_dir, f"infrared_city_buildings_{date_now}_tile_{idx}.bim")

    with open(dotbim_path, "w", encoding="utf-8") as f:
        json.dump(dotbim_data, f, ensure_ascii=False, indent=2)

    logger.info("Tile %d saved to %s and %s", idx, geojson_path, dotbim_path)

    # 512x512 bbox in layer CRS (for OGC API)
    bbox_512 = (
        bbox_rect_layer.xMinimum(),
        bbox_rect_layer.yMinimum(),
        bbox_rect_layer.xMaximum(),
        bbox_rect_layer.yMaximum(),
    )

    # 256x256 bbox in the same CRS, centered on the tile center
    display_tile_size = 256.0
    half_disp = display_tile_size / 2.0
    bbox_256 = (
        center_x - half_disp,
        center_y - half_disp,
        center_x + half_disp,
        center_y + half_disp,
    )

    crs_authid = layer_crs.authid()

    logger.info(f"Tile {idx}: CRS={crs_authid}, bbox_512={bbox_512}, bbox_256={bbox_256}")

    return geojson_path, dotbim_path, bbox_512, crs_authid, bbox_256


def get_selected_crs():
    layer = iface.activeLayer()
    if layer is None:
        
        logger.warning("No active layer found")
        return None 
    layer_crs = layer.crs()
    logger.info(f"Selected crs: {layer_crs.authid()}")
    return layer_crs.authid()

def get_selected_bbox():

    layer = iface.activeLayer()
    if layer is None:
        
        logger.warning("No active layer found")
        return None

    selected = layer.selectedFeatures()
    if not selected:
        logger.warning("No selected features found in the active layer")
        return None

    # Get bbox from first selected feature
    geom = selected[0].geometry()
    if geom is None or geom.isEmpty():
        logger.warning("First selected geometry is empty")
        return None
    bbox = geom.boundingBox()

    # If multiple features are selected, combine their bboxes
    for feat in selected[1:]:
        g = feat.geometry()
        if g is None or g.isEmpty():
            logger.debug("Skipping empty geometry in bbox combination")
            continue
        bbox.combineExtentWith(g.boundingBox())

    west  = bbox.xMinimum()
    south = bbox.yMinimum()
    east  = bbox.xMaximum()
    north = bbox.yMaximum()
    
    logger.info(f"Selected bbox: {west}, {south}, {east}, {north}")

    return west, south, east, north

def generate_tile_centers(west, south, east, north, tile_size=256):

    width = east - west
    height = north - south

    if width <= 0 or height <= 0:
        logger.error(f"Wrong bbox: width={width}, height={height} for ({west}, {south}, {east}, {north})")
        return []

    # How many tiles in X/Y directions, rounded up
    nx = math.ceil(width / tile_size)
    ny = math.ceil(height / tile_size)

    centers = []

    for j in range(ny):          # Y direction (row index)
        cy = south + (j + 0.5) * tile_size
        for i in range(nx):      # X direction (column index)
            cx = west + (i + 0.5) * tile_size
            centers.append((cx, cy))

    return centers

def collect_tile_centers_from_selection():

    # Bbox in active layer CRS based on selected features
    w, s, e, n = get_selected_bbox()
    logger.info(f"Selected bbox: W={w}, S={s}, E={e}, N={n}")

    # Generate tile centers in layer CRS
    tile_centers = generate_tile_centers(w, s, e, n)
    logger.info("✨ Generated %d tile centers", len(tile_centers))
    logger.info("✨ Tile centers: %s", tile_centers)

    return tile_centers

def get_center_lon_lat_from_bbox(bbox, crs_authid: str):
    """Return bbox center as lon/lat in EPSG:4326.

    `bbox` is (west, south, east, north) in the layer CRS given by `crs_authid`.
    If the CRS is not EPSG:4326, the bbox is transformed to WGS84 first using
    QgsCoordinateTransform, then the center is computed.
    """
    w, s, e, n = bbox
    layer_rect = QgsRectangle(w, s, e, n)

    if crs_authid and crs_authid != "EPSG:4326":
        layer_crs = QgsCoordinateReferenceSystem(crs_authid)
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(layer_crs, wgs84, QgsProject.instance())
        bbox_rect_wgs84 = transform.transformBoundingBox(layer_rect)
    else:
        bbox_rect_wgs84 = layer_rect

    center_lon = (bbox_rect_wgs84.xMinimum() + bbox_rect_wgs84.xMaximum()) / 2
    center_lat = (bbox_rect_wgs84.yMinimum() + bbox_rect_wgs84.yMaximum()) / 2
    return center_lon, center_lat
