
import datetime
import os

from qgis.core import QgsApplication

from ..infrared_logger import logger


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
