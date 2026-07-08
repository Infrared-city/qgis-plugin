# Vegetation (Tree) Input Requirements

How to prepare a **tree layer** for an Infrared City simulation. The short
version: **any OSM tree layer works as-is.** Trees are typed from their own
OpenStreetMap tags — you don't need to prepare or match anything. Only the
point geometry is mandatory; every other attribute is optional and only makes
the result more precise.

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
  into the area), but the counts in the Run Simulation dialog include **only
  trees inside the selected area** itself.

A layer fetched straight from OpenStreetMap (e.g. an Overpass export of
`natural=tree`) satisfies all of this — load it, name it `tree-*`, done.

## Per-tree attributes

Attribute names are matched **case-insensitively**. The only mandatory field
is the point geometry; everything else is optional.

| Attribute | Purpose | Required? |
|---|---|---|
| *point geometry* (lon/lat) | Tree position | **Mandatory** |
| `species` | OSM species binomial, e.g. `Quercus robur` | Optional — best type signal |
| `genus` | OSM genus, e.g. `Quercus` | Optional — type signal |
| `leaf_type` | OSM `broadleaved` / `needleleaved` | Optional — coarse type signal |
| `height` | Tree height in metres (`15` or `"15 m"`) | Optional — defaults to a representative size |
| `crownDiameter` / `diameter_crown` | Crown diameter in metres | Optional — defaults to a representative size |

These are the standard OpenStreetMap tree tags — see the OSM wiki
[`natural=tree`](https://wiki.openstreetmap.org/wiki/Tag:natural%3Dtree) for
the full scheme. Any other attributes (ids, notes, `taxon`, `leaf_cycle`,
`circumference`, …) are passed through untouched and don't affect the
simulation.

> There is no Infrared-specific attribute to add — the plugin reads the trees'
> own OSM tags.

## How a tree's type is resolved

Every tree renders — there is no "unsupported" type. Resolution happens in two
tiers:

1. **Precise species (exact mesh).** If a tree's `species` or `genus` matches a
   species in the vegetation registry (the [catalog](#precise-species-catalog)),
   it renders with that species' exact high-detail mesh. Example:
   `species = "Quercus robur"` → the English Oak mesh.
2. **Archetype (shape class).** Otherwise the tree is resolved to one of four
   low-poly shape classes from whichever tag it carries, in order:

   ```
   tree-type → genus → species (first word) → leaf_type → broadleaf (default)
   ```

   So `genus = "Acer"` → **broadleaf**, `leaf_type = "needleleaved"` →
   **conifer**, and a bare `natural=tree` point with no type tag → **broadleaf**.
   The four archetypes are **broadleaf, conifer, columnar, palm**.

You don't choose between the tiers — the plugin sends precise-species trees
with their registry id and everything else without one, and the backend
resolves the archetype. `leaf_cycle`, `taxon` and `circumference` are currently
**not** used.

## Sizes and defaults

- If a tree carries `height` / `crownDiameter` (or `diameter_crown`), those are
  used as-is (`"15 m"` with a unit is fine).
- A **precise species** with no size tag uses that species' catalog default
  (see the table below).
- An **archetype** tree with no size tag uses a representative default size for
  its shape class.

Provide `height` / `crownDiameter` per tree to override any default.

## Precise species catalog

The registry species — each is an **exact mesh**, not required for a run but
available when you want a specific tree. Also listed live in the plugin's
**Tree Catalog** dialog (fetched when you save your API key):

| Species (Latin name) | Default height | Default crown diameter |
|---|---|---|
| Pinus pinea (Stone Pine) | 30 m | 33.2 m |
| Euphorbia tirucallidis (Pencil Tree) | 9 m | 11 m |
| Cupressus sempervirens (Mediterranean Cypress) | 30 m | 7.5 m |
| Quercus ilex (Holm Oak) | 15 m | 20 m |
| Larix decidua (European Larch) | 40 m | 30 m |
| Quercus robur (English Oak) | 30 m | 27 m |
| Combretum collinum (Bushwillow) | 12 m | 9.7 m |
| Taxodium distichum (Bald Cypress) | 40 m | 22.2 m |

A tree matches one of these by its OSM `species` or `genus` tag (e.g.
`species = "Quercus robur"` → English Oak).

## Examples

A ready-to-load sample covering every resolution tier is in
[`examples/osm-trees-sample.geojson`](examples/osm-trees-sample.geojson) — open
it in QGIS, rename the layer to contain `tree-`, and it runs as-is.

### OSM-native layer (the common case)

A layer straight from OpenStreetMap — just the native tags:

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": 2044194845,
      "geometry": { "type": "Point", "coordinates": [16.3785, 48.2050] },
      "properties": {
        "natural": "tree",
        "species": "Acer platanoides",
        "leaf_type": "broadleaved",
        "height": "17",
        "diameter_crown": "6"
      }
    },
    {
      "type": "Feature",
      "id": 2044194872,
      "geometry": { "type": "Point", "coordinates": [16.3688, 48.2050] },
      "properties": { "natural": "tree" }
    }
  ]
}
```

Tree 1 (`Acer platanoides`) has no matching registry species, so it renders as
a **broadleaf** archetype at its tagged 17 m × 6 m. Tree 2 carries no type or
size — it renders as the **broadleaf** default at a representative size.

### Getting exact meshes

You get a precise mesh automatically when a tree's OSM `species` matches a
registry species — no extra attribute needed:

| `species` | `height` | `crownDiameter` |
|---|---|---|
| `Quercus robur` | 18 | 12 |
| `Quercus robur` | | |
| `Pinus pinea` | 25 | 20 |

Row 2 omits the size — that tree gets English Oak's catalog defaults
(30 m × 27 m). Species that aren't in the registry (e.g. `Acer platanoides`)
render as their archetype instead — still automatic, no attribute to add.

## Validation in the Run Simulation dialog

When you pick a tree layer, the dialog validates it over the selected area
(only trees **inside** the drawn polygon are counted) and reports the
breakdown, e.g.:

> 42 tree point(s) detected — 8 as a catalog species (5× English Oak, 3× Stone
> Pine); 30 by their OSM type (genus/species/leaf_type) as an archetype; 4 as
> the default broadleaf.

The run is **never blocked** — every tree renders. The breakdown just tells you
what to expect before submitting.

## Tree catalog (reference + override)

The **Tree Catalog** dialog:

- **lists the precise registry species** with their names and default
  dimensions (a reference — your layer doesn't need to use any of them; a
  matching OSM `species` picks the mesh automatically), and
- provides an **override**: pick a species + size, then tick *"Use tree catalog
  tree type"* in the Run Simulation dialog to force that one species onto
  **every** tree in the area (ignoring their per-tree OSM types). Useful when a
  layer has no type tags and you want a single deliberate species rather than
  the automatic archetype resolution.

Left un-ticked, per-tree OSM types are used as described above.
