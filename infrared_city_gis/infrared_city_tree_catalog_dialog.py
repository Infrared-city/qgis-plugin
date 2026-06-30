# -*- coding: utf-8 -*-
"""
/***************************************************************************
 InfraredCityTreeCatalogDialog
                                 A QGIS plugin
        copyright            : (C) 2025 by infrared.city
        email                : connectors@infrared.city
 ***************************************************************************/

 Simplified, ArcGIS-style tree catalog: a species dropdown + Small/Medium/Large
 radio buttons + an info label showing the height / crown diameter for the
 current choice. Species and dimensions are read from
 ``vegetation_registry.json`` (``clientModels``). The selection is persisted to
 the same QSettings keys the run-time vegetation collector reads:
 ``infrared_city/tree_type`` (species display name) and
 ``infrared_city/tree_size`` (``small`` | ``medium`` | ``large``) — so
 ``services/qgis_area_vegetation`` keeps working unchanged.
"""

import json
import os

from qgis.core import QgsApplication
from qgis.PyQt import QtWidgets, uic
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QRadioButton,
    QWidget,
)

from .infrared_logger import logger
from .models.vegetation_types import TreeType

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'infrared_city_tree_catalog_dialog.ui'))

_SIZES = ("small", "medium", "large")


def _resolve_registry_path() -> str:
    """Path to the active ``vegetation_registry.json`` (populated on API-key save)."""
    return os.path.join(
        QgsApplication.qgisSettingsDirPath(),
        "infrared_city_gis", "settings", "vegetation_registry.json",
    )


def _load_species() -> list:
    """Return ``[(display_name, entry_dict)]`` from the registry.

    Falls back to the bundled :class:`TreeType` names (with empty entries) when
    the registry is missing — e.g. before the user has saved an API key.
    """
    try:
        with open(_resolve_registry_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        models = data.get("clientModels", {}) or {}
        species = [
            (entry["displayName"], entry)
            for entry in models.values()
            if isinstance(entry, dict) and entry.get("displayName")
        ]
        if species:
            return species
    except Exception as e:
        logger.warning("Tree catalog: could not load registry (%s); using defaults", e)
    return [(tt.value, {}) for tt in TreeType]


def _dims_for_size(entry: dict, size: str):
    """Return (height, crown_diameter) for ``size`` from a registry entry.

    Mirrors ``services/qgis_area_vegetation``: small/large use the ends of
    ``heightRange`` / ``crownDiameterRange`` when present, medium uses the
    registry's default ``height`` / ``crownDiameter``.
    """
    hr = entry.get("heightRange")
    cr = entry.get("crownDiameterRange")
    h_def = entry.get("height")
    c_def = entry.get("crownDiameter")
    if size == "small":
        h = hr[0] if hr else h_def
        c = cr[0] if cr else c_def
    elif size == "large":
        h = hr[-1] if hr else h_def
        c = cr[-1] if cr else c_def
    else:
        h = h_def
        c = c_def
    return h, c


class InfraredCityTreeCatalogDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.setWindowTitle("Tree Catalog")
        self.resize(420, 220)

        self._species = _load_species()  # [(display_name, entry)]
        self.selected_tree_type = None
        self.selected_tree_size = None

        self._build_ui()
        self._restore_selection()
        self._update_info()
        logger.info("Tree catalog dialog loaded (%d species)", len(self._species))

    # ------------------------------------------------------------------

    def _build_ui(self):
        """Replace the .ui formWidget with a species combo + size radios + info."""
        layout = self.layout()
        old_widget = getattr(self, "formWidget", None)
        if old_widget is not None:
            layout.removeWidget(old_widget)
            old_widget.setParent(None)

        container = QWidget()
        form = QFormLayout(container)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        self.species_combo = QComboBox()
        for display_name, _entry in self._species:
            self.species_combo.addItem(display_name)
        self.species_combo.currentIndexChanged.connect(self._update_info)
        form.addRow("Species:", self.species_combo)

        size_row = QWidget()
        size_layout = QtWidgets.QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(12)
        self._size_group = QButtonGroup(self)
        self._size_buttons = {}
        for size in _SIZES:
            rb = QRadioButton(size.capitalize())
            self._size_buttons[size] = rb
            self._size_group.addButton(rb)
            size_layout.addWidget(rb)
        size_layout.addStretch()
        self._size_buttons["medium"].setChecked(True)
        self._size_group.buttonClicked.connect(self._update_info)
        form.addRow("Size:", size_row)

        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #2e6e2e;")
        form.addRow("", self.info_label)

        layout.insertWidget(0, container)

    def _current_size(self) -> str:
        for size, rb in self._size_buttons.items():
            if rb.isChecked():
                return size
        return "medium"

    def _current_entry(self) -> dict:
        idx = self.species_combo.currentIndex()
        if 0 <= idx < len(self._species):
            return self._species[idx][1]
        return {}

    def _update_info(self, *args):
        h, c = _dims_for_size(self._current_entry(), self._current_size())
        h_txt = f"{h} m" if h not in (None, 0) else "—"
        c_txt = f"{c} m" if c not in (None, 0) else "—"
        self.info_label.setText(f"Height: {h_txt}    Crown diameter: {c_txt}")

    # ------------------------------------------------------------------

    def _restore_selection(self):
        settings = QSettings()
        saved_type = settings.value("infrared_city/tree_type", None)
        saved_size = settings.value("infrared_city/tree_size", None)

        if saved_type:
            idx = self.species_combo.findText(saved_type)
            if idx >= 0:
                self.species_combo.setCurrentIndex(idx)
        if saved_size in self._size_buttons:
            self._size_buttons[saved_size].setChecked(True)

    # ------------------------------------------------------------------

    def accept(self):
        try:
            self.selected_tree_type = self.species_combo.currentText()
            self.selected_tree_size = self._current_size()
            if not self.selected_tree_type:
                QMessageBox.warning(self, "No selection", "Please select a tree species.")
                return

            settings = QSettings()
            settings.setValue("infrared_city/tree_type", self.selected_tree_type)
            settings.setValue("infrared_city/tree_size", self.selected_tree_size)

            logger.info(
                "Tree catalog selection — type: %s, size: %s",
                self.selected_tree_type, self.selected_tree_size,
            )
            QMessageBox.information(
                self, "Selection saved",
                f"Tree species:  {self.selected_tree_type}\n"
                f"Size:          {self.selected_tree_size.capitalize()}",
            )
        except Exception as e:
            logger.error("Error saving tree catalog selection: %s", e, exc_info=True)
            QMessageBox.critical(self, "Error", str(e))
            return

        super().accept()
