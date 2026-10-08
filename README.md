# Infrared City GIS — QGIS Plugin

QGIS plugin that connects to the [Infrared City](https://infrared.city) simulation platform for urban climate analysis.

Download building geometry with the help of Infrared City platform, run microclimate simulations, and visualize results as raster layers — all without leaving QGIS.

**An Infrared City subscription is required to run simulations.** [Get access →](https://infrared.city)

## Features

- Download building geometry for a 1 km × 1 km area with the help of Infrared City platform
- Run climate simulations: wind speed, pedestrian wind comfort (PWC), thermal comfort (UTCI/TCS), solar radiation, daylight availability, direct sun hours, sky view factors
- Upload a local EPW weather file for weather-based analyses (PWC / UTCI / TCS / solar radiation), or use the built-in weather lookup
- Vegetation from a tree point layer — any OpenStreetMap tree layer works as-is: trees are typed from their own `species` / `genus` / `leaf_type` tags (matching a registry species for an exact mesh, otherwise a broadleaf/conifer/columnar/palm archetype; untagged → broadleaf). Nothing is mandatory but the point geometry. See [`docs/vegetation-input.md`](docs/vegetation-input.md)
- Ground materials — download editable surface layers (asphalt, concrete, water, soil, vegetation) for a selected area and include them in the thermal comfort simulations (UTCI, TCS). See [`docs/ground-materials.md`](docs/ground-materials.md)
- Results visualized as raster layers in QGIS

## Requirements

- QGIS 3.44 – 3.x and QGIS 4.x (one package serves both Qt5 and Qt6)
- An Infrared City API key

## Installation

**From ZIP:**
1. Download `infrared-city-qgis.zip` from [Releases](https://github.com/Infrared-city/qgis-plugin/releases)
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**
3. Select the downloaded ZIP and click **Install Plugin**
4. Enable the plugin in the plugin manager

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

See [`infrared_city_gis/README.md`](infrared_city_gis/README.md) for the in-plugin documentation.

## Development

```bash
pip install -r infrared_city_gis/requirements.txt
```

Plugin packaging is configured in `infrared_city_gis/pb_tool.cfg`.

## License

GPL-2.0-or-later — see [LICENSE](LICENSE).

QGIS plugins must be GPL-compatible because they link against PyQGIS (itself GPL-licensed). The plugin code is open source; access to the Infrared City simulation backend requires a subscription.
