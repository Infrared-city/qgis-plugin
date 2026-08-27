"""The plugin identifies itself to the API on every call it makes itself.

Without these headers the gateway cannot attribute a request to QGIS and falls
back to guessing from the auth method, which lands plugin traffic in the same
bucket as any other API-key script (Infrared-city/qgis-plugin#43).

The header helper alone is not worth a test — what breaks in practice is a new
request site that forgets to merge it, or an old one that gets rewritten. So the
tests below call the real fetch functions with the transport swapped out and
assert on what was actually handed to it.
"""

import configparser
from pathlib import Path

import pytest

from infrared_city_gis.services import fetch, fetch_from_registry
from infrared_city_gis.utils.client_identity import (
    APPLICATION,
    CLIENT_NAME,
    client_headers,
    plugin_version,
)

PLUGIN_ROOT = Path(__file__).parent.parent


class _Response:
    """Minimal stand-in for a qgis_http response."""

    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        return str(self._payload)

    def raise_for_status(self):
        return None


class _Recorder:
    """Captures the headers each call was made with."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def _record(self, *args, **kwargs):
        self.calls.append(kwargs.get("headers") or {})
        return _Response(self._payload)

    get = _record
    post = _record


def test_version_comes_from_the_shipped_metadata():
    """metadata.txt is the only version source inside the package.

    version.txt and .release-please-manifest.json live at the repo root, which
    the release ZIP does not contain — reading either would report 'unknown' for
    every real user while looking correct in a dev checkout.
    """
    parser = configparser.ConfigParser()
    parser.read(PLUGIN_ROOT / "metadata.txt", encoding="utf-8")

    assert plugin_version() == parser["general"]["version"].strip()
    assert plugin_version() != "unknown"


def test_headers_carry_the_agreed_surface_and_client_name():
    headers = client_headers()

    assert headers["x-infrared-application"] == APPLICATION == "qgis"
    assert headers["x-infrared-sdk"] == f"{CLIENT_NAME}/{plugin_version()}"


@pytest.mark.parametrize(
    "module, call",
    [
        pytest.param(
            fetch,
            lambda: fetch.fetch_weather_file_names(16.373, 48.215, 100, "k"),
            id="weather-file-names",
        ),
        pytest.param(
            fetch_from_registry,
            # The private request helper, not the public fetch_from_registry()
            # wrapper: the wrapper fans out to three endpoints and resolves the
            # key from QSettings, neither of which this test is about.
            lambda: fetch_from_registry._get_json("utils/registry/models", "k"),
            id="registry",
        ),
    ],
)
def test_every_request_the_plugin_makes_identifies_itself(module, call, monkeypatch):
    """Each live request site merges the identity headers, alongside the API key."""
    recorder = _Recorder({"data": {"locations": []}})
    monkeypatch.setattr(module, "requests", recorder)

    call()

    assert recorder.calls, "the call did not reach the transport"
    for headers in recorder.calls:
        assert headers.get("x-infrared-application") == "qgis"
        assert headers.get("x-infrared-sdk", "").startswith(f"{CLIENT_NAME}/")
        # The identity headers must not displace authentication.
        assert headers.get("x-api-key") == "k"
