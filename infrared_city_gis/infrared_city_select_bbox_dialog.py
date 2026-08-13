import os
from datetime import datetime

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapToolEmitPoint
from qgis.PyQt import QtWidgets, uic
from qgis.PyQt.QtWidgets import QLabel, QPushButton, QVBoxLayout
from qgis.utils import iface

from .infrared_logger import logger
from .services import single_tile_selection
from .services.geometry import get_bbox

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'infrared_city_select_bbox_dialog.ui'))


class InfraredCitySelectBBoxDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            self.setupUi(self)
        except Exception:
            self.setWindowTitle("Select bbox by center")
            layout = QVBoxLayout()
            self.info_label = QLabel("Click the button, then pick a center on the map to select a 512×512 m bbox")
            layout.addWidget(self.info_label)
            self.btn_select = QPushButton("Select bbox by center")
            layout.addWidget(self.btn_select)
            self.setLayout(layout)

        self.rubber = None
        self.map_tool = None
        self.prev_map_tool = None

        try:
            btn = getattr(self, "btnSelect", None) or getattr(self, "btn_select", None) or getattr(self, "pushButton", None) or self.btn_select
            btn.clicked.connect(self.on_select_clicked)
        except Exception:
            self.btn_select.clicked.connect(self.on_select_clicked)

        # 🔹 Ezek lesznek az adatok, amiket a plugin olvas majd
        self.geojson_path = None
        self.dotbim_path = None
        self.bbox = None
        self.crs = None
        self.date_now = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    def _clear_rubber(self):
        if self.rubber is not None:
            try:
                self.rubber.reset(True)
            except Exception:
                self.rubber.reset()
            self.rubber = None

    def on_select_clicked(self):
        canvas = iface.mapCanvas()
        self.prev_map_tool = canvas.mapTool()
        self.map_tool = QgsMapToolEmitPoint(canvas)
        self.map_tool.canvasClicked.connect(self._on_map_clicked)
        canvas.setMapTool(self.map_tool)
        iface.messageBar().pushMessage("InfraredCity", "Click on the map to choose bbox center.", level=0)

    def _on_map_clicked(self, point, button):
        logger.info("Map clicked at: %.6f, %.6f", point.x(), point.y())

        try:
            # --- Restore map tool ---
            try:
                canvas = iface.mapCanvas()
                if self.map_tool:
                    try:
                        self.map_tool.canvasClicked.disconnect(self._on_map_clicked)
                    except Exception as e:
                        # Qt raises when the signal was never connected — that is
                        # the normal case on a second close, not a failure.
                        logger.debug("canvasClicked was not connected: %s", e)
                if self.prev_map_tool:
                    canvas.setMapTool(self.prev_map_tool)
                else:
                    canvas.unsetMapTool(self.map_tool)
            except Exception as e:
                logger.warning("Failed to restore map tool: %s", e)

            # --- Transform click to WGS84 ---
            project_crs = QgsProject.instance().crs()
            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            transform_to_wgs84 = QgsCoordinateTransform(project_crs, wgs84, QgsProject.instance())

            try:
                lonlat = transform_to_wgs84.transform(point)
            except Exception as e:
                iface.messageBar().pushMessage("InfraredCity", f"Transform failed: {e}", level=3)
                logger.error("Transform to WGS84 failed: %s", e)
                return

            center_lon, center_lat = lonlat.x(), lonlat.y()
            logger.info("Center (WGS84): %.6f, %.6f", center_lon, center_lat)

            # --- Compute bbox (in WGS84) ---
            try:
                xmin, ymin, xmax, ymax = get_bbox(center_lon, center_lat, 512)
                bbox_rect_wgs84 = QgsRectangle(xmin, ymin, xmax, ymax)
            except Exception as e:
                iface.messageBar().pushMessage("InfraredCity", f"get_bbox failed: {e}", level=3)
                logger.error("get_bbox failed: %s", e)
                return

            # --- Pick a buildings reference layer.
            # Use the active layer (whatever the user selected in the Layers panel),
            # with two sanity checks: (1) it must be a polygon vector layer, and
            # (2) if the project has multiple polygon vector layers, we ask the
            # user to make their choice explicit instead of guessing.
            def _is_polygon_vector(lyr):
                return isinstance(lyr, QgsVectorLayer) and lyr.geometryType() == QgsWkbTypes.PolygonGeometry

            ref_layer = iface.activeLayer()
            if not _is_polygon_vector(ref_layer):
                polygon_layers = [
                    layer for layer in QgsProject.instance().mapLayers().values()
                    if _is_polygon_vector(layer)
                ]
                if not polygon_layers:
                    iface.messageBar().pushMessage(
                        "InfraredCity",
                        "No polygon vector layer found. Add a buildings layer to the project first.",
                        level=2,
                    )
                    logger.error("No polygon vector layer in project.")
                    return
                if len(polygon_layers) > 1:
                    active_name = iface.activeLayer().name() if iface.activeLayer() else "none"
                    iface.messageBar().pushMessage(
                        "InfraredCity",
                        f"Multiple polygon layers found. Click the buildings layer in the Layers panel "
                        f"to make it active, then try again. Currently active: '{active_name}'.",
                        level=1,
                        duration=8,
                    )
                    logger.warning(
                        "Active layer '%s' is not a polygon vector; %d candidates available — asking user to pick.",
                        active_name, len(polygon_layers),
                    )
                    return
                ref_layer = polygon_layers[0]
                logger.info(
                    "Active layer was not a polygon vector; auto-picked the only polygon layer: '%s'.",
                    ref_layer.name(),
                )

            ref_crs = ref_layer.crs()
            logger.info("Reference layer: '%s' (CRS=%s)", ref_layer.name(), ref_crs.authid())

            # --- bbox in the ref layer CRS (used for both visual selection and center transform)
            if ref_crs.authid() != "EPSG:4326":
                transform_bbox_ref = QgsCoordinateTransform(wgs84, ref_crs, QgsProject.instance())
                bbox_rect_ref = transform_bbox_ref.transformBoundingBox(bbox_rect_wgs84)
            else:
                bbox_rect_ref = bbox_rect_wgs84

            # --- Visual feedback: highlight features in the reference layer
            count = 0
            try:
                ref_layer.removeSelection()
                ref_layer.selectByRect(bbox_rect_ref, QgsVectorLayer.SetSelection)
                count = ref_layer.selectedFeatureCount()
                logger.info("Selected %d features in '%s'.", count, ref_layer.name())
            except Exception as e:
                logger.warning("selectByRect failed on '%s': %s", ref_layer.name(), e)

            # Reject empty tiles: storing a selection with no buildings would let
            # "Run simulation" submit a single-tile job with no geometry, burning
            # a request for a meaningless/failed result. Clear any pending
            # selection and keep the dialog open so the user can pick again.
            if count == 0:
                single_tile_selection.clear()
                logger.warning(
                    "Selected tile has 0 features in '%s' — not storing selection",
                    ref_layer.name(),
                )
                iface.messageBar().pushMessage(
                    "InfraredCity",
                    "The selected tile contains no buildings. Pick a tile with "
                    "buildings, and make sure the buildings layer is active.",
                    level=Qgis.Warning,
                    duration=8,
                )
                return

            # --- Store the 512×512 m tile as a one-shot single-tile selection
            # (ArcGIS-style). No dotbim/geojson export here — the Run
            # Simulation dialog consumes this selection, collects buildings
            # from the active QGIS layer at run time, and submits ONE tile via
            # analyses.execute (≈10 tokens) instead of the area tiler.
            w = bbox_rect_wgs84.xMinimum()
            s = bbox_rect_wgs84.yMinimum()
            e = bbox_rect_wgs84.xMaximum()
            n = bbox_rect_wgs84.yMaximum()
            polygon = {
                "type": "Polygon",
                "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
            }
            single_tile_selection.set_selection(
                polygon=polygon,
                center_lon=center_lon,
                center_lat=center_lat,
                bbox=(w, s, e, n),
                crs="EPSG:4326",
                building_count=count,
            )
            self.bbox = (w, s, e, n)
            self.crs = "EPSG:4326"
            iface.messageBar().pushMessage(
                "InfraredCity",
                f"512×512 m tile selected ({count} buildings). "
                "Open 'Run simulation' to run it.",
                level=0,
                duration=8,
            )

        except Exception as e:
            logger.error("Failed to select features: %s", e)
            return

        self.accept()

    def closeEvent(self, event):
        try:
            if self.map_tool:
                try:
                    self.map_tool.canvasClicked.disconnect(self._on_map_clicked)
                except Exception as e:
                    # Not connected — normal when the dialog closes twice.
                    logger.debug("canvasClicked was not connected: %s", e)
            if self.prev_map_tool:
                iface.mapCanvas().setMapTool(self.prev_map_tool)
        except Exception as e:
            logger.error("Failed to restore map tool: %s", e)

        self._clear_rubber()
        super().closeEvent(event)
