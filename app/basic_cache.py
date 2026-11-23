"""Remembers the full PLAXIS run of an uploaded model (step 2 on the first page), by the content of its PLAXIS file.

The same PLAXIS file uploaded again (a new project, a restart, an update of OPTIMUS) is not calculated again: its
results and its model (geometry, materials, phases) are taken from the earlier full run. A changed file (another
hash) is calculated again. Kept in data/basic_cache/<hash>.json.
"""
import hashlib
import json
import time
from pathlib import Path

from .config import data_dir

PLAXIS_EXT = (".p2dx", ".p3d", ".p2d", ".p3dxml")


def _dir() -> Path:
    d = data_dir() / "basic_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_hash(files: list[dict]) -> str | None:
    """The PLAXIS project file's content (SHA-256), plus the names and sizes of its data files. None when the
    project has no PLAXIS file (example model, project open in PLAXIS)."""
    main, rest = [], []
    for f in files or []:
        p = Path(f.get("path") or "")
        if not p.is_file():
            continue
        (main if p.suffix.lower() in PLAXIS_EXT else rest).append(p)
    if not main:
        return None
    h = hashlib.sha256()
    for p in sorted(main, key=lambda x: x.name.lower()):
        h.update(p.name.lower().encode())
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    for p in sorted(rest, key=lambda x: x.name.lower()):
        h.update(f"{p.name.lower()}:{p.stat().st_size};".encode())
    return h.hexdigest()


def get(key: str | None) -> dict | None:
    if not key:
        return None
    try:
        return json.loads((_dir() / f"{key}.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def put(key: str | None, file_name: str, targets: list[dict], outputs: dict, model: dict | None) -> None:
    if not key:
        return
    data = {"at": time.time(), "file": file_name, "targets": targets, "outputs": outputs, "model": model}
    tmp = _dir() / f"{key}.tmp"
    tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
    tmp.replace(_dir() / f"{key}.json")
