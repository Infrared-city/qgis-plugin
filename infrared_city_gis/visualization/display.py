
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

def add_geojson_then_raster(geojson_path, geotiff_path, raster_opacity=0.7):
        logger.info("Adding GeoJSON layer: %s", geojson_path)
        logger.info("Adding GeoTIFF layer: %s", geotiff_path)

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

        color_items = [
            QgsColorRampShader.ColorRampItem(vmin, QColor("#d3d3d3"), "Min"),   # grey
            QgsColorRampShader.ColorRampItem(vmax, QColor("#ff0000"), "Max")    # piros
        ]
        color_ramp.setColorRampItemList(color_items)

        raster_shader = QgsRasterShader()
        raster_shader.setRasterShaderFunction(color_ramp)

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

    