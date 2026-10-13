"""Shared fakes for the API-key check tests (#53): the capabilities body, the
problem-details builder, a response, a transport and the ``use`` installer.

Kept apart so ``test_key_check.py`` and ``test_key_check_reason.py`` stay under
the 400-line file convention.
"""

import json

from infrared_city_gis.services import key_check, qgis_http

#: What the API answers on a 2xx: the document the SDK validates before every run.
CAPABILITIES = {
    "inputFormat": "irbf",
    "resultFormat": "irbf",
    "wireVersion": 1,
    "geometrySchemas": [1],
    "models": {},
    "limits": {},
}


def problem(status, detail=None, title="Forbidden"):
    """Build the API's problem-details error body (``detail`` omitted when None)."""
    body = {"type": f"https://httpstatuses.io/{status}", "title": title, "status": status}
    if detail is not None:
        body["detail"] = detail
    body["instance"] = "req-1"
    return body

KEY = "ik_abcdefghijklmnop"
MASKED_KEY = "ik_a…mnop"


class FakeResponse:
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


class Transport:
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


def use(monkeypatch, status=None, body=None, error=None):
    """Install a transport answering ``status``; ``body=None`` means a normal one.

    A normal body is the capabilities document on success and empty on an error,
    so tests only spell out a body when the body is what they are about.
    """
    if body is None:
        body = CAPABILITIES if status is not None and status < 400 else ""
    transport = Transport(
        response=None if status is None else FakeResponse(status, body), error=error
    )
    monkeypatch.setattr(key_check, "requests", transport)
    return transport
