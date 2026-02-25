import numpy as np
from shapely.geometry import Polygon
from typing import List, Tuple
import uuid
from pyproj import Transformer, CRS
from ..infrared_logger import logger
from ..models.analysis import GeometryTypes
from qgis.PyQt.QtCore import QSettings
from qgis.core import QgsApplication
import json
import os
from ..models.vegetation_types import TreeType, TreeSize

try:
    import mapbox_earcut as earcut
    HAS_EARCUT = True
    logger.info("mapbox_earcut found.")
except Exception:
    logger.warning("mapbox_earcut not found.")
    earcut = None
    HAS_EARCUT = False

def convert_to_local_coords(coords: List[Tuple[float, float]], center_x: float, center_y: float, crs: str) -> List[List[float]]:
    """Convert coordinates to local meters (x east, y north) relative to a center.

    - If CRS is geographic (e.g., EPSG:4326) **and** the center looks like valid lon/lat
      (|lat| <= 90, |lon| <= 180), we project to a local AEQD centered at that point.
    - Otherwise (projected CRS, or center clearly in meters), we assume inputs are already
      in meters and simply subtract the center.
    """
    local_coords: List[List[float]] = []
    crs_obj = CRS.from_user_input(crs)

    use_aeqd = crs_obj.is_geographic and abs(center_y) <= 90.0 and abs(center_x) <= 180.0

    if use_aeqd:
        # Input coords are lon/lat degrees: project to a local AEQD centered at the given point
        local_crs = CRS.from_proj4(
            f"+proj=aeqd +lat_0={center_y} +lon_0={center_x} +ellps=WGS84 +units=m +type=crs"
        )
        to_local = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True)
        for lon, lat in coords:
            x, y = to_local.transform(float(lon), float(lat))
            local_coords.append([float(x), float(y)])
    else:
        # Treat as projected coordinates in meters: subtract the center.
        for x, y in coords:
            local_coords.append([float(x) - float(center_x), float(y) - float(center_y)])

    return local_coords

def create_building_extrusion(polygon_coords_local: List[List[float]], height: float):
    """
    Given polygon coords in local meters (list of [x,y]), create vertices and triangle indices:
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
        logger.error(f"[create_building_extrusion] error: {e}")
        return None, None

def update_geometry(dotbim, shiftSize=256):
    """
    Shift all mesh coordinates into the positive quadrant and filter trivial meshes.
    - dotbim: dict of {name: {coordinates: [...], indices: [...]}}
    - shiftSize: value added to X and Y to avoid negatives (e.g., 256)
    Returns a filtered/shifted dict or None if nothing remains.
    """

    fixed_data = {}

    for key, mesh in dotbim.items():
        coords = np.array(mesh.get("coordinates", [])).reshape(-1, 3)
        indices = mesh.get("indices", [])

        if coords.size == 0 or len(indices) == 0:
            continue

        # skip if all Z == 0
        if np.max(coords[:, 2]) <= 1.0:
            continue

        # --- SHIFT ORIGIN ---
        coords[:, 0] += shiftSize
        coords[:, 1] += shiftSize


        # keep mesh
        mesh["coordinates"] = coords.flatten().tolist()
        fixed_data[key] = mesh

    # save if there is at least one valid mesh
    if fixed_data:
        return fixed_data
    else:

        return None

def get_bbox_center(bbox, crs = None):
    # bbox: [min_x, min_y, max_x, max_y]
    min_x, min_y, max_x, max_y = bbox

    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2

    if crs is not None:
        if crs == "EPSG:4326":
            return [center_x, center_y]

        transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(center_x, center_y)
        return [lon, lat]

    return [center_x, center_y]


def flatten_rings(holes: List[List[Tuple[float, float]]]) -> Tuple[List[float], List[int]]:
    """Flatten rings into a single [x,y,...] array and hole start indices for Earcut."""
    vertices: List[float] = []
    inner_hole_start_indices: List[int] = []

    for i, hole in enumerate(holes):
        for x, y in hole:
            vertices.extend([float(x), float(y)])
        if i != len(holes) - 1:
            inner_hole_start_indices.append(len(vertices) // 2)

    inner_hole_start_indices.append(len(vertices) // 2)

    return vertices, inner_hole_start_indices


def triangulate_volume(rings: List[List[Tuple[float, float]]], height: float):
    """Triangulate polygon (with optional holes) using mapbox_earcut and extrude to 3D.

    rings: [outer_ring, hole1, hole2, ...], each ring = [(x,y), ...]
    height: extrusion height
    Returns (flat_coordinates, all_faces) or (None, None) on failure.
    """
    if not HAS_EARCUT:
        return None, None

    # Flatten the input rings and identify hole start indices
    flattened_rings, hole_start_indices = flatten_rings(rings)
    verts_2d = np.array(flattened_rings, dtype=float).reshape(-1, 2)
    hole_starts = np.array(hole_start_indices, dtype=np.int32)

    if verts_2d.shape[0] < 3:
        return None, None

    # Perform triangulation on the 2D shape
    try:
        triangle_indices = earcut.triangulate_float32(verts_2d, hole_starts)
    except Exception as e:
        logger.error(f"[triangulate_volume] Earcut failed: {e}")
        return None, None

    triangle_indices = np.array(triangle_indices, dtype=np.int32).reshape(-1, 3)

    # Create bottom and top vertices (3D)
    bottom_vertices = [(float(x), float(y), 0.0) for x, y in verts_2d]
    top_vertices = [(float(x), float(y), float(height)) for x, y in verts_2d]
    all_vertices = bottom_vertices + top_vertices
    vertex_index_map = {v: i for i, v in enumerate(all_vertices)}

    # Triangulate bottom and top faces
    bottom_faces = [int(i) for tri in triangle_indices for i in tri]
    top_faces = [
        int(i) + len(verts_2d)
        for tri in triangle_indices
        for i in reversed(tri)  # Reverse order to maintain outward normal
    ]

    # Flatten all vertex coordinates for export
    flat_coordinates = [coord for vertex in all_vertices for coord in vertex]

    # Generate side wall faces (2 triangles per edge)
    side_faces: List[int] = []
    for ring in rings:
        for i in range(len(ring)):
            curr = ring[i]
            nxt = ring[(i + 1) % len(ring)]  # Wrap around

            # Indices for bottom and top vertices
            bi = vertex_index_map[(curr[0], curr[1], 0.0)]
            bip = vertex_index_map[(nxt[0], nxt[1], 0.0)]
            ti = vertex_index_map[(curr[0], curr[1], height)]
            tip = vertex_index_map[(nxt[0], nxt[1], height)]

            # First triangle
            side_faces.extend([bi, bip, tip])
            # Second triangle
            side_faces.extend([bi, tip, ti])

    all_faces = bottom_faces + top_faces + side_faces
    return flat_coordinates, all_faces

def scale_mesh(mesh_coords, height, crownDiameter):
    
    # Compute bounding box of original mesh
    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")
    for i in range(0, len(mesh_coords), 3):
        x = mesh_coords[i]
        y = mesh_coords[i + 1]
        z = mesh_coords[i + 2]
        if x < min_x:
            min_x = x
        if x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        if y > max_y:
            max_y = y
        if z < min_z:
            min_z = z
        if z > max_z:
            max_z = z

    current_height = max_z - min_z if max_z > min_z else 1.0
    current_diameter = max_x - min_x if max_x > min_x else 1.0

    # Target dimensions; if not provided, keep original size
    target_height = float(height) if height is not None else current_height
    target_diameter = float(crownDiameter) if crownDiameter is not None else current_diameter

    # Non-uniform scale: x/y by crown diameter, z by height
    sx = target_diameter / current_diameter if current_diameter != 0 else 1.0
    sz = target_height / current_height if current_height != 0 else 1.0

    # Scale around mesh center (keep tree roughly in place)
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    cz = min_z

    scaled_coords: List[float] = []
    for i in range(0, len(mesh_coords), 3):
        x = mesh_coords[i]
        y = mesh_coords[i + 1]
        z = mesh_coords[i + 2]

        sx_x = cx + (x - cx) * sx
        sx_y = cy + (y - cy) * sx
        sz_z = cz + (z - cz) * sz

        scaled_coords.extend([sx_x, sx_y, sz_z])
    
    return scaled_coords

def convert_tree_to_dotbim(geojson,center_x: float, center_y: float,crs: str):
    
                # Try to restore last saved selection from settings
    settings = QSettings()
    tree_type = settings.value("infrared_city/tree_type", None)
    tree_size = settings.value("infrared_city/tree_size", None)
    
    plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "settings")
    vegetation_file = os.path.join(plugin_data_dir, "vegetation_registry.json")

    try:
        with open(vegetation_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Could not read vegetation_registry.json: {e}")
        return None
 
    client_models = data.get("clientModels") or {}
    if not isinstance(client_models, dict):
        logger.warning("clientModels in vegetation_registry.json is not a dict")
        return None
 
    selected = None
    
    if tree_type:
        for model in client_models.values():
 
            if model.get("displayName") == tree_type:
                logger.info(f"Selected tree model: {model.get('displayName')}")
                selected = model
                break
 
    # 2) Fallback: first "tree" model
    if selected is None:
        for model in client_models.values():
            selected = model
            logger.info(f"Firsttree model was selected: {model.get('displayName')}")
            break

 
    if selected is None:
        logger.warning("No suitable tree model found in vegetation_registry.json")
        return None
 
 
    crownDiameter = selected.get("crownDiameter")
    height = selected.get("height")
    crownDiameterRange = selected.get("crownDiameterRange")
    heightRange = selected.get("heightRange")
    
    if tree_size:
        if tree_size == "small":
            height = heightRange[0]
            crownDiameter = crownDiameterRange[0]
        elif tree_size == "medium":
            height = (heightRange[0] + heightRange[1]) / 2
            crownDiameter = (crownDiameterRange[0] + crownDiameterRange[1]) / 2
        elif tree_size == "large":
            height = heightRange[1]
            crownDiameter = crownDiameterRange[1]
    
    
    
    mesh = selected.get("mesh")
    if mesh is None:
        logger.warning("Selected tree model has no 'mesh' field")
        return None

    # --- Scale mesh to requested height / crown diameter ---
    mesh_coords = mesh.get("coordinates", [])
    if not mesh_coords:
        logger.warning("Selected tree mesh has empty coordinates")
        return None

    scaled_coords = scale_mesh(mesh_coords, height, crownDiameter)

    # GeoJSON features: each feature gives a tree position
    features = geojson.get("features", [])
    dotbim_data = {}

    for idx, feat in enumerate(features):
        geom = (feat or {}).get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []

        # Expect MultiPoint: coordinates = [[lon, lat, z], ...]
        if gtype == "MultiPoint":
            if (not coords) or (not coords[0]) or len(coords[0]) < 2:
                continue
            lon = float(coords[0][0])
            lat = float(coords[0][1])
        elif gtype == "Point":
            # Optional support for Point geometries
            if len(coords) < 2:
                continue
            lon = float(coords[0])
            lat = float(coords[1])
        else:
            # Skip other geometry types
            continue

        # Convert lon/lat to local (meter) coordinates relative to center
        local_pts = convert_to_local_coords([(lon, lat)], center_x, center_y, crs)
        if not local_pts:
            continue
        dx, dy = local_pts[0]

        # Shift scaled mesh coordinates (flattened [x0, y0, z0, x1, y1, z1, ...])
        shifted_coords: List[float] = []
        for i in range(0, len(scaled_coords), 3):
            mx = scaled_coords[i]
            my = scaled_coords[i + 1]
            mz = scaled_coords[i + 2]
            shifted_coords.extend([mx + dx, my + dy, mz])

        name = str(uuid.uuid4())
        dotbim_data[name] = {
            "coordinates": shifted_coords,
            "indices": mesh.get("indices", [])
        }

    dotbim_updated = update_geometry(dotbim_data)
      
    return dotbim_updated

def process_geojson_file(geojson, center_x: float, center_y: float, crs: str):
    """
    Convert a GeoJSON FeatureCollection of building polygons to a dotbim-like mesh dict.
    - geojson: {type: "FeatureCollection", features: [...]}
    - center_lon/center_lat: reference center for local meter conversion
    - tile_id: identifier used to name meshes if no ID is present in properties
    Returns a dict {mesh_name: {mesh_id, coordinates, indices}}.
    """

    features = geojson.get("features", [])
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
        height = next(
            (props[k] for k in ("height", "building:height", "building_height") if k in props),
            None
        )
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
        local_coords = convert_to_local_coords(poly_lonlat, center_x, center_y, crs)
        # ensure open ring (no duplicate closing vertex)
        if len(local_coords) >= 2 and local_coords[0] == local_coords[-1]:
            local_coords = local_coords[:-1]

        # --- Triangulation ---
        # 1) Try high-quality Earcut-based triangulation if available
        rings = [local_coords]
        verts, inds = triangulate_volume(rings, h_val)

        # 2) Fallback to simple extrusion if Earcut is not available or failed
        if not verts or not inds:
            verts, inds = create_building_extrusion(local_coords, h_val)

        if verts and inds:
            name = str(uuid.uuid4())
            dotbim_data[name] = {
                "coordinates": verts,
                "indices": inds
            }

    dotbim_updated = update_geometry(dotbim_data)

    return dotbim_updated