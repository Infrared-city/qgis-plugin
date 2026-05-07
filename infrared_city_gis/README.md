# Infrared City GIS — QGIS Plugin

A QGIS plugin that connects to the [Infrared City](https://infrared.city) simulation platform, enabling urban planners and climate consultants to run climate analyses directly inside QGIS.

## Features

- Fetch building geometry from OpenStreetMap for any location
- Run climate simulations:
  - Wind Speed
  - Pedestrian Wind Comfort (PWC)
  - Thermal Comfort Index (UTCI)
  - Thermal Comfort Statistics (TCS)
  - Solar Radiation
  - Daylight Availability
  - Direct Sun Hours
  - Sky View Factors
  - Shadow Mask
- Visualize results as raster layers in QGIS
- Tree catalog integration for vegetation analysis

## Requirements

- QGIS 3.0 or later
- An Infrared City API key ([infrared.city](https://infrared.city))

## Installation

1. Download `infrared-city-qgis.zip`
2. Open QGIS → **Plugins** → **Manage and Install Plugins** → **Install from ZIP**
3. Select the downloaded zip file and click **Install Plugin**
4. Enable the plugin from the plugin manager

## Usage

1. Open the plugin from the QGIS toolbar or **Plugins** menu
2. Enter your Infrared City API key (saved locally for future sessions)
3. Select an area of interest on the map
4. Fetch building geometry from OpenStreetMap
5. Choose a simulation type and configure the parameters
6. Run the simulation — results appear as a raster layer in QGIS

## Contact

[connectors@infrared.city](mailto:connectors@infrared.city) · [infrared.city](https://infrared.city)

