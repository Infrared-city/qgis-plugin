from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtWidgets import QVBoxLayout, QPushButton, QLabel
from qgis.core import QgsRectangle, QgsProject, QgsVectorLayer
from qgis.gui import QgsRubberBand, QgsMapToolEmitPoint
from qgis.core import QgsCoordinateTransform, QgsCoordinateReferenceSystem, QgsPointXY, QgsProject
from qgis.core import QgsWkbTypes, QgsGeometry, QgsApplication, QgsUnitTypes
from qgis.utils import iface
from PyQt5.QtGui import QColor
from .infrared_logger import logger
from .services.geometry import get_bbox
from .services.geojson2dotbim import process_geojson_file
import os
import json
from datetime import datetime

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

        # --- Restore map tool ---
        try:
            canvas = iface.mapCanvas()
            if self.map_tool:
                try:
                    self.map_tool.canvasClicked.disconnect(self._on_map_clicked)
                except Exception:
                    pass
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

        # --- Get active layer ---
        layer = iface.activeLayer()
        if layer is None or not isinstance(layer, QgsVectorLayer):
            # fallback: use first vector layer in project
            for lyr in QgsProject.instance().mapLayers().values():
                if isinstance(lyr, QgsVectorLayer):
                    layer = lyr
                    break

        if layer is None or not isinstance(layer, QgsVectorLayer):
            iface.messageBar().pushMessage("InfraredCity", "No valid vector layer found in project.", level=2)
            logger.error("No valid vector layer found.")
            return
    

        layer_crs = layer.crs()
        logger.info("Layer CRS: %s", layer_crs.authid())

        # --- Transform bbox to layer CRS ---
        if layer_crs.authid() != "EPSG:4326":
            transform_bbox = QgsCoordinateTransform(wgs84, layer_crs, QgsProject.instance())
            bbox_rect_layer = transform_bbox.transformBoundingBox(bbox_rect_wgs84)
            logger.info("BBox transformed to layer CRS: %s", bbox_rect_layer)
        else:
            bbox_rect_layer = bbox_rect_wgs84
            logger.info("BBox in WGS84: %s", bbox_rect_layer)

        # --- Select features within bbox ---
        try:
            layer.removeSelection()
            layer.selectByRect(bbox_rect_layer, QgsVectorLayer.SetSelection)
            count = layer.selectedFeatureCount()
            logger.info("Selected %d features.", count)
        except Exception as e:
            logger.error("Selection failed: %s", e)
            iface.messageBar().pushMessage("InfraredCity", f"Selection failed: {e}", level=3)
            return

        iface.messageBar().pushMessage("InfraredCity", f"{count} features selected.", level=0)

        # --- Export selected features to GeoJSON ---
        selected_features = layer.selectedFeatures()
        geojson_dict = {
            "type": "FeatureCollection",
            "features": []
        }

        fields = [field.name() for field in layer.fields()]

        for feat in selected_features:
            geom = feat.geometry()
            geom_wgs84 = QgsGeometry(geom)
            if layer_crs.authid() != "EPSG:4326":
                transform_to_wgs84_back = QgsCoordinateTransform(layer_crs, wgs84, QgsProject.instance())
                geom_wgs84.transform(transform_to_wgs84_back)

            attr_values = feat.attributes()
            properties_dict = {fields[i]: attr_values[i] for i in range(len(fields))}

            geom_bbox = geom.boundingBox()
            height = geom_bbox.height()

            # Ha CRS fokban van, konvertáljuk méterre (durván 1° ≈ 111 km)
            if layer_crs.mapUnits() == QgsUnitTypes.DistanceDegrees:
                height *= 111_000

            properties_dict["height"] = round(height, 2)

            geojson_dict["features"].append({
                "type": "Feature",
                "geometry": json.loads(geom_wgs84.asJson()),
                "properties": properties_dict
            })

        plugin_data_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "data")
        os.makedirs(plugin_data_dir, exist_ok=True)
        geojson_path = os.path.join(plugin_data_dir, f"infrared_city_buildings_{self.date_now}.geojson")

        with open(geojson_path, "w", encoding="utf-8") as f:
            json.dump(geojson_dict, f, ensure_ascii=False, indent=2)

        dotbim_data = process_geojson_file(geojson_path, center_lon, center_lat,layer_crs.authid())
        dotbim_path = os.path.join(plugin_data_dir, f"infrared_city_buildings_{self.date_now}.bim")

        with open(dotbim_path, "w", encoding="utf-8") as f:
            json.dump(dotbim_data, f, ensure_ascii=False, indent=2)

        iface.messageBar().pushMessage("InfraredCity", f"Saved to {geojson_path}", level=0)
        logger.info("Saved to %s", geojson_path)
        logger.info("Saved to %s", dotbim_path)

        # 🔹 self változók beállítása, hogy a plugin olvassa majd
        self.geojson_path = geojson_path
        self.dotbim_path = dotbim_path
        self.bbox = (bbox_rect_layer.xMinimum(), bbox_rect_layer.yMinimum(), bbox_rect_layer.xMaximum(), bbox_rect_layer.yMaximum())
        self.crs = layer_crs.authid()

        # 🔹 dialog bezárása és értékek visszaadása
        self.accept()

    def closeEvent(self, event):
        try:
            if self.map_tool:
                try:
                    self.map_tool.canvasClicked.disconnect(self._on_map_clicked)
                except Exception:
                    pass
            if self.prev_map_tool:
                iface.mapCanvas().setMapTool(self.prev_map_tool)
        except Exception:
            pass

        self._clear_rubber()
        super().closeEvent(event)