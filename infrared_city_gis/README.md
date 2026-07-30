# Infrared City GIS — QGIS Plugin

A QGIS plugin that connects to the [Infrared City](https://infrared.city) simulation platform, enabling urban planners and climate consultants to run climate analyses directly inside QGIS.

## Features

- Fetch building geometry (1 km × 1 km) with the help of Infrared City platform for any location
- Run climate simulations:
  - Wind Speed
  - Pedestrian Wind Comfort (PWC)
  - Thermal Comfort Index (UTCI)
  - Thermal Comfort Statistics (TCS)
  - Solar Radiation
  - Daylight Availability
  - Direct Sun Hours
  - Sky View Factors
- Upload a local EPW weather file for weather-based analyses (PWC / UTCI / TCS / Solar Radiation), or use the built-in weather lookup
- Visualize results as raster layers in QGIS
- Vegetation from a tree point layer — any OpenStreetMap tree layer works as-is: trees are typed from their own `species` / `genus` / `leaf_type` tags (registry species → exact mesh, otherwise a broadleaf/conifer/columnar/palm archetype; untagged → broadleaf). Only the point geometry is mandatory
- Ground materials — fetch editable surface layers (asphalt, concrete, water, soil, vegetation) for a selected area and include them in thermal/solar simulations

## Requirements

- QGIS 3.44 – 3.x (QGIS 4 is not yet supported)
- An Infrared City API key ([infrared.city](https://infrared.city))

## Installation

1. Download `infrared-city-qgis.zip`
2. Open QGIS → **Plugins** → **Manage and Install Plugins** → **Install from ZIP**
3. Select the downloaded zip file and click **Install Plugin**
4. Enable the plugin from the plugin manager

## Usage

1. Open the plugin from the QGIS toolbar or **Plugins** menu
2. Enter your Infrared City API key — it is verified against the server, then saved locally for future sessions; the other toolbar actions are disabled until a valid key is saved
3. Select an area of interest on the map
4. Fetch building geometry (a 1 km × 1 km area around the entered coordinates)
5. Choose a simulation type and configure the parameters — for weather-based analyses you can **Upload EPW…** to use a local weather file instead of the built-in lookup
6. Run the simulation — results appear as a raster layer in QGIS

## Contact

[connectors@infrared.city](mailto:connectors@infrared.city) · [infrared.city](https://infrared.city)

