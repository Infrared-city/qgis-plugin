"""Headers that identify this plugin to the Infrared API.

Infrared's analytics cannot attribute a call without them: the gateway's
``detectClient()`` knows a fixed set of surface names and otherwise guesses from
the auth method, so an API-key call from QGIS was indistinguishable from a
generic script. Two headers fix that, and every Infrared client sends the same
pair (see Infrared-city/qgis-plugin#43)::

    x-infrared-application: qgis
    x-infrared-sdk:         qgis-plugin/<version>

**Scope, so the numbers are read correctly.** These cover the calls the plugin
makes itself through ``services.qgis_http`` — the registry fetches at startup,
the weather-file lookup, and the building-geometry fetch. They do NOT cover
anything routed through ``infrared-sdk``, which hardcodes
``x-infrared-application: "sdk"`` in each of its service clients with no way to
override it. That is where the simulations run, so ``client = 'qgis'`` currently
counts plugin *sessions and fetches*, not analyses. Attributing those needs an
SDK change; until then, do not read the figure as a run count.
"""

from __future__ import annotations

import configparser
import os
from typing import Dict

#: Surface name for this client, from the agreed vocabulary
#: (qgis | arcgis | sketchup | platform | webapp | script | …).
APPLICATION = "qgis"

#: Name half of ``x-infrared-sdk``; the version is appended at call time.
CLIENT_NAME = "qgis-plugin"

_UNKNOWN_VERSION = "unknown"
_version_cache: str = ""


def plugin_version() -> str:
    """Return the shipped plugin version, read from ``metadata.txt``.

    ``metadata.txt`` is the only version source inside the package — the repo
    also carries ``version.txt`` and ``.release-please-manifest.json``, but the
    release ZIP contains just ``infrared_city_gis/``, so neither is present at
    runtime. Release Please bumps ``metadata.txt`` on every release, which keeps
    this in step without a second place to remember.

    Never raises: an unreadable metadata file must not break an API call, so a
    missing version degrades to ``"unknown"`` rather than propagating.
    """
    global _version_cache
    if _version_cache:
        return _version_cache

    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "metadata.txt")
    version = _UNKNOWN_VERSION
    try:
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        version = parser.get("general", "version", fallback=_UNKNOWN_VERSION).strip()
    except (configparser.Error, OSError):
        version = _UNKNOWN_VERSION

    _version_cache = version or _UNKNOWN_VERSION
    return _version_cache


def client_headers() -> Dict[str, str]:
    """The identifying headers to merge into every outgoing plugin request."""
    return {
        "x-infrared-application": APPLICATION,
        "x-infrared-sdk": f"{CLIENT_NAME}/{plugin_version()}",
    }
