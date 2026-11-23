"""App settings changed from the UI (PLAXIS connection). Saved in data/settings.json; .env gives the defaults."""
import glob
import json
import os
import sys
from pathlib import Path

from .config import data_dir, env

FIELDS = ("solver", "host", "port", "password", "plxscripting_path", "anthropic_api_key", "runs_dir", "launched_pid", "input_exe", "bad_exes", "connect_mode",
          "projects_dir", "smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from")


def _file() -> Path:
    return data_dir() / "settings.json"


def load() -> dict:
    s = {"solver": env("SOLVER_BACKEND", "simulated").lower(), "host": env("PLAXIS_HOST", "localhost"),
         "port": int(env("PLAXIS_INPUT_PORT", "10000")), "password": env("PLAXIS_PASSWORD"),
         "plxscripting_path": env("PLXSCRIPTING_PATH")}
    try:
        s.update(json.loads(_file().read_text()))
    except (FileNotFoundError, ValueError):
        pass
    return s


def save(new: dict) -> dict:
    s = load()
    for k in FIELDS:
        if k in new and new[k] is not None:
            if k in ("password", "anthropic_api_key", "smtp_password") and new[k] == "":
                continue  # blank = keep the saved secret
            s[k] = int(new[k]) if k in ("port", "smtp_port") and str(new[k]).strip() else new[k]
    tmp = _file().with_suffix(".tmp")  # written whole, then swapped: a crash never leaves a half-written file
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(_file())
    return s


def public(s: dict | None = None) -> dict:
    s = dict(s or load())
    s["password_set"] = bool(s.pop("password", ""))
    s["smtp_password_set"] = bool(s.pop("smtp_password", ""))
    from .config import api_key
    s.pop("anthropic_api_key", None)
    s["api_key_set"] = bool(api_key())
    return s


# Newer PLAXIS versions install under Seequent, older ones under Bentley or Plaxis.
SEARCH_ROOTS = [r"C:\ProgramData\Seequent", r"C:\Program Files\Seequent", r"C:\Program Files (x86)\Seequent",
                r"C:\ProgramData\Bentley", r"C:\Program Files\Bentley", r"C:\Program Files (x86)\Bentley",
                r"C:\Program Files\Plaxis", r"C:\Program Files (x86)\Plaxis",
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Seequent"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Bentley"),
                os.path.join(os.environ.get("APPDATA", ""), "Bentley")]


BROAD_ROOTS = [r"C:\Program Files", r"C:\ProgramData", r"C:\Program Files (x86)", r"D:\Program Files"]


def find_plxscripting_dirs(max_depth: int = 7) -> list[str]:
    """Folders that contain a plxscripting package (the folder to add to the import path, not the package)."""
    found = _walk(SEARCH_ROOTS, max_depth)
    return found or _walk(BROAD_ROOTS, 6)  # slower fallback: any vendor folder


def _walk(roots, max_depth) -> list[str]:
    found = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        base = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, _ in os.walk(root):
            if os.path.basename(dirpath).lower() == "plxscripting" and os.path.isfile(os.path.join(dirpath, "easy.py")):
                found.append(os.path.dirname(dirpath))
                dirnames[:] = []
                continue
            if dirpath.count(os.sep) - base >= max_depth:
                dirnames[:] = []
    return found


LAST_ERROR = {"text": ""}


def _try_import(folder: str | None) -> bool:
    added = False
    if folder:
        folder = folder.strip().strip('"')
        if os.path.basename(folder.rstrip("\\/")).lower() == "plxscripting":
            folder = os.path.dirname(folder.rstrip("\\/"))  # user pasted the package folder itself
        if not os.path.isdir(os.path.join(folder, "plxscripting")):
            return False
        if folder not in sys.path:
            sys.path.append(folder)
            added = True
    try:
        for m in [k for k in sys.modules if k == "plxscripting" or k.startswith("plxscripting.")]:
            del sys.modules[m]
        import plxscripting.easy  # noqa: F401
        return True
    except ModuleNotFoundError as e:
        if e.name and e.name.split(".")[0] == "plxscripting":
            return False  # simply not here
        LAST_ERROR["text"] = f"plxscripting was found in {folder} but needs a module that is missing: {e.name}. " \
                             f"Install it with: python -m pip install {e.name}."
        if added:
            sys.path.remove(folder)
        return False
    except Exception as e:  # noqa: BLE001 - e.g. a missing dependency inside plxscripting
        LAST_ERROR["text"] = f"plxscripting was found in {folder or 'the Python path'} but failed to load: " \
                             f"{type(e).__name__}: {e}"
        if added:
            sys.path.remove(folder)
        return False


def ensure_plxscripting_importable(s: dict) -> str:
    """plxscripting ships with PLAXIS. Returns the folder it was loaded from, or raises ImportError with details."""
    LAST_ERROR["text"] = ""
    if _try_import(None):
        return "Python path"
    candidates = [s.get("plxscripting_path")] if s.get("plxscripting_path") else []
    candidates += find_plxscripting_dirs()
    for c in candidates:
        if _try_import(c):
            if not s.get("plxscripting_path"):
                save({"plxscripting_path": c})  # remember what we found
            return c
    raise ImportError(LAST_ERROR["text"] or "Could not find PLAXIS' plxscripting folder on this PC "
                      "(searched the Seequent, Bentley and Plaxis folders under Program Files and ProgramData).")
