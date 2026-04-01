import os
import sys
import subprocess
import shutil
from pathlib import Path
from typing import List

# Paths relative to this file (utils/)
PLUGIN_DIR = Path(__file__).resolve().parents[1]
THIRDPARTY = PLUGIN_DIR / "thirdparty"
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
        "--disable-pip-version-check",
        "--no-input",
        "--target",
        str(THIRDPARTY),
    ]

    # 1) In-process pip (modern API) — same interpreter, no subprocess needed
    try:
        from pip._internal.cli.main import main as pip_main  # type: ignore
        rc = pip_main(["install"] + base_cmd + packages)
        if rc == 0:
            return
    except Exception:
        pass

    # 2) subprocess candidates — sys.executable always first so we use the
    #    exact same Python/architecture that QGIS is running
    def _candidate_pythons() -> List[str]:
        cands: List[str] = []
        exe = Path(sys.executable) if sys.executable else None
        if exe:
            # Always add sys.executable regardless of its name (covers QGIS on Mac/Windows)
            cands.append(str(exe))
            # On Windows QGIS the exe may be qgis-bin.exe; add sibling python executables too
            if not exe.name.lower().startswith("python"):
                cands.append(str(exe.with_name("python.exe")))
                cands.append(str(exe.with_name("pythonw.exe")))
                # On Mac the Python binary sits in bin/ next to the QGIS binary
                cands.append(str(exe.parent / "bin" / "python3"))
                cands.append(str(exe.parent / "python3"))
        # PATH fallbacks (last resort — may be a different arch/version)
        for prog in ("python3", "python"):
            p = shutil.which(prog)
            if p:
                cands.append(p)
        # Deduplicate while preserving order
        seen: set = set()
        uniq: List[str] = []
        for c in cands:
            if c and c not in seen:
                seen.add(c)
                uniq.append(c)
        return uniq

    last_err: Exception | None = None
    for py in _candidate_pythons():
        try:
            cmd = [py, "-m", "pip", "install"] + base_cmd + packages
            subprocess.check_call(cmd, env=os.environ.copy())
            return
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err


def ensure_deps(plugin_name: str = "infrared_city_gis") -> None:
    """Ensure dependencies listed in requirements.txt are available.

    - Adds local 'thirdparty' to sys.path
    - Installs any missing packages into 'thirdparty' 
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
