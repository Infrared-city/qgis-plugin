"""The API-key check must probe a route every valid key can read (#53).

The probe used to be ``/v2/webhooks``, a plan-restricted route that answers 403
for Trial-plan keys, so valid keys were reported as rejected. It is now the
capabilities document the SDK reads before every simulation run. These tests
pin the URL and that ``verify_api_key`` really requests it, so the probe cannot
drift back onto a plan-restricted route (or the retiring ``/v2/utils`` service)
unnoticed.

They also pin how the answer is read: a 401 means the key is unknown
(``InfraredAPIError``), a 403 carrying the API's JSON means the key is
recognised but this request was refused (``ApiKeyRestricted``, so callers save
the key), anything else is "could not tell" (False). A 2xx or 403 whose body is
not the API's JSON (a proxy or captive portal answering) is "could not tell"
too. The server's reason is surfaced, with the key masked and markup dropped.
"""

import json
from pathlib import Path

import pytest

from infrared_city_gis.constants import VERIFY_API_KEY_URL
from infrared_city_gis.exceptions import ApiKeyRestricted, InfraredAPIError
from infrared_city_gis.services import key_check, qgis_http


class _Response:
    """Minimal stand-in for a qgis_http response that was accepted."""

    status_code = 200

    def json(self):
        return {"wireVersion": 1}

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

_KEY = "ik_abcdefghijklmnop"
_MASKED_KEY = "ik_a…mnop"


class _FakeResponse:
    """A qgis_http-shaped response: status, text, headers and a strict json()."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.headers = {}

    def json(self):
        if not self.text.strip():
            raise ValueError("empty response body")
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code == 0:
            raise qgis_http.RequestException("no HTTP status in reply", response=self)
        if self.status_code >= 400:
            raise qgis_http.HTTPError(f"HTTP {self.status_code}", response=self)


class _Transport:
    """Stands in for ``qgis_http``: returns a response or raises, records calls.

    The exception classes are the real ones so ``except requests.HTTPError`` in
    ``key_check`` matches exactly as it does in production.
    """

    HTTPError = qgis_http.HTTPError
    RequestException = qgis_http.RequestException
    Timeout = qgis_http.Timeout

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def get(self, url, *args, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


def _use(monkeypatch, status=None, body=None, error=None):
    """Install a transport answering ``status``; ``body=None`` means a normal one.

    A normal body is the API's JSON object on success and empty on an error,
    so tests only spell out a body when the body is what they are about.
    """
    if body is None:
        body = {"wireVersion": 1} if status is not None and status < 400 else ""
    transport = _Transport(
        response=None if status is None else _FakeResponse(status, body), error=error
    )
    monkeypatch.setattr(key_check, "requests", transport)
    return transport


def test_accepted_key_is_true(monkeypatch):
    _use(monkeypatch, 200, {"wireVersion": 1})
    assert key_check.verify_api_key(_KEY) is True


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
    _use(monkeypatch, 200, body)
    assert key_check.verify_api_key(_KEY) is False


def test_401_is_a_rejection_with_the_server_reason(monkeypatch):
    detail = "Missing or invalid authentication credentials."
    _use(monkeypatch, 401, {"title": "Unauthorized", "status": 401, "detail": detail})

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(_KEY)

    assert not isinstance(caught.value, ApiKeyRestricted)
    assert caught.value.status_code == 401
    assert caught.value.server_message == detail


def test_403_means_key_recognised_but_restricted(monkeypatch):
    _use(monkeypatch, 403, {"message": "Forbidden"})

    with pytest.raises(ApiKeyRestricted) as caught:
        key_check.verify_api_key(_KEY)

    exc = caught.value
    assert isinstance(exc, InfraredAPIError)
    assert exc.status_code == 403
    assert exc.server_message == "Forbidden"
    assert exc.title == ApiKeyRestricted.KEY_CHECK_TITLE
    assert "recognised and has been saved" in exc.detail
    assert "Forbidden" in exc.detail
    assert "Access Denied" not in str(exc)


def test_reason_prefers_message_then_detail_then_title(monkeypatch):
    _use(monkeypatch, 401, {"title": "T", "detail": "D", "message": "M"})
    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(_KEY)
    assert caught.value.server_message == "M"

    _use(monkeypatch, 401, {"title": "T", "detail": "D"})
    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(_KEY)
    assert caught.value.server_message == "D"

    _use(monkeypatch, 401, {"title": "T", "detail": "  ", "message": 5})
    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(_KEY)
    assert caught.value.server_message == "T"


@pytest.mark.parametrize(
    "body",
    ["  <html>\n  <body>Forbidden</body>\n</html>  ", "", "Forbidden", "[1, 2]"],
    ids=["html", "empty", "text", "json-list"],
)
def test_a_403_without_the_apis_json_object_is_unreachable(monkeypatch, body):
    # Not the API talking (proxy, firewall): the key was not recognised by anyone.
    _use(monkeypatch, 403, body)
    assert key_check.verify_api_key(_KEY) is False


def test_403_with_a_json_object_lacking_reason_fields(monkeypatch):
    _use(monkeypatch, 403, {"status": 403, "code": 7})

    with pytest.raises(ApiKeyRestricted) as caught:
        key_check.verify_api_key(_KEY)

    assert caught.value.server_message is None
    assert "Server message" not in caught.value.detail


def test_a_long_reason_is_truncated(monkeypatch):
    _use(monkeypatch, 403, {"message": "x" * 5000})

    with pytest.raises(ApiKeyRestricted) as caught:
        key_check.verify_api_key(_KEY)

    message = caught.value.server_message
    assert len(message) <= 201
    assert message.endswith("…")


def test_401_with_an_html_body_is_still_a_rejection_without_markup(monkeypatch):
    _use(monkeypatch, 401, "<html><body>Sign in</body></html>")

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(_KEY)

    assert caught.value.status_code == 401
    assert caught.value.server_message is None
    assert "<" not in caught.value.detail


def test_401_with_a_plain_text_body_uses_the_text(monkeypatch):
    _use(monkeypatch, 401, "Unauthorized")

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(_KEY)

    assert caught.value.server_message == "Unauthorized"


@pytest.mark.parametrize("body", ["null", "5", '["a"]', '"text"'], ids=["null", "number", "list", "string"])
def test_401_with_json_that_is_not_an_object_has_no_reason(monkeypatch, body):
    _use(monkeypatch, 401, body)

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(_KEY)

    assert caught.value.status_code == 401
    assert caught.value.server_message is None


def test_401_with_a_json_object_lacking_reason_fields_has_no_reason(monkeypatch):
    _use(monkeypatch, 401, {"status": 401})

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(_KEY)

    assert caught.value.server_message is None


def test_an_echoed_key_is_masked_everywhere(monkeypatch):
    _use(monkeypatch, 401, {"message": f"bad key {_KEY}"})

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(_KEY)

    exc = caught.value
    assert exc.server_message == f"bad key {_MASKED_KEY}"
    for text in (exc.server_message, str(exc), exc.detail):
        assert _KEY not in text


def test_a_key_straddling_the_truncation_point_does_not_leak(monkeypatch):
    # The key starts 195 characters in, so a cut at 200 would split it.
    _use(monkeypatch, 403, {"message": "x" * 195 + _KEY + "y" * 50})

    with pytest.raises(ApiKeyRestricted) as caught:
        key_check.verify_api_key(_KEY)

    message = caught.value.server_message
    assert _KEY[:8] not in message
    assert _KEY[-8:] not in message


def test_a_short_echoed_key_is_fully_hidden(monkeypatch):
    _use(monkeypatch, 403, {"message": "bad key abc123"})

    with pytest.raises(ApiKeyRestricted) as caught:
        key_check.verify_api_key("abc123")

    assert caught.value.server_message == "bad key ••••••"


@pytest.mark.parametrize("status", [400, 404, 429, 500, 502, 503, 0])
def test_other_statuses_are_unreachable_not_rejection(monkeypatch, status):
    _use(monkeypatch, status, {"message": "nope"})
    assert key_check.verify_api_key(_KEY) is False


@pytest.mark.parametrize(
    "error",
    [
        qgis_http.RequestException("connection refused"),
        qgis_http.Timeout("timed out"),
        RuntimeError("boom"),
    ],
)
def test_network_failures_are_unreachable(monkeypatch, error):
    _use(monkeypatch, error=error)
    assert key_check.verify_api_key(_KEY) is False


def test_empty_key_is_false_and_makes_no_request(monkeypatch):
    transport = _use(monkeypatch, 200)

    assert key_check.verify_api_key("") is False
    assert transport.calls == 0


# ---------------------------------------------------------------------------
# saved_key_rejected: only a 401 may lock a saved key out
# ---------------------------------------------------------------------------


def test_saved_key_rejected_on_401(monkeypatch):
    _use(monkeypatch, 401, {"message": "Unauthorized"})
    assert key_check.saved_key_rejected(_KEY) is True


@pytest.mark.parametrize("status", [403, 500, 200])
def test_saved_key_kept_on_everything_but_401(monkeypatch, status):
    _use(monkeypatch, status, {"message": "x"})
    assert key_check.saved_key_rejected(_KEY) is False


def test_saved_key_kept_on_a_non_api_403(monkeypatch):
    _use(monkeypatch, 403, "<html><body>Blocked by policy</body></html>")
    assert key_check.saved_key_rejected(_KEY) is False


def test_saved_key_kept_on_network_error(monkeypatch):
    _use(monkeypatch, error=qgis_http.RequestException("offline"))
    assert key_check.saved_key_rejected(_KEY) is False


def test_saved_key_kept_on_an_unexpected_error(monkeypatch):
    def boom(api_key):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(key_check, "verify_api_key", boom)
    assert key_check.saved_key_rejected(_KEY) is False


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
