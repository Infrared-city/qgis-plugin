import logging
import os
from datetime import datetime

import structlog
from qgis.core import QgsApplication


def setup_logger() -> structlog.stdlib.BoundLogger:
    plugin_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "logs")
    os.makedirs(plugin_dir, exist_ok=True)
    log_file = os.path.join(plugin_dir, f"infrared_city_{datetime.now().strftime('%Y-%m-%d')}.log")

    # Attach handler only to the InfraredCity logger — not the root logger
    stdlib_logger = logging.getLogger("InfraredCity")
    stdlib_logger.setLevel(logging.DEBUG)
    stdlib_logger.propagate = False  # prevent bubbling up to root logger

    if not stdlib_logger.handlers:
        fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(message)s"))
        stdlib_logger.addHandler(fh)

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.ExceptionRenderer(),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    return structlog.get_logger("InfraredCity")


logger = setup_logger()
