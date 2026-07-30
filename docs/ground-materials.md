# Ground Materials

How to fetch, edit, and include ground-material (surface) layers in an
Infrared City simulation. Ground materials tell the thermal analyses (UTCI,
TCS) what each surface is made of — without them, every surface runs with a
generic server default.

They only matter for the two thermal analyses: **UTCI and TCS** (the
backend models use them for the per-material ground-longwave term). All
other analyses ignore the input — the solar/daylight family and SVF accept
but explicitly discard it, and the wind models don't read it at all — so
for those the Run Simulation dialog hides the ground-material section and
nothing is sent.

## Supported materials

Listed bottom → top in the stacking order (see *Overlaps and stacking* below):

| Material | What it covers |
|---|---|
| `asphalt` | Roads, paved areas (also the server's gap-fill default) |
| `concrete` | Hard surfaces — parking, industrial/commercial areas |
| `water` | Water bodies, wetlands |
| `soil` | Bare ground, sand, agriculture |
| `vegetation` | **Green surfaces** — grass, lawns, parks |

The list is registry-driven: the plugin refreshes it from the materials
registry when you save your API key, so new backend materials appear without
a plugin update. These five are the whole set — the fetch returns nothing
else, and the run dialog offers nothing else.

> **A `ground-*` layer naming a material that isn't on this list is ignored**
> — a typo (`ground-asphlat`), a leftover `ground-building`, an unrelated
> `ground-parcels`. The server would not reject such a key; it would silently
> assign a fabricated mid-range surface and quietly change your result, so the
> run dialog does not list those layers. Buildings in particular are 3D volumes
> that belong in the building layer, not a flat ground surface.

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
   (Overture land cover/use + road-surface FlatGeobuf, cleaned server-side:
   streets and water are carved out of vegetation/soil and gaps are filled
   with asphalt).

The result is added as one **editable vector layer per material**, named by
convention:

```
ground-asphalt   ground-concrete   ground-water
ground-soil      ground-vegetation
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

## Overlaps and stacking

Where two materials cover the same spot, the thermal model resolves it
geometrically: the surfaces are stacked as 2.5D geometry a hundredth of a
millimetre apart and a ray fired straight down from each sensor takes the
**topmost** one. The order (bottom → top) is
`asphalt → concrete → water → soil → vegetation`, and a material the backend
adds later stacks above all five. Asphalt is lowest because it is the gap-fill
background covering the whole area — anything above it wins; vegetation is
highest, so a park drawn over a road reads as vegetation.

Two consequences for hand-drawn layers:

- You don't need to cut holes in the layers underneath. Draw a pond in
  `ground-water` straight over `ground-asphalt` and the pond wins — but note
  water sits *below* soil and vegetation, so a park polygon overlapping the
  pond would win instead. Trim the vegetation polygon if that's not what you
  want.
- The order is the platform's own (`_CANONICAL_Z_ORDER` in the
  utilities-service), and the plugin emits the payload in it so a manual run
  stacks identically to an auto-fetched one.

## Size limits

Both the fetch dialog and the simulation dialog cap the selection at **100
tiles** (≈ 26 km²) — the same limit the SDK enforces internally, so the two
can never disagree. Large payloads are handled automatically (the SDK
switches to an S3 upload for request bodies over 5 MiB).
