# Infrared City GIS — QGIS Plugin

A QGIS plugin that connects to the [Infrared City](https://infrared.city) simulation platform, enabling urban planners and climate consultants to run climate analyses directly inside QGIS.

## Features

- Download building geometry (1 km × 1 km) with the help of Infrared City platform for any location
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
- Ground materials — download editable surface layers (asphalt, concrete, water, soil, vegetation) for a selected area and include them in the thermal comfort simulations (UTCI, TCS)

## Requirements

- QGIS 3.44 – 3.x and QGIS 4.x
- An Infrared City API key ([infrared.city](https://infrared.city))

## Installation

1. Download `infrared-city-qgis.zip`
2. Open QGIS → **Plugins** → **Manage and Install Plugins** → **Install from ZIP**
3. Select the downloaded zip file and click **Install Plugin**
4. Enable the plugin from the plugin manager

## Usage

1. Open the plugin from the QGIS toolbar or **Plugins** menu
2. **Save API Key** — your Infrared City API key is verified against the server, then saved locally for future sessions; the other toolbar actions stay disabled until a valid key is saved
3. **Download building geometry** — enter the centre coordinates; a 1 km × 1 km area of buildings is added as a layer
4. Choose what to simulate: **select buildings** on that layer (an area), or press **Select tile** and click the map (one 512 × 512 m tile) — see *Area or single tile* below
5. Optional, for thermal comfort (UTCI, TCS): **Download ground materials** for the same selection, and add a `tree-*` point layer for trees
6. **Run simulation** — choose the analysis and its parameters (for weather-based analyses you can **Upload EPW…** to use a local weather file instead of the built-in lookup), then run; the result appears as a raster layer

## Area or single tile

Simulations run on 512 × 512 m tiles. You choose what to run in one of two ways:

- **Area (the default)** — select buildings on your buildings layer with any
  QGIS selection tool, then open **Run simulation**. The plugin covers the
  selection with tiles and runs each one as a separate job; the dialog title
  shows how many (*Run Simulation — 4 tiles selected*). The tiles overlap — they
  step every 256 m — so even a selection 512 m across needs four of them. Up to
  100 tiles.
- **Single tile** — press **Select tile** in the toolbar, then
  *Select bbox by center*, and click the map. A 512 × 512 m tile centred on your
  click is picked and its buildings are highlighted (the buildings layer must
  be the active layer, and a tile without buildings is refused). The run is
  **one job**: the dialog title reads
  *single tile (512×512 m) · 1 tile · ~10 tokens*.

While a tile is picked, the **Select tile** button stays pressed, and
**Download ground materials** downloads exactly that tile. The tile stays
picked until you run a simulation, click the pressed button again, clear the
map selection yourself, or save a different API key — then the plugin is back
in area mode.

## Contact

[connectors@infrared.city](mailto:connectors@infrared.city) · [infrared.city](https://infrared.city)

