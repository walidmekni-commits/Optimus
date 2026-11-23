import os
from pathlib import Path


def _load_dotenv() -> None:
    """Read KEY=VALUE lines from .env next to the app (works the same on Windows, macOS and Linux).
    Real environment variables win over the file."""
    f = Path(__file__).resolve().parent.parent / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "AI_MODE":
            continue  # old .env files had AI_MODE=demo, which forced example tables; the API key decides now
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def max_retries() -> int:
    return int(env("MAX_RETRIES", "3"))


def retry_delay() -> float:
    return float(env("RETRY_DELAY_SECONDS", "2"))


def max_runs() -> int:
    return int(env("MAX_RUNS", "200"))


def api_key() -> str:
    from .settings import load
    key = load().get("anthropic_api_key") or env("ANTHROPIC_API_KEY")
    return "" if key.endswith("...") else key


def ai_mode() -> str:
    """claude when an API key is set; "offline" otherwise (rule-based assistant, no key needed).
    AI_MODE=demo gives canned example tables (used by the tests)."""
    mode = env("AI_MODE").lower()
    if mode == "demo":
        return "demo"
    return "claude" if api_key() else "offline"


def pdf_import_enabled() -> bool:
    """Cross sections from PDFs, scans, pictures and SVG are set aside for now: only CAD (DXF / DWG) and
    coordinate tables (CSV) are offered. PDF_IMPORT=1 in .env brings the old readers back (kept, not deleted)."""
    return env("PDF_IMPORT", "0").strip().lower() in ("1", "true", "yes", "on")


def backend_name() -> str:
    from .settings import load
    return load()["solver"]


APP_DIR = Path(__file__).resolve().parent.parent


_MIGRATED = False


def store_dir() -> Path:
    """Where the live work is kept (automation.db and data/): next to the app, except when the app folder is synced
    by OneDrive (e.g. on the Desktop). OneDrive locks and re-uploads a database while it is being written, which
    can stop the app and lose the last changes, so the work is kept in %LOCALAPPDATA%\\OPTIMUS there. The first
    time, the work already next to the app is copied over (nothing is deleted)."""
    global _MIGRATED
    if os.name != "nt" or "onedrive" not in str(APP_DIR).lower() or not os.environ.get("LOCALAPPDATA") \
            or env("OPTIMUS_STORE", "").lower() == "app":
        return APP_DIR
    d = Path(os.environ["LOCALAPPDATA"]) / "OPTIMUS"
    if not _MIGRATED:
        _MIGRATED = True
        import shutil
        d.mkdir(parents=True, exist_ok=True)
        try:
            if not (d / "automation.db").exists() and (APP_DIR / "automation.db").exists():
                shutil.copy2(APP_DIR / "automation.db", d / "automation.db")
            if not (d / "data").exists() and (APP_DIR / "data").is_dir():
                shutil.copytree(APP_DIR / "data", d / "data")
        except OSError:
            pass
    return d


def data_dir() -> Path:
    """Always an absolute path (PLAXIS resolves relative paths from its own folder, not ours)."""
    p = Path(env("DATA_DIR", "data"))
    if not p.is_absolute():
        p = store_dir() / p
    p = p.resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def runs_dir() -> Path:
    """Where PLAXIS saves each run. Absolute; outside OneDrive by default, because OneDrive syncing can lock
    PLAXIS project files while they are being written. Can be changed in Settings (runs folder)."""
    from .settings import load
    custom = (load().get("runs_dir") or env("RUNS_DIR")).strip().strip('"')
    if custom:
        p = Path(custom).expanduser()
    elif "onedrive" in str(APP_DIR).lower():
        p = Path.home() / "PLAXIS_runs"
    else:
        p = data_dir() / "runs"
    p = p.resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


MODEL = "claude-opus-5-5"
# Reading drawings (scanned PDFs, pictures) and checking soil / structure lines: Claude Fable, the most capable model.
DRAWING_MODEL = env("DRAWING_MODEL", "claude-fable-5-2")
