"""No plugin code may reach the retiring utilities service (#47).

Its routes all live under ``/v2/utils``. The last two the plugin used — the
weather-station lookup and the ground-material collect — were removed; this
keeps a new one from creeping back in through a URL constant or an f-string.

Read with ``ast`` rather than grep, so prose that names the old routes (the
docstrings explaining why they are gone) cannot fail it, while a URL built in
an f-string still does.
"""

import ast
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent
UTILITIES_PATH = "/utils"
_NOT_SHIPPED = {"tests", "test", "venv", ".venv", "thirdparty"}


def utilities_strings(path: Path) -> list:
    """Every non-docstring string literal in *path* that names a /utils route."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        f"{path.name}:{node.lineno}: {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
        and UTILITIES_PATH in node.value
    ]


def test_no_code_string_points_at_the_utilities_service():
    offenders = []
    for path in sorted(PLUGIN_ROOT.rglob("*.py")):
        if not _NOT_SHIPPED & set(path.relative_to(PLUGIN_ROOT).parts):
            offenders.extend(utilities_strings(path))
    assert offenders == [], "utilities-service URLs in plugin code:\n" + "\n".join(offenders)


def test_the_scan_catches_an_f_string_url_and_spares_docstrings(tmp_path):
    """The removed constants were f-strings; a docstring naming them is fine."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        'BASE = "https://api.infrared.city/v2"\n'
        'URL = f"{BASE}/utils/weather/location"\n'
        "def f():\n"
        '    """Docstrings may name /utils/weather/location."""\n',
        encoding="utf-8",
    )

    found = utilities_strings(sample)

    assert len(found) == 1
    assert "sample.py:2:" in found[0]
