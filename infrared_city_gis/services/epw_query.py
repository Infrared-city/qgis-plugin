
from enum import Enum
from ..exceptions import InfraredAPIError

import requests

class Query_Type (str,Enum):
    UTCI = "utci"
    WIND = "wind-seasonal"

def make_uri (fileName: str):
    return f"https://app.infrared.city/api/public/weatherfiles/{fileName}/data"

def query_infrared_epw (file_name: str, type: Query_Type, time_frame: dict, api_key: str):
    """
    Query ewp files from infrared-city data-base

    Returns
    -------
    dict
        epw-data object
    """
    try: 
        filters = {
            "type": type.value,
            "filter": {
                "timeFrame": time_frame
            }
        }

        uri = make_uri(file_name)
        response = requests.post(
            uri, 
            headers={"X-Api-Key": api_key},
            json=filters
        )
        if response.status_code != 200:
            raise Exception(f"Error getting epw data for {file_name} {response.text}")

        data = response.json()
    except requests.RequestException as e:
        logger.error(f"Epw query request failed: {e}")
        status = e.response.status_code if e.response is not None else None
        parsed_message = None
        if e.response is not None:
            try:
                parsed_message = e.response.json().get("message")
            except Exception:
                pass
        raise InfraredAPIError(status_code=status, server_message=parsed_message) from e


    return  data
