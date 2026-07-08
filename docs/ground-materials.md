# Ground Materials

How to fetch, edit, and include ground-material (surface) layers in an
Infrared City simulation. Ground materials tell the thermal analyses (UTCI,
TCS) and the solar/daylight analyses what each surface is made of — without
them, every surface runs with a generic server default.

They only matter for analyses that use surface materials: **UTCI, TCS,
Solar Radiation, Daylight Availability, Direct Sun Hours**. Wind Speed,
Pedestrian Wind Comfort, and Sky View Factors are pure geometry — for those
the Run Simulation dialog hides the ground-material section and nothing is
sent.

## Supported materials

| Material | What it covers |
|---|---|
| `asphalt` | Roads, paved areas (also the server's gap-fill default) |
| `building` | Building footprints (as land cover) |
| `concrete` | Hard surfaces — parking, industrial/commercial areas |
| `vegetation` | **Green surfaces** — grass, lawns, parks |
| `soil` | Bare ground, sand, agriculture |
| `water` | Water bodies, wetlands |

The list is registry-driven: the plugin refreshes it from the materials
registry when you save your API key, so new backend materials appear without
a plugin update. The six above are the canonical set the automatic fetch can
return.

> **`vegetation` here is NOT trees.** This material is 2D green *surface*
> polygons (grass, parks). Trees are separate 3D objects that live in a
> `tree-*` **point** layer — see
> [`vegetation-input.md`](vegetation-input.md). The two never mix: ground
> materials are `ground-*` polygon layers.

## Fetching ground materials

Use the **Fetch ground materials** toolbar action:

1. Select features on your **building layer** first — the selection defines
   the fetch area (the dialog asks you to *"select a building area first"*
   otherwise). If you have a pending **Select tile** pick (single-tile
   mode), that takes precedence: the fetch covers that one tile and the
   pick stays available for Run Simulation afterwards.
2. The dialog shows the selection size in tiles (512×512 m each). Areas over
   **100 tiles** are rejected — select a smaller area.
3. **Fetch** pulls the surface layers from the Infrared City platform
   (Mapbox land cover, cleaned server-side: surfaces under buildings are
   masked out and gaps are filled with asphalt).

The result is added as one **editable vector layer per material**, named by
convention:

```
ground-asphalt   ground-building   ground-concrete
ground-vegetation   ground-soil   ground-water
```

Fetching again (a different area, a larger selection) numbers the new layers
— `ground-asphalt-2`, `ground-water-2`, … — so downloads stay
distinguishable. The trailing number is ignored when the material is
resolved, and the simulation dialog lists every layer separately so you can
tick exactly the ones you want.

> **Why does `ground-asphalt` contain a huge rectangle?** That's the
> server's gap-fill: every spot not covered by another material is asphalt
> by default, so the layer ships a selection-covering background polygon.
> It's intentional and needed by the simulation — don't delete it. The
> layers are drawn semi-transparent so it doesn't hide the map.

## Editing / drawing your own

The `ground-*` layers are ordinary QGIS memory layers — edit them freely
before running a simulation (reshape polygons, delete wrong areas, add new
ones). You can also create a layer from scratch: any polygon layer named
`ground-<material>` participates automatically, so a hand-drawn
`ground-water` (or a future registry material) works without any plugin
support.

## Using them in a simulation

For the analyses that use surface materials, the Run Simulation dialog shows
a **Ground materials** section with two ways to provide them:

- **Use Infrared ground materials (auto-fetch)** — tick this to skip the
  layer workflow entirely: the plugin fetches Infrared's own surface layers
  for your selected area at submit time and ignores any `ground-*` layers.
- **Layer list** — when the project contains `ground-*` layers, one
  checkable row per layer (`asphalt — ground-asphalt`). Nothing is ticked by
  default: tick the layers you want to include. **One layer per material** —
  the simulation takes a single layer per surface type, so ticking a second
  asphalt layer automatically unticks the first. Only ticked layers are
  validated, and only problems are reported: a ticked layer with nothing
  inside your selection shows up as
  *"Not found on the selected area: water — ground-water-2."* — silence
  means every ticked layer covers the area.

- Layers left unticked are not sent — those surfaces run with the server
  default material.
- If none of the ticked layers has features inside your selection:
  *"No ground material data found on the selected area — the ticked layer
  has no features inside your selection."* (or *"…the ticked layers
  have…"* when several are ticked).
- With no `ground-*` layers in the project the list is hidden (the
  auto-fetch option stays available).

At submission each ticked layer is read into its material's
FeatureCollection. Features are never merged across materials — the
material identity is the dict key the server's emissivity lookup uses, and
it comes from the layer name. Like buildings and trees, surfaces up to
~100 m outside the selection are also sent as context.

## Size limits

Both the fetch dialog and the simulation dialog cap the selection at **100
tiles** (≈ 26 km²) — the same limit the SDK enforces internally, so the two
can never disagree. Large payloads are handled automatically (the SDK
switches to an S3 upload for request bodies over 5 MiB).
