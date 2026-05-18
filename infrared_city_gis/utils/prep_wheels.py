import subprocess
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
    subprocess.check_call(cmd)
    print("Wheels downloaded to:", WHEELS)


if __name__ == "__main__":
    main()
