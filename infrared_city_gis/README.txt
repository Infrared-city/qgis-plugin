Infrared City GIS — QGIS Plugin
================================

A QGIS plugin that connects to the infrared.city simulation platform,
enabling urban planners and climate consultants to run microclimate
analyses directly inside QGIS.

Features
--------
- Download building geometry (1 km x 1 km) with the help of Infrared City platform
- Run microclimate simulations: wind speed, pedestrian wind comfort,
  thermal comfort (UTCI/TCS), solar radiation, daylight availability,
  direct sun hours, sky view factors
- Visualize results as raster layers in QGIS
- Tree catalog integration for vegetation analysis

Requirements
------------
- QGIS 3.44 - 3.x and QGIS 4.x
- An infrared.city API key (https://infrared.city)

Installation
------------
1. Download infrared-city-qgis.zip
2. Open QGIS > Plugins > Manage and Install Plugins > Install from ZIP
3. Select the downloaded zip and click Install Plugin
4. Enable the plugin from the plugin manager

Usage
-----
1. Save API Key - your Infrared City API key is verified, then saved locally
2. Download building geometry - a 1 km x 1 km area around the coordinates
3. Select buildings on that layer (an area), or press Select tile and click
   the map (one 512 x 512 m tile)
4. Optional, for UTCI/TCS: Download ground materials for the same selection
5. Run simulation - the result appears as a raster layer

Area or single tile
-------------------
Simulations run on 512 x 512 m tiles. An AREA run covers your building
selection with overlapping tiles (they step every 256 m, so even a 512 m
selection needs four) and runs each as a separate job, up to 100 tiles.
A SINGLE TILE run is one job (about 10 tokens): press Select tile, then
"Select bbox by center", and click the map - the 512 x 512 m tile around the
click is picked (the buildings layer must be active). The button stays pressed
until you run a simulation, click it again, clear the selection, or save a
different API key. Download ground materials uses the picked tile too.

Contact
-------
connectors@infrared.city
https://infrared.city
