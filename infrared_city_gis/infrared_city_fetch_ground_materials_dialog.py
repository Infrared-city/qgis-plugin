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
from qgis.PyQt.QtCore import QElapsedTimer, QTimer
from qgis.PyQt.QtWidgets import (
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from .infrared_logger import logger
from .services import single_tile_selection
from .services.ground_material_reader import GroundMaterialReader
from .services.polygon_from_selection import (
    create_wgs84_geojson_polygon_from_selection,
)
from .services.secret_manager import get_api_key
from .services.user_errors import show_error_dialog
from .utils.client_identity import make_client
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
        self.is_single_tile = False
        # Live read state. `_reader` doubles as "a read is in flight".
        self._reader = None
        self._chunks = None
        self._elapsed = QElapsedTimer()
        self._ticker = QTimer(self)
        self._ticker.timeout.connect(self._tick)
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
            logger.warning("Ground materials fetch refused: no API key saved")
            QMessageBox.warning(
                self, "No API Key",
                "Fetching ground materials requires an Infrared City API key.\n"
                "Please save your API key first (Save API Key).",
            )
            return

        # An armed tile takes precedence, and the fetch reads its BOX — the
        # same polygon the simulation will run on, so the materials cover
        # exactly that ground. A fetch never disarms: it is preparation for a
        # run on this tile, and ending the mode here would make the user
        # re-pick to use what they just fetched.
        _armed = single_tile_selection.peek()
        self.is_single_tile = _armed is not None
        if self.is_single_tile:
            self.polygon = _armed.polygon
            self.tile_count = 1
        else:
            selection = create_wgs84_geojson_polygon_from_selection()
            if selection is None:
                logger.info("Ground materials fetch refused: no selection")
                QMessageBox.warning(
                    self, "No selection",
                    "Please select a building area first — select features on "
                    "your building layer, then reopen this dialog.",
                )
                return
            self.polygon = selection
            try:
                client = make_client(self.api_key)
                preview = client.preview_area(self.polygon)
                self.tile_count = preview.tile_count
            except Exception as e:
                logger.exception("Ground materials: preview_area failed: %s", e)
                show_error_dialog(self, "Computing the selection area", e)
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

        # Name the SOURCE of the area, not just its size. A forgotten tile
        # pick used to show a bare "Selected area: 1 tile" while the user was
        # looking at a far larger building selection, and nothing on screen
        # explained the gap.
        if self.is_single_tile:
            area_line = (
                "Selected area: the 512 x 512 m tile you picked "
                "('Select tile' is pressed in the toolbar).\n"
                "Release that button to use your QGIS feature selection "
                "instead."
            )
        else:
            area_line = (
                f"Selected area: {self.tile_count} tile"
                f"{'s' if self.tile_count != 1 else ''}, from your QGIS "
                f"feature selection."
            )

        self.info_label.setText(
            f"{area_line}\n\n"
            f"Fetching adds one editable 'ground-<material>' layer per "
            f"surface type (asphalt, concrete, vegetation, soil, water, "
            f"building). Note: 'ground-vegetation' is green surfaces (grass, "
            f"parks) — trees are separate 'tree-*' point layers."
        )
        self._init_ok = True

    # ------------------------------------------------------------------

    def accept(self):
        """Start the read on a worker thread and keep the dialog responsive.

        The read is one blocking SDK call that moves far more data than it
        returns, so it used to lock the whole application for as long as it ran
        — minutes on a slow link, with nothing on screen moving and no way to
        close the dialog. It now runs off the UI thread, so the dialog stays
        alive, reports elapsed time and can be closed. This dialog is modal,
        though, so QGIS itself is still out of reach until it is closed.
        """
        if self.polygon is None or self.tile_count is None:
            return
        if self._reader is not None:
            return  # already running; the button is disabled, but be certain

        self._set_fetch_enabled(False)
        self._elapsed.start()
        self._chunks = None
        self._tick()
        self._ticker.start(1000)

        # Deliberately UNPARENTED. Qt deletes a child with its parent, and the
        # worker thread outlives this dialog whenever the user closes it
        # mid-read — a deleted QObject with a running QThread behind it takes
        # QGIS down. `_ACTIVE_READERS` owns it instead, until it retires itself.
        self._reader = GroundMaterialReader(self.api_key, self.polygon)
        self._reader.chunk_done.connect(self._on_chunk_done)
        self._reader.finished.connect(self._on_read_finished)
        self._reader.failed.connect(self._on_read_failed)
        self._reader.start()

    # -- progress -------------------------------------------------------

    def _tick(self):
        """Elapsed time, once a second.

        The honest progress signal here: the SDK reports once per read CHUNK,
        and a site this size is ONE chunk, so a percentage would sit at zero
        for the whole run. A clock at least shows the plugin is alive.
        """
        seconds = int(self._elapsed.elapsed() / 1000)
        chunks = f" · chunk {self._chunks[0]}/{self._chunks[1]}" if self._chunks else ""
        self.status_label.setText(
            f"Reading ground materials from Overture… "
            f"{seconds // 60}:{seconds % 60:02d}{chunks}\n"
            f"This moves a lot of data and can take a few minutes on a slow "
            f"connection."
        )

    def _on_chunk_done(self, completed, total):
        self._chunks = (completed, total)
        self._tick()

    def _stop_progress(self):
        self._ticker.stop()
        self._reader = None
        self.status_label.setText("")

    # -- outcomes -------------------------------------------------------

    def _on_read_failed(self, message, user_error):
        # The reader already logged the traceback and classified the failure:
        # a timeout is about the connection (this read sends no API key at
        # all), a missing component is about the install, not the network.
        self._stop_progress()
        self._set_fetch_enabled(True)
        QMessageBox.critical(
            self, user_error.title, user_error.message("Fetching ground materials"),
        )

    def _on_read_finished(self, area_gm):
        self._stop_progress()

        if not area_gm.layers:
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

    def reject(self):
        """Closing mid-read gives up on the result, not on the download.

        The SDK cannot interrupt a read in flight, so the thread runs itself
        out in the background rather than being abandoned — dropping a live
        QThread takes QGIS with it.
        """
        if self._reader is not None:
            self._reader.detach()
            self._ticker.stop()
            self._reader = None
        super().reject()
