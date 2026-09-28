"""Verify an Infrared API key against the server.

This used to be a side effect of the registry refresh: the registries were read
from the utilities service with the key attached, so a 401 there meant a bad
key. The registries moved to a public mirror that takes no credentials
(``constants.REGISTRY_DOCUMENTS``), which left the two callers that depended on
the auth half — the key-save dialog and the startup gate — with nothing to
check, so the check lives here on its own now.

``GET /v2/webhooks`` is the probe: an authenticated route that lists rather than
creates, so it costs nothing, has no side effects, and answers 401/403 for a key
the server rejects. It goes through ``services.qgis_http`` like every other
direct call, so it honours the proxy configured in QGIS Options → Network.

Three outcomes, matching what the callers have always distinguished:

    verified    -> True
    rejected    -> InfraredAPIError with status_code 401 or 403
    unreachable -> False

The last one is deliberately not an exception. An outage must not be presented
to the user as a bad key, and it must not lock an already-saved key out of the
plugin.
"""

from __future__ import annotations

from ..constants import VERIFY_API_KEY_URL
from ..exceptions import InfraredAPIError
from ..infrared_logger import logger
from ..utils.client_identity import client_headers
from . import qgis_http as requests

#: Short read budget: this is a small list response, and both callers block the
#: UI thread while it runs (key save shows a wait cursor, startup delays the
#: toolbar). A slow network should fall through to "unreachable" quickly.
_VERIFY_TIMEOUT_SEC = 10


def verify_api_key(api_key: str) -> bool:
    """Return True if the server accepts ``api_key``.

    Raises:
        InfraredAPIError: the server rejected the key (401 / 403). Carries the
            status code so a caller can tell the two apart.

    Returns:
        bool: True when the key was accepted, False when the server could not
            be reached at all (offline, DNS, 5xx, timeout, malformed response).
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
        logger.warning("API key verification failed: HTTP %s", status)
        if status in (401, 403):
            raise InfraredAPIError(status_code=status) from e
        return False
    except Exception as e:
        logger.warning("API key verification could not complete: %s", e)
        return False
