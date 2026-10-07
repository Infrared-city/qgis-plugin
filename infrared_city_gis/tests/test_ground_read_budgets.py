"""Every Overture ground-material read in the plugin passes an explicit budget.

Without ``timeout=`` the SDK's 60 s per-read default applies, and a single tile
already takes 39-64 s on a good line and over 150 s on a slow one (#47). The
three call sites live in three modules, so a new one, or a refactor that drops
the keyword from an old one, would only show up as fetches failing in the
field. Read with ``ast``: a call is a call, wherever it is.
"""

import ast
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent
_NOT_SHIPPED = {"tests", "test", "venv", ".venv", "thirdparty"}
_REQUIRED = {"timeout", "total_timeout"}


def ground_reads(path: Path) -> list:
    """``(where, keywords)`` of every ``<…>.ground_materials.get_area(…)`` call."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if (
            node.func.attr == "get_area"
            and isinstance(owner, ast.Attribute)
            and owner.attr == "ground_materials"
        ):
            keywords = {kw.arg for kw in node.keywords if kw.arg}
            found.append((f"{path.name}:{node.lineno}", keywords))
    return found


def _all_reads() -> list:
    reads = []
    for path in sorted(PLUGIN_ROOT.rglob("*.py")):
        if not _NOT_SHIPPED & set(path.relative_to(PLUGIN_ROOT).parts):
            reads.extend(ground_reads(path))
    return reads


def test_every_ground_read_has_an_explicit_budget():
    reads = _all_reads()
    missing = [where for where, keywords in reads if not _REQUIRED <= keywords]

    assert missing == [], "get_area without timeout/total_timeout: " + ", ".join(missing)


def test_the_scan_still_sees_the_three_call_sites():
    """If this drops, the scan stopped matching, not the code getting safer."""
    assert len(_all_reads()) == 3
