"""The server's reason text reaches dialogs and logs, so it must be bounded,
key-free and markup-free (#53).
"""

import pytest

from infrared_city_gis.exceptions import ApiKeyCheckRefused, InfraredAPIError
from infrared_city_gis.services import key_check
from infrared_city_gis.tests._key_check_fakes import KEY, MASKED_KEY, problem, use


def test_reason_prefers_message_then_detail_then_title(monkeypatch):
    use(monkeypatch, 401, {"title": "T", "detail": "D", "message": "M"})
    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(KEY)
    assert caught.value.server_message == "M"

    use(monkeypatch, 401, {"title": "T", "detail": "D"})
    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(KEY)
    assert caught.value.server_message == "D"

    use(monkeypatch, 401, {"title": "T", "detail": "  ", "message": 5})
    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(KEY)
    assert caught.value.server_message == "T"


def test_403_with_a_problem_lacking_reason_fields(monkeypatch):
    use(monkeypatch, 403, problem(403, title=" "))

    with pytest.raises(ApiKeyCheckRefused) as caught:
        key_check.verify_api_key(KEY)

    assert caught.value.server_message is None
    assert "Server message" not in caught.value.detail


def test_a_long_reason_is_truncated(monkeypatch):
    use(monkeypatch, 403, problem(403, "x" * 5000))

    with pytest.raises(ApiKeyCheckRefused) as caught:
        key_check.verify_api_key(KEY)

    message = caught.value.server_message
    assert len(message) <= 201
    assert message.endswith("…")


def test_401_with_an_html_body_is_still_a_rejection_without_markup(monkeypatch):
    use(monkeypatch, 401, "<html><body>Sign in</body></html>")

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(KEY)

    assert caught.value.status_code == 401
    assert caught.value.server_message is None
    assert "<" not in caught.value.detail


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"detail": "Please sign in at <a href='http://x'>portal</a>"}, None),
        ("Sign in at <a href=x>portal</a>", None),
        ("value > limit", None),
        ("<b>bold</b>", None),
        (
            {"detail": "Missing or invalid authentication credentials."},
            "Missing or invalid authentication credentials.",
        ),
    ],
    ids=["json-link-mid-sentence", "text-link-mid-sentence", "bare-gt", "leading-tag", "normal-reason-kept"],
)
def test_a_reason_with_markup_anywhere_is_dropped(monkeypatch, body, expected):
    use(monkeypatch, 401, body)

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(KEY)

    assert caught.value.status_code == 401
    assert caught.value.server_message == expected


def test_a_403_problem_with_markup_in_detail_is_still_refused_without_a_reason(monkeypatch):
    use(monkeypatch, 403, problem(403, "Upgrade at <a href='http://x'>portal</a>"))

    with pytest.raises(ApiKeyCheckRefused) as caught:
        key_check.verify_api_key(KEY)

    assert caught.value.server_message is None
    assert "Server message" not in caught.value.detail


def test_401_with_a_plain_text_body_uses_the_text(monkeypatch):
    use(monkeypatch, 401, "Unauthorized")

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(KEY)

    assert caught.value.server_message == "Unauthorized"


@pytest.mark.parametrize("body", ["null", "5", '["a"]', '"text"'], ids=["null", "number", "list", "string"])
def test_401_with_json_that_is_not_an_object_has_no_reason(monkeypatch, body):
    use(monkeypatch, 401, body)

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(KEY)

    assert caught.value.status_code == 401
    assert caught.value.server_message is None


def test_401_with_a_json_object_lacking_reason_fields_has_no_reason(monkeypatch):
    use(monkeypatch, 401, {"status": 401})

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(KEY)

    assert caught.value.server_message is None


def test_an_echoed_key_is_masked_everywhere(monkeypatch):
    use(monkeypatch, 401, {"message": f"bad key {KEY}"})

    with pytest.raises(InfraredAPIError) as caught:
        key_check.verify_api_key(KEY)

    exc = caught.value
    assert exc.server_message == f"bad key {MASKED_KEY}"
    for text in (exc.server_message, str(exc), exc.detail):
        assert KEY not in text


def test_a_key_straddling_the_truncation_point_does_not_leak(monkeypatch):
    # The key starts 195 characters in, so a cut at 200 would split it.
    use(monkeypatch, 403, problem(403, "x" * 195 + KEY + "y" * 50))

    with pytest.raises(ApiKeyCheckRefused) as caught:
        key_check.verify_api_key(KEY)

    message = caught.value.server_message
    assert KEY[:8] not in message
    assert KEY[-8:] not in message


def test_a_short_echoed_key_is_fully_hidden(monkeypatch):
    use(monkeypatch, 403, problem(403, "bad key abc123"))

    with pytest.raises(ApiKeyCheckRefused) as caught:
        key_check.verify_api_key("abc123")

    assert caught.value.server_message == "bad key ••••••"


def test_a_proxys_403_reason_is_still_carried(monkeypatch):
    use(monkeypatch, 403, {"message": f"Blocked by policy for {KEY}"})

    with pytest.raises(ApiKeyCheckRefused) as caught:
        key_check.verify_api_key(KEY)

    assert caught.value.server_message == f"Blocked by policy for {MASKED_KEY}"
    assert KEY not in caught.value.detail
