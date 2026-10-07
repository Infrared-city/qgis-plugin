"""
Constants for the Infrared City GIS plugin.
"""

INFRARED_API_BASE_URL = "https://api.infrared.city"
INFRARED_API_V2_URL = f"{INFRARED_API_BASE_URL}/v2"

# Where user-facing error messages send people. The same address as
# metadata.txt, so the plugin page and the error dialogs agree.
SUPPORT_EMAIL = "connectors@infrared.city"


# Fetch. Nothing here may point at /v2/utils — the utilities service is being
# retired (#47); weather stations come from the SDK's static catalog instead.
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

# Overture ground-material reads (SDK ground_materials.get_area), in seconds.
# Without these the SDK's 60 s per-read default applies, which a single tile
# already reaches on a good line (39-64 s measured) and a slow office line
# misses by far (154-161 s, #47).
# Manual fetch: the Ground Materials dialog reads on a worker thread, so QGIS
# stays usable and the budget can be generous.
GROUND_FETCH_TIMEOUT_S = 300            # per read
GROUND_FETCH_TOTAL_TIMEOUT_S = 600      # whole site (large areas read in chunks)
# Auto-fetch at submit ("Use Infrared City ground materials"): runs on the main
# thread, so this is also the longest QGIS can stop responding. On timeout the
# run continues without ground materials and the user is told.
GROUND_AUTO_FETCH_TIMEOUT_S = 120       # per read AND whole site
