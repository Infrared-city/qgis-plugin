import json
import os
import tempfile
from datetime import datetime

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor

from ..infrared_logger import logger


def ground_package_path():
    """Where one ground-material fetch is saved: a new GeoPackage per fetch.

    Next to the fetched buildings (``fetch.py``) in the plugin's data folder,
    so both survive a QGIS restart and a saved project finds them again. Note
    ``utils.helper.cleanup_old_data`` deletes files there that have not been
    modified for 30 days, like the building files.
    """
    folder = os.path.join(QgsApplication.qgisSettingsDirPath(), "infrared_city_gis", "data")
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    return os.path.join(folder, f"infrared_city_ground_materials_{stamp}.gpkg")


def _save_material_layer(name, collection, gpkg_path):
    """Write one FeatureCollection as table ``name`` of ``gpkg_path``; load it back.

    Read through OGR rather than rebuilt point by point, so polygon holes
    survive (the old in-memory builder kept only each outer ring, which filled
    a courtyard or a pond with the surrounding material) and 2.5D coordinates
    need no special casing. Written 2D MultiPolygon with geometry only: the
    collector derives z and the material stamp itself, and a stray attribute
    would ride along into the payload.

    Returns the loaded, file-backed layer, or None when nothing was written.
    """
    if not isinstance(collection, dict) or not collection.get("features"):
        return None
    fd, tmp = tempfile.mkstemp(suffix=".geojson")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(collection, fh)
        source = QgsVectorLayer(tmp, name, "ogr")
        if not source.isValid() or source.featureCount() == 0:
            logger.warning("%s: the fetched collection could not be read", name)
            return None
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = name
        options.fileEncoding = "UTF-8"
        options.overrideGeometryType = Qgis.WkbType.MultiPolygon
        options.forceMulti = True
        options.includeZ = False
        options.skipAttributeCreation = True
        options.actionOnExistingFile = (
            QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
            if os.path.exists(gpkg_path)
            else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
        )
        error, message, _path, _layer = QgsVectorFileWriter.writeAsVectorFormatV3(
            source, gpkg_path, QgsProject.instance().transformContext(), options,
        )
        del source  # release the temp file before deleting it (Windows locks it)
        if error != QgsVectorFileWriter.WriterError.NoError:
            logger.error("%s: could not be saved to %s: %s", name, gpkg_path, message)
            return None
    finally:
        try:
            os.remove(tmp)
        except OSError as e:
            logger.debug("could not remove the temporary file %s: %s", tmp, e)
    layer = QgsVectorLayer(f"{gpkg_path}|layername={name}", name, "ogr")
    if not layer.isValid():
        logger.error("%s: saved to %s but could not be loaded back", name, gpkg_path)
        return None
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
    layer = QgsVectorLayer(geojson_path, "Infrared City Buildings", "ogr")
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
    """Save one editable ``ground-<material>`` layer per material, and add them.

    All layers of one fetch go into one GeoPackage in the plugin's data folder
    (:func:`ground_package_path`), so they are files, not QGIS scratch layers:
    they survive a restart, a saved project reopens them, and edits are written
    straight back to the file.

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
    gpkg_path = ground_package_path()
    for material, collection in sorted((ground_materials or {}).items()):
        base = f"{GROUND_LAYER_PREFIX}{material}"
        name = base
        n = 2
        while name.lower() in existing:
            name = f"{base}-{n}"
            n += 1
        existing.add(name.lower())
        layer = _save_material_layer(name, collection, gpkg_path)
        if layer is not None:
            try:
                layer.renderer().symbol().setColor(QColor(*material_color(material)))
            except Exception as e:
                # Styling is cosmetic — a layer with default symbology still works.
                logger.debug("could not set the symbol color on %r: %s", name, e)
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
            QgsProject.instance().addMapLayer(layer)
            created[name] = layer.featureCount()
    if created:
        logger.info("Ground materials saved to %s", gpkg_path)
    else:
        logger.warning("No ground material layers were created (no features in response)")
    return created


def deselect_all():
    """Remove selection from all vector layers in the current QGIS project."""
    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorLayer):
            layer.removeSelection()
