# -*- coding: utf-8 -*-
"""
/***************************************************************************
 InfraredCityFetchGroundMaterialsDialog
                                 A QGIS plugin
        copyright            : (C) 2026 by infrared.city
        email                : connectors@infrared.city
 ***************************************************************************/

 Fetch ground-material layers (asphalt, concrete, vegetation, soil, water,
 building) for the current building-layer selection and add them to the
 project as editable ``ground-<material>`` vector layers.

 Flow mirrors the Run Simulation dialog's selection handling: the selection
 polygon comes from ``create_wgs84_geojson_polygon_from_selection`` and is
 previewed with ``client.preview_area`` so the user sees the tile count
 before committing; areas over the tile cap are rejected with a
 "select a smaller area" message (the SDK enforces the same 100-tile limit
 internally, so the two can never disagree).
"""

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from .infrared_logger import logger
from .services import single_tile_selection
from .services.polygon_from_selection import (
    create_wgs84_geojson_polygon_from_selection,
)
from .services.secret_manager import get_api_key
from .visualization.layers import display_ground_materials

# Same cap as the Run Simulation dialog — and the SDK's own
# MAX_NON_EMPTY_TILES, so the plugin-side gate always fires first.
_MAX_TILES = 100


class InfraredCityFetchGroundMaterialsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fetch Ground Materials")
        self.setMinimumSize(460, 220)

        self.polygon = None
        self.tile_count = None
        self.created_layers = {}
        self._init_ok = False

        layout = QVBoxLayout(self)
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #555;")
        layout.addWidget(self.status_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Fetch")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self._prepare()

    # ------------------------------------------------------------------

    def _set_fetch_enabled(self, enabled: bool):
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(enabled)

    def _prepare(self):
        """Validate API key + selection and preview the tile count."""
        self.api_key = get_api_key()
        if not self.api_key:
            QMessageBox.warning(
                self, "No API Key",
                "Fetching ground materials requires an Infrared City API key.\n"
                "Please save your API key first (Save API Key).",
            )
            return

        # A pending "Select tile" pick takes precedence — peek (don't
        # consume) so a Run Simulation opened afterwards still enters
        # single-tile mode. It's exactly one tile, mirroring the sim dialog.
        _tile_sel = single_tile_selection.peek()
        if _tile_sel is not None:
            self.polygon = _tile_sel.polygon
            self.tile_count = 1
        else:
            self.polygon = create_wgs84_geojson_polygon_from_selection()
            if self.polygon is None:
                QMessageBox.warning(
                    self, "No selection",
                    "Please select a building area first — select features on "
                    "your building layer, then reopen this dialog.",
                )
                return

            try:
                from infrared_sdk import InfraredClient

                client = InfraredClient(api_key=self.api_key)
                preview = client.preview_area(self.polygon)
                self.tile_count = preview.tile_count
            except Exception as e:
                logger.exception("Ground materials: preview_area failed: %s", e)
                QMessageBox.warning(
                    self, "Error",
                    f"Could not compute the selection area.\n\n{e}",
                )
                return

        logger.info("Ground materials fetch: selection = %d tile(s)", self.tile_count)
        if self.tile_count > _MAX_TILES:
            self.info_label.setText(
                f"The selected area is too large (~{self.tile_count} tiles, "
                f"the maximum is {_MAX_TILES}). "
                f"Please select a smaller area and reopen this dialog."
            )
            self._set_fetch_enabled(False)
            self._init_ok = True
            return

        self.info_label.setText(
            f"Selected area: {self.tile_count} tile"
            f"{'s' if self.tile_count != 1 else ''}.\n\n"
            f"Fetching adds one editable 'ground-<material>' layer per "
            f"surface type (asphalt, concrete, vegetation, soil, water, "
            f"building). Note: 'ground-vegetation' is green surfaces (grass, "
            f"parks) — trees are separate 'tree-*' point layers."
        )
        self._init_ok = True

    # ------------------------------------------------------------------

    def accept(self):
        """Fetch ground materials for the selection polygon and display them."""
        if self.polygon is None or self.tile_count is None:
            return

        self._set_fetch_enabled(False)
        self.status_label.setText("Fetching ground materials…")
        QApplication.processEvents()

        def on_progress(progress):
            try:
                self.status_label.setText(
                    f"Fetching ground materials… "
                    f"{progress.completed_count}/{progress.total_count} tiles"
                )
                QApplication.processEvents()
            except Exception:
                pass

        try:
            from infrared_sdk import InfraredClient

            with InfraredClient(api_key=self.api_key) as client:
                area_gm = client.ground_materials.get_area(
                    self.polygon, on_progress=on_progress,
                )
        except Exception as e:
            logger.error("Ground materials fetch failed: %s", e, exc_info=True)
            self.status_label.setText("")
            self._set_fetch_enabled(True)
            QMessageBox.critical(
                self, "Fetch Failed",
                f"Failed to fetch ground materials.\n\n{e}\n\n"
                "Check your API key/subscription and network, then try again.",
            )
            return

        if not area_gm.layers:
            self.status_label.setText("")
            self._set_fetch_enabled(True)
            QMessageBox.information(
                self, "No Ground Materials",
                "No ground material data was found for the selected area.",
            )
            return

        self.created_layers = display_ground_materials(area_gm.layers)
        summary = ", ".join(
            f"{name}: {count}" for name, count in sorted(self.created_layers.items())
        )
        logger.info(
            "Ground materials fetched: %d features across %d layer(s) (%s)",
            area_gm.total_features, len(self.created_layers), summary,
        )
        QMessageBox.information(
            self, "Ground Materials Added",
            f"Added {len(self.created_layers)} ground material layer(s):\n\n"
            f"{summary}\n\n"
            "You can edit these layers before running a simulation.",
        )
        super().accept()
