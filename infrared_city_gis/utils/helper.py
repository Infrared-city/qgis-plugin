
import json
import base64
import zipfile
import io
from ..infrared_logger import logger
import gzip
import os
import datetime
from shutil import copyfile
from qgis.core import QgsApplication
    
def decode_gzip_base64_binary(b64_data: str) -> bytes:
    """Decode gzip+base64 string into raw binary bytes (handles both TIFF and gzip+base64)."""
    try:
        logger.info("Decoding gzip+base64 string...")
        decoded_bytes = base64.b64decode(b64_data)

        if decoded_bytes[:2] == b'\x1f\x8b':
            with gzip.GzipFile(fileobj=io.BytesIO(decoded_bytes)) as f:
                return f.read()
        else:
            logger.info("Decoding gzip+base64 string, not gzip, so it's a GeoTIFF or other binary data")
            return decoded_bytes

    except Exception as e:
        logger.info(f"Decoding error: {e}")
        return None

def decode(response_content):
    try:
        logger.info("Decoding response content...")
        if isinstance(response_content, bytes):
            response_content = response_content.decode()
    except UnicodeDecodeError as e:

        logger.info(f"Response content could not be decoded to UTF-8 string: {e}")

        raise

    try:
        json_data = json.loads(response_content)
    except json.JSONDecodeError as e:
        logger.info(f"Invalid JSON: {e}")
        raise

    try:
        encoded = json_data.get("result")
        if not encoded:
            logger.info(f"Missing 'result' field in response: {e}")

        # Base64 decode
        decoded = base64.b64decode(encoded)

        # Open ZIP archive
        with zipfile.ZipFile(io.BytesIO(decoded)) as zip_file:
            json_filename = "data.json"
            if json_filename not in zip_file.namelist():
                logger.info(f"Filename not found in zip archive: {json_filename}")


            with zip_file.open(json_filename) as f:
                content = f.read().decode("utf-8")
                data = json.loads(content)  
                logger.info("Decoding pipeline successful")
                return data  

    except Exception as e:
        logger.info(f"Decoding pipeline failed: {e}")
        raise

def _clean_up(folder_name):
    """Delete all files older then 30 days from directory."""
    plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", folder_name)

    if not os.path.exists(plugin_data_dir):
        return

    cutoff = datetime.datetime.now() - datetime.timedelta(days=30)

    for filename in os.listdir(plugin_data_dir):
        file_path = os.path.join(plugin_data_dir, filename)
        try:
            if os.path.isfile(file_path):
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
                if mtime < cutoff:
                    os.remove(file_path)
                    logger.info(f"Deleted old file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to remove old file {file_path}: {e}")
    

def cleanup_old_data():
    """Delete all files older than 1 month (00:00:00) from directory."""
    _clean_up("data")
    _clean_up("logs")
    


def update_vegetation_registry():
    #TODO: utility service call after it is available in production
    """Ensure vegetation_registry.json exists in the user settings dir.

    If the file is missing in
      <qgisSettingsDir>/infrared_city_gis/settings/vegetation_registry.json
    it is copied from the plugin source root
      <plugin_root>/vegetation_registry.json
    """

    settings_dir = os.path.join(
        QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "settings"
    )
    os.makedirs(settings_dir, exist_ok=True)

    dest_path = os.path.join(settings_dir, "vegetation_registry.json")
    if os.path.exists(dest_path):
        logger.info("vegetation_registry.json already present in settings dir")
        return

    # Plugin root is one level above this utils/ directory
    plugin_root = os.path.dirname(os.path.dirname(__file__))
    src_path = os.path.join(plugin_root, "vegetation_registry.json")

    if not os.path.exists(src_path):
        logger.warning("Source vegetation_registry.json not found at %s", src_path)
        return

    try:
        copyfile(src_path, dest_path)
        logger.info("Copied vegetation_registry.json from %s to %s", src_path, dest_path)
    except Exception as e:
        logger.warning("Failed to copy vegetation_registry.json: %s", e)