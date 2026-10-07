"""
Custom exceptions for the Infrared City GIS plugin.
"""

from .constants import SUPPORT_EMAIL


class NothingToRunError(Exception):
    """An area submission that scheduled no jobs: nothing ran, nothing was charged."""

    title = "Nothing to Simulate"
    detail = (
        "The selected area produced no simulation tiles.\n\n"
        "Check that the area contains buildings and that the buildings layer "
        "is active, then try again."
    )
    #: Read by ``services.user_errors``: no request reached the paid step.
    pre_accept = True


class InfraredAPIError(Exception):
    """Raised when the Infrared City API returns a non-2xx HTTP response or is unreachable.

    Attributes
    ----------
    status_code : int or None
        The HTTP status code, or None for connection-level errors.
    server_message : str or None
        Optional message parsed from the response body (``message`` JSON field).
    title : str
        Short, human-readable title suitable for a dialog window title.
    detail : str
        Longer description with actionable advice for the user.
    """

    _STATUS_MAP = {
        401: (
            "Authentication Failed (401)",
            "Your API key was not recognised by the Infrared City server.\n\n"
            "Please check that the key is entered correctly in the plugin settings.",
        ),
        403: (
            "Access Denied (403)",
            "Your API key does not have permission to perform this action.\n\n"
            "Your subscription plan may not include this analysis type. "
            "Visit infrared.city to review your plan or contact "
            f"{SUPPORT_EMAIL} for help.",
        ),
        429: (
            "Too Many Requests (429)",
            "You have exceeded the request limit for your plan.\n\n"
            "Please wait a moment before running another simulation.",
        ),
        500: (
            "Server Error (500)",
            "The Infrared City server encountered an unexpected error.\n\n"
            "Please try again in a few minutes. If the problem persists, "
            f"contact {SUPPORT_EMAIL}.",
        ),
        502: (
            "Server Unavailable (502)",
            "The Infrared City server is temporarily unavailable.\n\n"
            "Please try again in a few minutes.",
        ),
        503: (
            "Service Unavailable (503)",
            "The Infrared City service is temporarily unavailable.\n\n"
            "Please try again in a few minutes.",
        ),
        504: (
            "Gateway Timeout (504)",
            "The request timed out waiting for the Infrared City server.\n\n"
            "Please try again in a few minutes.",
        ),
    }

    def __init__(self, status_code=None, server_message=None):
        self.status_code = status_code
        self.server_message = server_message

        if status_code in self._STATUS_MAP:
            self.title, self.detail = self._STATUS_MAP[status_code]
        elif status_code is not None and status_code >= 500:
            self.title = f"Server Error ({status_code})"
            self.detail = (
                f"The Infrared City server returned an unexpected error (HTTP {status_code}).\n\n"
                "Please try again in a few minutes. If the problem persists, "
                f"contact {SUPPORT_EMAIL}."
            )
        elif status_code is not None:
            self.title = f"Request Failed ({status_code})"
            self.detail = (
                f"The request failed with HTTP status {status_code}.\n\n"
                "Check the plugin log for more details."
            )
        else:
            self.title = "Connection Error"
            self.detail = (
                "Could not reach the Infrared City server.\n\n"
                "Please check your internet connection and try again."
            )

        # Append the server's own message if it adds useful context
        if server_message:
            self.detail += f"\n\nServer message: {server_message}"

        super().__init__(f"[HTTP {status_code}] {self.title}")
