"""
Constants for the Infrared City GIS plugin.
"""

INFRARED_API_BASE_URL = "https://api.infrared.city"
INFRARED_API_V2_URL = f"{INFRARED_API_BASE_URL}/v2"


# Fetch
FETCH_GROUND_MATERIAL_URL = f"{INFRARED_API_V2_URL}/utils/ground-material/collect"
FETCH_WEATHER_FILES_URL = f"{INFRARED_API_V2_URL}/utils/weather/location"
# Building geometry (core-geometries-service, Mapbox-backed). NOTE: NOT under
# /utils — it is mounted directly at /v2/buildings (same endpoint the SDK's
# client.buildings.get_area uses).
FETCH_BUILDINGS_URL = f"{INFRARED_API_V2_URL}/buildings"
FETCH_FROM_REGISTRY_URL = INFRARED_API_V2_URL


# HTTP timeouts (seconds) — passed to requests as (connect, read).
# Connect: TCP handshake / TLS negotiation budget. ~5–10 s catches dead routes
# and DNS holes quickly without false-positives on slow networks.
# Read: how long to wait for the server's response body. Everything reached
# from here is a metadata or geometry lookup that answers in seconds; the
# long-running simulation calls go through the SDK, which sets its own.
FETCH_HTTP_TIMEOUT = (10, 30)           # weather / OSM / EPW metadata
