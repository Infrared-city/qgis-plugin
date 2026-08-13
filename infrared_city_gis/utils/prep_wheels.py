"""Dev-only helper: download the plugin's dependencies as wheels.

Not shipped (excluded from the release ZIP) and not imported by the plugin. It
dates from an abandoned attempt to vendor dependencies inside the package —
that failed because wheels are per-Python-version and per-platform, and one
plugin ZIP serves Windows, macOS and Linux. Kept as the starting point if we
ever revisit vendoring at CI build time, which would also remove the runtime
pip install that plugins.qgis.org reviewers ask about.
"""

import subprocess  # nosec B404 - dev-only helper, see the module docstring
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
REQ = PLUGIN_DIR / "requirements.txt"
WHEELS = PLUGIN_DIR / "wheels"


def main():
    WHEELS.mkdir(exist_ok=True)
    if not REQ.exists():
        print("requirements.txt not found:", REQ)
        sys.exit(1)

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "-r",
        str(REQ),
        "-d",
        str(WHEELS),
        "--only-binary",
        ":all:",
    ]
    print("Running:", " ".join(cmd))
    # nosec B603 - fixed argv list, no shell; every element is built here
    # from sys.executable and the plugin's own requirements.txt.
    subprocess.check_call(cmd)  # nosec B603
    print("Wheels downloaded to:", WHEELS)


if __name__ == "__main__":
    main()
