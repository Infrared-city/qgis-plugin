import json
import math
import os
from datetime import datetime

from qgis.core import QgsApplication

from ..constants import (
    FETCH_BUILDINGS_URL,
    FETCH_GROUND_MATERIAL_URL,
    FETCH_HTTP_TIMEOUT,
    FETCH_WEATHER_FILES_URL,
)
from ..exceptions import InfraredAPIError
from ..infrared_logger import logger
from ..utils.client_identity import client_headers
from . import qgis_http as requests
from .geometry import get_bbox

# Tile size (m) for the buildings fetch fallback. Matches the single-tile
# selection size and the SDK's per-tile request size, so a 1024 m area splits
# cleanly into a 2x2 grid.
_TILE_SIZE_M = 512.0


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

    headers = {**client_headers(), **({"x-api-key": api_key} if api_key else {})}

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

    headers = {**client_headers(), **({"x-api-key": api_key} if api_key else {})}

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


def fetch_geometry_from_infrared(lon: float, lat: float, size_m: float, api_key: str):
    """Fetch building footprints around (lon, lat) from infrared.city as GeoJSON.

    Replaces the old OSM/Overpass fetch. Calls ``POST /v2/buildings``
    (core-geometries-service, Mapbox-backed) with ``outputFormat=GeoJson`` over a
    ``size_m`` x ``size_m`` area centred on the point and writes the resulting
    FeatureCollection to the plugin data dir.

    Returns ``(geojson_path, bbox, error)``:
      * success       -> ``(path, bbox, None)``
      * empty area    -> ``(None, None, None)``    request(s) OK, just no buildings
      * hard failure  -> ``(None, None, reason)``  transport/API/all-tiles-failed

    Strategy: try ONE request for the whole area first. The tiled fallback runs
    ONLY on an actual request failure (the server can reject on size, or a dense
    area can exceed the gateway response limit) — a successful-but-empty response
    means the area genuinely has no buildings and is NOT an error.

    Requires a subscription API key. ``bbox`` is ``[west, south, east, north]``
    in WGS84, matching ``get_bbox``.
    """
    logger.info(
        "Fetching geometry from infrared.city: lon=%s lat=%s size_m=%s", lon, lat, size_m
    )

    if not api_key:
        logger.error("No API key configured — cannot fetch geometry from infrared.city")
        return None, None, "no API key configured"

    bbox = get_bbox(lon, lat, size_m)

    # 1. Single request for the whole area (happy path).
    #    None = request FAILED; [] = succeeded but no buildings; [...] = buildings.
    features = _fetch_buildings_request(lat, lon, size_m, size_m, api_key)

    # 2. Fallback ONLY on an actual failure (size limit / transport / API error),
    #    never on a successful-but-empty response.
    if features is None:
        logger.warning(
            "Single-request buildings fetch FAILED; falling back to %dm tiling "
            "for the %dx%d m area.", int(_TILE_SIZE_M), int(size_m), int(size_m),
        )
        tiled, failed, total = _fetch_buildings_tiled(bbox, size_m, api_key)
        if not tiled and failed == total:
            logger.error(
                "Buildings fetch FAILED for lon=%s lat=%s: single request and all "
                "%d fallback tiles failed.", lon, lat, total,
            )
            return None, None, "the buildings request failed (all fallback tiles failed)"
        features = tiled

    # 3. Request(s) succeeded but the area has no buildings — not an error.
    if not features:
        logger.info(
            "No buildings in the %dx%d m area at lon=%s lat=%s.",
            int(size_m), int(size_m), lon, lat,
        )
        return None, None, None

    plugin_data_dir = os.path.join(
        QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "data"
    )
    os.makedirs(plugin_data_dir, exist_ok=True)
    date_now = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    geojson_path = os.path.join(
        plugin_data_dir, f"infrared_city_buildings_{date_now}.geojson"
    )

    geojson = {"type": "FeatureCollection", "features": features}
    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    logger.info("Saved %d building features to %s", len(features), geojson_path)

    return geojson_path, bbox, None


def _fetch_buildings_request(lat, lon, size_x, size_y, api_key):
    """One ``POST /v2/buildings`` GeoJson request.

    Returns the list of GeoJSON Features on success (possibly empty), or
    ``None`` on an HTTP/parse/API error so the caller can distinguish "no
    buildings here" from "the request failed".
    """
    payload = {
        "coordinates": {"latitude": lat, "longitude": lon},
        "size": {"x": size_x, "y": size_y},
        "outputFormat": "GeoJson",
        "returnBuildingIds": False,
        "compress": False,
    }
    headers = {**client_headers(), "x-api-key": api_key,
               "Content-Type": "application/json"}

    logger.info("POST %s (size=%sx%s m)", FETCH_BUILDINGS_URL, size_x, size_y)
    try:
        response = requests.post(
            FETCH_BUILDINGS_URL, json=payload, headers=headers, timeout=FETCH_HTTP_TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as e:
        status = e.response.status_code if e.response is not None else None
        body_text = e.response.text if e.response is not None else ""
        logger.error("Buildings request failed (status=%s): %s", status, body_text or e)
        return None

    try:
        body = response.json()
    except ValueError:
        logger.error("Buildings response was not valid JSON")
        return None

    if isinstance(body, dict) and body.get("success") is False:
        logger.error("Buildings API returned an error: %s", body.get("error"))
        return None

    return _buildings_to_features(body)


def _fetch_buildings_tiled(bbox, size_m, api_key):
    """Fallback: split the area into ``_TILE_SIZE_M`` tiles, fetch each, merge.

    Splits the ``bbox`` into an ``n x n`` grid (``n = ceil(size_m/_TILE_SIZE_M)``)
    and requests one ``_TILE_SIZE_M`` tile per cell centre. Per-tile failures are
    logged but don't abort the others — partial coverage beats nothing. Buildings
    that straddle a tile boundary are de-duplicated.

    Returns ``(features, failed, total)`` so the caller can tell "every tile
    request failed" (hard failure) from "tiles succeeded but the area is empty".
    """
    west, south, east, north = bbox[0], bbox[1], bbox[2], bbox[3]
    n_side = max(1, math.ceil(size_m / _TILE_SIZE_M))
    dlon = (east - west) / n_side
    dlat = (north - south) / n_side

    all_features: list = []
    failed = 0
    total = n_side * n_side
    for i in range(n_side):
        for j in range(n_side):
            tile_lon = west + (i + 0.5) * dlon
            tile_lat = south + (j + 0.5) * dlat
            feats = _fetch_buildings_request(
                tile_lat, tile_lon, _TILE_SIZE_M, _TILE_SIZE_M, api_key
            )
            if feats is None:
                failed += 1
                logger.warning(
                    "Tiled fallback: tile (%d,%d) at lon=%.6f lat=%.6f FAILED",
                    i, j, tile_lon, tile_lat,
                )
                continue
            all_features.extend(feats)

    if failed:
        logger.error("Tiled fallback: %d/%d tiles failed", failed, total)
    else:
        logger.info("Tiled fallback: all %d tiles fetched", total)

    return _dedup_features(all_features), failed, total


def _dedup_features(features: list) -> list:
    """Drop duplicate building features (adjacent tiles overlap at boundaries).

    Keys on the feature ``id`` (or ``properties.id``) when present, else on the
    serialised geometry as a stable fallback.
    """
    seen: set = set()
    unique: list = []
    for feat in features:
        if not isinstance(feat, dict):
            continue
        fid = feat.get("id")
        if fid is None and isinstance(feat.get("properties"), dict):
            fid = feat["properties"].get("id")
        key = str(fid) if fid is not None else json.dumps(
            feat.get("geometry"), sort_keys=True
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(feat)
    return unique


def _buildings_to_features(body) -> list:
    """Flatten the /v2/buildings GeoJson payload into a list of GeoJSON Features.

    The endpoint returns ``data.buildings`` as a mapping ``{key: [Feature, ...]}``
    (one entry per building / group). We defensively also accept a plain
    FeatureCollection or a bare list so a future response-shape tweak doesn't
    silently drop everything.
    """
    data = body.get("data", body) if isinstance(body, dict) else body
    buildings = data.get("buildings", data) if isinstance(data, dict) else data

    features: list = []

    def _add(obj):
        if isinstance(obj, dict) and obj.get("type") == "Feature":
            features.append(obj)
        elif isinstance(obj, dict) and obj.get("type") == "FeatureCollection":
            features.extend(obj.get("features", []))
        elif isinstance(obj, list):
            for item in obj:
                _add(item)

    if isinstance(buildings, dict) and buildings.get("type") in ("Feature", "FeatureCollection"):
        _add(buildings)
    elif isinstance(buildings, dict):
        for value in buildings.values():
            _add(value)
    else:
        _add(buildings)

    return features
