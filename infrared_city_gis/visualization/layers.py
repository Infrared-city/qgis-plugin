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
            # Index positions instead of tuple-unpacking: several endpoints
            # (e.g. ground-material clean-v3) return 2.5D coordinates
            # ([lon, lat, z]) and `for x, y in ...` raises on those — which
            # silently dropped every feature here.
            if gtype == "Point":
                qgeom = QgsGeometry.fromPointXY(
                    QgsPointXY(float(coords[0]), float(coords[1]))
                )
            elif gtype == "Polygon":
                pts = [QgsPointXY(float(p[0]), float(p[1])) for p in coords[0]]
                qgeom = QgsGeometry.fromPolygonXY([pts])
            elif gtype == "MultiPolygon":
                polys = []
                for poly in coords:
                    if not poly:
                        continue
                    pts = [QgsPointXY(float(p[0]), float(p[1])) for p in poly[0]]
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
    """Create one editable ``ground-<material>`` layer per material.

    ``ground_materials`` is the SDK's ``AreaGroundMaterials.layers`` mapping
    (``{material_name: FeatureCollection}``). Layer names follow the
    ``ground-*`` convention the simulation dialog collects by, and colors come
    from the materials registry (with hardcoded fallbacks). Materials are
    keyed by whatever the server returned — the old hardcoded list looked up a
    "grass" key the server never emits (the material is "vegetation"), which
    is why this helper previously produced no green layer.

    Returns ``{layer_name: feature_count}`` for the layers created — keyed
    by the actual (possibly numbered) layer name so repeated fetches report
    ``ground-asphalt-2`` etc. in summaries.
    """
    from ..services.ground_materials import (
        GROUND_LAYER_PREFIX,
        material_color,
        material_opacity,
    )

    logger.info("Displaying ground material layers")
    # Number repeated fetches (ground-asphalt, ground-asphalt-2, …) so the
    # user can tell downloads over different areas apart. The simulation
    # dialog strips the trailing -N when resolving the material.
    existing = {
        ly.name().strip().lower()
        for ly in QgsProject.instance().mapLayers().values()
    }
    created: dict = {}
    for material, collection in sorted((ground_materials or {}).items()):
        base = f"{GROUND_LAYER_PREFIX}{material}"
        name = base
        n = 2
        while name.lower() in existing:
            name = f"{base}-{n}"
            n += 1
        existing.add(name.lower())
        color = QColor(*material_color(material))
        layer = _create_layer_from_feature_collection(name, collection, color)
        if layer is not None:
            # Styling comes from the materials registry (diffuseColor +
            # opacity), scaled by a 0.55 display factor: the asphalt layer
            # carries a bbox-covering background polygon (the server's
            # gap-fill default, near-black per its registry diffuseColor)
            # that would otherwise paint a solid box over the whole map.
            layer.setOpacity(0.55 * material_opacity(material))
            layer.triggerRepaint()
            logger.info(
                "Ground material layer created: %s (%d features)",
                name, layer.featureCount(),
            )
            created[name] = layer.featureCount()
    if not created:
        logger.warning("No ground material layers were created (no features in response)")
    return created


def deselect_all():
    """Remove selection from all vector layers in the current QGIS project."""
    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorLayer):
            layer.removeSelection()
