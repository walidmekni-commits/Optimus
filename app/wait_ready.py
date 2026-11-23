"""Waits until the OPTIMUS server answers (used by run.bat before it opens the app window)."""
import sys
import time
import urllib.request


def main(url: str = "http://127.0.0.1:8000", seconds: float = 90) -> int:
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url.rstrip("/") + "/api/objectives", timeout=2)
            return 0
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.5)
    return 1


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:2] or []), *([float(sys.argv[2])] if len(sys.argv) > 2 else [])))
