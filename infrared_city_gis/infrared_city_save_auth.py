import os
import json
from qgis.PyQt.QtWidgets import QDialog, QMessageBox
from qgis.core import QgsApplication
from qgis.PyQt import uic
from .infrared_logger import logger
from .services.fetch_from_registry import fetch_from_registry


# This loads your .ui file so that PyQt can populate your plugin with the elements from Qt Designer
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), "infrared_city_save_auth.ui"))

class InfraredCitySaveAuthDialog(QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        """Constructor."""
        super().__init__(parent)
        self.setupUi(self)
        
        # Load existing API key if available
        self.load_existing_api_key()
        
        # Connect signals
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        
        # Set initial status
        self.update_status()
    
    def load_existing_api_key(self):
        """Load existing API key from user.json if it exists."""
        try:
            plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "settings")
            user_file = os.path.join(plugin_data_dir, "user.json")
            
            if os.path.exists(user_file):
                with open(user_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                api_key = data.get("api-key", "")
                if api_key:
                    self.api_key_input.setText(api_key)
                    logger.info("Existing API key loaded")
                else:
                    logger.info("No API key found in user.json")
            else:
                logger.info("user.json does not exist")
                
        except Exception as e:
            logger.error(f"Error loading existing API key: {e}")
            self.status_label.setText("Error loading existing API key")
            self.status_label.setStyleSheet("color: red;")
    
    def update_status(self):
        """Update status label based on current input."""
        api_key = self.api_key_input.text().strip()
        
        if not api_key:
            self.status_label.setText("Please enter an API key")
            self.status_label.setStyleSheet("color: orange;")
        elif len(api_key) < 10:
            self.status_label.setText("API key seems too short")
            self.status_label.setStyleSheet("color: orange;")
        else:
            self.status_label.setText("API key looks valid")
            self.status_label.setStyleSheet("color: green;")
    
    def get_api_key(self):
        """Get the current API key from input."""
        return self.api_key_input.text().strip()
    
    def save_api_key(self, api_key):
        """Save API key to user.json file."""
        try:
            # Ensure settings directory exists
            plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "settings")
            os.makedirs(plugin_data_dir, exist_ok=True)
            
            user_file = os.path.join(plugin_data_dir, "user.json")
            
            # Load existing data or create new
            if os.path.exists(user_file):
                with open(user_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {}
            
            # Update API key
            data["api-key"] = api_key
            
            # Save to file
            with open(user_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"API key saved to {user_file}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving API key: {e}")
            return False
    
    def accept(self):
        """Override accept to validate and save API key."""
        api_key = self.get_api_key()
        
        if not api_key:
            QMessageBox.warning(self, "Invalid Input", "Please enter an API key.")
            return
        
        if len(api_key) < 10:
            reply = QMessageBox.question(
                self, 
                "Short API Key", 
                "The API key seems unusually short. Are you sure you want to save it?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return
        
        # Save API key
        if self.save_api_key(api_key):
            # Refresh both registries (model + vegetation) with the new key so
            # settings/model_registry.json and settings/vegetation_registry.json
            # are populated immediately. Without this, the customer would have
            # to restart QGIS before trees / colormaps would work.
            try:
                fetch_from_registry(api_key=api_key)
            except Exception as e:
                logger.warning("Registry refresh after save failed: %s", e)
            QMessageBox.information(self, "Success", "API key saved successfully!")
            super().accept()
        else:
            QMessageBox.critical(self, "Error", "Failed to save API key. Please check the logs.")
    
    def reject(self):
        """Override reject to handle cancellation."""
        super().reject()
