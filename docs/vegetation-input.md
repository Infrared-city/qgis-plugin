# Vegetation (Tree) Input Requirements

How to prepare a **tree layer** for an Infrared City simulation: which
attributes each tree point must carry, the supported tree types, and what the
plugin validates before a run.

> Trees are 3D point objects. Green *surfaces* (grass, lawns, parks) are a
> **ground material** (`ground-vegetation` polygon layer) — see
> [`ground-materials.md`](ground-materials.md).

## What the plugin expects

- A **point** vector layer whose name contains `tree-` (so it appears in the
  "Tree layer" dropdown of the Run Simulation dialog).
- **One point per tree.** Non-point features are skipped.
- Any CRS — the plugin reprojects to WGS84 (`EPSG:4326`) before submission.
- Trees are filtered to the **selected simulation area**. Trees up to ~100 m
  outside it are also sent as context (they cast shadows / disturb airflow
  into the area), but the validation counts in the Run Simulation dialog
  include **only trees inside the selected area** itself.

## Per-tree attributes

| Attribute (case-insensitive) | Purpose | Required? |
|---|---|---|
| *point geometry* (lon/lat) | Tree position | **Mandatory** |
| `genusCode` | Tree type — one of the [supported values](#supported-tree-types) below | **Mandatory** for typed trees |
| `height` | Tree height in metres | Optional — defaults to the type's catalog height |
| `crownDiameter` (or `diameter_crown`) | Crown diameter in metres | Optional — defaults to the type's catalog crown diameter |

Other attributes (ids, notes, anything else) are passed through untouched and
don't affect the simulation.

The `genusCode` value is matched **case-insensitively** against the supported
types; the Latin name (e.g. `Quercus Robur`) or display name (e.g.
`English Oak`) are also accepted in the same attribute. A tree whose type
doesn't match any supported value is simulated with a generic default tree
(≈ 6 m tall, 4 m crown), so always check the validation count in the Run
Simulation dialog.

## Supported tree types

Current catalog (also listed live in the plugin's **Tree Catalog** dialog,
which is fetched from the vegetation registry when you save your API key):

| `genusCode` | Species | Default height | Default crown diameter |
|---|---|---|---|
| `pinus-pinea` | Stone Pine | 30 m | 33.2 m |
| `euphorbia-tirucallidis` | Pencil Tree | 9 m | 11 m |
| `cupressus-sempervirens` | Mediterranean Cypress | 30 m | 7.5 m |
| `quercus-ilex` | Holm Oak | 15 m | 20 m |
| `larix-decidua` | European Larch | 40 m | 30 m |
| `quercus-robur` | English Oak | 30 m | 27 m |
| `combretum-collinum` | Bushwillow | 12 m | 9.7 m |
| `taxodium-distichum` | Bald Cypress | 40 m | 22.2 m |

The *default* height / crown diameter apply when your layer doesn't provide
`height` / `crownDiameter` for a tree. Provide them per tree to override.

## Examples

### QGIS attribute table

A minimal typed tree layer looks like this (one row per tree point):

| `genusCode` | `height` | `crownDiameter` |
|---|---|---|
| `quercus-robur` | 18 | 12 |
| `quercus-robur` | | |
| `pinus-pinea` | 25 | 20 |

Row 2 omits the size — that tree gets English Oak's catalog defaults
(30 m × 27 m). Only `genusCode` (plus the point geometry) is needed to type a
tree.

### GeoJSON

The same data as GeoJSON (e.g. when importing a layer from file):

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [9.1815, 45.4685] },
      "properties": {
        "genusCode": "quercus-robur",
        "height": 18,
        "crownDiameter": 12
      }
    },
    {
      "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [9.1821, 45.4689] },
      "properties": {
        "genusCode": "pinus-pinea"
      }
    }
  ]
}
```

## Validation in the Run Simulation dialog

When you pick a tree layer, the dialog validates it over the selected area
(only trees **inside** the drawn polygon are counted) and shows how many tree
points were detected and how many resolved to a supported type, with a
per-type count, e.g.:

> 42 tree point(s) detected — 40 with a supported tree type: 25× English Oak,
> 15× Stone Pine.

Outcomes:

- **Some trees resolve** — they are simulated with their own type and size;
  unresolved ones fall back to the generic default tree.
- **Trees found, but none resolve** — *"No tree type was detected on this
  area"* — either
  1. add a `genusCode` attribute per this document, or
  2. tick **"Use tree catalog tree type"** to fall back to a single catalog
     species + size applied to every tree in the area.

  If neither is satisfied, the run is blocked with a clear message.
- **No tree points in the selected area** — the catalog fallback is disabled
  (there is nothing to apply it to) and the run is not blocked.

## Tree catalog (fallback + reference)

The **Tree Catalog** dialog:

- **lists the supported tree types** with their `genusCode` and default
  dimensions (fetched from the vegetation registry when you save your API
  key), and
- provides the **fallback** type: when a layer has no usable `genusCode` and
  you tick *"Use tree catalog tree type"*, the selected catalog species + size
  are applied to every tree in the area (the legacy behaviour).

It never overrides layers that already carry a usable `genusCode` — those are
used as-is, per tree.
