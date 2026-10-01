"""Turn an exception into a message a QGIS user can act on.

The SDK's own messages are written for developers — "This endpoint does not
advertise binary transport version 1" — and shown raw in a dialog they tell
the user neither what happened, nor what to do next, nor whether a token was
spent. Every user-facing failure goes through :func:`describe_error`, which
answers those three questions in plain words and keeps the raw message as the
last "Details" line, because that line is what support needs.

Classification is by class NAME and duck-typed attributes, never by importing
the SDK's private modules: the plugin runs against whatever SDK version the
user's deps folder holds, and an ``infrared_sdk._internal`` import that moved
would break the error path itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from qgis.core import Qgis
from qgis.PyQt.QtWidgets import QApplication, QMessageBox
from qgis.utils import iface

from ..constants import SUPPORT_EMAIL
from ..exceptions import InfraredAPIError

_LOG_HINT = "See the plugin log for more."
_RETRY_OR_SUPPORT = (
    f"Please try again. If it keeps happening, contact {SUPPORT_EMAIL} "
    "and attach the plugin log."
)


@dataclass(frozen=True)
class UserError:
    """What a failure means to the user, plus the raw text for support."""

    title: str
    summary: str
    advice: str = ""
    #: ``False`` only when the failure provably happened before the paid
    #: request; ``None`` means "cannot tell" and says nothing about tokens.
    charged: Optional[bool] = None
    detail: str = ""

    def message(self, action: str) -> str:
        """Full dialog text: what failed, why, what to do, then the raw cause."""
        parts = [f"{action} failed.", self.summary, self.advice]
        if self.charged is False:
            parts.append("No tokens were charged.")
        if self.detail:
            parts.append(f"Details: {self.detail}")
        parts.append(_LOG_HINT)
        return "\n\n".join(p for p in parts if p)


def _class_names(exc: BaseException) -> set:
    return {cls.__name__ for cls in type(exc).__mro__}


def _cause_chain(exc: BaseException) -> list:
    """*exc*, then what it wraps: the SDK's ``.error`` and every ``__cause__``.

    The SDK wraps the real cause one to three levels deep — a missing pyarrow
    S3 build reaches the plugin as ``TiledRunError`` from
    ``AreaOvertureReadError`` from ``ImportError`` — so the outermost class
    alone says almost nothing about what to do.
    """
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        wrapped = getattr(current, "error", None)
        current = (
            wrapped if isinstance(wrapped, BaseException) else current.__cause__
        )
    return chain


def _is_connection_error(exc: BaseException) -> bool:
    # requests' ConnectionError/Timeout, and the stdlib ones under them.
    return bool(_class_names(exc) & {
        "ConnectionError", "Timeout", "ConnectTimeout", "ReadTimeout",
        "TimeoutError",
    })


def _server_not_ready(text: str) -> bool:
    # NOTE: the SDK raises a bare ValueError for these (binary_capability.py),
    # so the text is the only thing to match. Both mean the gateway is older
    # than the SDK: no /binary/v1/capabilities route, or no geometry schema
    # the SDK writes. Revisit if the SDK grows a typed error for it.
    markers = ("does not advertise binary transport", "reads binary geometry schema")
    return any(marker in text for marker in markers)


def failed_on_server(detail: str) -> UserError:
    """A job the server accepted and then reported as failed."""
    return UserError(
        "Simulation Failed",
        "The simulation failed on the Infrared City server.",
        _RETRY_OR_SUPPORT, detail=detail,
    )


def run_timed_out(detail: str) -> UserError:
    """The plugin stopped waiting; the server may still finish the job."""
    return UserError(
        "Simulation Timed Out",
        "The simulation did not finish within the time the plugin waits.",
        "The jobs may still complete on the server. Try a smaller area, "
        "or try again later.",
        detail=detail,
    )


def describe_error(exc: BaseException) -> UserError:
    """Translate *exc* into a :class:`UserError`. Never raises."""
    detail = str(exc) or type(exc).__name__
    names = _class_names(exc)
    # A failure before the paid POST cannot have created a job (SDK D59).
    pre_accept = getattr(exc, "pre_accept", False) is True
    charged = False if pre_accept else None
    chain = _cause_chain(exc)

    has_user_text = all(
        isinstance(getattr(exc, attr, None), str) for attr in ("title", "detail")
    )
    if isinstance(exc, InfraredAPIError) or has_user_text:
        # The plugin's own errors already carry user-facing text.
        return UserError(exc.title, exc.detail, charged=charged)

    if any(_server_not_ready(str(e)) for e in chain):
        return UserError(
            "Server Not Ready",
            "The Infrared City server does not support the simulation format "
            "this plugin version uses yet. This is a server-side issue, not a "
            "problem with your data or your API key.",
            f"Please try again later, or contact {SUPPORT_EMAIL}.",
            charged=False, detail=detail,
        )

    if "UnsafeSubmissionError" in names:
        return UserError(
            "Submission Not Confirmed",
            "The server may have accepted the simulation, but its reply could "
            "not be confirmed.",
            "Do not resubmit straight away — a second run could be charged "
            f"again. Check your runs on infrared.city or contact {SUPPORT_EMAIL}.",
            detail=detail,
        )

    if any(isinstance(e, ImportError) for e in chain):
        return UserError(
            "Plugin Component Missing",
            "A component the plugin needs could not be loaded, so this step "
            "cannot run on this computer.",
            "Restart QGIS. If it keeps happening, reinstall the plugin and "
            f"send the plugin log to {SUPPORT_EMAIL}.",
            charged=charged, detail=detail,
        )

    if any(_is_connection_error(e) for e in chain):
        return UserError(
            "Connection Error",
            "A network request failed, so the plugin could not reach the "
            "data it needs.",
            "Please check your internet connection and try again.",
            charged=charged, detail=detail,
        )

    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 400:
        if status in InfraredAPIError._STATUS_MAP or status >= 500:
            # Same wording the plugin's direct HTTP calls use for that status.
            api = InfraredAPIError(status_code=status)
            return UserError(api.title, api.detail, charged=charged, detail=detail)
        return UserError(
            f"Request Rejected ({status})",
            "The Infrared City server rejected the request.",
            _RETRY_OR_SUPPORT, charged=charged, detail=detail,
        )

    if "JobFailedError" in names:
        return failed_on_server(getattr(exc, "error_message", None) or detail)

    if names & {"JobTimeoutError", "AreaTimeoutError"}:
        return run_timed_out(detail)

    if "AreaRunError" in names:
        return UserError(
            "Incomplete Result",
            "Some tiles of the area did not return a result, so no complete "
            "map could be built.",
            _RETRY_OR_SUPPORT, detail=detail,
        )

    return UserError(
        "Unexpected Error",
        "Something went wrong that the plugin did not expect.",
        _RETRY_OR_SUPPORT, charged=charged, detail=detail,
    )


def show_error_dialog(parent, action: str, exc: BaseException) -> None:
    """Modal dialog for a failure the user is waiting on (dialog still open)."""
    err = describe_error(exc)
    QMessageBox.critical(parent, err.title, err.message(action))


def push_error(action: str, err: UserError, duration: int = 0) -> None:
    """Message-bar notice for a failure that happens after the dialog closed.

    ``duration=0`` keeps it until dismissed: a run fails minutes after the
    user moved on, and a 15-second toast is gone before they look back. The
    bar shows the one-line summary; "More" opens the full text.
    """
    bar = iface.messageBar() if iface else None
    if bar is None:
        return
    bar.clearWidgets()
    bar.pushMessage(
        "InfraredCity", f"{action} failed — {err.summary}", err.message(action),
        level=Qgis.MessageLevel.Critical, duration=duration,
    )
    QApplication.processEvents()
