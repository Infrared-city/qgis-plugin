"""
Custom exceptions for the Infrared City GIS plugin.
"""


class InfraredAPIError(Exception):
    """Raised when the Infrared API returns a non-2xx HTTP response or is unreachable.

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
            "Your API key was not recognised by the Infrared server.\n\n"
            "Please check that the key is entered correctly in the plugin settings.",
        ),
        403: (
            "Access Denied (403)",
            "Your API key does not have permission to perform this action.\n\n"
            "Your subscription plan may not include this analysis type. "
            "Visit infrared.city to review your plan or contact "
            "support@infrared.city for help.",
        ),
        429: (
            "Too Many Requests (429)",
            "You have exceeded the request limit for your plan.\n\n"
            "Please wait a moment before running another simulation.",
        ),
        500: (
            "Server Error (500)",
            "The Infrared server encountered an unexpected error.\n\n"
            "Please try again in a few minutes. If the problem persists, "
            "contact support@infrared.city.",
        ),
        502: (
            "Server Unavailable (502)",
            "The Infrared server is temporarily unavailable.\n\n"
            "Please try again in a few minutes.",
        ),
        503: (
            "Service Unavailable (503)",
            "The Infrared service is temporarily unavailable.\n\n"
            "Please try again in a few minutes.",
        ),
        504: (
            "Gateway Timeout (504)",
            "The request timed out waiting for the Infrared server.\n\n"
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
                f"The Infrared server returned an unexpected error (HTTP {status_code}).\n\n"
                "Please try again in a few minutes. If the problem persists, "
                "contact support@infrared.city."
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
                "Could not reach the Infrared server.\n\n"
                "Please check your internet connection and try again."
            )

        # Append the server's own message if it adds useful context
        if server_message:
            self.detail += f"\n\nServer message: {server_message}"

        super().__init__(f"[HTTP {status_code}] {self.title}")
