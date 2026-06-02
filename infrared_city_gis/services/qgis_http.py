"""Thin wrapper around QgsBlockingNetworkRequest with a requests-compatible API.

Replaces the ``requests`` library throughout the plugin so that all HTTP
calls go through QGIS's own network stack and therefore respect the proxy
settings configured by the user in QGIS Options → Network.

Public API mirrors the subset of ``requests`` used by this plugin:

    from .services import qgis_http as requests

    resp = requests.get(url, params={...}, headers={...}, timeout=20)
    resp = requests.post(url, json={...}, headers={...}, timeout=(10, 300))
    resp = requests.post(url, data="raw string", headers={...}, timeout=20)
    resp = requests.post(url, data={"key": "val"}, timeout=20)  # form-encoded

Response objects expose:
    .status_code  int
    .content      bytes
    .text         str (UTF-8 decoded)
    .json()       parsed dict / list

Exception hierarchy (mirrors requests):
    RequestException          — base for all errors
    ├── Timeout               — network timeout
    └── HTTPError             — raised by resp.raise_for_status() on 4xx/5xx

Both ``requests.Timeout`` and ``requests.exceptions.Timeout`` patterns work
because ``exceptions`` is an alias namespace that re-exports the same classes.

NOTE: QgsBlockingNetworkRequest must be called from the main Qt thread.
      All callers in this plugin run from dialog event handlers, so this is safe.
"""

from __future__ import annotations

import json as _json
from urllib.parse import urlencode

from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtCore import QByteArray, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class RequestException(Exception):
    """Base exception for all HTTP/network errors."""

    def __init__(self, msg: str = "", response=None):
        self.response = response
        super().__init__(msg)


class Timeout(RequestException):
    """Raised when the request times out."""


class HTTPError(RequestException):
    """Raised by Response.raise_for_status() on 4xx / 5xx responses."""


class _ExceptionsNamespace:
    """Allows ``requests.exceptions.Timeout`` / ``.RequestException`` usage."""
    RequestException = RequestException
    Timeout = Timeout
    HTTPError = HTTPError


exceptions = _ExceptionsNamespace()

# Module-level aliases so ``requests.RequestException`` etc. work directly.
RequestException = RequestException
HTTPError = HTTPError
Timeout = Timeout


# ---------------------------------------------------------------------------
# Response wrapper
# ---------------------------------------------------------------------------

class Response:
    """Wraps QgsNetworkReplyContent to look like a requests.Response."""

    def __init__(self, reply):
        self._content: bytes = bytes(reply.content())
        self.status_code: int = (
            reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
        )
        self.headers: dict = {
            bytes(k).decode(): bytes(reply.rawHeader(k)).decode()
            for k in reply.rawHeaderList()
        }

    @property
    def content(self) -> bytes:
        return self._content

    @property
    def text(self) -> str:
        return self._content.decode("utf-8", errors="replace")

    def json(self):
        return _json.loads(self._content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(
                f"HTTP {self.status_code}",
                response=self,
            )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _timeout_ms(timeout) -> int:
    """Convert a requests-style timeout to milliseconds.

    ``timeout`` can be a number (seconds) or a (connect, read) tuple.
    We use the read timeout as the transfer-timeout budget passed to Qt,
    since connect and read can't be separated at the QNetworkRequest level.
    """
    if isinstance(timeout, (list, tuple)):
        return int(timeout[-1] * 1000)
    return int(timeout * 1000)


def _build_request(url: str, headers: dict | None, timeout) -> QNetworkRequest:
    req = QNetworkRequest(QUrl(url))
    if headers:
        for k, v in headers.items():
            req.setRawHeader(
                k.encode() if isinstance(k, str) else k,
                v.encode() if isinstance(v, str) else v,
            )
    # Qt 5.15+ / QGIS 3.14+: per-request transfer timeout.
    try:
        req.setTransferTimeout(_timeout_ms(timeout))
    except AttributeError:
        pass
    return req


def _check(err_code: int, blocker: QgsBlockingNetworkRequest) -> Response:
    """Map QgsBlockingNetworkRequest error codes to exceptions or Response."""
    if err_code == QgsBlockingNetworkRequest.NoError:
        resp = Response(blocker.reply())
        if resp.status_code >= 400:
            raise HTTPError(f"HTTP {resp.status_code}", response=resp)
        return resp

    if err_code == QgsBlockingNetworkRequest.TimeoutError:
        raise Timeout(blocker.errorMessage(), response=None)

    if err_code == QgsBlockingNetworkRequest.ServerExceptionError:
        # Server returned a 4xx / 5xx — attach the response so callers can
        # inspect the body (e.g. extract a JSON error message).
        resp = Response(blocker.reply())
        raise HTTPError(f"HTTP {resp.status_code}", response=resp)

    # QgsBlockingNetworkRequest.NetworkError (and anything else)
    raise RequestException(blocker.errorMessage(), response=None)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get(url: str, params: dict | None = None, headers: dict | None = None,
        timeout=20) -> Response:
    """HTTP GET. ``params`` are appended as query-string parameters."""
    if params:
        from qgis.PyQt.QtCore import QUrlQuery
        qurl = QUrl(url)
        q = QUrlQuery()
        for k, v in params.items():
            q.addQueryItem(str(k), str(v))
        qurl.setQuery(q)
        url = qurl.toString()

    req = _build_request(url, headers, timeout)
    blocker = QgsBlockingNetworkRequest()
    err = blocker.get(req, forceRefresh=True)
    return _check(err, blocker)


def post(url: str, data=None, json=None, headers: dict | None = None,
         timeout=20) -> Response:
    """HTTP POST.

    ``json``  — serialised as application/json body.
    ``data``  — if dict: form-encoded (application/x-www-form-urlencoded).
                if str/bytes: sent as-is (caller must set Content-Type header).
    """
    req = _build_request(url, headers, timeout)

    if json is not None:
        body = QByteArray(_json.dumps(json).encode("utf-8"))
        req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
    elif data is not None:
        if isinstance(data, dict):
            body = QByteArray(urlencode(data).encode("utf-8"))
            req.setHeader(
                QNetworkRequest.ContentTypeHeader,
                "application/x-www-form-urlencoded",
            )
        elif isinstance(data, str):
            body = QByteArray(data.encode("utf-8"))
        else:
            body = QByteArray(data)
    else:
        body = QByteArray()

    blocker = QgsBlockingNetworkRequest()
    err = blocker.post(req, body)
    return _check(err, blocker)
