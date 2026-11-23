"""Runs the OPTIMUS server for the desktop program (run.bat):

- every message and error goes to a log file (data/logs/optimus-server.log), so a crash can be read afterwards;
- if the server stops on an error it is started again (the work is in the database, nothing is lost);
- it stops by itself when no OPTIMUS window has been open for a while and no PLAXIS run is in progress
  (closing the window never cuts a run short).
"""
import logging
import os
import sys
import time
import traceback
from logging.handlers import RotatingFileHandler


def main(port: int = 8000) -> int:
    os.environ.setdefault("OPTIMUS_DESKTOP", "1")
    from .config import data_dir
    logs = data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:  # pythonw (no console): print() must not fail
        out = open(logs / "optimus-console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or out
        sys.stderr = sys.stderr or out
    h = RotatingFileHandler(logs / "optimus-server.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handlers = [h, logging.StreamHandler()]
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    log = logging.getLogger("optimus")
    import uvicorn
    for attempt in range(1, 6):
        start = time.time()
        try:
            log.info("OPTIMUS server starting on port %s (start %s)", port, attempt)
            uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_config=None)
            log.info("OPTIMUS server stopped")
            return 0
        except SystemExit as e:
            if e.code in (0, None):
                return 0
            log.error("OPTIMUS server exited (%s)", e.code)
        except Exception:  # noqa: BLE001 - logged, then the server is started again
            log.error("OPTIMUS server crashed:\n%s", traceback.format_exc())
        if time.time() - start < 5 and attempt >= 3:
            break  # it fails at once every time (port taken, broken install): no endless loop
        time.sleep(2)
    return 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8000))
