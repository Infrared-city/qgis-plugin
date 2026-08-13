"""Pin ``_fake_dialog.FakeRunDialog`` to the real Run Simulation dialog.

The fake exists so the e2e tests can drive ``build_sdk_payload`` without the
whole UI. Its weakness is structural: it encodes what we *believe* the dialog
exposes, so a widget rename leaves the e2e tests green while the real dialog
breaks. These tests close that gap by comparing three sources that must agree:

* the widget object names in ``infrared_city_run_simulation_dialog.ui``
* the ``dlg.<attr>`` reads in ``services/sdk_payloads.py``
* the widget attributes ``FakeRunDialog`` sets

Free and offline — it reads files, it does not build a QDialog. That is also
why it checks names rather than behaviour: constructing the real dialog needs
``iface``, which is ``None`` outside QGIS.
"""

import re
from pathlib import Path

import pytest
from _fake_dialog import FakeRunDialog, _Check, _Combo, _Spin

from infrared_city_gis.models.analysis import AnalysisType

PLUGIN_ROOT = Path(__file__).parent.parent
UI_FILE = PLUGIN_ROOT / "infrared_city_run_simulation_dialog.ui"
PAYLOADS_FILE = PLUGIN_ROOT / "services" / "sdk_payloads.py"

#: Attributes the builder reads that are NOT widgets — the dialog sets them in
#: Python (``__init__`` or the analysis-type change handler), so they cannot be
#: expected in the .ui file.
NON_WIDGET_ATTRS = {
    "analysis_type",
    "api_key",
    "bbox",
    "crs",
    "sub_analysis_type",
    "_epw_paths",
    "min_legend_value",
    "max_legend_value",
    "use_infrared_ground_materials",
}


def _ui_widget_names():
    """Object names declared in the dialog's .ui file."""
    xml = UI_FILE.read_text(encoding="utf-8")
    return set(re.findall(r'name="([A-Za-z_][A-Za-z0-9_]*)"', xml))


def _builder_reads():
    """Attribute names ``build_sdk_payload`` and its helpers read off the dialog."""
    src = PAYLOADS_FILE.read_text(encoding="utf-8")
    names = set(re.findall(r"\bdlg\.([A-Za-z_][A-Za-z0-9_]*)", src))
    names |= set(re.findall(r'getattr\(dlg,\s*"([A-Za-z_][A-Za-z0-9_]*)"', src))
    return names


def _fake_widget_attrs(analysis_type):
    """Widget-like attributes ``FakeRunDialog`` exposes for one analysis type."""
    dlg = FakeRunDialog(
        analysis_type,
        api_key="dummy",
        bbox=(16.370, 48.213, 16.376, 48.217),
        crs="EPSG:4326",
        weather_file="dummy.epw",
    )
    return {
        name for name, value in vars(dlg).items()
        if isinstance(value, (_Combo, _Check, _Spin))
    }


def test_ui_file_is_readable():
    assert UI_FILE.exists(), f"the dialog .ui file moved: {UI_FILE}"
    assert _ui_widget_names(), "no object names parsed out of the .ui file"


@pytest.mark.parametrize("analysis_type", list(AnalysisType), ids=str)
def test_fake_widgets_exist_in_the_real_dialog(analysis_type):
    """Every widget the fake models is a real widget in the .ui file."""
    missing = sorted(_fake_widget_attrs(analysis_type) - _ui_widget_names())
    assert not missing, (
        f"FakeRunDialog models widgets the real dialog does not have for "
        f"{analysis_type}: {missing}. Either the .ui was renamed and the fake "
        "was not updated, or the fake invented a widget."
    )


def test_every_widget_the_builder_reads_is_modelled():
    """The fake covers every widget ``build_sdk_payload`` touches.

    Union across all analysis types: the builder reads one branch at a time, and
    a name is only reachable through its own branch's fake.
    """
    modelled = set()
    for analysis_type in AnalysisType:
        modelled |= _fake_widget_attrs(analysis_type)

    read = _builder_reads() - NON_WIDGET_ATTRS
    # Methods, not widgets — the collectors call these on the dialog.
    read -= {"selected_ground_material_layers"}

    missing = sorted(read - modelled)
    assert not missing, (
        f"build_sdk_payload reads dialog attributes the fake does not model: "
        f"{missing}. Add them to FakeRunDialog (or to NON_WIDGET_ATTRS if the "
        "dialog sets them in Python rather than in the .ui)."
    )


def test_builder_reads_only_real_widgets():
    """The builder does not read a widget name that no longer exists in the .ui."""
    read = _builder_reads() - NON_WIDGET_ATTRS - {"selected_ground_material_layers"}
    missing = sorted(read - _ui_widget_names())
    assert not missing, (
        f"build_sdk_payload reads widgets that are not in the .ui file: {missing}. "
        "This breaks the real dialog at runtime, not just the tests."
    )
