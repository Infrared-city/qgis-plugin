"""The plugin identifies itself to the API on every call, direct and via the SDK.

Without these headers the gateway cannot attribute a request to QGIS and falls
back to guessing from the auth method, which lands plugin traffic in the same
bucket as any other API-key script (Infrared-city/qgis-plugin#43).

The header helper alone is not worth a test — what breaks in practice is a new
request site that forgets to merge it, or an old one that gets rewritten. So the
tests below call the real fetch functions with the transport swapped out and
assert on what was actually handed to it, and build a real ``InfraredClient``
through the factory to assert on the headers the SDK resolved.
"""

import configparser
from pathlib import Path

import pytest

from infrared_city_gis.services import fetch, fetch_from_registry, key_check
from infrared_city_gis.utils.client_identity import (
    APPLICATION,
    CLIENT_NAME,
    client_headers,
    make_client,
    plugin_version,
    sdk_id,
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
            lambda: fetch._fetch_buildings_request(48.215, 16.373, 512.0, 512.0, "k"),
            id="building-geometry",
        ),
        pytest.param(
            key_check,
            lambda: key_check.verify_api_key("k"),
            id="api-key-check",
        ),
    ],
)
def test_every_authenticated_request_identifies_itself(module, call, monkeypatch):
    """Each authenticated request site merges the identity headers next to the key."""
    recorder = _Recorder({"data": {"locations": []}})
    monkeypatch.setattr(module, "requests", recorder)

    call()

    assert recorder.calls, "the call did not reach the transport"
    for headers in recorder.calls:
        assert headers.get("x-infrared-application") == "qgis"
        assert headers.get("x-infrared-sdk", "").startswith(f"{CLIENT_NAME}/")
        # The identity headers must not displace authentication.
        assert headers.get("x-api-key") == "k"


def test_weather_stations_come_through_the_sdk_client(monkeypatch):
    """The station lookup used to be a direct GET on /v2/utils/weather/location.

    That route belongs to the retiring utilities service (#47), so it now goes
    through the SDK's static catalog — on a client from the identity-carrying
    factory, with no direct HTTP request of the plugin's own.
    """

    class _Weather:
        def __init__(self):
            self.calls = []

        def get_weather_file_from_location(self, *, lat, lon, radius):
            self.calls.append((lat, lon, radius))
            return [{"fileName": "A"}, {"uuid": "no-name"}, {"fileName": "B"}]

    class _Client:
        def __init__(self):
            self.weather = _Weather()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    client, built = _Client(), []
    recorder = _Recorder({})
    monkeypatch.setattr(fetch, "make_client", lambda key: built.append(key) or client)
    monkeypatch.setattr(fetch, "requests", recorder)

    assert fetch.fetch_weather_file_names(16.373, 48.215, 100, "k") == ["A", "B"]
    assert built == ["k"]
    assert client.weather.calls == [(48.215, 16.373, 100)]
    assert recorder.calls == [], "the lookup made a direct HTTP request"


def test_the_registry_reads_identify_themselves_but_carry_no_key():
    """The registries are public documents; sending the key there is a leak.

    Asserted on the real fan-out rather than the request helper: what this
    guards is that no future caller reintroduces an authenticated registry
    read, and the fan-out is where such a caller would land.
    """
    recorder = _Recorder({"version": "1.0.0", "visualConfigurations": {}})
    original = fetch_from_registry.requests
    fetch_from_registry.requests = recorder
    try:
        fetch_from_registry.fetch_from_registry()
    finally:
        fetch_from_registry.requests = original

    assert len(recorder.calls) == 3, "expected one read per registry document"
    for headers in recorder.calls:
        assert headers.get("x-infrared-application") == "qgis"
        assert "x-api-key" not in headers


def test_the_sdk_client_carries_the_same_identity():
    """Simulations run through the SDK, so the factory must label them too.

    Asserted on the resolved headers rather than on the constructor call: the
    SDK owns how ``sdk_id`` reaches the wire (it chains its own token on), and a
    test that only checked the arguments would keep passing if that contract
    changed under us. Constructing a client makes no request, so a throwaway key
    is enough.
    """
    client = make_client("k")
    try:
        headers = client.telemetry.as_headers()
    finally:
        client.close()

    assert headers["x-infrared-application"] == APPLICATION

    # The plugin's own token leads; the SDK appends its own version behind it.
    tokens = headers["x-infrared-sdk"].split()
    assert tokens[0] == sdk_id()
    assert any(token.startswith("infrared-sdk/") for token in tokens[1:])


def test_the_factory_passes_extra_arguments_through():
    """Callers tune the run (max_workers and friends) on the same constructor."""
    client = make_client("k", transport="json")
    try:
        assert client.telemetry.application == APPLICATION
    finally:
        client.close()
