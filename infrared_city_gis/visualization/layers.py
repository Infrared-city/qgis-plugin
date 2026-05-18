from PyQt5.QtGui import QColor
from qgis.core import (
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)

from ..infrared_logger import logger


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
    qgis_geom_type = "Polygon" if gtype in ("Polygon", "MultiPolygon") else "Point"

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
                pts = [QgsPointXY(float(x), float(y)) for x, y in coords[0]]
                qgeom = QgsGeometry.fromPolygonXY([pts])
            elif gtype == "MultiPolygon":
                polys = []
                for poly in coords:
                    if not poly:
                        continue
                    pts = [QgsPointXY(float(x), float(y)) for x, y in poly[0]]
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
        layer.renderer().symbol().setColor(color)
    except Exception:
        pass

    QgsProject.instance().addMapLayer(layer)
    return layer


def display_route_and_points(route, points):
    line_layer = QgsVectorLayer("LineString?crs=EPSG:4326", "route_line", "memory")
    provider = line_layer.dataProvider()
    line_feature = QgsFeature()
    line_feature.setGeometry(
        QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in route])
    )
    provider.addFeatures([line_feature])
    QgsProject.instance().addMapLayer(line_layer)

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


def display_geojson(geojson_path):
    layer = QgsVectorLayer(geojson_path, "Infrared Buildings", "ogr")
    if layer.isValid():
        symbol = layer.renderer().symbol()
        symbol.setColor(QColor("#555555"))
        symbol.symbolLayer(0).setStrokeColor(QColor("black"))
        symbol.symbolLayer(0).setStrokeWidth(0.5)
        QgsProject.instance().addMapLayer(layer)
        logger.info("Layer loaded successfully")
    else:
        logger.error("Layer could not be loaded")


def display_ground_materials(ground_materials):
    logger.info("Displaying ground materials layers")
    material_defs = [
        ("Ground Asphalt",  ground_materials.get("asphalt"),    QColor(77, 77, 77)),
        ("Ground Building", ground_materials.get("building"),   QColor(128, 128, 128)),
        ("Ground Concrete", ground_materials.get("concrete"),   QColor(191, 191, 191)),
        ("Ground Grass",    ground_materials.get("grass"),      QColor(76, 175, 80)),
        ("Ground Soil",     ground_materials.get("soil"),       QColor(139, 69, 19)),
        ("Ground Water",    ground_materials.get("water"),      QColor(33, 150, 243)),
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
    """Remove selection from all vector layers in the current QGIS project."""
    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorLayer):
            layer.removeSelection()
