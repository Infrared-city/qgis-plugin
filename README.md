# Infrared City GIS — QGIS Plugin

QGIS plugin that connects to the [Infrared City](https://infrared.city) simulation platform for urban climate analysis.

Fetch building geometry with the help of Infrared City platform, run microclimate simulations, and visualize results as raster layers — all without leaving QGIS.

**An Infrared City subscription is required to run simulations.** [Get access →](https://infrared.city)

## Features

- Fetch building geometry for a 1 km × 1 km area with the help of Infrared City platform
- Run climate simulations: wind speed, pedestrian wind comfort (PWC), thermal comfort (UTCI/TCS), solar radiation, daylight availability, direct sun hours, sky view factors
- Upload a local EPW weather file for weather-based analyses (PWC / UTCI / TCS / solar radiation), or use the built-in weather lookup
- Vegetation from a tree point layer — any OpenStreetMap tree layer works as-is: trees are typed from their own `species` / `genus` / `leaf_type` tags (matching a registry species for an exact mesh, otherwise a broadleaf/conifer/columnar/palm archetype; untagged → broadleaf). Nothing is mandatory but the point geometry. See [`docs/vegetation-input.md`](docs/vegetation-input.md)
- Ground materials — fetch editable surface layers (asphalt, concrete, vegetation, soil, water, building) for a selected area and include them in thermal/solar simulations. See [`docs/ground-materials.md`](docs/ground-materials.md)
- Results visualized as raster layers in QGIS

## Requirements

- QGIS 3.44 – 3.x (QGIS 4 is not yet supported — the plugin is PyQt5-based)
- An Infrared City API key

## Installation

**From ZIP:**
1. Download `infrared-city-qgis.zip` from [Releases](https://github.com/Infrared-city/qgis-plugin/releases)
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**
3. Select the downloaded ZIP and click **Install Plugin**
4. Enable the plugin in the plugin manager

## Usage

1. Open the plugin from the QGIS toolbar or **Plugins** menu
2. Enter your Infrared City API key — it is verified against the server and stored locally; the other toolbar actions stay disabled until a valid key is saved
3. Select an area of interest on the map
4. Fetch building geometry (a 1 km × 1 km area around the entered coordinates)
5. Choose a simulation type and configure parameters — for weather-based analyses you can **Upload EPW…** to use a local weather file instead of the built-in lookup
6. Run — results appear as a raster layer

See [`infrared_city_gis/README.md`](infrared_city_gis/README.md) for the in-plugin documentation.

## Development

```bash
pip install -r infrared_city_gis/requirements.txt
```

Plugin packaging is configured in `infrared_city_gis/pb_tool.cfg`.

## License

GPL-2.0-or-later — see [LICENSE](LICENSE).

QGIS plugins must be GPL-compatible because they link against PyQGIS (itself GPL-licensed). The plugin code is open source; access to the Infrared City simulation backend requires a subscription.
