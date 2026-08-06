#!/usr/bin/env python3
"""Resolve every Qt name the plugin uses against a real PyQt binding.

Qt6 removed all *unscoped* enum members and moved a few classes between
modules. Those breakages are invisible to a linter and to an import smoke
test alike: the accesses sit inside functions, so nothing touches them until
a user opens the relevant dialog. This script resolves them ahead of time.

It parses the source with :mod:`ast` (not a regex, so comments and docstrings
can never produce a false failure), collects every dotted attribute access
whose root is a Qt class, and looks each one up in the requested binding.

    python scripts/check_qt6_names.py infrared_city_gis            # PyQt6
    python scripts/check_qt6_names.py infrared_city_gis --binding pyqt5

Exit status is non-zero when any name fails to resolve.

Scope: Qt names only. QGIS classes (``Qgs*``, ``Qgis``) need a QGIS install,
which CI does not have — they are reported as skipped so the gap is visible
rather than silent. To cover those too, run this with the interpreter inside
a QGIS app bundle (see docs/battle-scars.md for the invocation).
"""
from __future__ import annotations

import argparse
import ast
import importlib
import pathlib
import sys

# The Qt modules the plugin imports (via the qgis.PyQt shim). Keep in sync
# with: grep -rho 'qgis\.PyQt\.Qt[A-Za-z]*' infrared_city_gis | sort -u
QT_MODULES = ("QtCore", "QtGui", "QtWidgets", "QtNetwork")

# Directory names never worth scanning, at any depth.
SKIP_DIRS = {"__pycache__", "site-packages", "venv"}


def dotted_name(node: ast.Attribute) -> str | None:
    """Return ``A.b.c`` for an attribute chain rooted in a plain name."""
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return ".".join(reversed(parts))


def collect_names(root: pathlib.Path) -> dict[str, list[str]]:
    """Map ``Qt name -> ["file:line", ...]`` for every Qt access under root."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        # Skip caches and any vendored environment living inside the package
        # (`.venv/`, `venv/`): their test suites are full of Q-prefixed locals
        # that have nothing to do with Qt.
        parts = path.parts
        if set(parts) & SKIP_DIRS:
            continue
        if any(part.startswith(".") for part in parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            print(f"  ! could not parse {path}: {exc}", file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            name = dotted_name(node)
            if not name:
                continue
            # Anything Q-prefixed: Qt classes (QLineEdit, QTimer, Qt) resolve
            # below, QGIS ones (QgsProject, Qgis) do not and get reported as
            # uncovered rather than silently dropped here.
            if name.split(".", 1)[0].startswith("Q"):
                found.setdefault(name, []).append(f"{path}:{node.lineno}")
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="package directory to scan")
    ap.add_argument("--binding", default="pyqt6", choices=("pyqt5", "pyqt6"))
    args = ap.parse_args()

    pkg = "PyQt6" if args.binding == "pyqt6" else "PyQt5"
    modules = []
    for name in QT_MODULES:
        try:
            modules.append(importlib.import_module(f"{pkg}.{name}"))
        except ImportError as exc:
            print(f"{pkg}.{name} is not importable: {exc}", file=sys.stderr)
            return 2

    # Roots resolve either as a class exported by a Qt module (``QLineEdit``)
    # or as a module alias, since the plugin also does
    # ``from qgis.PyQt import QtWidgets`` and then ``QtWidgets.QDialog``.
    by_module = {name: mod for name, mod in zip(QT_MODULES, modules)}

    def resolve_root(head: str):
        if head in by_module:
            return by_module[head]
        for mod in modules:
            obj = getattr(mod, head, None)
            if obj is not None:
                return obj
        return None

    names = collect_names(pathlib.Path(args.path))
    failures: list[tuple[str, str, list[str]]] = []
    skipped: set[str] = set()
    checked = 0

    for name, locations in sorted(names.items()):
        head, *rest = name.split(".")
        obj = resolve_root(head)
        if obj is None:
            skipped.add(head)  # QGIS class, or not part of this binding
            continue
        checked += 1
        for attr in rest:
            obj = getattr(obj, attr, None)
            if obj is None:
                failures.append((name, f"{head} has no attribute chain .{attr}", locations))
                break

    print(f"{pkg}: {checked} Qt name(s) checked, {len(failures)} unresolved")
    if skipped:
        print(f"  not covered here (needs QGIS): {', '.join(sorted(skipped))}")
    for name, why, locations in failures:
        print(f"\n  ✗ {name} — {why}")
        for loc in locations:
            print(f"      {loc}")
    if failures:
        print(
            "\nQt6 removed unscoped enum members: use the scoped spelling "
            "(Qt.CheckState.Checked, QLineEdit.EchoMode.Password, ...), which "
            "works on PyQt5 too."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
