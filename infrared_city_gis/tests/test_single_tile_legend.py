"""The single-tile path legends a result the same way the area path does.

Both now apply the same three tiers — the backend's recommendation, the grid's
own range where it sent none, and the dialog's manual values over both. Before
this the single-tile path skipped the first tier, so the same scenario run as
one tile and as an area produced two different colour scales and could not be
compared.

The wire keys are the fragile part, and they have broken before: the SDK's own
reader matched camelCase only, so `min_legend` came back None for every area
run for a whole release and nobody noticed, because falling back to the grid
range looks entirely reasonable. That is the failure these tests are for.
"""

import pytest

from infrared_city_gis.services.sdk_single_tile import legend_from_result


def test_the_kebab_wire_keys_are_read():
    """What a real job result actually carries."""
    assert legend_from_result({
        "output": [], "min-legend": 21.0, "max-legend": 30.0,
    }) == (21.0, 30.0)


def test_camel_case_is_accepted_as_a_fallback():
    """Mirrors the SDK's own defensive fallback for unaudited workers."""
    assert legend_from_result({"minLegend": 21, "maxLegend": 30}) == (21.0, 30.0)


def test_kebab_wins_when_both_are_present():
    assert legend_from_result({
        "min-legend": 21.0, "minLegend": 99.0,
        "max-legend": 30.0, "maxLegend": 99.0,
    }) == (21.0, 30.0)


def test_a_result_without_a_legend_yields_nothing():
    """Then the caller falls back to the grid's own range, as it always did."""
    assert legend_from_result({"output": []}) == (None, None)


@pytest.mark.parametrize("value", [None, "30", [30], {}, True])
def test_a_non_numeric_legend_is_ignored(value):
    """A bad value must not reach the ramp as a bound.

    ``True`` is in here deliberately: it is an ``int`` to Python, and a legend
    max of 1.0 would silently collapse the whole colour scale.
    """
    assert legend_from_result({"max-legend": value})[1] is None


def test_integers_come_back_as_floats():
    """The ramp arithmetic is float; an int bound must not change that."""
    low, high = legend_from_result({"min-legend": 21, "max-legend": 30})

    assert isinstance(low, float) and isinstance(high, float)


def test_one_sided_legends_are_allowed():
    """A result may carry one bound and not the other."""
    assert legend_from_result({"max-legend": 30.0}) == (None, 30.0)
