import logging, os
from qgis.core import QgsApplication
from datetime import datetime

def setup_logger():
    plugin_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "logs")
    os.makedirs(plugin_dir, exist_ok=True)
    log_file = os.path.join(plugin_dir, f"infrared_city_{datetime.now().strftime('%Y-%m-%d')}.log")
    logger = logging.getLogger("InfraredCity")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fh = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] [%(filename)s:%(funcName)s:%(lineno)d] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger

logger = setup_logger()