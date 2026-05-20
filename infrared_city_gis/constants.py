"""
Constants for the Infrared City GIS plugin.
"""

INFRARED_API_BASE_URL = "https://api.infrared.city"
INFRARED_API_V2_URL = f"{INFRARED_API_BASE_URL}/v2"


#Run analysis:
RUN_ANALYSIS_ENDPOINT = f"{INFRARED_API_V2_URL}/run-analysis"

#Fetch
FETCH_GROUND_MATERIAL_URL = f"{INFRARED_API_V2_URL}/utils/ground-material/collect"
FETCH_WEATHER_FILES_URL = f"{INFRARED_API_V2_URL}/utils/weather/location"
FETCH_FROM_REGISTRY_URL = INFRARED_API_V2_URL


# HTTP timeouts (seconds) — passed to requests as (connect, read).
# Connect: TCP handshake / TLS negotiation budget. ~5–10 s catches dead routes
# and DNS holes quickly without false-positives on slow networks.
# Read: how long to wait for the server's response body. The simulation
# endpoint can run a few minutes for a 512×512 tile, so the read timeout is
# generous; lighter endpoints (registry, EPW lookup) get shorter reads.
RUN_ANALYSIS_HTTP_TIMEOUT = (10, 300)   # simulation can take several minutes
FETCH_HTTP_TIMEOUT = (10, 30)           # weather / OSM / EPW metadata


