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

    verified    -> True
    restricted  -> ApiKeyRestricted (403): the key IS recognised but this
                   request was refused, so the caller saves it and shows a note
    rejected    -> InfraredAPIError with status_code 401: the key is unknown
    unreachable -> False

The last one is deliberately not an exception. An outage must not be presented
to the user as a bad key, and it must not lock an already-saved key out of the
plugin. For the same reason only a 401 ever counts as a rejection of a key that
is already saved (see :func:`saved_key_rejected`).
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


def _server_reason(response, api_key: str) -> str | None:
    """Return a short, key-free reason from an error ``response``, or None.

    Takes the first of ``message`` / ``detail`` / ``title`` from a JSON object
    body, else the body text. Whitespace is collapsed, the API key is masked if
    the server echoed it, and the result is cut to ``_REASON_MAX_CHARS``. The
    key is masked BEFORE the cut so a truncation cannot leave part of it behind.
    Never raises.
    """
    try:
        if response is None:
            return None
        reason = None
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            for field in _REASON_FIELDS:
                value = body.get(field)
                if isinstance(value, str) and value.strip():
                    reason = value
                    break
        if reason is None:
            reason = response.text.strip()
        reason = re.sub(r"\s+", " ", reason).strip()
        if not reason:
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
            request (403, e.g. plan or permissions). The key is valid: callers
            save it and show the exception's ``title`` / ``detail`` as a note.
        InfraredAPIError: the server does not recognise the key (401).

    Returns:
        bool: True when the key was accepted, False when the server could not
            be reached or gave no usable answer (offline, DNS, 5xx, other 4xx,
            timeout, malformed response).
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
