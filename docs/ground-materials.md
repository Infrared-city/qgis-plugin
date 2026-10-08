# Ground Materials

How to download, edit, and include ground-material (surface) layers in an
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
a plugin update. These five are the whole set — the download returns nothing
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

## Downloading ground materials

Use the **Download ground materials** toolbar action:

1. Select features on your **building layer** first — the selection defines
   the download area (the dialog asks you to *"select a building area first"*
   otherwise). If the **Select tile** toolbar button is pressed, that takes
   precedence: the download covers that one 512 m box — the same ground the
   simulation will run on — and the dialog says so. Downloading does not release
   the button, so you can run on the materials you just downloaded.
2. The dialog shows the selection size in tiles (512×512 m each). Areas over
   **100 tiles** are rejected — select a smaller area.
3. **Download** reads the surface layers straight from **Overture Maps**
   (land cover/use + a road-surface FlatGeobuf) on your own computer, through
   the Infrared City SDK, and cleans them the way the platform does: streets and
   water are carved out of vegetation/soil and gaps are filled with asphalt.
   This download sends **no API key and costs no tokens**, but it moves a lot
   of data — tens of seconds on a good connection, a few minutes on a slow
   one. See *How long it takes, and what a failure means* below.

The result is added as one **editable vector layer per material**, named by
convention:

```
ground-asphalt   ground-concrete   ground-water
ground-soil      ground-vegetation
```

Downloading again (a different area, a larger selection) numbers the new layers
— `ground-asphalt-2`, `ground-water-2`, … — so downloads stay
distinguishable. The trailing number is ignored when the material is
resolved, and the simulation dialog lists every layer separately so you can
tick exactly the ones you want.

> **Why does `ground-asphalt` contain a huge rectangle?** That's the
> server's gap-fill: every spot not covered by another material is asphalt
> by default, so the layer ships a selection-covering background polygon.
> It's intentional and needed by the simulation — don't delete it. The
> layers are drawn semi-transparent so it doesn't hide the map.

### How long it takes, and what a failure means

- The read runs in the background: the dialog keeps repainting, shows the
  elapsed time, and can be closed mid-read. Closing does **not** stop the
  download — the SDK has no way to interrupt it — so it finishes in the
  background and its result is dropped. Until it does, a new download is refused
  with *Previous Download Still Running*, so retries never stack up downloads on
  the same connection.
- Time limit: **5 minutes per read, 10 minutes in total** (large selections
  are read in chunks). The values are `GROUND_FETCH_TIMEOUT_S` and
  `GROUND_FETCH_TOTAL_TIMEOUT_S` in `constants.py`.
- *Download Timed Out* — the read did not finish in time: a slow or congested
  connection. A smaller area downloads less; your own `ground-*` layers need
  no download at all.
- *Ground Materials Could Not Be Read* — the download failed. Behind a
  company proxy or firewall, `overturemaps-us-west-2.s3.us-west-2.amazonaws.com`
  must be reachable.
- Neither message is about your API key: this read never sends one.
- **Windows:** QGIS's own Arrow library is built without S3 support, so the
  SDK (1.0 and later) reads the same public files over plain HTTPS instead —
  same result, one warning line in the log (infrared-core #645).

## Editing / drawing your own

Each download is saved as one GeoPackage in the plugin's data folder
(`<QGIS profile>/infrared_city_gis/data/infrared_city_ground_materials_<date-time>.gpkg`,
one table per `ground-*` layer), next to the downloaded buildings. They are files,
not QGIS scratch layers: no "Temporary scratch layer only!" warning, they
survive a QGIS restart, and a saved project opens them again. Edit them freely
before running a simulation (reshape polygons, delete wrong areas, add new
ones) — edits are written back to the file. Like the building files, a file
there that has not been modified for 30 days is deleted when the plugin starts;
to keep a set for longer, save it next to your project (*Export → Save
Features As…*). You can also create a layer from scratch: any polygon layer named
`ground-<material>` participates automatically, so a hand-drawn
`ground-water` (or a future registry material) works without any plugin
support.

## Using them in a simulation

For the analyses that use surface materials, the Run Simulation dialog shows
a **Ground materials** section listing the `ground-*` layers in the project —
downloaded with the dialog above, or drawn yourself. The run never downloads
anything itself: an earlier "fetch at submit" option froze QGIS for the whole
read and downloaded the same area again on every run, so it was removed (#47).
Download once, then run as many analyses as you like on the same layers.

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
- With no `ground-*` layers in the project the list is hidden and the
  section says to download them with the *Download ground materials* dialog first,
  or draw your own.

At submission each ticked layer is read into its material's
FeatureCollection. Features are never merged across materials — the
material identity is the dict key the server's emissivity lookup uses, and
it comes from the layer name. Surfaces outside the selection are sent too,
as far as the download itself reaches: a circle around the selection's centre,
half its bounding-box diagonal and at least 544 m (on a 1 km square, ~210 m
past each side). The thermal model of the edge tiles uses that band, and the
SDK still crops per tile. Buildings and trees use a 100 m band.

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
  utilities-service), and the plugin emits the payload in it so a run on
  your layers stacks the way the platform's own does.

## Size limits

Both the download dialog and the simulation dialog cap the selection at **100
tiles** (≈ 26 km²) — the same limit the SDK enforces internally, so the two
can never disagree. Large payloads are handled automatically (the SDK
switches to an S3 upload for request bodies over 5 MiB).
