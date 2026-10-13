"""Verify an Infrared City API key against the server.

This used to be a side effect of the registry refresh: the registries were read
from the utilities service with the key attached, so a 401 there meant a bad
key. The registries moved to a public mirror that takes no credentials
(``constants.REGISTRY_DOCUMENTS``), which left the two callers that depended on
the auth half — the key-save dialog and the startup gate — with nothing to
check, so the check lives here on its own now.

``GET /v2/binary/v1/capabilities`` is the probe: an authenticated, read-only
route, so it costs nothing and has no side effects. It is the same document the
SDK reads before every simulation run, so a key that can read it can run
simulations, and it is not plan-restricted. The probe used to be ``/v2/webhooks``,
which answers 403 for Trial-plan keys and made valid keys look rejected (#53).
It goes through ``services.qgis_http`` like every other direct call, so it
honours the proxy configured in QGIS Options → Network.

Four outcomes, matching what the callers distinguish:

    verified    -> True (a 2xx carrying the API's JSON object)
    restricted  -> ApiKeyRestricted (403 with the API's JSON body): the key IS
                   recognised but this request was refused, so the caller saves
                   it and shows a note
    rejected    -> InfraredAPIError with status_code 401: the key is unknown
    unreachable -> False

The last one is deliberately not an exception. An outage must not be presented
to the user as a bad key, and it must not lock an already-saved key out of the
plugin. For the same reason only a 401 ever counts as a rejection of a key that
is already saved (see :func:`saved_key_rejected`).

A 2xx or 403 is only trusted when its body is the API's JSON object. A captive
portal, corporate proxy or firewall answers with its own page (usually HTML),
and that must neither verify a key that was never checked nor claim the server
recognised one, so a non-JSON 2xx or 403 counts as unreachable. A 401 is
rejected whatever its body: failing closed on a key check is safe, because the
user is only asked to re-enter the key.
"""

from __future__ import annotations

import re

from ..constants import VERIFY_API_KEY_URL
from ..exceptions import ApiKeyRestricted, InfraredAPIError
from ..infrared_logger import logger
from ..utils.client_identity import client_headers
from . import qgis_http as requests

#: Short read budget: this is a small list response, and both callers block the
#: UI thread while it runs (key save shows a wait cursor, startup delays the
#: toolbar). A slow network should fall through to "unreachable" quickly.
_VERIFY_TIMEOUT_SEC = 10

#: Longest server reason kept; it is shown in a dialog and written to the log.
_REASON_MAX_CHARS = 200
#: JSON fields that carry a human-readable reason, in order of preference.
_REASON_FIELDS = ("message", "detail", "title")


def _mask_key(api_key: str) -> str:
    """Return ``api_key`` in a form that is safe to show or log."""
    if len(api_key) > 8:
        return f"{api_key[:4]}…{api_key[-4:]}"
    return "•" * len(api_key)


def _json_object(response) -> dict | None:
    """Return the parsed JSON body of ``response`` if it is an object, else None.

    The API answers with JSON objects; anything else (an HTML page from a
    proxy, an empty body, a bare list or string) is not the API talking.
    Never raises.
    """
    try:
        body = response.json()
    except Exception:  # not JSON, empty, or no json() at all
        return None
    return body if isinstance(body, dict) else None


def _server_reason(response, api_key: str) -> str | None:
    """Return a short, key-free reason from an error ``response``, or None.

    A body that parses as JSON yields the first non-empty string among
    ``message`` / ``detail`` / ``title`` when it is an object, and nothing
    otherwise; the raw text is never used for valid JSON. Only a body that is
    not JSON at all falls back to its stripped text. Whitespace is collapsed,
    the API key is masked if the server echoed it, and the result is cut to
    ``_REASON_MAX_CHARS``. The key is masked BEFORE the cut so a truncation
    cannot leave part of it behind. A reason that starts with ``<`` is markup
    (a proxy's HTML page) and is dropped, so it never reaches a Qt message box,
    which renders rich text. Never raises.
    """
    try:
        if response is None:
            return None
        reason = None
        try:
            body = response.json()
        except ValueError:
            reason = response.text.strip()
        else:
            if isinstance(body, dict):
                for field in _REASON_FIELDS:
                    value = body.get(field)
                    if isinstance(value, str) and value.strip():
                        reason = value
                        break
        if reason is None:
            return None
        reason = re.sub(r"\s+", " ", reason).strip()
        if not reason or reason.startswith("<"):
            return None
        key = (api_key or "").strip()
        if key:
            reason = reason.replace(key, _mask_key(key))
        if len(reason) > _REASON_MAX_CHARS:
            reason = reason[:_REASON_MAX_CHARS] + "…"
        return reason
    except Exception:  # a reason is a courtesy, never a failure
        return None


def verify_api_key(api_key: str) -> bool:
    """Return True if the server accepts ``api_key``.

    Raises:
        ApiKeyRestricted: the server recognised the key but refused this
            request (403 with the API's JSON body, e.g. plan or permissions).
            Callers save the key and show the exception's ``title`` /
            ``detail`` as a note.
        InfraredAPIError: the server does not recognise the key (401).

    Returns:
        bool: True when the key was accepted (a 2xx with the API's JSON
            object). False when the server could not be reached or gave no
            usable answer: offline, DNS, 5xx, other 4xx, timeout, or a 2xx or
            403 whose body is not the API's JSON (a proxy or captive portal
            answering instead, so nothing was verified).
    """
    if not api_key:
        logger.warning("verify_api_key: no api-key given")
        return False

    headers = {**client_headers(), "x-api-key": api_key}
    try:
        logger.info("Verifying API key against %s", VERIFY_API_KEY_URL)
        r = requests.get(
            VERIFY_API_KEY_URL, headers=headers, timeout=_VERIFY_TIMEOUT_SEC
        )
        r.raise_for_status()
        if _json_object(r) is None:
            logger.warning(
                "API key verification got an unexpected HTTP %s answer (not the API's JSON); "
                "treating as unreachable",
                r.status_code,
            )
            return False
        return True
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        reason = _server_reason(e.response, api_key)
        logger.warning(
            "API key verification failed: HTTP %s (%s)", status, reason or "no reason given"
        )
        if status == 401:
            raise InfraredAPIError(status_code=401, server_message=reason) from e
        if status == 403:
            if _json_object(e.response) is None:
                logger.warning(
                    "API key verification got a 403 that is not the API's JSON "
                    "(a proxy or firewall?); treating as unreachable"
                )
                return False
            raise ApiKeyRestricted(status_code=403, server_message=reason) from e
        return False
    except Exception as e:
        logger.warning("API key verification could not complete: %s", e)
        return False


def saved_key_rejected(api_key: str) -> bool:
    """Return True only if the server says it does not recognise ``api_key``.

    For the startup gate, which decides whether an already-saved key is dropped.
    An outage, a timeout or a plan-restricted 403 must never lock a saved key
    out of the plugin, so every outcome other than a 401 returns False.
    """
    try:
        verify_api_key(api_key)
    except ApiKeyRestricted as e:
        logger.info("Saved API key kept: HTTP %s, key recognised but access restricted", e.status_code)
        return False
    except InfraredAPIError as e:
        rejected = e.status_code == 401
        logger.info("Saved API key check: HTTP %s, rejected=%s", e.status_code, rejected)
        return rejected
    except Exception as e:  # never lock a key out on an unexpected error
        logger.warning("Saved API key check could not complete: %s", type(e).__name__)
        return False
    return False
