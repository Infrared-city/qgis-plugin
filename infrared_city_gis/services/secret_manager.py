"""Centralised API-key storage for the Infrared City plugin.

The API key is an opaque token (revocable from the Infrared City admin
UI), not a user password — so we don't need a master-password unlock
ceremony or OS-keychain prompt every time. Storage lives in QGIS's
QSettings, which is platform-native (Windows registry / macOS plist /
Linux ini), accessible only from the user's own login session, and
shared by the plugin's dialogs without any extra plumbing.

Read precedence:

1. ``INFRARED_API_KEY`` environment variable — escape hatch for CI /
   agent scenarios and for users who don't want any persistent state in
   QGIS. Wins over QSettings if both are present.
2. ``QSettings("infrared_city/api_key")`` — the durable store written
   by the "Save API Key" dialog.

Logging contract: ``get_api_key`` and ``set_api_key`` log only metadata
(presence / source / "saved") — never the literal value. Callers should
treat the return value as a secret and avoid printing it.
"""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import QSettings

from ..infrared_logger import logger

# QSettings key name. Single string keeps spelling consistent across
# every read/write site; keep "infrared_city/" namespace so it sits next
# to the existing tree_type / tree_size keys.
_QS_KEY = "infrared_city/api_key"

# Env var fallback — also what infrared_sdk's InfraredClient honours, so
# advanced users can run the plugin entirely from environment.
_ENV_VAR = "INFRARED_API_KEY"


def get_api_key() -> str:
    """Return the saved API key, or an empty string if none is configured.

    Reads in precedence order: env var → QSettings. Never raises; never
    logs the secret value itself, only the source it came from.
    """
    env_value = os.getenv(_ENV_VAR, "")
    if env_value:
        logger.debug("API key resolved from environment variable")
        return env_value.strip()

    raw = QSettings().value(_QS_KEY, "")
    if isinstance(raw, str) and raw.strip():
        logger.debug("API key resolved from QSettings")
        return raw.strip()
    return ""


def set_api_key(api_key: str) -> bool:
    """Persist the API key to QSettings.

    The env var path is read-only by design — we only ever write to
    QSettings. ``True`` on success, ``False`` if the input is empty (the
    caller's UI should prevent this, but the guard keeps the helper
    safe to call defensively).
    """
    if not api_key or not api_key.strip():
        logger.warning("set_api_key: refusing to save an empty value")
        return False
    QSettings().setValue(_QS_KEY, api_key.strip())
    logger.info("API key saved to QSettings")
    return True


def clear_api_key() -> None:
    """Remove the saved API key from QSettings.

    Useful for "Sign out" flows. The env var (if set) is unaffected.
    """
    settings = QSettings()
    if settings.contains(_QS_KEY):
        settings.remove(_QS_KEY)
        logger.info("API key cleared from QSettings")


def has_api_key() -> bool:
    """Convenience check — same source precedence as :func:`get_api_key`,
    but returns a boolean without copying the secret string out."""
    return bool(get_api_key())
