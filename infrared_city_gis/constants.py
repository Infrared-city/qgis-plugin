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

# The cheapest authenticated GET on the API, used only to verify an API key.
# A list route rather than a dedicated one: there is no /whoami, and this
# answers 401/403 for a bad key without costing tokens or creating anything.
VERIFY_API_KEY_URL = f"{INFRARED_API_V2_URL}/webhooks"

# Model / vegetation / material registries.
#
# These are PUBLIC documents, mirrored to R2 and served without credentials —
# the plugin used to read them from the utilities service
# (GET /v2/utils/registry/*), which is being retired. Same documents, verified
# identical version-for-version; the API key is no longer sent, so a registry
# read can no longer double as an auth check (see services.key_check).
INFRARED_REGISTRY_BASE_URL = "https://registry.infrared.city"
REGISTRY_DOCUMENTS = {
    "model": f"{INFRARED_REGISTRY_BASE_URL}/models/latest.json",
    "vegetation": f"{INFRARED_REGISTRY_BASE_URL}/vegetation/latest.json",
    "materials": f"{INFRARED_REGISTRY_BASE_URL}/materials/latest.json",
}


# HTTP timeouts (seconds) — passed to requests as (connect, read).
# Connect: TCP handshake / TLS negotiation budget. ~5–10 s catches dead routes
# and DNS holes quickly without false-positives on slow networks.
# Read: how long to wait for the server's response body. Everything reached
# from here is a metadata or geometry lookup that answers in seconds; the
# long-running simulation calls go through the SDK, which sets its own.
FETCH_HTTP_TIMEOUT = (10, 30)           # weather / OSM / EPW metadata
