"""End-to-end workflow against prod: fetch buildings, fetch ground materials, simulate.

A developer test, not a CI test:

    INFRARED_API_KEY=<key> pytest -m e2e -s                       # fetch checks only
    INFRARED_RUN_SIMULATIONS=1 INFRARED_API_KEY=<key> pytest -m e2e -s
    UPDATE_BASELINE=1 INFRARED_RUN_SIMULATIONS=1 … pytest -m e2e -s   # re-record numbers
    UPDATE_FIXTURES=1 INFRARED_API_KEY=<key> pytest -m e2e -s     # re-record the inputs

Without ``INFRARED_API_KEY`` the whole module skips, so CI stays free; the
simulations are gated separately on ``INFRARED_RUN_SIMULATIONS=1`` because each
is billable. How a run is produced lives in ``_sim_runner.py``; this file is
about what we assert. Every run writes its summary to ``tests/results/``.

**Recorded inputs, live checks.** A simulation result is only comparable to an
earlier one if it came from the same inputs, so the simulations do NOT run on
whatever prod returns today: buildings and ground materials are replayed from
``fixtures/``, recorded once with ``UPDATE_FIXTURES=1``. The fetch tests still
call prod and compare their counts to the baseline — that is how a platform-side
data refresh gets noticed, without it moving the simulation numbers too.

The buildings never reach a simulation "from prod" in any case: the plugin only
ever reads a QGIS layer (``collect_qgis_area_buildings``), which the fetch dialog
fills from ``POST /v2/buildings`` — or the user fills themselves. These tests
load the recorded GeoJSON into the project exactly as that dialog would.

What stays unpinned is the model version, which is server-side. So values carry
a drift tolerance (``DRIFT_LIMIT``, plus ``ABS_TOL`` for quantities that sit at
zero) while the *inputs* are compared exactly — a settings change fails loudly
instead of quietly rebasing.
"""

import json
import os
from pathlib import Path

import pytest
from _baseline import DRIFT_LIMIT, compare_to_baseline, record
from _sim_runner import (
    AREA_SIZE_M,
    BASELINE,
    LAT,
    LON,
    SITE,
    WEATHER_FILE,
    polygon,
    run_single_tile,
)

from infrared_city_gis.models.analysis import AnalysisType

pytestmark = pytest.mark.e2e

FIXTURES_DIR = Path(__file__).parent / "fixtures"
BUILDINGS_FIXTURE = FIXTURES_DIR / f"{BASELINE}_buildings.geojson"
GROUND_FIXTURE = FIXTURES_DIR / f"{BASELINE}_ground_materials.json"

UTCI = AnalysisType.THERMAL_COMFORT_INDEX

#: Smallest change worth failing over, per analysis type, in its own unit. These
#: carry the near-zero comparisons, where relative drift has nothing to divide by
#: — SVF, sun hours and solar radiation all bottom out at 0 in deep shade. They
#: are "half a unit the user would notice", not measured noise floors.
ABS_TOL = {
    AnalysisType.WIND_SPEED: 0.05,                  # m/s
    AnalysisType.PEDESTRIAN_WIND_COMFORT: 0.05,     # Lawson class index
    AnalysisType.THERMAL_COMFORT_INDEX: 0.1,        # °C
    AnalysisType.THERMAL_COMFORT_STATISTICS: 0.1,   # °C
    AnalysisType.SOLAR_RADIATION: 0.5,              # W/m²
    AnalysisType.DAYLIGHT_AVAILABILITY: 0.5,        # %
    AnalysisType.DIRECT_SUN_HOURS: 0.05,            # hours
    AnalysisType.SKY_VIEW_FACTORS: 0.5,             # %, 0–100 (not a 0–1 factor)
}

requires_simulations = pytest.mark.skipif(
    os.environ.get("INFRARED_RUN_SIMULATIONS", "").strip() != "1",
    reason="set INFRARED_RUN_SIMULATIONS=1 — these submit paid single-tile runs",
)


@pytest.fixture(scope="module", autouse=True)
def _require_key():
    if not os.environ.get("INFRARED_API_KEY", "").strip():
        pytest.skip("INFRARED_API_KEY is not set — e2e tests hit prod and cost tokens")


@pytest.fixture(scope="module")
def key():
    return os.environ["INFRARED_API_KEY"].strip()


def _recording_fixtures() -> bool:
    return os.environ.get("UPDATE_FIXTURES", "").strip() == "1"


# --- 1. Buildings — live fetch (checked), recorded copy (simulated on) -------

@pytest.fixture(scope="module")
def live_buildings(key, qgis_app):
    """Fetch the 1 km x 1 km building tile from prod, and record it if asked.

    Needs ``qgis_app``: the plugin's HTTP layer is ``QgsBlockingNetworkRequest``,
    which segfaults rather than raising if QGIS was never initialised.
    """
    from infrared_city_gis.services.fetch import fetch_geometry_from_infrared

    path, bbox, error = fetch_geometry_from_infrared(LON, LAT, AREA_SIZE_M, key)
    assert error is None, f"buildings fetch failed: {error}"
    assert path, "buildings fetch returned no file — the area came back empty"

    with open(path, encoding="utf-8") as fh:
        collection = json.load(fh)

    if _recording_fixtures():
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        BUILDINGS_FIXTURE.write_text(json.dumps(collection), encoding="utf-8")
        record("fixtures.buildings", "recorded", BUILDINGS_FIXTURE.name)

    return {"path": path, "bbox": bbox, "features": collection.get("features", [])}


def test_buildings_fetch(live_buildings):
    """We get buildings back, and the count has not moved unexpectedly."""
    features = live_buildings["features"]

    assert len(features) > 0, "no buildings returned for a dense urban tile"
    assert live_buildings["bbox"], "no bbox returned alongside the geometry"

    with_geometry = [f for f in features if f.get("geometry")]
    assert len(with_geometry) == len(features), "some features carry no geometry"

    compare_to_baseline(BASELINE, "buildings.count", len(features))


@pytest.fixture(scope="module")
def project_buildings(qgis_app, request):
    """Load the RECORDED buildings into the QGIS project, as the fetch dialog does.

    This is the simulation input: ``collect_qgis_area_buildings`` resolves its
    source by looking for a "building" layer in the project. Recorded rather
    than live so the geometry behind every ``sim.*`` baseline stays fixed.
    """
    from infrared_city_gis.visualization.layers import display_geojson

    if _recording_fixtures():
        request.getfixturevalue("live_buildings")   # writes the fixture file

    if not BUILDINGS_FIXTURE.exists():
        pytest.skip(
            f"{BUILDINGS_FIXTURE.name} has not been recorded — run once with "
            "UPDATE_FIXTURES=1 INFRARED_API_KEY=<key> pytest -m e2e"
        )

    display_geojson(str(BUILDINGS_FIXTURE))
    count = len(json.loads(BUILDINGS_FIXTURE.read_text(encoding="utf-8"))["features"])
    record("fixtures.buildings", "ok", f"{count} features from {BUILDINGS_FIXTURE.name}")
    return BUILDINGS_FIXTURE


# --- 2. Ground materials — live fetch (checked), recorded copy (simulated on) -

@pytest.fixture(scope="module")
def live_ground_materials(key, qgis_app):
    """Fetch ground materials for the same area, and record them if asked.

    Deliberately does NOT add layers to the project: the ``ground-*`` layers the
    simulations collect come from the recorded fixture, and two sets of them in
    one project would make ``ground_material_layers()`` ambiguous.
    """
    from infrared_sdk import InfraredClient

    with InfraredClient(api_key=key) as client:
        area_gm = client.ground_materials.get_area(polygon(LON, LAT, AREA_SIZE_M))

    assert area_gm.layers, "ground-material fetch returned no layers"

    if _recording_fixtures():
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        GROUND_FIXTURE.write_text(json.dumps(area_gm.layers), encoding="utf-8")
        record("fixtures.ground", "recorded", GROUND_FIXTURE.name)

    return area_gm.layers


#: Materials whose count moves faster than ``DRIFT_LIMIT`` for reasons outside
#: this plugin. Measured 2026-08-12 on the reference tile: vegetation returned
#: 615 → 556 → 622 → 630 → 650 → 661 → 638 across seven calls, six of them inside
#: an hour — up and down, not a one-way refresh — while asphalt (1376), concrete
#: (126), water (21) and soil (0) never moved at all. Every run still records the
#: count (the number is the signal); it just cannot be a 5% assertion until the
#: ground-material source question is settled. Tighten back to the default then.
UNSTABLE_MATERIAL_TOL = {"vegetation": 0.5}


def test_ground_materials_fetch(live_ground_materials):
    """Per-layer feature counts, and the materials stay within the canon."""
    from infrared_city_gis.services.ground_materials import MATERIAL_Z_ORDER

    for material in live_ground_materials:
        assert material in MATERIAL_Z_ORDER, (
            f"server returned an unknown material {material!r} — the plugin would "
            "send it on and the model would fall back to a default surface"
        )

    total = 0
    for material, collection in sorted(live_ground_materials.items()):
        count = len(collection.get("features", []))
        total += count
        compare_to_baseline(
            BASELINE, f"ground.{material}.count", count,
            rel_tol=UNSTABLE_MATERIAL_TOL.get(material))
    compare_to_baseline(BASELINE, "ground.total", total,
                        rel_tol=UNSTABLE_MATERIAL_TOL.get("vegetation"))


@pytest.fixture(scope="module")
def saved_ground_layers(qgis_app, request):
    """Build the ``ground-*`` layers from the RECORDED materials.

    Mirrors the fetch dialog: ``display_ground_materials`` names the layers and
    adds them to the project, then ``ground_material_layers()`` collects them
    back the way the simulation dialog does.
    """
    from infrared_city_gis.services.ground_materials import ground_material_layers
    from infrared_city_gis.visualization.layers import display_ground_materials

    if _recording_fixtures():
        request.getfixturevalue("live_ground_materials")   # writes the fixture file

    if not GROUND_FIXTURE.exists():
        pytest.skip(
            f"{GROUND_FIXTURE.name} has not been recorded — run once with "
            "UPDATE_FIXTURES=1 INFRARED_API_KEY=<key> pytest -m e2e"
        )

    saved = json.loads(GROUND_FIXTURE.read_text(encoding="utf-8"))
    created = display_ground_materials(saved)
    assert created, "no ground-* layers were created from the recorded materials"

    layers = ground_material_layers()
    assert layers, "the ground-* layers are not in the project"
    record("fixtures.ground", "ok", f"{sorted(layers)} from {GROUND_FIXTURE.name}")
    return layers


# --- 3. Weather --------------------------------------------------------------

def test_weather_files_available(key, qgis_app):
    """The weather catalogue for this location has not changed under us."""
    from infrared_city_gis.services.fetch import fetch_weather_file_names

    names = fetch_weather_file_names(LON, LAT, 100, key)

    assert names, "no weather files found for the reference location"
    compare_to_baseline(BASELINE, "weather.count", len(names))
    compare_to_baseline(BASELINE, "weather.names", sorted(names))


@pytest.fixture(scope="module")
def weather_file(key, qgis_app):
    """The weather file the matrix runs with — pinned, not "whatever sorts first".

    A simulation baseline is only valid for the weather it was recorded with, so
    this cannot be derived from the live catalogue: a new station appearing
    earlier in the sort order would silently change the inputs while the
    baseline stayed put. Pinned in ``_sim_runner``, and checked to still exist.
    """
    from infrared_city_gis.services.fetch import fetch_weather_file_names

    names = fetch_weather_file_names(LON, LAT, 100, key)
    assert WEATHER_FILE in names, (
        f"the pinned weather file {WEATHER_FILE!r} is gone from the catalogue for "
        f"this location — every sim.* baseline was recorded with it. Pick a "
        f"replacement from {sorted(names)} and re-record."
    )
    record("weather.used", "ok", WEATHER_FILE)
    return WEATHER_FILE


# --- 4. Simulations ----------------------------------------------------------

def _check_against_baseline(analysis_type, stats, *, suffix=""):
    """Compare one run's inputs (exactly) and its numbers (with tolerance).

    Inputs first: numbers recorded under different settings are not a baseline,
    they are a different measurement. This fails before the values do, and says so.
    """
    prefix = f"sim.{analysis_type}{suffix}"
    compare_to_baseline(BASELINE, "site", SITE)
    compare_to_baseline(BASELINE, f"{prefix}.config", stats["config"])

    tol = ABS_TOL[analysis_type]
    for field in ("min", "max", "mean"):
        compare_to_baseline(
            BASELINE, f"{prefix}.{field}", round(stats[field], 3), abs_tol=tol)


@requires_simulations
@pytest.mark.parametrize("analysis_type", list(AnalysisType), ids=str)
def test_single_tile_matches_baseline(
        key, analysis_type, project_buildings, saved_ground_layers, weather_file):
    """Every analysis type still produces the grid we recorded earlier.

    One paid single-tile run per type, driven through the plugin's own payload
    builder and collectors with the stand-in dialog — so a regression anywhere
    in that chain moves these numbers. Every input is recorded: the buildings
    layer, the ground-* layers, the weather file and the analysis settings.

    min/max/mean rather than the whole grid: the grid is 512×512 versioned model
    output, so storing it would be a baseline nobody can read and everybody
    re-records. mean is the one that catches a regression that leaves the
    extremes intact — a shifted or partly-empty surface.
    """
    stats = run_single_tile(
        key, analysis_type, weather_file=weather_file, ground=saved_ground_layers)

    record(f"sim.{analysis_type}", "ok",
           f"min {stats['min']:.3f} max {stats['max']:.3f} mean {stats['mean']:.3f}")
    _check_against_baseline(analysis_type, stats)


@requires_simulations
def test_utci_ground_materials_change_the_result(
        key, project_buildings, saved_ground_layers, weather_file):
    """No ground materials at all vs. water only — the surface must matter.

    Two paid runs. Each is compared to its own baseline, and against each other:
    if supplying a water surface does not move the thermal result, the materials
    are being dropped somewhere between the collector and the model, and no
    absolute baseline would tell us that on its own.
    """
    assert "water" in saved_ground_layers, (
        "the recorded ground materials have no water layer — pick a tile that "
        "has one, or drop this test"
    )

    bare = run_single_tile(key, UTCI, weather_file=weather_file, ground=None)
    water = run_single_tile(
        key, UTCI, weather_file=weather_file,
        ground={"water": saved_ground_layers["water"]})

    record("sim.utci.no-ground", "ok",
           f"min {bare['min']:.3f} max {bare['max']:.3f} mean {bare['mean']:.3f}")
    record("sim.utci.water-only", "ok",
           f"min {water['min']:.3f} max {water['max']:.3f} mean {water['mean']:.3f}")

    _check_against_baseline(UTCI, bare, suffix=".no-ground")
    _check_against_baseline(UTCI, water, suffix=".water-only")

    delta = water["mean"] - bare["mean"]
    record("utci water vs no ground", "ok" if delta else "FAIL", f"Δmean {delta:+.3f}")
    assert delta, (
        "adding a water surface changed nothing in the UTCI result — the ground "
        "materials are not reaching the model"
    )


@requires_simulations
def test_utci_auto_fetch_matches_manual_layers(
        key, project_buildings, saved_ground_layers, weather_file):
    """The two ways of supplying ground materials must agree.

    Auto-fetch pulls the layers from the platform at submit time; the manual
    path collects the ground-* layers already in the project. Both are cropped
    server-side to the same tile bbox, so the results should land on top of each
    other. They are NOT bit-identical — the manual collector ships a 100 m
    context margin the server then discards, and the layers made a round trip
    through QGIS — hence a relative tolerance rather than equality.

    This is the assertion worth keeping long-term: it compares two code paths
    against each other, so it survives data refreshes and model updates that
    would invalidate any absolute number. It is also the one place a live fetch
    still feeds a simulation — that is what the auto path *is*, and it is why
    this test can drift when the recorded materials no longer match prod.
    """
    auto = run_single_tile(key, UTCI, weather_file=weather_file, ground="auto")
    manual = run_single_tile(
        key, UTCI, weather_file=weather_file, ground=saved_ground_layers)

    record("utci.auto", "ok", f"min {auto['min']:.3f} max {auto['max']:.3f}")
    record("utci.manual", "ok", f"min {manual['min']:.3f} max {manual['max']:.3f}")

    for field in ("min", "max"):
        a, m = auto[field], manual[field]
        drift = abs(a - m) / max(abs(m), 1e-9)
        status = "ok" if drift <= DRIFT_LIMIT else "FAIL"
        record(f"utci.{field} auto vs manual", status, f"Δ {a - m:+.3f} ({drift:.2%})")
        assert drift <= DRIFT_LIMIT, (
            f"UTCI {field} differs by {drift:.1%} between auto-fetched and manually "
            f"supplied ground materials ({a:.3f} vs {m:.3f}). The two paths should "
            "produce the same surface — one of them is dropping or mis-ordering "
            "material layers."
        )
