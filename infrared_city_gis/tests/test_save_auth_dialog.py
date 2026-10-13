"""Saving an API key: what the dialog does with each answer of the key check.

A 403 from the check means the server recognised the key but refused that
request (#53), so the key is saved and the user gets a note; only a 401 means
the key is unknown. These tests drive the real dialog's ``accept()`` with the
network call, the key store and the message boxes replaced, and pin which of
those the user gets for 2xx / 403 / 401 / unreachable.
"""

import pytest

from infrared_city_gis.exceptions import ApiKeyRestricted, InfraredAPIError

pytestmark = pytest.mark.qgis

KEY = "abcd1234efgh5678"
MASKED = "abcd…5678"


class _FakeMessageBox:
    """Records ``(title, text)`` for every box the dialog would have shown."""

    class StandardButton:
        Yes = 1
        No = 2

    information_calls = []
    warning_calls = []
    critical_calls = []
    question_calls = []

    @classmethod
    def reset(cls):
        for calls in (cls.information_calls, cls.warning_calls,
                      cls.critical_calls, cls.question_calls):
            calls.clear()

    @classmethod
    def information(cls, parent, title, text, *args):
        cls.information_calls.append((title, text))

    @classmethod
    def warning(cls, parent, title, text, *args):
        cls.warning_calls.append((title, text))

    @classmethod
    def critical(cls, parent, title, text, *args):
        cls.critical_calls.append((title, text))

    @classmethod
    def question(cls, parent, title, text, *args):
        cls.question_calls.append((title, text))
        return cls.StandardButton.Yes


@pytest.fixture
def harness(qgis_app, monkeypatch):
    """Build the real dialog; return ``(make_dialog, boxes, saved)``."""
    from infrared_city_gis import infrared_city_save_auth as mod

    _FakeMessageBox.reset()
    saved = []
    state = {"save_ok": True, "verify": lambda key: True}

    def set_api_key(key):
        saved.append(key)
        return state["save_ok"]

    # Patched before the dialog is built, so nothing is pre-filled from the
    # developer's real QSettings.
    monkeypatch.setattr(mod, "get_api_key", lambda: "")
    monkeypatch.setattr(mod, "set_api_key", set_api_key)
    monkeypatch.setattr(mod, "verify_api_key", lambda key: state["verify"](key))
    monkeypatch.setattr(mod, "QMessageBox", _FakeMessageBox)

    def make_dialog(text=KEY, *, verify=None, save_ok=True):
        if verify is not None:
            state["verify"] = verify
        state["save_ok"] = save_ok
        dialog = mod.InfraredCitySaveAuthDialog()
        dialog.api_key_input.setText(text)
        return dialog

    return make_dialog, _FakeMessageBox, saved


def _raises(exc):
    def verify(key):
        raise exc
    return verify


def test_a_2xx_saves_the_key_and_says_success(harness):
    make_dialog, boxes, saved = harness
    dialog = make_dialog(verify=lambda key: True)

    dialog.accept()

    assert saved == [KEY]
    assert dialog.key_verified is True
    assert len(boxes.information_calls) == 1
    title, text = boxes.information_calls[0]
    assert title == "Success"
    assert MASKED in text
    assert KEY not in text
    assert not boxes.critical_calls and not boxes.warning_calls


def test_a_403_saves_the_key_and_shows_the_limited_access_note(harness):
    make_dialog, boxes, saved = harness
    dialog = make_dialog(verify=_raises(
        ApiKeyRestricted(status_code=403, server_message="Forbidden")))

    dialog.accept()

    assert saved == [KEY]
    assert dialog.key_verified is True
    assert len(boxes.information_calls) == 1
    title, text = boxes.information_calls[0]
    assert title == ApiKeyRestricted.KEY_CHECK_TITLE
    assert "Forbidden" in text
    assert MASKED in text
    assert KEY not in text
    assert not boxes.critical_calls and not boxes.warning_calls


def test_a_401_does_not_save_and_shows_the_server_message(harness):
    make_dialog, boxes, saved = harness
    message = "Missing or invalid authentication credentials."
    dialog = make_dialog(verify=_raises(
        InfraredAPIError(status_code=401, server_message=message)))

    dialog.accept()

    assert saved == []
    assert dialog.key_verified is False
    assert len(boxes.critical_calls) == 1
    _, text = boxes.critical_calls[0]
    assert "NOT saved" in text
    assert message in text
    assert KEY not in text
    assert not boxes.information_calls


def test_an_unreachable_server_does_not_save(harness):
    make_dialog, boxes, saved = harness
    dialog = make_dialog(verify=lambda key: False)

    dialog.accept()

    assert saved == []
    assert dialog.key_verified is False
    assert [title for title, _ in boxes.warning_calls] == ["Could Not Verify API Key"]
    assert not boxes.information_calls and not boxes.critical_calls


def test_a_403_whose_save_fails_reports_the_failure_not_the_note(harness):
    make_dialog, boxes, saved = harness
    dialog = make_dialog(save_ok=False, verify=_raises(
        ApiKeyRestricted(status_code=403, server_message="Forbidden")))

    dialog.accept()

    assert saved == [KEY]
    assert [title for title, _ in boxes.critical_calls] == ["Could Not Save API Key"]
    assert not boxes.information_calls


def test_the_input_is_trimmed_before_it_is_verified_and_saved(harness):
    make_dialog, boxes, saved = harness
    verified = []

    def verify(key):
        verified.append(key)
        return True

    dialog = make_dialog(f"  {KEY}  ", verify=verify)

    dialog.accept()

    assert verified == [KEY]
    assert saved == [KEY]
