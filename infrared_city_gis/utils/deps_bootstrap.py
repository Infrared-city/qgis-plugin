import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

# Paths relative to this file (utils/)
PLUGIN_DIR = Path(__file__).resolve().parents[1]

# Legacy install location: deps used to live *inside* the plugin folder. That
# breaks plugin uninstall — QGIS deletes the whole plugin directory, but the
# loaded numpy/mapbox_earcut native extensions (.pyd/.dll) are locked by the
# running process on Windows, so the folder can't be removed and the uninstall
# fails. We now install outside the plugin dir (see _deps_dir) and best-effort
# remove this stale folder on load.
_LEGACY_THIRDPARTY = PLUGIN_DIR / "thirdparty"
_LEGACY_MARKER = PLUGIN_DIR / ".deps_ok"

# Re-entry guard. Set in the env when we spawn a pip subprocess so that if the
# child process happens to be a QGIS-style binary that re-loads the plugin, the
# nested ensure_deps() call returns immediately instead of recursing.
_REENTRY_ENV = "INFRARED_BOOTSTRAP_RUNNING"

# Cached resolved deps directory (resolved lazily — qgis may be unavailable at
# import time, e.g. under pytest).
_DEPS_DIR: Optional[Path] = None


def _deps_root() -> Path:
    """Parent dir holding the per-requirement-set deps folders — OUTSIDE the plugin.

    Kept in the active QGIS profile dir so that uninstalling the plugin (which
    deletes the plugin folder) never has to touch these files. On Windows the
    loaded native extensions are locked by the running process; if they lived
    under the plugin folder the uninstall would abort with "folder in use".

    Falls back to ``~/.qgis_infrared_city_gis`` if the QGIS settings dir can't
    be resolved (e.g. running outside QGIS).
    """
    base: Optional[Path] = None
    try:
        from qgis.core import QgsApplication
        settings = QgsApplication.qgisSettingsDirPath()
        if settings:
            base = Path(settings)
    except Exception:
        base = None
    if base is None:
        base = Path.home() / ".qgis_infrared_city_gis"
    return base / "infrared_city_gis"


def _reqs_hash() -> str:
    """Short, order-independent hash of the requirement specs.

    Used to namespace the deps folder so that a plugin version with a DIFFERENT
    dependency set installs into a fresh, empty folder (correct versions) rather
    than reusing stale packages from the previous set. ``_find_missing`` only
    checks importability, not version, so without this a bumped pin (e.g.
    ``infrared-sdk>=0.4.10`` -> ``>=0.5.0``) would never be reinstalled.
    """
    specs = sorted(_read_requirements())
    digest = hashlib.sha1("\n".join(specs).encode("utf-8")).hexdigest()
    return digest[:12]


def _deps_dir() -> Path:
    """The deps folder for the CURRENT requirement set: ``<root>/deps-<hash>``."""
    global _DEPS_DIR
    if _DEPS_DIR is not None:
        return _DEPS_DIR
    _DEPS_DIR = _deps_root() / f"deps-{_reqs_hash()}"
    return _DEPS_DIR


def _marker() -> Path:
    return _deps_dir() / ".deps_ok"


def _cleanup_legacy_thirdparty() -> None:
    """Best-effort removal of the old in-plugin thirdparty/ folder.

    Safe because we no longer add it to ``sys.path``, so on a fresh QGIS
    session nothing has imported from it and the files aren't locked. Uses
    ``ignore_errors`` so a still-locked leftover never breaks plugin load.
    """
    for stale in (_LEGACY_THIRDPARTY, _LEGACY_MARKER):
        try:
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
            elif stale.exists():
                stale.unlink()
        except Exception:
            pass


def _cleanup_old_deps_dirs() -> None:
    """Remove deps folders for OTHER requirement sets (previous plugin versions).

    Only the current ``deps-<hash>`` is on ``sys.path`` this session, so sibling
    folders aren't loaded and can be deleted. ``ignore_errors`` keeps a folder
    still locked from a same-session reload from breaking the load — it'll be
    cleaned on the next fresh start.
    """
    keep = _deps_dir().name
    root = _deps_root()
    try:
        if not root.is_dir():
            return
        for child in root.iterdir():
            if child.is_dir() and child.name.startswith("deps-") and child.name != keep:
                shutil.rmtree(child, ignore_errors=True)
    except Exception:
        pass


def _read_requirements() -> List[str]:
    req_file = PLUGIN_DIR / "requirements.txt"
    if not req_file.exists():
        return []
    lines: List[str] = []
    for raw in req_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _ensure_sys_path():
    deps = _deps_dir()
    deps.mkdir(parents=True, exist_ok=True)
    p = str(deps)
    if p not in sys.path:
        sys.path.insert(0, p)


def _base_pkg_name(spec: str) -> str:
    # Extract import name from requirement spec like 'pkg', 'pkg>=1.0', 'pkg[extra]==1.2.3'
    name_chars = []
    for ch in spec:
        if ch in "=<>!~[; ":
            break
        name_chars.append(ch)
    return "".join(name_chars).strip().replace("-", "_")


def _find_missing(packages: List[str]) -> List[str]:
    missing: List[str] = []
    for spec in packages:
        base = _base_pkg_name(spec)
        if not base:
            continue
        try:
            __import__(base)
        except Exception:
            missing.append(spec)
    return missing


def _is_python_interpreter(exe: str) -> bool:
    """Verify a candidate path is actually a Python interpreter.

    The macOS QGIS app bundle exposes ``sys.executable`` as the QGIS binary
    itself (``/Applications/QGIS.app/Contents/MacOS/QGIS``). Naively passing
    that to ``subprocess`` with ``-m pip ...`` launches a fresh QGIS instance
    instead of running pip — which then re-loads the plugin and triggers
    bootstrap recursion. To prevent this we probe each candidate first.
    """
    if not exe:
        return False
    try:
        result = subprocess.run(
            [exe, "-c", "import sys; print(sys.version_info[0])"],
            capture_output=True,
            timeout=5,
            text=True,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip().isdigit()


def _looks_like_python_basename(p: Path) -> bool:
    name = p.name.lower()
    return name.startswith("python") or name.startswith("pythonw")


def _pip_install(packages: List[str]):
    if not packages:
        return

    base_cmd = [
        "--disable-pip-version-check",
        "--no-input",
        "--target",
        str(_deps_dir()),
    ]

    # 1) In-process pip (modern API) — same interpreter, no subprocess needed.
    #    This is the happy path on every supported QGIS Python (>=3.11).
    try:
        from pip._internal.cli.main import main as pip_main  # type: ignore
        rc = pip_main(["install"] + base_cmd + packages)
        if rc == 0:
            return
    except Exception:
        pass

    # 2) Subprocess fallback. Build a candidate list of *real* Python
    #    interpreters — never the QGIS binary itself.
    cands = _candidate_pythons()
    verified: List[str] = []
    for c in cands:
        if _is_python_interpreter(c):
            verified.append(c)

    if not verified:
        raise RuntimeError(
            "infrared_city_gis bootstrap: no Python interpreter found to run "
            "pip in subprocess (in-process pip also failed). sys.executable="
            f"{sys.executable!r}. The plugin cannot install its dependencies "
            "automatically; install them manually into "
            f"{_deps_dir()!s} or report this with the QGIS Message Log."
        )

    # Set re-entry guard env var so a nested QGIS load (defense in depth)
    # would short-circuit and not loop.
    child_env = os.environ.copy()
    child_env[_REENTRY_ENV] = "1"

    last_err: Optional[Exception] = None
    for py in verified:
        try:
            cmd = [py, "-m", "pip", "install"] + base_cmd + packages
            subprocess.check_call(cmd, env=child_env)
            return
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err


def _candidate_pythons() -> List[str]:
    """Return likely Python interpreter paths, *excluding* the QGIS binary.

    Order:
      1. ``sys.executable`` — only if its basename looks like ``python*``.
      2. Sibling ``python.exe``/``pythonw.exe`` (Windows QGIS).
      3. ``<sys.executable parent>/bin/python3`` (macOS QGIS app bundle).
      4. ``<sys.executable parent>/python3``.
      5. ``python3`` / ``python`` from PATH (last resort, may differ in arch).
    """
    cands: List[str] = []
    exe = Path(sys.executable) if sys.executable else None

    if exe is not None:
        if _looks_like_python_basename(exe):
            cands.append(str(exe))

        # Common siblings — these are added unconditionally; the
        # ``_is_python_interpreter`` probe filters out anything that doesn't
        # actually run python.
        for sibling in (
            exe.with_name("python.exe"),
            exe.with_name("pythonw.exe"),
            exe.parent / "bin" / "python3",
            exe.parent / "python3",
        ):
            cands.append(str(sibling))

    for prog in ("python3", "python"):
        p = shutil.which(prog)
        if p:
            cands.append(p)

    seen: set = set()
    uniq: List[str] = []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def ensure_deps(plugin_name: str = "infrared_city_gis") -> None:
    """Ensure dependencies listed in requirements.txt are available.

    - Adds the (out-of-plugin) per-requirement-set deps dir to sys.path
    - Installs any missing packages into it
    - Removes the stale in-plugin thirdparty/ folder and deps dirs from other
      requirement sets (migration / version-bump cleanup)
    - Creates a '.deps_ok' marker to avoid repeated installs
    - Returns immediately if invoked recursively from a child pip subprocess
      (defense-in-depth against any future regression in candidate selection)
    """
    if os.environ.get(_REENTRY_ENV) == "1":
        # Inside a child process spawned by _pip_install. Do nothing.
        return

    _cleanup_legacy_thirdparty()
    _cleanup_old_deps_dirs()
    _ensure_sys_path()
    reqs = _read_requirements()
    missing = _find_missing(reqs)
    if missing:
        _pip_install(missing)
    try:
        if not _find_missing(reqs):
            _marker().write_text("ok", encoding="utf-8")
    except Exception:
        # Non-fatal if marker can't be written
        pass
