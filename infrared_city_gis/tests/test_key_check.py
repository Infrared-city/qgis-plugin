"""The API-key check must probe a route every valid key can read (#53).

The probe used to be ``/v2/webhooks``, a plan-restricted route that answers 403
for Trial-plan keys, so valid keys were reported as rejected. It is now the
capabilities document the SDK reads before every simulation run. These tests
pin the URL and that ``verify_api_key`` really requests it, so the probe cannot
drift back onto a plan-restricted route (or the retiring ``/v2/utils`` service)
unnoticed.

They also pin how the answer is read: a 401 means the key is unknown
(``InfraredAPIError``), a 403 is refused whatever its body (``ApiKeyCheckRefused``:
a proxy can send problem-details JSON too, so it proves nothing and the key is
never saved), anything else is "could not tell" (False). A 2xx is only a
verified key when its body is the capabilities document; any other body (a
proxy or captive portal answering, even with JSON of its own) is "could not
tell" too, so an unverified key never replaces a saved one. How the server's
reason text is surfaced lives in ``test_key_check_reason.py``.
"""

from pathlib import Path

import pytest

from infrared_city_gis.constants import VERIFY_API_KEY_URL
from infrared_city_gis.exceptions import ApiKeyCheckRefused, InfraredAPIError
from infrared_city_gis.services import key_check, qgis_http
from infrared_city_gis.tests._key_check_fakes import CAPABILITIES, KEY, problem, use


class _Response:
    """Minimal stand-in for a qgis_http response that was accepted."""

    status_code = 200

    def json(self):
        return CAPABILITIES

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


# ---------------------------------------------------------------------------
# Outcomes: 401 rejected, 403 refused, everything else "could not tell"
# ---------------------------------------------------------------------------

def test_accepted_key_is_true(monkeypatch):
    use(monkeypatch, 200, CAPABILITIES)
    assert key_check.verify_api_key(KEY) is True


@pytest.mark.parametrize(
    "body",
    [
        {"status": "ok"},
        {key: value for key, value in CAPABILITIES.items() if key != "wireVersion"},
        {**CAPABILITIES, "wireVersion": True},
        {**CAPABILITIES, "wireVersion": "1"},
        {**CAPABILITIES, "inputFormat": "other"},
        {**CAPABILITIES, "resultFormat": "other"},
        {},
    ],
    ids=[
        "status-ok",
        "no-wire-version",
        "bool-wire-version",
        "str-wire-version",
        "other-input-format",
        "other-result-format",
        "empty-object",
    ],
)
def test_a_2xx_json_that_is_not_the_capabilities_document_is_unreachable(monkeypatch, body):
    # A proxy can answer 200 with JSON of its own: that verifies nothing.
    use(monkeypatch, 200, body)
    assert key_check.verify_api_key(KEY) is False


@pytest.mark.parametrize(
    "body",
    [
        "<html><body>Please sign in to the network</body></html>",
        ["not", "an", "object"],
        "",
        "null",
        "plain text",
    ],
    ids=["html", "json-list", "empty", "json-null", "text"],
)
def test_a_2xx_that_is_not_the_apis_json_is_unreachable(monkeypatch, body):
    # A captive portal or proxy answers 200 with its own page: nothing was verified.
    use(monkeypatch, 200, body)
    assert key_check.verify_api_key(KEY) is False


def test_401_is_a_rejection_with_the_server_reason(monkeypatch):
    detail = "Missing or invalid authentication credentials."
    use(monkeypatch, 401, {"title": "Unauthorized", "status": 401, "detail": detail})

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(KEY)

    assert not isinstance(caught.value, ApiKeyCheckRefused)
    assert caught.value.status_code == 401
    assert caught.value.server_message == detail


@pytest.mark.parametrize(
    "body",
    [
        {"message": "Blocked by policy"},
        problem(403, "Forbidden"),
        {**problem(403, "x"), "status": "403"},
        problem(401, "x"),
        {"status": 403, "detail": "x"},
        "  <html>\n  <body>Forbidden</body>\n</html>  ",
        "",
        "Forbidden",
        "[1, 2]",
        "null",
    ],
    ids=[
        "proxy-json",
        "problem-details",
        "status-as-string",
        "problem-for-401",
        "no-title",
        "html",
        "empty",
        "text",
        "json-list",
        "json-null",
    ],
)
def test_any_403_is_refused_not_saved(monkeypatch, body):
    # A 403 proves nothing about the key (a proxy can send problem-details too).
    use(monkeypatch, 403, body)

    with pytest.raises(ApiKeyCheckRefused) as caught:
        key_check.verify_api_key(KEY)

    exc = caught.value
    assert isinstance(exc, InfraredAPIError)
    assert exc.status_code == 403
    assert exc.title == ApiKeyCheckRefused.KEY_CHECK_TITLE
    assert "Access Denied" not in str(exc)


def test_a_403_carries_the_servers_reason(monkeypatch):
    detail = "Blocked by policy"
    use(monkeypatch, 403, problem(403, detail))

    with pytest.raises(ApiKeyCheckRefused) as caught:
        key_check.verify_api_key(KEY)

    assert caught.value.server_message == detail
    assert detail in caught.value.detail


@pytest.mark.parametrize("status", [400, 404, 429, 500, 502, 503, 0])
def test_other_statuses_are_unreachable_not_rejection(monkeypatch, status):
    use(monkeypatch, status, {"message": "nope"})
    assert key_check.verify_api_key(KEY) is False


@pytest.mark.parametrize(
    "error",
    [
        qgis_http.RequestException("connection refused"),
        qgis_http.Timeout("timed out"),
        RuntimeError("boom"),
    ],
)
def test_network_failures_are_unreachable(monkeypatch, error):
    use(monkeypatch, error=error)
    assert key_check.verify_api_key(KEY) is False


def test_empty_key_is_false_and_makes_no_request(monkeypatch):
    transport = use(monkeypatch, 200)

    assert key_check.verify_api_key("") is False
    assert transport.calls == 0


# ---------------------------------------------------------------------------
# saved_key_rejected: only a 401 may lock a saved key out
# ---------------------------------------------------------------------------


def test_saved_key_rejected_on_401(monkeypatch):
    use(monkeypatch, 401, {"message": "Unauthorized"})
    assert key_check.saved_key_rejected(KEY) is True


@pytest.mark.parametrize("status", [500, 200])
def test_saved_key_kept_on_everything_but_401(monkeypatch, status):
    use(monkeypatch, status, {"message": "x"})
    assert key_check.saved_key_rejected(KEY) is False


@pytest.mark.parametrize(
    "body",
    [
        problem(403, "Forbidden"),
        {"message": "Blocked by policy"},
        "<html><body>Blocked by policy</body></html>",
        "",
    ],
    ids=["problem-details", "proxy-json", "html", "empty"],
)
def test_saved_key_kept_on_any_403(monkeypatch, body):
    use(monkeypatch, 403, body)
    assert key_check.saved_key_rejected(KEY) is False


def test_saved_key_kept_on_network_error(monkeypatch):
    use(monkeypatch, error=qgis_http.RequestException("offline"))
    assert key_check.saved_key_rejected(KEY) is False


def test_saved_key_kept_on_an_unexpected_error(monkeypatch):
    def boom(api_key):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(key_check, "verify_api_key", boom)
    assert key_check.saved_key_rejected(KEY) is False


# ---------------------------------------------------------------------------
# The exception itself
# ---------------------------------------------------------------------------


def test_api_key_check_refused_is_an_api_error():
    assert issubclass(ApiKeyCheckRefused, InfraredAPIError)

    exc = ApiKeyCheckRefused()
    assert exc.status_code == 403
    assert str(exc) == "[HTTP 403] API Key Not Saved (403)"
    assert "unchanged" in exc.detail


def test_simulation_403_text_is_unchanged():
    assert InfraredAPIError(403).title == "Access Denied (403)"
    assert (
        InfraredAPIError(403, server_message="x").detail
        == InfraredAPIError._STATUS_MAP[403][1] + "\n\nServer message: x"
    )


# ---------------------------------------------------------------------------
# The startup gate in the plugin class
# ---------------------------------------------------------------------------


def test_startup_gate_locks_only_on_a_401():
    source = (Path(__file__).resolve().parents[1] / "infrared_city_gis.py").read_text(
        encoding="utf-8"
    )

    assert "saved_key_rejected(" in source
    assert "(401, 403)" not in source
    assert "in (401, 403)" not in source
