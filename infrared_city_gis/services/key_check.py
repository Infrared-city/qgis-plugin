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

    verified    -> True (a 2xx carrying the API's capabilities document)
    refused     -> ApiKeyCheckRefused (any 403): proves nothing about the key,
                   so it is never saved and never locks a saved key
    rejected    -> InfraredAPIError with status_code 401: the key is unknown
    unreachable -> False

Unreachable is deliberately not an exception. An outage must not be presented
to the user as a bad key, and it must not lock an already-saved key out of the
plugin. For the same reason only a 401 ever counts as a rejection of a key that
is already saved (see :func:`saved_key_rejected`).

Only a verified 2xx saves a key, and a 2xx is only trusted when its body is the
capabilities document the API itself sends. A captive portal, corporate proxy or
firewall answers with its own page or its own JSON (``{"message": "Blocked by
policy"}``, ``{"status": "ok"}``), and that must not verify a key that was never
checked, so any other 2xx body (other JSON, HTML, empty) counts as unreachable
and the key already saved is never overwritten by an unverified one.

A 403 is not trusted whatever its body. Problem-details JSON is a generic
standard (RFC 7807), so a proxy can send a convincing one, and a 403 therefore
cannot show that the server recognised the key. It raises ApiKeyCheckRefused
and the key is not saved. A 401 is rejected whatever its body: failing closed
on a key check is safe, because the user is only asked to re-enter the key.
"""

from __future__ import annotations

import re

from ..constants import VERIFY_API_KEY_URL
from ..exceptions import ApiKeyCheckRefused, InfraredAPIError
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


def _parse_json(response):
    """Return the parsed JSON body of ``response``, or None if it has none.

    None covers an HTML page from a proxy, an empty body and a response with no
    ``json()`` at all. Never raises.
    """
    try:
        return response.json()
    except Exception:  # not JSON, empty, or no json() at all
        return None


def _is_capabilities(body) -> bool:
    """Return True if ``body`` is the API's capabilities document.

    This is the shape the SDK checks before every run, so a proxy's own JSON
    cannot pass as a verified key. ``wireVersion`` must be a real int: ``True``
    is an int to Python, but not what the API sends.
    """
    if not isinstance(body, dict):
        return False
    if body.get("inputFormat") != "irbf" or body.get("resultFormat") != "irbf":
        return False
    return type(body.get("wireVersion")) is int


def _server_reason(response, api_key: str) -> str | None:
    """Return a short, key-free reason from an error ``response``, or None.

    A body that parses as JSON yields the first non-empty string among
    ``message`` / ``detail`` / ``title`` when it is an object, and nothing
    otherwise; the raw text is never used for valid JSON. Only a body that is
    not JSON at all falls back to its stripped text. Whitespace is collapsed,
    the API key is masked if the server echoed it, and the result is cut to
    ``_REASON_MAX_CHARS``. The key is masked BEFORE the cut so a truncation
    cannot leave part of it behind. A reason containing ``<`` or ``>``
    anywhere is markup (a proxy's HTML page, or a link inside a sentence) and
    is dropped, so server text can never render as rich text in a Qt message
    box, which auto-detects it. The API's own reasons never contain either
    character. Never raises.
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
        # Markup anywhere is dropped: a QMessageBox auto-detects rich text.
        if not reason or "<" in reason or ">" in reason:
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
        ApiKeyCheckRefused: the check was answered with a 403, whatever its
            body (a proxy or firewall can send one, so it proves nothing about
            the key). Callers must not save the key.
        InfraredAPIError: the server does not recognise the key (401).

    Returns:
        bool: True when the key was accepted (a 2xx whose body is the API's
            capabilities document). False when the server could not be reached
            or gave no usable answer: offline, DNS, 5xx, other 4xx, timeout, or
            a 2xx whose body is not what the API sends (any other JSON, HTML,
            empty: a proxy or captive portal answering instead, so nothing was
            verified and a saved key is never overwritten by it).
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
        if not _is_capabilities(_parse_json(r)):
            logger.warning(
                "API key verification got an unexpected HTTP %s answer (not the API's capabilities document); "
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
            raise ApiKeyCheckRefused(status_code=403, server_message=reason) from e
        return False
    except Exception as e:
        logger.warning("API key verification could not complete: %s", e)
        return False


def saved_key_rejected(api_key: str) -> bool:
    """Return True only if the server says it does not recognise ``api_key``.

    For the startup gate, which decides whether an already-saved key is dropped.
    An outage, a timeout or a 403 (which proves nothing about the key) must
    never lock a saved key out of the plugin, so every outcome other than a 401
    returns False.
    """
    try:
        verify_api_key(api_key)
    except ApiKeyCheckRefused as e:
        logger.info("Saved API key kept: HTTP %s, the key check was refused", e.status_code)
        return False
    except InfraredAPIError as e:
        rejected = e.status_code == 401
        logger.info("Saved API key check: HTTP %s, rejected=%s", e.status_code, rejected)
        return rejected
    except Exception as e:  # never lock a key out on an unexpected error
        logger.warning("Saved API key check could not complete: %s", type(e).__name__)
        return False
    return False
