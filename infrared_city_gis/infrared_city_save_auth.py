import os

from qgis.PyQt import uic
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QApplication, QDialog, QLineEdit, QMessageBox

from .exceptions import InfraredAPIError
from .infrared_logger import logger
from .services.fetch_from_registry import fetch_from_registry
from .services.secret_manager import get_api_key, set_api_key

CONTACT_EMAIL = "connectors@infrared.city"

# This loads your .ui file so that PyQt can populate your plugin with the
# elements from Qt Designer.
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), "infrared_city_save_auth.ui"))


class InfraredCitySaveAuthDialog(QDialog, FORM_CLASS):
    """Dialog for saving the user's Infrared City API key into QSettings.

    Storage moved from a plain JSON file (``settings/user.json``) to
    QSettings (platform-native: Windows registry / macOS plist / Linux
    ini). The shared ``services.secret_manager`` module owns the read /
    write side; this dialog just drives the UI.

    The input field uses ``QLineEdit.EchoMode.Password`` echo so the secret is
    masked while typing. We never log the literal value — only metadata
    ("loaded existing key", "saved", etc.).
    """

    def __init__(self, parent=None, saved_key_rejected=False):
        """``saved_key_rejected``: the plugin's startup check got a 401/403
        for the stored key — the dialog then opens with a red "not valid
        anymore" status instead of the green format check, until the user
        types a different key.
        """
        super().__init__(parent)
        self.setupUi(self)

        self._saved_key_rejected = saved_key_rejected
        # The key that was pre-filled from the store; the rejected-key
        # status only applies while the input still shows this value.
        self._loaded_key = ""

        # Mask the secret while the user types — defends against shoulder
        # surfing / accidental screen recording. The .ui file also sets
        # echoMode, but doing it here as well makes the contract obvious
        # to anyone reading the code.
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)

        # Set by accept(): True once the save-time validation call got a
        # 2xx. The dialog only accepts (and only saves the key) when this
        # is True — rejected keys and unreachable-server saves both bail.
        self.key_verified = False

        self.load_existing_api_key()
        self.api_key_input.textChanged.connect(self.update_status)
        self.update_status()

    # -- load / save ---------------------------------------------------

    def load_existing_api_key(self) -> None:
        """Pre-fill the input from the secret store, if a key is saved.

        The status label only reflects whether a value was found — we do
        not display, log, or echo the secret itself.
        """
        try:
            existing = get_api_key()
        except Exception as e:
            logger.error("Error loading existing API key: %s", e)
            self.status_label.setText("Error loading existing API key")
            self.status_label.setStyleSheet("color: red;")
            return

        if existing:
            self._loaded_key = existing
            self.api_key_input.setText(existing)
            logger.info("Existing API key loaded into dialog")
        else:
            logger.info("No existing API key configured")

    def get_api_key_from_input(self) -> str:
        """Return the trimmed value from the input field."""
        return self.api_key_input.text().strip()

    @staticmethod
    def _mask_key(api_key: str) -> str:
        """Return a shoulder-surf-safe preview: first & last 4 chars only.

        Lets the user confirm *which* key is stored without revealing it.
        Keys of 8 chars or fewer are fully masked, since first4+last4 would
        otherwise expose the whole value.
        """
        api_key = api_key.strip()
        if len(api_key) <= 8:
            return "•" * len(api_key)
        return f"{api_key[:4]}…{api_key[-4:]}"

    # -- status label --------------------------------------------------

    def update_status(self) -> None:
        api_key = self.get_api_key_from_input()
        key_known_bad = (
            self._saved_key_rejected and api_key and api_key == self._loaded_key
        )
        if key_known_bad:
            # The stored key was rejected by the server at startup — a green
            # "format looks OK" would be misleading for a key we KNOW is bad.
            # The masked preview stays in: a user screenshotting this dialog
            # for support lets us identify WHICH key they used.
            self.status_label.setText(
                f"Your API key ({self._mask_key(api_key)}) is not valid "
                f"anymore. Please save a new valid API key, or contact us "
                f"at {CONTACT_EMAIL}."
            )
            self.status_label.setStyleSheet("color: red;")
        elif not api_key:
            self.status_label.setText("Please enter an API key")
            self.status_label.setStyleSheet("color: orange;")
        elif len(api_key) < 10:
            self.status_label.setText("API key seems too short")
            self.status_label.setStyleSheet("color: orange;")
        else:
            # Format check only — real validation (a live API call) happens
            # in accept().
            self.status_label.setText(f"API key format looks OK ({self._mask_key(api_key)})")
            self.status_label.setStyleSheet("color: green;")

    # -- accept / reject ----------------------------------------------

    def accept(self) -> None:
        api_key = self.get_api_key_from_input()

        if not api_key:
            QMessageBox.warning(self, "Invalid Input", "Please enter an API key.")
            return

        if len(api_key) < 10:
            reply = QMessageBox.question(
                self, "Short API Key",
                "The API key seems unusually short. Are you sure you want to save it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return

        # Validate the key with a real authenticated call BEFORE saving: the
        # registry refresh doubles as the key check (it needs to run on every
        # key change anyway so trees / colormaps work without a QGIS restart).
        # Only a verified key is saved:
        #   - 2xx        -> key verified, save + accept
        #   - 401 / 403  -> key rejected by the server, do NOT save
        #   - anything else (offline, 5xx, timeout) -> do NOT save either,
        #     but tell the user it was a connection problem, not a bad key.
        # Note this strictness only applies to SAVING a new key — a key that
        # was already saved keeps working through outages (the startup check
        # in infrared_city_gis.py only locks the UI on a confirmed 401/403).
        self.key_verified = False
        self.status_label.setText("Verifying API key…")
        self.status_label.setStyleSheet("color: orange;")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        # The validation call below blocks this thread — repaint first so the
        # "Verifying…" state is actually visible.
        QApplication.processEvents()
        try:
            results = fetch_from_registry(api_key=api_key)
            self.key_verified = any(results.values())
        except InfraredAPIError as e:
            logger.warning("API key validation failed: %s", e)
            self.update_status()
            QMessageBox.critical(
                self, e.title,
                f"{e.detail}\n\n"
                f"The key ({self._mask_key(api_key)}) was NOT saved. "
                f"If you believe the key is correct, please contact us "
                f"at {CONTACT_EMAIL}.",
            )
            return
        except Exception as e:
            logger.warning("Registry refresh during key save failed: %s", e)
        finally:
            QApplication.restoreOverrideCursor()

        if not self.key_verified:
            self.update_status()
            QMessageBox.warning(
                self, "Could Not Verify API Key",
                f"The Infrared server could not be reached, so the API key "
                f"could not be verified.\n\n"
                f"The key was NOT saved. Please check your internet "
                f"connection and try again. If the problem persists, "
                f"please contact us at {CONTACT_EMAIL}.",
            )
            return

        if not set_api_key(api_key):
            self.update_status()
            QMessageBox.critical(
                self, "Error",
                "Failed to save API key. Please check the logs.",
            )
            return

        QMessageBox.information(
            self, "Success",
            f"API key verified and saved successfully!\n"
            f"Stored key: {self._mask_key(api_key)}",
        )
        super().accept()

    def reject(self) -> None:
        super().reject()
