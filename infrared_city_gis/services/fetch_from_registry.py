"""Fetch and load the Infrared City model, vegetation and material registries.

The three documents are PUBLIC — they are mirrored to ``registry.infrared.city``
and served without credentials. The plugin used to read them from the utilities
service (``GET /v2/utils/registry/*``, API key attached), which is being
retired; the mirror carries the same documents, verified identical
version-for-version.

Two consequences worth knowing:

* **No API key is sent any more**, so these reads no longer double as an auth
  check. That job moved to :mod:`services.key_check`.
* **They work before a key is saved**, so colormaps and the tree catalog are
  populated on first launch rather than on the first key save.

Three on-disk files under ``<QGIS settings>/infrared_city_gis/settings/`` remain
the single source of truth for the rest of the plugin:

  - ``model_registry.json``      visualConfigurations (colormaps, steps, units).
  - ``vegetation_registry.json`` tree / vegetation species metadata.
  - ``materials_registry.json``  the ground-material catalog.

Public API:
  - ``load_registry_visual_configs()`` reads ``model_registry.json`` (with a
    small in-memory cache for visualConfigurations).
  - ``fetch_registry_visual_configs()`` GETs the models mirror and overwrites
    ``model_registry.json``.
  - ``fetch_registry_vegetation()`` GETs the vegetation mirror and overwrites
    ``vegetation_registry.json``.
  - ``fetch_registry_materials()`` GETs the materials mirror and overwrites
    ``materials_registry.json``.
  - ``fetch_from_registry()`` runs all three. Called once, at plugin start
    (the documents are versioned server-side, so a refresh is how a new
    release's colormaps reach an installed plugin). Saving an API key does not
    refresh them: the mirror is public and needs no key.
"""

import json
import os
from threading import Lock

from qgis.core import QgsApplication

from ..constants import REGISTRY_DOCUMENTS
from ..infrared_logger import logger
from ..utils.client_identity import client_headers
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


def _get_json(url):
    """GET a public registry document. Returns the parsed JSON, or ``None``.

    Every failure is transient by definition here: the plugin keeps working
    from the on-disk copies, and there is no auth outcome to distinguish any
    more — the mirror takes no credentials, so it cannot reject a key.

    The identity headers still ride along. They carry no secret and the mirror
    logs them, which is how registry traffic from QGIS is told apart from a
    browser or another host.
    """
    headers = client_headers()
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


def fetch_registry_visual_configs():
    """Fetch ``visualConfigurations`` from the public models mirror.

    Always hits the network. Persists the full JSON response to
    ``settings/model_registry.json`` and refreshes the in-memory cache.

    Returns:
        dict: the ``visualConfigurations`` dict on success.
        None: on any failure (network, parse).
    """
    doc = _get_json(REGISTRY_DOCUMENTS["model"])
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


def fetch_registry_vegetation():
    """Fetch the vegetation registry from the public mirror.

    Always hits the network. Persists the full JSON response to
    ``settings/vegetation_registry.json``.

    Returns:
        dict: the parsed JSON document on success.
        None: on any failure (network, parse).
    """
    doc = _get_json(REGISTRY_DOCUMENTS["vegetation"])
    if doc is None:
        return None

    _write_json(_vegetation_registry_path(), doc)

    logger.info("Vegetation registry fetched (version=%s)", doc.get("version"))
    return doc


def fetch_registry_materials():
    """Fetch the ground-material registry from the public mirror.

    Always hits the network. Persists the full JSON response to
    ``settings/materials_registry.json``.

    Returns:
        dict: the parsed JSON document on success.
        None: on any failure (network, parse).
    """
    doc = _get_json(REGISTRY_DOCUMENTS["materials"])
    if doc is None:
        return None

    _write_json(_materials_registry_path(), doc)

    logger.info("Materials registry fetched (version=%s)", doc.get("version"))
    return doc


def fetch_from_registry():
    """Refresh the model, vegetation and materials registries from the mirror.

    Convenience wrapper used on plugin init and after the user saves an API
    key. Returns a dict with all results (any may be ``None`` if that
    particular document failed).

    Takes no API key: the mirror is public. A caller that wants to know whether
    a key is good wants :func:`services.key_check.verify_api_key` instead.
    """
    return {
        "model": fetch_registry_visual_configs(),
        "vegetation": fetch_registry_vegetation(),
        "materials": fetch_registry_materials(),
    }
