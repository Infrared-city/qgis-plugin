"""Fetch and load the Infrared model and vegetation registries.

Two on-disk files are the single source of truth:

  - ``<QGIS settings>/infrared_city_gis/settings/model_registry.json``
    visualConfigurations (per-analysis-type colormaps, steps, units, …).
  - ``<QGIS settings>/infrared_city_gis/settings/vegetation_registry.json``
    Tree / vegetation species metadata.

Public API:
  - ``load_registry_visual_configs()`` reads ``model_registry.json`` (with a
    small in-memory cache for visualConfigurations).
  - ``fetch_registry_visual_configs(api_key)`` GETs ``/v2/utils/registry/models``
    and overwrites ``model_registry.json``.
  - ``fetch_registry_vegetation(api_key)`` GETs ``/v2/utils/registry/vegetation``
    and overwrites ``vegetation_registry.json``.
  - ``fetch_registry_materials(api_key)`` GETs ``/v2/utils/registry/materials``
    and overwrites ``materials_registry.json`` (ground-material catalog).
  - ``fetch_from_registry(api_key)`` runs all fetches. Called on plugin init
    (when an API key is already saved) and after the user saves a new API key.
"""

import json
import os
from threading import Lock

from qgis.core import QgsApplication

from ..constants import FETCH_FROM_REGISTRY_URL
from ..infrared_logger import logger
from . import qgis_http as requests

# In-memory cache of the last known good visualConfigurations. Populated from
# disk on the first ``load_...`` call after startup, refreshed by ``fetch_...``.
_cache = {"data": None}
_cache_lock = Lock()
_REGISTRY_TIMEOUT_SEC = 10


def _settings_dir():
    """Return (and create if needed) the plugin's on-disk settings dir."""
    settings_dir = os.path.join(
        QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "settings"
    )
    os.makedirs(settings_dir, exist_ok=True)
    return settings_dir


def _model_registry_path():
    return os.path.join(_settings_dir(), "model_registry.json")


def _vegetation_registry_path():
    return os.path.join(_settings_dir(), "vegetation_registry.json")


def _materials_registry_path():
    return os.path.join(_settings_dir(), "materials_registry.json")


def _load_api_key():
    """Load the saved API key from QSettings (or env var), '' if neither.

    Centralised in :mod:`secret_manager`; this thin wrapper exists only
    to keep the existing ``_load_api_key`` callsites in this module
    untouched.
    """
    from .secret_manager import get_api_key
    return get_api_key()


def _get_json(path_suffix, api_key):
    """GET ``{FETCH_FROM_REGISTRY_URL}/{path_suffix}`` with x-api-key header.

    Returns the parsed JSON body or ``None`` on any failure.
    """
    url = f"{FETCH_FROM_REGISTRY_URL}/{path_suffix.lstrip('/')}"
    headers = {"x-api-key": api_key}
    try:
        logger.info("Fetching %s", url)
        r = requests.get(url, headers=headers, timeout=_REGISTRY_TIMEOUT_SEC)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning("Registry GET %s failed: %s", url, e)
        return None


def _write_json(path, doc):
    """Write ``doc`` to ``path`` as pretty JSON. Returns True on success."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        logger.info("Saved %s", path)
        return True
    except Exception as e:
        logger.warning("Could not persist %s: %s", path, e)
        return False


def load_registry_visual_configs():
    """Return ``visualConfigurations`` dict from disk, or ``None`` if missing/invalid.

    Uses an in-memory cache after the first successful read. Does NOT trigger a
    network fetch — callers that need fresh data should call
    ``fetch_registry_visual_configs(...)`` explicitly.
    """
    with _cache_lock:
        if _cache["data"] is not None:
            return _cache["data"]

    path = _model_registry_path()
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        visual_configs = doc.get("visualConfigurations") or None
        if visual_configs:
            with _cache_lock:
                _cache["data"] = visual_configs
            logger.info(
                "Loaded model_registry.json from disk (version=%s, %d analysis types)",
                doc.get("version"),
                len(visual_configs),
            )
        return visual_configs
    except Exception as e:
        logger.warning("Could not read model_registry.json: %s", e)
        return None


def fetch_registry_visual_configs(api_key=None):
    """Fetch ``visualConfigurations`` from ``/v2/utils/registry/models``.

    Always hits the network. Persists the full JSON response to
    ``settings/model_registry.json`` and refreshes the in-memory cache.
    If ``api_key`` is not provided, falls back to the one stored via
    :func:`services.secret_manager.get_api_key` (QSettings + env var).

    Returns:
        dict: the ``visualConfigurations`` dict on success.
        None: on any failure (network, auth, missing key, parse).
    """
    if not api_key:
        api_key = _load_api_key()
    if not api_key:
        logger.warning("fetch_registry_visual_configs: no api-key available")
        return None

    doc = _get_json("utils/registry/models", api_key)
    if doc is None:
        return None

    _write_json(_model_registry_path(), doc)

    visual_configs = doc.get("visualConfigurations") or {}
    logger.info(
        "Model registry fetched (version=%s, %d analysis types)",
        doc.get("version"),
        len(visual_configs),
    )
    with _cache_lock:
        _cache["data"] = visual_configs
    return visual_configs


def fetch_registry_vegetation(api_key=None):
    """Fetch the vegetation registry from ``/v2/utils/registry/vegetation``.

    Always hits the network. Persists the full JSON response to
    ``settings/vegetation_registry.json``. If ``api_key`` is not provided,
    falls back to the one stored via
    :func:`services.secret_manager.get_api_key` (QSettings + env var).

    Returns:
        dict: the parsed JSON document on success.
        None: on any failure (network, auth, missing key, parse).
    """
    if not api_key:
        api_key = _load_api_key()
    if not api_key:
        logger.warning("fetch_registry_vegetation: no api-key available")
        return None

    doc = _get_json("utils/registry/vegetation", api_key)
    if doc is None:
        return None

    _write_json(_vegetation_registry_path(), doc)

    logger.info("Vegetation registry fetched (version=%s)", doc.get("version"))
    return doc


def fetch_registry_materials(api_key=None):
    """Fetch the ground-material registry from ``/v2/utils/registry/materials``.

    Always hits the network. Persists the full JSON response to
    ``settings/materials_registry.json``. If ``api_key`` is not provided,
    falls back to the one stored via
    :func:`services.secret_manager.get_api_key` (QSettings + env var).

    Returns:
        dict: the parsed JSON document on success.
        None: on any failure (network, auth, missing key, parse).
    """
    if not api_key:
        api_key = _load_api_key()
    if not api_key:
        logger.warning("fetch_registry_materials: no api-key available")
        return None

    doc = _get_json("utils/registry/materials", api_key)
    if doc is None:
        return None

    _write_json(_materials_registry_path(), doc)

    logger.info("Materials registry fetched (version=%s)", doc.get("version"))
    return doc


def fetch_from_registry(api_key=None):
    """Refresh the model, vegetation, and materials registries from the API.

    Convenience wrapper used on plugin init and after the user saves an API
    key. Returns a dict with all results (any may be ``None`` if that
    particular endpoint failed).
    """
    if not api_key:
        api_key = _load_api_key()
    if not api_key:
        logger.warning("fetch_from_registry: no api-key available")
        return {"model": None, "vegetation": None, "materials": None}

    return {
        "model": fetch_registry_visual_configs(api_key=api_key),
        "vegetation": fetch_registry_vegetation(api_key=api_key),
        "materials": fetch_registry_materials(api_key=api_key),
    }
