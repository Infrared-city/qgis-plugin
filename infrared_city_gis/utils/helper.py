import datetime
import os

from qgis.core import QgsApplication

from ..infrared_logger import logger

#: Only the plugin's own logs are pruned. NOT the `data` folder: it holds the
#: downloaded buildings (GeoJSON) and ground materials (GeoPackage) that saved
#: QGIS projects load their layers from, and a file left unmodified for a month
#: is still in use by a project nobody has opened for a month. Deleting it
#: silently destroyed the layers and the user's edits (PR #51 review).
_PRUNED_FOLDERS = ("logs",)
_MAX_AGE_DAYS = 30


def _clean_up(folder_name):
    """Delete files older than ``_MAX_AGE_DAYS`` from a plugin folder."""
    plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", folder_name)

    if not os.path.exists(plugin_data_dir):
        return

    cutoff = datetime.datetime.now() - datetime.timedelta(days=_MAX_AGE_DAYS)

    for filename in os.listdir(plugin_data_dir):
        file_path = os.path.join(plugin_data_dir, filename)
        try:
            if os.path.isfile(file_path):
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
                if mtime < cutoff:
                    os.remove(file_path)
                    logger.info("Deleted old file: %s", file_path)
        except Exception as e:
            logger.warning("Failed to remove old file %s: %s", file_path, e)


def cleanup_old_logs():
    """Delete plugin log files older than ``_MAX_AGE_DAYS`` (see ``_PRUNED_FOLDERS``)."""
    for folder in _PRUNED_FOLDERS:
        _clean_up(folder)
