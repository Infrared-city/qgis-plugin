
import base64
import datetime
import gzip
import io
import json
import os
import zipfile

from qgis.core import QgsApplication

from ..infrared_logger import logger


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
            logger.info("Missing 'result' field in response")

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

