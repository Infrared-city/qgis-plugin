import numpy as np
from shapely.geometry import Polygon
from typing import List, Tuple
import uuid
from pyproj import Transformer, CRS
import mapbox_earcut as earcut
from ..infrared_logger import logger

def convert_to_local_coords(coords: List[Tuple[float, float]], center_x: float, center_y: float, crs: str) -> List[List[float]]:
    """
    Convert coordinates to local meters (x east, y north) relative to center (center_lon, center_lat).
    - If CRS is geographic (e.g., EPSG:4326): inputs are lon/lat degrees; we project to a local AEQD centered at (center_lon, center_lat).
    - If CRS is projected (e.g., EPSG:25832): inputs are meters; we subtract the values from the center.
    Returns list of [x, y] pairs.
    """
    local_coords: List[List[float]] = []
    crs_obj = CRS.from_user_input(crs)

    if crs_obj.is_geographic:
        # Input coords are lon/lat: project to a local AEQD centered at the given point
        local_crs = CRS.from_proj4(
            f"+proj=aeqd +lat_0={center_y} +lon_0={center_x} +datum=WGS84 +units=m +no_defs"
        )
        to_local = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True)
        for lon, lat in coords:
            x, y = to_local.transform(float(lon), float(lat))
            local_coords.append([float(x), float(y)])
    else:
        # Input coords are already in a projected CRS (e.g., EPSG:25832) in meters.
        # We subtract the center to get local meters.
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


def flatten_rings(holes: List[List[Tuple[float, float]]]) -> Tuple[
  List[float], List[int]]:
  vertices: List[float] = []

  inner_hole_start_indices = []
  for i, hole in enumerate(holes):
    for x, y in hole:
      vertices.extend([float(x), float(y)])
    if i != len(holes) - 1:
      inner_hole_start_indices.append(len(vertices) // 2)

  inner_hole_start_indices.append(len(vertices) // 2)

  return vertices, inner_hole_start_indices

def triangulate_volume(rings: List[List[Tuple[float, float]]], height: float):
  # Flatten the input rings and identify hole start indices
  flattened_rings, hole_start_indices = flatten_rings(rings)
  verts_2d = np.array(flattened_rings).reshape(-1, 2)
  hole_starts = np.array(hole_start_indices)

  # Perform triangulation on the 2D shape
  triangle_indices = earcut.triangulate_float32(verts_2d, hole_starts)
  triangle_indices = np.array(triangle_indices).reshape(-1, 3)

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
  side_faces = []
  for ring in rings:
    for i in range(len(ring)):
      curr = ring[i]
      next = ring[(i + 1) % len(ring)]  # Wrap around

      # Indices for bottom and top vertices
      bi = vertex_index_map[(curr[0], curr[1], 0.0)]
      bip = vertex_index_map[(next[0], next[1], 0.0)]
      ti = vertex_index_map[(curr[0], curr[1], height)]
      tip = vertex_index_map[(next[0], next[1], height)]

      # First triangle
      side_faces.extend([bi, bip, tip])
      # Second triangle
      side_faces.extend([bi, tip, ti])

  all_faces = bottom_faces + top_faces + side_faces
  return flat_coordinates, all_faces

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


        # create extrusion mesh (in local meters)
        rings = [local_coords]
        verts, inds = triangulate_volume(rings, h_val)
        if verts and inds:
            name = str(uuid.uuid4())
            dotbim_data[name] = {
                "coordinates": verts,
                "indices": inds
            }

    dotbim_updated = update_geometry(dotbim_data)

    return dotbim_updated