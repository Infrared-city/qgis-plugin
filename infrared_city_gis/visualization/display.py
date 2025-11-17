
from qgis.core import QgsVectorLayer, QgsProject
from ..infrared_logger import logger
from qgis.core import (
    QgsVectorLayer,
    QgsProject
)
from PyQt5.QtGui import QColor
from qgis.core import QgsVectorLayer
from qgis.core import QgsRasterLayer
from qgis.core import QgsRasterLayer, QgsVectorLayer, QgsProject
from PyQt5.QtGui import QPainter
from qgis.core import (
    QgsRasterLayer,
    QgsVectorLayer,
    QgsProject,
    QgsRasterShader,
    QgsColorRampShader,
    QgsSingleBandPseudoColorRenderer,
    QgsFillSymbol,
    QgsSymbol
)
from PyQt5.QtGui import QColor
import json
import os

from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY

def display_route_and_points(route, points):
    

# --- Create polyline layer ---
    line_layer = QgsVectorLayer("LineString?crs=EPSG:4326", "route_line", "memory")
    provider = line_layer.dataProvider()

    line_feature = QgsFeature()
    line_geom = QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in route])
    line_feature.setGeometry(line_geom)
    provider.addFeatures([line_feature])

    QgsProject.instance().addMapLayer(line_layer)

    # --- Create points layer ---
    points_layer = QgsVectorLayer("Point?crs=EPSG:4326", "route_points", "memory")
    prov_points = points_layer.dataProvider()

    features = []
    for x, y in points:
        f = QgsFeature()
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        features.append(f)

    prov_points.addFeatures(features)
    QgsProject.instance().addMapLayer(points_layer)

    logger.info("✅ Polyline and points added to map.")

def _rgb_to_hex(rgb_list):
    """Convert [R, G, B] list to hex string."""
    if not isinstance(rgb_list, (list, tuple)) or len(rgb_list) != 3:
        return None
    try:
        return "#{:02X}{:02X}{:02X}".format(*[int(v) for v in rgb_list])
    except Exception:
        return None


def get_visual_config(analysis_type, sub_analysis_type=None):
    """
    Extract visual configuration (colors, steps, stepsNames, etc.)
    from settings.json for a given analysis type.
    """
    settings_path = os.path.join(os.path.dirname(__file__), "settings.json")

    try:
        with open(settings_path, "r", encoding="utf-8") as fh:
            visual_configs = json.load(fh).get("visualConfigurations", {})
    except Exception as e:
        logger.error(f"Failed to load settings.json: {e}")
        return None

    # --- 1️⃣ Find top-level config ---
    config = visual_configs.get(analysis_type)
    if not config:
        logger.warning(f"Analysis type '{analysis_type}' not found in visualConfigurations.")
        return None

    # --- 2️⃣ If subtype is provided, go deeper ---
    if sub_analysis_type:
        sub_cfg = config.get(sub_analysis_type)
        if not sub_cfg:
            logger.warning(f"Subtype '{sub_analysis_type}' not found under '{analysis_type}'.")
            return None
        else:
            logger.info(f"Subtype '{sub_analysis_type}' found under '{analysis_type}'.")
        cfg = sub_cfg
        logger.info(f"Config: {cfg}")
    else:
        cfg = config
        logger.info(f"Config: {cfg}")

    # --- 3️⃣ Extract values safely ---
    colors_raw = cfg.get("colors", [])
    colors = [_rgb_to_hex(c) for c in colors_raw if _rgb_to_hex(c)]

    result = {
        "colors": colors,
        "steps": cfg.get("steps", []),
        "stepsNames": cfg.get("stepsNames", []),
        "info": cfg.get("info"),
        "unit": cfg.get("unit"),
        "colorInterpolation": cfg.get("colorInterpolation"),
        "legendType": cfg.get("legendType")
    }

    return result


def display_geojson(geojson_path):
    layer = QgsVectorLayer(geojson_path, "Infrared Buildings", "ogr")
    if layer.isValid():
        logger.info("Layer loaded successfully")
        
        symbol = layer.renderer().symbol()
        symbol.setColor(QColor("#555555")) # dark gray
        symbol.symbolLayer(0).setStrokeColor(QColor("black")) 
        symbol.symbolLayer(0).setStrokeWidth(0.5) 
        
        QgsProject.instance().addMapLayer(layer)
    else:
        logger.error("Layer could not be loaded")

def _build_color_ramp_items(visual_config):
    colors = visual_config.get("colors", [])
    steps = visual_config.get("steps", [])
    steps_names = visual_config.get("stepsNames", [])
    interpolation = visual_config.get("colorInterpolation", "linear")

    shader = QgsColorRampShader()
    color_items = []

    # ---- 1️⃣ Ha kategóriás (nem numerikus) lépések ----
    if steps and not all(isinstance(s, (int, float)) for s in steps):
        # pl. ["A", "B", "C", "D", "E", "S15", "S20"]
        for i, color in enumerate(colors):
            color_qt = QColor(*color) if isinstance(color, list) else QColor(color)
            label = str(steps[i]) if i < len(steps) else f"Class {i+1}"
            color_items.append(QgsColorRampShader.ColorRampItem(i, color_qt, label))

        shader.setColorRampType(QgsColorRampShader.Discrete)
        shader.setColorRampItemList(color_items)
        return shader, color_items

    # ---- 2️⃣ Ha numerikus értékek vannak ----
    if steps and len(steps) >= 2:
        vmin, vmax = float(steps[0]), float(steps[-1])
    else:
        # fallback, ha nincs steps vagy csak 1 érték van
        vmin, vmax = 0.0, float(len(colors) - 1)

    step_range = vmax - vmin if vmax != vmin else 1.0
    num_colors = len(colors)

    for i, color in enumerate(colors):
        value = vmin + (i / max(1, num_colors - 1)) * step_range
        color_qt = QColor(*color) if isinstance(color, list) else QColor(color)
        label = (
            steps_names[i]
            if i < len(steps_names)
            else (str(steps[i]) if i < len(steps) else f"{value:.2f}")
        )
        color_items.append(QgsColorRampShader.ColorRampItem(value, color_qt, label))

    # ---- 3️⃣ Interpoláció beállítása ----
    if interpolation == "binned":
        shader.setColorRampType(QgsColorRampShader.Discrete)
    else:
        shader.setColorRampType(QgsColorRampShader.Interpolated)

    shader.setColorRampItemList(color_items)
    return shader, color_items

def add_geojson_then_raster(geojson_path, geotiff_path, analysis_type, sub_analysis_type, raster_opacity=0.7):
        logger.info("Adding GeoJSON layer: %s", geojson_path)
        logger.info("Adding GeoTIFF layer: %s", geotiff_path)

        visual_config = get_visual_config(analysis_type, sub_analysis_type)
        if not visual_config:
            logger.error("Visual configuration not found for analysis type: %s, sub analysis type: %s", analysis_type, sub_analysis_type)
            raise RuntimeError("Visual configuration not found for analysis type: " + analysis_type + ", sub analysis type: " + sub_analysis_type)

        logger.info(f"Visual configuration: {visual_config}")

        # --- GeoJSON réteg ---
        vlayer = QgsVectorLayer(geojson_path, "Infrared Buildings", "ogr")
        if not vlayer.isValid():
            logger.error("GeoJSON layer loading failed: %s", geojson_path)
            raise RuntimeError("GeoJSON layer loading failed: " + geojson_path)

        # Átlátszó kitöltés, fekete körvonal (vonalvastagság megadása a 'outline_width'-dal)
        # Használjuk a createSimple-t, ami egy egyszerű dictionary-vel beállítható
        fill_sym = QgsFillSymbol.createSimple({
            'color': '0,0,0,0',           # belső: teljesen átlátszó (R,G,B,A)
            'outline_color': '0,0,0',     # körvonal: fekete
            'outline_width': '0.8'        # körvonal vastagsága (pixel/mérték)
        })
        vlayer.renderer().setSymbol(fill_sym)

        QgsProject.instance().addMapLayer(vlayer)

        # --- GeoTIFF réteg ---
        rlayer = QgsRasterLayer(geotiff_path, "Infrared Result", "gdal")
        if not rlayer.isValid():
            logger.error("GeoTIFF layer loading failed: %s", geotiff_path)
            raise RuntimeError("GeoTIFF layer loading failed: " + geotiff_path)

        # --- Min / Max értékek lekérése ---
        stats = rlayer.dataProvider().bandStatistics(1)
        vmin, vmax = stats.minimumValue, stats.maximumValue
        logger.info("Raster stats: min=%s, max=%s", vmin, vmax)

        # Ha valamiért statisztika nem elérhető, fallback
        if vmin is None or vmax is None:
            # próbáljuk meg a rasterio-t vagy vegyük az 0-1 tartományt
            logger.warning("Raster band statistics not available; using default 0..1")
            vmin, vmax = 0.0, 1.0

        # --- Color ramp (zöld -> piros) ---
        color_ramp = QgsColorRampShader()
        color_ramp.setColorRampType(QgsColorRampShader.Interpolated)

        shader, color_items = _build_color_ramp_items(visual_config)
        logger.info("Color items: %s", color_items)
        #color_ramp.setColorRampItemList(color_items)

        raster_shader = QgsRasterShader()
        raster_shader.setRasterShaderFunction(shader)

        renderer = QgsSingleBandPseudoColorRenderer(rlayer.dataProvider(), 1, raster_shader)
        rlayer.setRenderer(renderer)

        # Opacitás a raszterhez
        rlayer.setOpacity(float(raster_opacity))

        QgsProject.instance().addMapLayer(rlayer)

        # ----- Réteg sorrend: vektor legyen felül -----
        try:
            root = QgsProject.instance().layerTreeRoot()
            # keressük meg a node-okat
            v_node = root.findLayer(vlayer.id())
            r_node = root.findLayer(rlayer.id())
            if v_node is not None and r_node is not None:
                # távolítsuk el a vektor node-ot és helyezzük a tetejére
                parent = v_node.parent()
                parent.removeChildNode(v_node)
                root.insertChildNode(0, v_node)  # beillesztés a tetejére
        except Exception as e:
            logger.warning("Could not reorder layers automatically: %s", e)

        logger.info("✅ GeoJSON and colorized GeoTIFF added (green→red). Raster opacity=%s", raster_opacity)

    