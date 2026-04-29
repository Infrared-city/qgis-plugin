"""
Constants for the Infrared City GIS plugin.
"""

INFRARED_API_BASE_URL = "https://fbiw2nq5ac.execute-api.eu-central-1.amazonaws.com"
INFRARED_API_PROD_URL = f"{INFRARED_API_BASE_URL}/prod"
INFRARED_API_V1_URL = f"{INFRARED_API_BASE_URL}/v1"
INFRARED_API_V2_URL = f"{INFRARED_API_BASE_URL}/v2"


#Run analysis:
RUN_ANALYSIS_ENDPOINT = f"{INFRARED_API_V2_URL}/api/run-analysis"

#Fetch
FETCH_GROUND_MATERIAL_URL = f"{INFRARED_API_V2_URL}/utils/ground-material/collect"
FETCH_WEATHER_FILES_URL = f"{INFRARED_API_V2_URL}/utils/weather/location"
FETCH_FROM_REGISTRY_URL = INFRARED_API_V2_URL


