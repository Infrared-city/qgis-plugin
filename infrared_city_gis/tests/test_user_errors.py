"""Every failure the user sees says what happened, what to do, and whether it cost.

``describe_error`` classifies by class name and attributes so it keeps working
across SDK versions. That is exactly what can rot silently: an SDK rename turns
a precise message into "Unexpected Error" with no failing import anywhere. So
these tests build the REAL SDK exceptions — a rename fails here instead.
"""

import requests
from infrared_sdk._internal.binary_transport import BinaryPreAcceptError
from infrared_sdk._internal.geodata.overture_area import AreaOvertureReadError
from infrared_sdk.analyses.jobs import (
    AmbiguousSubmitResponseError,
    JobFailedError,
    JobSubmitError,
)
from infrared_sdk.tiling.types import AreaRunError, TiledRunError

from infrared_city_gis.constants import SUPPORT_EMAIL
from infrared_city_gis.exceptions import NothingToRunError
from infrared_city_gis.services.user_errors import describe_error

# The text SDK 0.9.6 raises when the gateway has no binary capability route.
_NOT_ADVERTISED = "This endpoint does not advertise binary transport version 1"


def _capability_error() -> ValueError:
    error = ValueError(_NOT_ADVERTISED)
    error.status_code = 400  # the SDK attaches the gateway's status
    return error


def test_server_without_binary_route_is_a_server_issue_and_free():
    err = describe_error(_capability_error())

    assert err.title == "Server Not Ready"
    assert "server-side issue" in err.summary
    assert err.charged is False
    text = err.message("Single-tile submission")
    assert text.startswith("Single-tile submission failed.")
    assert "No tokens were charged." in text
    # The raw SDK text stays, as the last line support reads.
    assert f"Details: {_NOT_ADVERTISED}" in text


def test_schema_mismatch_reads_as_the_same_server_issue():
    err = describe_error(ValueError(
        "This endpoint reads binary geometry schema(s) [1]; this SDK writes schema 2."
    ))
    assert err.title == "Server Not Ready"
    assert err.charged is False


def test_pre_accept_connection_failure_is_free_and_says_check_the_network():
    exc = BinaryPreAcceptError(requests.ConnectionError("reset"), step="upload")

    err = describe_error(exc)

    assert err.title == "Connection Error"
    assert err.charged is False


def test_rejected_key_on_submit_uses_the_plugins_401_wording():
    exc = JobSubmitError("submit failed", status_code=401, response_body="")

    err = describe_error(exc)

    assert err.title == "Authentication Failed (401)"
    # A 4xx on the paid POST is not provably free: say nothing about tokens.
    assert err.charged is None
    assert "No tokens were charged." not in err.message("Area submission")


def test_unmapped_client_error_is_a_plain_rejection():
    err = describe_error(JobSubmitError("bad", status_code=418, response_body=""))
    assert err.title == "Request Rejected (418)"


def test_server_side_job_failure_shows_the_servers_reason():
    exc = JobFailedError("job failed", job_id="j1", error_message="mesh invalid")

    err = describe_error(exc)

    assert err.title == "Simulation Failed"
    assert err.detail == "mesh invalid"


def test_incomplete_area_merge():
    err = describe_error(AreaRunError("2 tiles missing", failed_jobs=["a", "b"]))
    assert err.title == "Incomplete Result"


def test_uncertain_submission_warns_against_resubmitting():
    exc = AmbiguousSubmitResponseError("2xx without a job", response_body="{}")

    err = describe_error(exc)

    assert err.title == "Submission Not Confirmed"
    assert "Do not resubmit" in err.advice
    assert err.charged is None


def test_plugin_errors_keep_their_own_text_and_empty_area_is_free():
    err = describe_error(NothingToRunError("submission scheduled 0 jobs"))

    assert err.title == NothingToRunError.title
    assert err.summary == NothingToRunError.detail
    assert err.charged is False


def test_anything_else_points_to_support_with_the_raw_text():
    err = describe_error(RuntimeError("kaboom"))

    assert err.title == "Unexpected Error"
    text = err.message("Starting the simulation")
    assert SUPPORT_EMAIL in text
    assert "Details: kaboom" in text
    assert text.endswith("See the plugin log for more.")


def _overture_failure(cause: BaseException) -> TiledRunError:
    """The chain a failed Overture read reaches the plugin as (seen in QGIS)."""
    try:
        try:
            raise cause
        except BaseException as inner:
            raise AreaOvertureReadError(f"the area Overture read failed: {inner}") from inner
    except AreaOvertureReadError as read_error:
        wrapped = TiledRunError("1 of 1 site read chunks failed", failed_tiles=[])
        wrapped.__cause__ = read_error
        return wrapped


def test_a_failed_overture_read_is_named_and_is_free():
    """It used to be "Unexpected Error", which said nothing a user could act on.

    The read sends no API key, so the message must not point at the key, and
    it costs no tokens.
    """
    err = describe_error(_overture_failure(OSError(
        "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/... sent no "
        "ETag, so its reads cannot be pinned with If-Match"
    )))

    assert err.title == "Ground Materials Could Not Be Read"
    assert err.charged is False
    assert "API key" in err.summary and "does not use" in err.summary
    assert "ground-*" in err.advice
    assert "sent no ETag" in err.detail or "site read chunks failed" in err.detail


def test_a_more_specific_cause_under_an_overture_read_still_wins():
    """A missing component or a dead connection says more than "the read failed"."""
    missing = describe_error(_overture_failure(ImportError("no module named pyarrow._s3fs")))
    offline = describe_error(_overture_failure(requests.ConnectionError("unreachable")))

    assert missing.title == "Plugin Component Missing"
    assert offline.title == "Connection Error"

