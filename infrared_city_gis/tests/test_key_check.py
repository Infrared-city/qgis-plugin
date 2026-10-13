"""The API-key check must probe a route every valid key can read (#53).

The probe used to be ``/v2/webhooks``, a plan-restricted route that answers 403
for Trial-plan keys, so valid keys were reported as rejected. It is now the
capabilities document the SDK reads before every simulation run. These tests
pin the URL and that ``verify_api_key`` really requests it, so the probe cannot
drift back onto a plan-restricted route (or the retiring ``/v2/utils`` service)
unnoticed.

They also pin how the answer is read: a 401 means the key is unknown
(``InfraredAPIError``), a 403 carrying the API's problem-details JSON means the key is
recognised but this request was refused (``ApiKeyRestricted``, so callers save
the key), anything else is "could not tell" (False). A 2xx is only a verified
key when its body is the capabilities document, and a 403 is only "recognised"
when its body is the API's problem-details error with status 403. Any other body
(a proxy or captive portal answering, even with JSON of its own) is "could not
tell" too, so an unverified key never replaces a saved one. How the server's
reason text is surfaced lives in ``test_key_check_reason.py``.
"""

from pathlib import Path

import pytest

from infrared_city_gis.constants import VERIFY_API_KEY_URL
from infrared_city_gis.exceptions import ApiKeyRestricted, InfraredAPIError
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
# Outcomes: 401 rejected, 403 restricted, everything else "could not tell"
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

    assert not isinstance(caught.value, ApiKeyRestricted)
    assert caught.value.status_code == 401
    assert caught.value.server_message == detail


def test_403_means_key_recognised_but_restricted(monkeypatch):
    use(monkeypatch, 403, problem(403, "Forbidden"))

    with pytest.raises(ApiKeyRestricted) as caught:
        key_check.verify_api_key(KEY)

    exc = caught.value
    assert isinstance(exc, InfraredAPIError)
    assert exc.status_code == 403
    assert exc.server_message == "Forbidden"
    assert exc.title == ApiKeyRestricted.KEY_CHECK_TITLE
    assert "recognised and has been saved" in exc.detail
    assert "Forbidden" in exc.detail
    assert "Access Denied" not in str(exc)


@pytest.mark.parametrize(
    "body",
    ["  <html>\n  <body>Forbidden</body>\n</html>  ", "", "Forbidden", "[1, 2]"],
    ids=["html", "empty", "text", "json-list"],
)
def test_a_403_without_the_apis_json_object_is_unreachable(monkeypatch, body):
    # Not the API talking (proxy, firewall): the key was not recognised by anyone.
    use(monkeypatch, 403, body)
    assert key_check.verify_api_key(KEY) is False


def test_a_403_with_a_proxys_own_json_is_unreachable_not_restricted(monkeypatch):
    # JSON, but not the API's problem-details shape: must not be saved as "recognised".
    use(monkeypatch, 403, {"message": "Blocked by policy"})
    assert key_check.verify_api_key(KEY) is False


def test_a_403_problem_carries_the_detail_as_the_server_message(monkeypatch):
    detail = "Your plan does not include this route."
    use(monkeypatch, 403, problem(403, detail))

    with pytest.raises(ApiKeyRestricted) as caught:
        key_check.verify_api_key(KEY)

    assert caught.value.status_code == 403
    assert caught.value.server_message == detail


@pytest.mark.parametrize(
    "body",
    [
        {**problem(403, "x"), "status": "403"},
        problem(401, "x"),
        {**problem(403, "x"), "status": True},
        {"status": 403, "detail": "x"},
        {**problem(403, "x"), "title": 5},
    ],
    ids=["status-as-string", "status-401", "status-bool", "no-title", "title-not-a-string"],
)
def test_a_403_that_is_not_a_403_problem_is_unreachable(monkeypatch, body):
    use(monkeypatch, 403, body)
    assert key_check.verify_api_key(KEY) is False


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


@pytest.mark.parametrize("status", [403, 500, 200])
def test_saved_key_kept_on_everything_but_401(monkeypatch, status):
    use(monkeypatch, status, {"message": "x"})
    assert key_check.saved_key_rejected(KEY) is False


def test_saved_key_kept_on_a_restricted_403(monkeypatch):
    use(monkeypatch, 403, problem(403, "Forbidden"))
    assert key_check.saved_key_rejected(KEY) is False


def test_saved_key_kept_on_a_non_api_403(monkeypatch):
    use(monkeypatch, 403, "<html><body>Blocked by policy</body></html>")
    assert key_check.saved_key_rejected(KEY) is False


def test_saved_key_kept_on_a_proxys_json_403(monkeypatch):
    use(monkeypatch, 403, {"message": "Blocked by policy"})
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


def test_api_key_restricted_is_an_api_error():
    assert issubclass(ApiKeyRestricted, InfraredAPIError)

    exc = ApiKeyRestricted()
    assert exc.status_code == 403
    assert str(exc) == "[HTTP 403] API Key Saved With Limited Access"


def test_simulation_403_text_is_unchanged():
    assert InfraredAPIError(403).title == "Access Denied (403)"
    assert (
        InfraredAPIError(403, server_message="x").detail
        == InfraredAPIError._STATUS_MAP[403][1] + "\n\nServer message: x"
    )


def test_limited_access_note_says_the_key_was_recognised():
    assert ApiKeyRestricted().detail.startswith("Your API key was recognised")


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
