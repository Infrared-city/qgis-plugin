"""A binned ramp must colour the pixels above its top band, not drop them.

The legend the backend recommends is a DISPLAY range, not the data range. A
UTCI run legended 21-30 over a grid that reaches 31 therefore has valid pixels
above the top band — and a QGIS Discrete ramp colours a pixel with the first
item whose value is >= the pixel's, so nothing matched and QGIS drew them as
transparent. Over the canvas that reads as white, and it was 15% of the valid
pixels: the hottest ones, open sun and water, which is what the map is read
for.

These tests drive the real ``QgsColorRampShader``. The behaviour being fixed is
QGIS's, not ours, so asserting on our item list would prove nothing.
"""

import pytest

from infrared_city_gis.visualization.color_ramp import _build_color_ramp_items

pytestmark = pytest.mark.qgis

#: Shaped like the registry's thermal-comfort-index entry: binned, no steps,
#: so the caller's vmin/vmax are what the ramp spans.
UTCI_CONFIG = {
    "colors": [
        [56, 70, 114], [56, 174, 173], [105, 173, 56],
        [222, 226, 105], [194, 134, 62], [240, 0, 0], [140, 0, 0],
    ],
    # Both are explicit JSON nulls in the registry, not missing keys.
    "steps": None,
    "stepsNames": None,
    "colorInterpolation": "binned",
}

LINEAR_CONFIG = dict(UTCI_CONFIG, colorInterpolation="linear")


def _shade(shader, value):
    """``(ok, r, g, b)`` for one pixel value."""
    ok, r, g, b, _a = shader.shade(float(value))
    return ok, (r, g, b)


@pytest.fixture
def utci_shader(qgis_app):
    shader, _items, _vmin, _vmax = _build_color_ramp_items(
        UTCI_CONFIG, "thermal-comfort-index", vmin=21.0, vmax=30.0,
    )
    return shader


def test_a_pixel_above_the_legend_still_gets_a_colour(utci_shader):
    """The bug itself: 31 C with a 21-30 legend used to render as nothing."""
    ok, rgb = _shade(utci_shader, 31.0)

    assert ok, "a pixel above the top band was left uncoloured"
    assert rgb == (140, 0, 0), "it should take the top band's colour"


def test_far_above_the_legend_is_still_the_top_colour(utci_shader):
    """No upper bound on how far out of range a pixel may be."""
    ok, rgb = _shade(utci_shader, 1e6)

    assert ok
    assert rgb == (140, 0, 0)


def test_values_inside_the_legend_are_unchanged(utci_shader):
    """Opening the top band must not shift anything below it.

    The bottom and an interior value both keep their own colours — a fix that
    flattened the ramp into its top colour would pass the tests above.
    """
    ok_low, rgb_low = _shade(utci_shader, 21.0)
    ok_mid, rgb_mid = _shade(utci_shader, 25.5)

    assert ok_low and rgb_low == (56, 70, 114)
    assert ok_mid and rgb_mid == (222, 226, 105)


def test_the_legend_still_names_its_real_bound(qgis_app):
    """The label is taken before the value is opened up, so it is not 'inf'."""
    _shader, items, _vmin, _vmax = _build_color_ramp_items(
        UTCI_CONFIG, "thermal-comfort-index", vmin=21.0, vmax=30.0,
    )

    assert items[-1].value == float("inf")
    assert "inf" not in items[-1].label.lower()
    assert items[-1].label == "30.00"


def test_an_interpolated_ramp_is_left_alone(qgis_app):
    """QGIS already clamps an Interpolated ramp to its end colours."""
    _shader, items, _vmin, _vmax = _build_color_ramp_items(
        LINEAR_CONFIG, "thermal-comfort-index", vmin=21.0, vmax=30.0,
    )

    assert items[-1].value == 30.0


def test_a_categorical_ramp_is_left_alone(qgis_app):
    """Class matrices are 1..N integers — nothing can fall off the top."""
    config = {
        "colors": [[56, 70, 114], [56, 174, 173], [105, 173, 56]],
        "steps": ["A", "B", "C"],
        "stepsNames": [],
        "colorInterpolation": "binned",
    }

    _shader, items, vmin, vmax = _build_color_ramp_items(
        config, "pedestrian-wind-comfort",
    )

    assert [i.value for i in items] == [1, 2, 3]
    assert (vmin, vmax) == (1.0, 3.0)


#: Registry 1.6 gives thermal-comfort-index the full UTCI scale as `steps`.
UTCI_FULL_SCALE_CONFIG = dict(UTCI_CONFIG, steps=[-40, 46], stepsNames=[])


def test_the_registry_scale_does_not_override_the_legend(qgis_app):
    """A 23-31 C grid legended 21-30 was drawn on -40..46 — one colour band."""
    _shader, items, vmin, vmax = _build_color_ramp_items(
        UTCI_FULL_SCALE_CONFIG, "thermal-comfort-index", vmin=21.0, vmax=30.0,
    )

    assert (vmin, vmax) == (21.0, 30.0)
    assert items[0].value == 21.0
    # A [min, max] pair is not one label per colour: no band is called "46".
    assert [i.label for i in items[:2]] == ["21.00", "22.50"]


def test_the_registry_scale_is_the_fallback_without_a_legend(qgis_app):
    _shader, _items, vmin, vmax = _build_color_ramp_items(
        UTCI_FULL_SCALE_CONFIG, "thermal-comfort-index",
    )

    assert (vmin, vmax) == (-40.0, 46.0)
