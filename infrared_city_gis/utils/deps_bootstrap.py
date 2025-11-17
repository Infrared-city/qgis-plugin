import os
import sys
import subprocess
from pathlib import Path
from typing import List

# Paths relative to this file (utils/)
PLUGIN_DIR = Path(__file__).resolve().parents[1]
THIRDPARTY = PLUGIN_DIR / "thirdparty"
WHEELS_DIR = PLUGIN_DIR / "wheels"
MARKER = PLUGIN_DIR / ".deps_ok"


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
    THIRDPARTY.mkdir(exist_ok=True)
    p = str(THIRDPARTY)
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


def _pip_install(packages: List[str]):
    if not packages:
        return
    base_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--target",
        str(THIRDPARTY),
    ]
    cmd = base_cmd + packages
    env = os.environ.copy()
    subprocess.check_call(cmd, env=env)


def ensure_deps(plugin_name: str = "infrared_city_gis") -> None:
    """Ensure dependencies listed in requirements.txt are available.

    - Adds local 'thirdparty' to sys.path
    - Installs any missing packages into 'thirdparty' (from wheels/ if present, else PyPI)
    - Creates a '.deps_ok' marker to avoid repeated installs
    """
    _ensure_sys_path()
    reqs = _read_requirements()
    missing = _find_missing(reqs)
    if missing:
        _pip_install(missing)
    try:
        if not _find_missing(reqs):
            MARKER.write_text("ok", encoding="utf-8")
    except Exception:
        # Non-fatal if marker can't be written
        pass
