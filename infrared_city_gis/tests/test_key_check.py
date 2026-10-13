"""The API-key check must probe a route every valid key can read (#53).

The probe used to be ``/v2/webhooks``, a plan-restricted route that answers 403
for Trial-plan keys, so valid keys were reported as rejected. It is now the
capabilities document the SDK reads before every simulation run. These tests
pin the URL and that ``verify_api_key`` really requests it, so the probe cannot
drift back onto a plan-restricted route (or the retiring ``/v2/utils`` service)
unnoticed.
"""

from infrared_city_gis.constants import VERIFY_API_KEY_URL
from infrared_city_gis.services import key_check


class _Response:
    """Minimal stand-in for a qgis_http response that was accepted."""

    status_code = 200

    def raise_for_status(self):
        return None


class _Recorder:
    """Captures the URL each GET was made against."""

    def __init__(self):
        self.urls = []

    def get(self, url, *args, **kwargs):
        self.urls.append(url)
        return _Response()


def test_probe_is_the_capabilities_route():
    assert VERIFY_API_KEY_URL.startswith("https://api.infrared.city")
    assert VERIFY_API_KEY_URL.endswith("/v2/binary/v1/capabilities")


def test_probe_avoids_plan_restricted_and_retiring_routes():
    assert "/webhooks" not in VERIFY_API_KEY_URL
    assert "/utils" not in VERIFY_API_KEY_URL


def test_verify_api_key_requests_the_probe_url(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(key_check, "requests", recorder)

    assert key_check.verify_api_key("k") is True
    assert recorder.urls == [VERIFY_API_KEY_URL]
