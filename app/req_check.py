"""run.bat: are the requirements already installed for this requirements.txt? Exit 0 = yes (no pip install needed).
"--done" records that they were installed. The record lives in data\\ so an update with new requirements installs again."""
import hashlib
import sys
from pathlib import Path


def _stamp() -> Path:
    from .config import data_dir
    return Path(data_dir()) / ".requirements.md5"


def _digest() -> str:
    return hashlib.md5(Path("requirements.txt").read_bytes()).hexdigest()


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["--done"]:
            _stamp().parent.mkdir(parents=True, exist_ok=True)
            _stamp().write_text(_digest())
            sys.exit(0)
        ok = _stamp().is_file() and _stamp().read_text().strip() == _digest()
        if ok:  # the main packages import: nothing to install
            import docx  # noqa: F401
            import fastapi  # noqa: F401
            import matplotlib  # noqa: F401
            import openpyxl  # noqa: F401
        sys.exit(0 if ok else 1)
    except Exception:  # noqa: BLE001 - anything unclear: install
        sys.exit(1)
