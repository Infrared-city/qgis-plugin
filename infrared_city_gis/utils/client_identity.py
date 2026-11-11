"""Headers that identify this plugin to the Infrared City API.

Infrared City's analytics cannot attribute a call without them: the gateway's
``detectClient()`` knows a fixed set of surface names and otherwise guesses from
the auth method, so an API-key call from QGIS was indistinguishable from a
generic script. Two headers fix that, and every Infrared City client sends the same
pair (see Infrared-city/qgis-plugin#43)::

    x-infrared-application: qgis
    x-infrared-sdk:         qgis-plugin/<version>

**Both halves of the plugin's traffic are covered.**

* The calls the plugin makes itself through ``services.qgis_http`` — the
  registry reads, the weather-file lookup, the building-geometry fetch — merge
  :func:`client_headers` into their own header block.
* The calls routed through ``infrared-sdk`` — every simulation, the
  ground-material reads, the EPW query — go through :func:`make_client`, which
  passes the same two values to ``InfraredClient``. The SDK appends its own
  token to the second one, so the wire carries
  ``qgis-plugin/<version> infrared-sdk/<sdk version>``: the surface is
  attributed without losing which SDK version ran.

The SDK arguments arrived in 0.9.4 (infrared-sdk#195); before that each service
client hardcoded ``x-infrared-application: "sdk"`` with no way to override it,
which is why ``client = 'qgis'`` used to count plugin sessions and fetches but
not analyses. ``requirements.txt`` pins the floor that makes them available.
"""

from __future__ import annotations

import configparser
import os
from typing import Any, Dict

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


def sdk_id() -> str:
    """The ``x-infrared-sdk`` value this plugin contributes.

    The SDK chains its own ``infrared-sdk/<version>`` token onto whatever it is
    given, so this is the leading token only — never the full header value.
    """
    return f"{CLIENT_NAME}/{plugin_version()}"


def client_headers() -> Dict[str, str]:
    """The identifying headers to merge into every outgoing plugin request."""
    return {
        "x-infrared-application": APPLICATION,
        "x-infrared-sdk": sdk_id(),
    }


def make_client(api_key: str, **kwargs: Any):
    """Build an ``InfraredClient`` that identifies this plugin to the API.

    The one construction point for the SDK client, so a new call site cannot
    forget the attribution the way a hand-rolled ``InfraredClient(api_key=...)``
    silently did. ``kwargs`` is passed straight through for the callers that
    need to tune the run (``max_workers`` and friends).

    Imported lazily: ``infrared-sdk`` is installed at runtime by
    ``utils.deps_bootstrap``, so a module-level import here would run before the
    bootstrap on a cold profile.
    """
    from infrared_sdk import InfraredClient

    return InfraredClient(
        api_key=api_key,
        application=APPLICATION,
        sdk_id=sdk_id(),
        **kwargs,
    )
