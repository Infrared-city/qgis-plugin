import os

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QLineEdit, QMessageBox

from .infrared_logger import logger
from .services.fetch_from_registry import fetch_from_registry
from .services.secret_manager import get_api_key, set_api_key

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

    The input field uses ``QLineEdit.Password`` echo so the secret is
    masked while typing. We never log the literal value — only metadata
    ("loaded existing key", "saved", etc.).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)

        # Mask the secret while the user types — defends against shoulder
        # surfing / accidental screen recording. The .ui file also sets
        # echoMode, but doing it here as well makes the contract obvious
        # to anyone reading the code.
        self.api_key_input.setEchoMode(QLineEdit.Password)

        self.load_existing_api_key()
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
        if not api_key:
            self.status_label.setText("Please enter an API key")
            self.status_label.setStyleSheet("color: orange;")
        elif len(api_key) < 10:
            self.status_label.setText("API key seems too short")
            self.status_label.setStyleSheet("color: orange;")
        else:
            self.status_label.setText(f"API key looks valid ({self._mask_key(api_key)})")
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
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                return

        if not set_api_key(api_key):
            QMessageBox.critical(
                self, "Error",
                "Failed to save API key. Please check the logs.",
            )
            return

        # Refresh both registries (model + vegetation) with the new key so
        # settings/model_registry.json and settings/vegetation_registry.json
        # are populated immediately. Without this, the user would have to
        # restart QGIS before trees / colormaps would work.
        try:
            fetch_from_registry(api_key=api_key)
        except Exception as e:
            logger.warning("Registry refresh after save failed: %s", e)

        QMessageBox.information(
            self, "Success",
            f"API key saved successfully!\nStored key: {self._mask_key(api_key)}",
        )
        super().accept()

    def reject(self) -> None:
        super().reject()
