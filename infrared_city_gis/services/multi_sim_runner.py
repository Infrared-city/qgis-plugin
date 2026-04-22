"""Payload building and tile processing loop for Run Multiple Simulations.

Extracted from InfraredCityRunMultipleSimulationDialog to keep the dialog
file under the 400-line Infrared convention limit.
"""

import os

import numpy as np
from osgeo import gdal
from qgis.core import Qgis
from qgis.PyQt.QtWidgets import QApplication
from qgis.utils import iface

from ..client import (
    get_daylight_availability_payload,
    get_direct_sun_hours_payload,
    get_pwc_payload,
    get_shadow_mask_payload,
    get_solar_radiation_payload,
    get_tcs_payload,
    get_utci_payload,
    get_windspeed_payload,
    load_dotbim,
    process_run_analysis,
)
from ..infrared_logger import logger
from ..models.analysis import AnalysisType, GeometryTypes
from ..models.timeframes_parser import makeTimeFrameObj, makeTimeFrameObjWithMonth
from ..services.epw_query import Query_Type, query_infrared_epw
from ..services.geometry import collect_geometries, get_center_lon_lat_from_bbox
from ..services.geotiff import crop_matrix, generate_geotiff
from ..visualization.display import add_geojson_then_raster, deselect_all


def _status(msg, level=Qgis.Info, duration=0):
    iface.messageBar().clearWidgets()
    iface.messageBar().pushMessage("InfraredCity", msg, level=level, duration=duration)
    QApplication.processEvents()


def build_payload(dlg):
    """Build the API payload from the dialog's current UI state.

    Returns the payload dict, or None if validation failed (the method already
    showed a warning dialog in that case).
    """
    from qgis.PyQt.QtWidgets import QMessageBox

    at = dlg.analysis_type

    if at == AnalysisType.WIND_SPEED:
        wind_speed = dlg.wind_speed_input.value()
        wind_direction = dlg.wind_direction_input.value()
        if wind_speed <= 0 or wind_direction < 0 or wind_direction > 360:
            QMessageBox.warning(dlg, "Invalid Parameters", "Wind speed must be >0 and direction 0–360°.")
            return None
        return get_windspeed_payload(wind_direction, wind_speed)

    if at == AnalysisType.PEDESTRIAN_WIND_COMFORT:
        try:
            pwc_type = dlg.pwc_type_dropdown.currentData()
            dlg.sub_analysis_type = pwc_type
            selected_season = dlg.season_dropdown_pwc.currentData()
            selected_hours = dlg.hours_dropdown_pwc.currentData()
            weather_file = dlg.weather_file_input_pwc.currentText().strip()
        except AttributeError:
            QMessageBox.warning(dlg, "Missing Input", "Please fill in all fields!")
            return None
        time_frame = makeTimeFrameObj(isNorthHem=True, season=selected_season.value, hourly=selected_hours.value)
        wind_data = query_infrared_epw(file_name=weather_file, type=Query_Type.WIND, time_frame=time_frame, api_key=dlg.api_key)
        logger.info("Wind data: windSpeed length=%d, windDirection length=%d", 
                    len(wind_data["windSpeed"]), len(wind_data["windDirection"]))
        
        return get_pwc_payload(wind_data, dlg.sub_analysis_type.value)

    if at == AnalysisType.THERMAL_COMFORT_INDEX:
        try:
            selected_month = dlg.month_dropdown_tci.currentData()
            selected_hours = dlg.hours_dropdown_tci.currentData()
            if dlg.legend_min_enable_tci.isChecked():
                dlg.min_legend_value = dlg.legend_min_input_tci.value()
            if dlg.legend_max_enable_tci.isChecked():
                dlg.max_legend_value = dlg.legend_max_input_tci.value()
            weather_file = dlg.weather_file_input_tci.currentText().strip()
        except AttributeError:
            QMessageBox.warning(dlg, "Missing Input", "Selected month and hours are required.")
            return None
        time_frame = makeTimeFrameObjWithMonth(month=selected_month.number, hourly=selected_hours.value)
        weather_data = query_infrared_epw(file_name=weather_file, type=Query_Type.UTCI, time_frame=time_frame, api_key=dlg.api_key)
        center_lon, center_lat = get_center_lon_lat_from_bbox(dlg.bbox, dlg.crs)
        return get_utci_payload(weather_data, center_lon, center_lat, time_frame)

    if at == AnalysisType.THERMAL_COMFORT_STATISTICS:
        try:
            selected_season = dlg.season_dropdown_tcs.currentData()
            selected_hours = dlg.hours_dropdown_tcs.currentData()
            selected_tcs_type = dlg.tcs_type_dropdown.currentData()
            weather_file = dlg.weather_file_input_tcs.currentText().strip()
        except AttributeError:
            QMessageBox.warning(dlg, "Missing Input", "TCS type is required.")
            return None
        time_frame = makeTimeFrameObj(isNorthHem=True, season=selected_season.value, hourly=selected_hours.value, analysis_type=at)
        weather_data = query_infrared_epw(file_name=weather_file, type=Query_Type.UTCI, time_frame=time_frame, api_key=dlg.api_key)
        center_lon, center_lat = get_center_lon_lat_from_bbox(dlg.bbox, dlg.crs)
        return get_tcs_payload(weather_data, center_lon, center_lat, time_frame, selected_tcs_type.value)

    if at == AnalysisType.SOLAR_RADIATION:
        try:
            selected_month = dlg.month_dropdown_sr.currentData()
            selected_hours = dlg.hours_dropdown_sr.currentData()
            weather_file = dlg.weather_file_input_sr.currentText().strip()
        except AttributeError:
            QMessageBox.warning(dlg, "Missing Input", "Selected month and hours are required.")
            return None
        time_frame = makeTimeFrameObjWithMonth(month=selected_month.number, hourly=selected_hours.value)
        weather_data = query_infrared_epw(file_name=weather_file, type=Query_Type.UTCI, time_frame=time_frame, api_key=dlg.api_key)
        center_lon, center_lat = get_center_lon_lat_from_bbox(dlg.bbox, dlg.crs)
        return get_solar_radiation_payload(weather_data, center_lon, center_lat, time_frame)

    if at == AnalysisType.DAYLIGHT_AVAILABILITY:
        selected_month = dlg.month_dropdown_da.currentData()
        selected_hours = dlg.hours_dropdown_da.currentData()
        center_lon, center_lat = get_center_lon_lat_from_bbox(dlg.bbox, dlg.crs)
        return get_daylight_availability_payload(month=selected_month.number, hourly=selected_hours.value, lon=center_lon, lat=center_lat)

    if at == AnalysisType.DIRECT_SUN_HOURS:
        selected_month = dlg.month_dropdown_dsh.currentData()
        selected_hours = dlg.hours_dropdown_dsh.currentData()
        center_lon, center_lat = get_center_lon_lat_from_bbox(dlg.bbox, dlg.crs)
        return get_direct_sun_hours_payload(month=selected_month.number, hourly=selected_hours.value, lon=center_lon, lat=center_lat)

    if at == AnalysisType.SKY_VIEW_FACTORS:
        return {"analysis-type": at.value, "geometries": None}

    if at == AnalysisType.SHADOW_MASK:
        try:
            selected_datetime = dlg.datetime_dropdown.currentData()
        except AttributeError:
            from qgis.PyQt.QtWidgets import QMessageBox
            QMessageBox.warning(dlg, "Missing Input", "Selected datetime is required.")
            return None
        datetime_str = selected_datetime.strftime("%Y-%m-%dT%H:%M:%S+02:00")
        center_lon, center_lat = get_center_lon_lat_from_bbox(dlg.bbox, dlg.crs)
        return get_shadow_mask_payload(datetime_str, center_lon, center_lat)

    logger.warning("build_payload: unknown analysis type %s", at)
    return None


def run_tiles(dlg, payload, tile_centers):
    """Run simulation for each tile center and display the result.

    Updates the QGIS message bar with live progress. Stores the last
    geotiff_path on dlg.geotiff_path after each tile.
    """
    total = len(tile_centers)
    _status(f"🗺 {total} tile{'s' if total != 1 else ''} will be simulated", level=Qgis.Success)

    for idx, (center_x, center_y) in enumerate(tile_centers):
        logger.info("Processing tile %d/%d at (%s, %s)", idx + 1, total, center_x, center_y)

        buildings = collect_geometries(center_x, center_y, idx, geometry_type=GeometryTypes.BUILDINGS)
        trees = collect_geometries(center_x, center_y, idx, geometry_type=GeometryTypes.TREES)
        tree_dotbim = None

        if not buildings:
            logger.info("Tile %d: no buildings found, skipping.", idx)
            continue

        geojson_path, dotbim_path, bbox_512, crs_authid, bbox_256 = buildings

        if trees:
            _, dotbim_path_trees, *_ = trees
            tree_dotbim = load_dotbim(dotbim_path_trees)
            logger.info("Tile %d: trees loaded.", idx)

        try:
            dlg.dotbim_path = dotbim_path
            payload["geometries"] = None
            dotbim = load_dotbim(dotbim_path)
            if not dotbim:
                raise RuntimeError("No building geometry found.")
            payload["geometries"] = dotbim
            if tree_dotbim is not None:
                payload["vegetation"] = tree_dotbim

            _status(f"{idx+1}/{total} started…")
            dlg.geotiff_path, api_min, api_max = process_run_analysis(
                payload=payload,
                geometry_path=dotbim_path,
                bbox=bbox_512,
                crs=crs_authid,
                api_key=dlg.api_key,
                analysis_type=dlg.analysis_type.value,
            )

            # Legend range
            if dlg.analysis_type == AnalysisType.THERMAL_COMFORT_INDEX:
                leg_min = dlg.min_legend_value if dlg.legend_min_enable_tci.isChecked() else api_min
                leg_max = dlg.max_legend_value if dlg.legend_max_enable_tci.isChecked() else api_max
            elif dlg.analysis_type in {
                AnalysisType.SOLAR_RADIATION, AnalysisType.DAYLIGHT_AVAILABILITY,
                AnalysisType.DIRECT_SUN_HOURS, AnalysisType.SKY_VIEW_FACTORS,
            }:
                leg_min, leg_max = api_min, api_max
            else:
                leg_min, leg_max = None, None

            # Crop & display
            ds = gdal.Open(dlg.geotiff_path)
            if ds is None:
                logger.error("Could not open GeoTIFF for tile %d", idx)
                continue
            matrix = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
            ds = None

            cropped = crop_matrix(matrix, core_size=256)
            base = os.path.splitext(os.path.basename(dlg.geotiff_path))[0]
            cropped_path = os.path.join(os.path.dirname(dlg.geotiff_path), f"{base}_crop.tif")
            generate_geotiff(cropped, bbox_256, crs_authid, cropped_path, simulation_type=dlg.analysis_type.value)

            add_geojson_then_raster(
                geojson_path=geojson_path,
                geotiff_path=cropped_path,
                analysis_type=dlg.analysis_type.value,
                sub_analysis_type=dlg.sub_analysis_type.value if dlg.sub_analysis_type else None,
                min_legend_value=leg_min,
                max_legend_value=leg_max,
                tile_id=f"-tile {idx + 1}",
            )
            deselect_all()
            _status(f"{idx+1}/{total} displayed ✓", level=Qgis.Success)
            iface.mapCanvas().refresh()
            QApplication.processEvents()

        except Exception as e:
            logger.error("Tile %d failed: %s", idx, e, exc_info=True)
            _status(f"{idx+1}/{total} failed: {str(e)[:80]}", level=Qgis.Warning)

        logger.info("Tile %d/%d done.", idx + 1, total)

    result_dir = os.path.dirname(dlg.geotiff_path) if dlg.geotiff_path else ""
    _status(
        f"✅ Completed — {total}/{total} tiles simulated. Results saved to: {result_dir}",
        level=Qgis.Success,
        duration=15,
    )
