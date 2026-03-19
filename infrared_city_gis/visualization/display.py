
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
    QgsSymbol,
    QgsRasterRange
)
from PyQt5.QtGui import QColor
import json
import os
import math


from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY

def _create_layer_from_feature_collection(name, collection, color):
    """Create an in-memory layer from a GeoJSON FeatureCollection dict.

    Supports Point, Polygon and MultiPolygon geometries in EPSG:4326.
    Returns the created QgsVectorLayer or None if no valid features.
    """
    if not collection or not isinstance(collection, dict):
        return None

    if collection.get("type") != "FeatureCollection":
        return None

    features_in = collection.get("features") or []
    if not features_in:
        return None

    first_geom = (features_in[0] or {}).get("geometry") or {}
    gtype = first_geom.get("type", "Point")

    if gtype == "Point":
        qgis_geom_type = "Point"
    elif gtype in ("Polygon", "MultiPolygon"):
        qgis_geom_type = "Polygon"
    else:
        qgis_geom_type = "Point"

    layer = QgsVectorLayer(f"{qgis_geom_type}?crs=EPSG:4326", name, "memory")
    provider = layer.dataProvider()

    new_features = []
    for f in features_in:
        geom = (f or {}).get("geometry") or {}
        coords = geom.get("coordinates")
        if not coords:
            continue

        qfeat = QgsFeature()

        try:
            if gtype == "Point":
                x, y = coords
                qgeom = QgsGeometry.fromPointXY(QgsPointXY(float(x), float(y)))
            elif gtype == "Polygon":
                ring = coords[0]
                pts = [QgsPointXY(float(x), float(y)) for x, y in ring]
                qgeom = QgsGeometry.fromPolygonXY([pts])
            elif gtype == "MultiPolygon":
                polys = []
                for poly in coords:
                    if not poly:
                        continue
                    ring = poly[0]
                    pts = [QgsPointXY(float(x), float(y)) for x, y in ring]
                    polys.append([pts])
                if not polys:
                    continue
                qgeom = QgsGeometry.fromMultiPolygonXY(polys)
            else:
                continue
        except Exception:
            continue

        qfeat.setGeometry(qgeom)
        new_features.append(qfeat)

    if not new_features:
        return None

    provider.addFeatures(new_features)

    try:
        symbol = layer.renderer().symbol()
        symbol.setColor(color)
    except Exception:
        pass

    QgsProject.instance().addMapLayer(layer)
    return layer


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

    logger.info("Settings path: %s", settings_path)

    try:
        with open(settings_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            visual_configs = data.get("settings", {}).get("visualConfigurations", {})
    except Exception as e:
        logger.error(f"Failed to load settings.json: {e}")
        return None

    # --- 1️⃣ Find top-level config ---
    logger.info("Available visual configurations: %s", list(visual_configs.keys()))

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

def _build_color_ramp_items(visual_config,analysis_type, vmin=None, vmax=None):
    colors = visual_config.get("colors", [])
    steps = visual_config.get("steps", [])
    steps_names = visual_config.get("stepsNames", [])
    interpolation = visual_config.get("colorInterpolation", "linear")

    shader = QgsColorRampShader()
    color_items = []

    # ---- if chategorical values----
    if steps and not all(isinstance(s, (int, float)) for s in steps):
        # pl. ["A", "B", "C", "D", "E", "S15", "S20"]
        for i, color in enumerate(colors):
            color_qt = QColor(*color) if isinstance(color, list) else QColor(color)
            label = str(steps[i]) if i < len(steps) else f"Class {i+1}"
            
            if analysis_type == "pedestrian-wind-comfort":
                color_items.append(QgsColorRampShader.ColorRampItem(i+1, color_qt, label))
            else:
                color_items.append(QgsColorRampShader.ColorRampItem(i, color_qt, label))

        shader.setColorRampType(QgsColorRampShader.Discrete)
        shader.setColorRampItemList(color_items)
        return shader, color_items

    # ---- if numerical values ----
    if steps and len(steps) >= 2:
        vmin, vmax = float(steps[0]), float(steps[-1])
    else:
        # fallback, if no steps are provided
        # use the min and max values of the raster
        if vmin is None or vmax is None:
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
        if analysis_type == "pedestrian-wind-comfort":
            color_items.append(QgsColorRampShader.ColorRampItem(i+1, value, color_qt, label))
        else:
            color_items.append(QgsColorRampShader.ColorRampItem(value, color_qt, label))


    # ---- if interpolation ----
    if interpolation == "binned":
        shader.setColorRampType(QgsColorRampShader.Discrete)
    else:
        shader.setColorRampType(QgsColorRampShader.Interpolated)

    shader.setColorRampItemList(color_items)
    return shader, color_items

def add_geojson_then_raster(geojson_path, geotiff_path, analysis_type, sub_analysis_type, raster_opacity=0.7, min_legend_value=None, max_legend_value=None):
        logger.info("Adding GeoJSON layer: %s", geojson_path)
        logger.info("Adding GeoTIFF layer: %s", geotiff_path)

        visual_config = get_visual_config(analysis_type, sub_analysis_type)
        if not visual_config:
            logger.error("Visual configuration not found for analysis type: %s, sub analysis type: %s", analysis_type, sub_analysis_type)
            raise RuntimeError("Visual configuration not found for analysis type: " + analysis_type + ", sub analysis type: " + sub_analysis_type)

        logger.info(f"Visual configuration: {visual_config}")

        # --- GeoJSON layer ---
        vlayer = QgsVectorLayer(geojson_path, "Infrared Buildings", "ogr")
        if not vlayer.isValid():
            logger.error("GeoJSON layer loading failed: %s", geojson_path)
            raise RuntimeError("GeoJSON layer loading failed: " + geojson_path)

        # Transparent fill, black outline (outline width can be set with 'outline_width')
        # Use createSimple with a simple dictionary
        fill_sym = QgsFillSymbol.createSimple({
            'color': '0,0,0,0',           # inner: fully transparent (R,G,B,A)
            'outline_color': '0,0,0',     # outline: black
            'outline_width': '0.8'        # outline width (pixel/measure)
        })
        vlayer.renderer().setSymbol(fill_sym)

        QgsProject.instance().addMapLayer(vlayer)

        # --- GeoTIFF layer ---
        rlayer = QgsRasterLayer(geotiff_path, "Infrared Result", "gdal")
        if not rlayer.isValid():
            logger.error("GeoTIFF layer loading failed: %s", geotiff_path)
            raise RuntimeError("GeoTIFF layer loading failed: " + geotiff_path)

        # Handle No-data value QGIS side: if the source band has a No-data value,
        # set it as a user No-data range so that statistics and colorization
        # skip these pixels.
        provider = rlayer.dataProvider()
        try:
            nodata = provider.sourceNoDataValue(1)
        except Exception:
            nodata = None

        if nodata is not None:
            # If the no-data is not NaN (NaN would not work well in range comparison),
            # create a narrow QgsRasterRange for this value.
            if isinstance(nodata, (int, float)) and not math.isnan(nodata):
                try:
                    provider.setUserNoDataValues(1, [QgsRasterRange(nodata, nodata)])
                    logger.info("User no-data range set for band 1: %s", nodata)
                except Exception as e:
                    logger.warning("Failed to set user no-data values: %s", e)

        # --- Min / Max values ---
        stats = rlayer.dataProvider().bandStatistics(1)
        vmin, vmax = stats.minimumValue, stats.maximumValue
        logger.info("Original raster stats: min=%s, max=%s", vmin, vmax)

        if min_legend_value is not None:
            vmin = min_legend_value
            logger.info("Using min_legend_value: %s", min_legend_value)
        if max_legend_value is not None:
            vmax = max_legend_value
            logger.info("Using max_legend_value: %s", max_legend_value)

        # If statistics are not available, fallback
        if vmin is None or vmax is None:
            # Try to use the 0-1 range
            logger.warning("Raster band statistics not available; using default 0..1")
            vmin, vmax = 0.0, 1.0

        # --- Color ramp (green -> red) ---
        color_ramp = QgsColorRampShader()
        color_ramp.setColorRampType(QgsColorRampShader.Interpolated)

        # Build color ramp items for the color ramp
        shader, color_items = _build_color_ramp_items(visual_config,analysis_type,vmin=vmin, vmax=vmax)
        logger.info("Color items: %s", color_items)
        #color_ramp.setColorRampItemList(color_items)

        raster_shader = QgsRasterShader()
        raster_shader.setRasterShaderFunction(shader)

        renderer = QgsSingleBandPseudoColorRenderer(rlayer.dataProvider(), 1, raster_shader)
        rlayer.setRenderer(renderer)

        # Set opacity for the raster
        rlayer.setOpacity(float(raster_opacity))

        QgsProject.instance().addMapLayer(rlayer)

        # ----- Layer order: vector should be on top -----
        try:
            root = QgsProject.instance().layerTreeRoot()
            # Find the nodes
            v_node = root.findLayer(vlayer.id())
            r_node = root.findLayer(rlayer.id())
            if v_node is not None and r_node is not None:
                # Remove the vector node and insert it at the top
                parent = v_node.parent()
                parent.removeChildNode(v_node)
                root.insertChildNode(0, v_node)  # beillesztés a tetejére
        except Exception as e:
            logger.warning("Could not reorder layers automatically: %s", e)

        logger.info("✅ GeoJSON and colorized GeoTIFF added (green→red). Raster opacity=%s", raster_opacity)


def display_ground_materials(ground_materials):
    asphalt = ground_materials.get("asphalt")  # dark gray
    building = ground_materials.get("building")  # medium gray
    concrete = ground_materials.get("concrete")  # light gray
    vegetation = ground_materials.get("grass")  # green
    soil = ground_materials.get("soil")  # brown
    water = ground_materials.get("water")  # blue

    logger.info("Displaying ground materials layers")

    material_defs = [
        ("Ground Asphalt", asphalt, QColor(77, 77, 77)),       # #4D4D4D
        ("Ground Building", building, QColor(128, 128, 128)),  # #808080
        ("Ground Concrete", concrete, QColor(191, 191, 191)),  # #BFBFBF
        ("Ground Grass", vegetation, QColor(76, 175, 80)),     # #4CAF50
        ("Ground Soil", soil, QColor(139, 69, 19)),            # #8B4513
        ("Ground Water", water, QColor(33, 150, 243)),         # #2196F3
    ]

    created_any = False
    for name, collection, color in material_defs:
        layer = _create_layer_from_feature_collection(name, collection, color)
        if layer is not None:
            logger.info("Ground material layer created: %s (%d features)", name, layer.featureCount())
            created_any = True

    if not created_any:
        logger.warning("No ground material layers were created (no features in response)")

def deselect_all():
    """
    Remove selection from all vector layers in the current QGIS project.
    """
    project = QgsProject.instance()
    for layer in project.mapLayers().values():
        if isinstance(layer, QgsVectorLayer):
            layer.removeSelection()