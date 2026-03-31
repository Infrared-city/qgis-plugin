import requests
import os
import base64
import logging

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock constants (replace with actual values)
INFRARED_URL = "https://example.com/api"
PAYLOAD_PATH = "/path/to/payload.json"

def post_payload_with_detailed_error_handling():
    try:
        # Read and encode payload
        with open(PAYLOAD_PATH, "rb") as f:
            payload_data = f.read()
        
        base64_encoded = base64.b64encode(payload_data)
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer your_token_here"
        }
        
        print(f"Posting {os.path.basename(PAYLOAD_PATH)} ...")
        response = requests.post(INFRARED_URL, data=base64_encoded, headers=headers)
        response.raise_for_status()
        print(f"Status: {response.status_code}")
        
    except requests.RequestException as e:
        # Detailed error handling
        status = e.response.status_code if e.response is not None else None
        body_text = e.response.text if e.response is not None else ""
        
        logger.error(f"Request failed with status: {status}")
        logger.error(f"Response body: {body_text}")
        
        if e.response is not None:
            logger.error(f"Full response object: {e.response}")
            logger.error(f"Response headers: {dict(e.response.headers)}")
            
            # Try to parse JSON response
            try:
                body_json = e.response.json()
                logger.error(f"Parsed JSON response: {body_json}")
                
                # Extract specific message if available
                parsed_message = body_json.get("message")
                if parsed_message:
                    logger.error(f"Server message: {parsed_message}")
                    
                # Extract additional error details if available
                if "error" in body_json:
                    logger.error(f"Error details: {body_json['error']}")
                    
            except ValueError as json_error:
                logger.error(f"Failed to parse JSON response: {json_error}")
                logger.error(f"Raw response text: {body_text}")
        
        # Log the original exception
        logger.error(f"Original exception: {e}")
        logger.error(f"Exception type: {type(e).__name__}")
        
        # Re-raise or handle as needed
        raise

if __name__ == "__main__":
    post_payload_with_detailed_error_handling()
