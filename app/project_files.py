"""Projects on disk: Save / Save As / Open, organized folders, and the PLAXIS file of every run.

<Projects folder>/<Project>/
    <Project>.optimus                 the whole project (setup, studies, runs, results): Open brings it back
    Model/                            the uploaded PLAXIS model
    Basic Model Run/                  step 2 on the first page
    Sensitivity Analysis/Study 01 - 2026-11-19 0930/
        results.xlsx                  the results of the study
        Run 003/                      the PLAXIS file of that run (and its data folder)
    Design Optimization/ Design Modification/ Design Scenarios/ Calibration - Back-analysis/ Advanced Reporting/

The live work is always kept in the database as well (saved on every change): Save writes the project file and
its folders; nothing is lost if the window is closed without saving.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import db
from .config import runs_dir

EXT = ".optimus"
FORMAT = 1
STUDY_FOLDERS = {"sensitivity": "Sensitivity Analysis", "optimize": "Design Optimization",
                 "design_change": "Design Modification", "combinations": "Design Scenarios",
                 "calibration": "Calibration - Back-analysis", "reporting": "Advanced Reporting",
                 "basic": "Basic Model Run"}
PLAXIS_EXT = (".p2dx", ".p3d", ".p2d", ".p3dxml")


class ProjectFileError(Exception):
    pass


def safe_name(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (s or "").strip()).strip(" .")
    return s[:80] or "OPTIMUS project"


def projects_dir() -> Path:
    """Settings › Projects folder, else Documents\\OPTIMUS Projects."""
    from .settings import load
    custom = (load().get("projects_dir") or "").strip().strip('"')
    if custom:
        p = Path(custom).expanduser()
    else:
        home = Path(os.environ.get("USERPROFILE") or Path.home())
        docs = home / "Documents"
        p = (docs if docs.is_dir() else home) / "OPTIMUS Projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


# --- what a project holds ----------------------------------------------------------------
def tasks_of(pid: str) -> list[dict]:
    rows = db._query("SELECT id FROM tasks WHERE project_id = ? ORDER BY created_at", (pid,))
    return [db.get_task(r["id"]) for r in rows]


def kind_of(t: dict) -> str:
    plan = t.get("plan") or {}
    if plan.get("basic"):
        return "basic"
    return (plan.get("study") or {}).get("mode") or t.get("objective") or "optimize"


def studies(pid: str) -> list[dict]:
    """The studies of the project, numbered per kind, with their runs and whether each run has a PLAXIS file."""
    out, count = [], {}
    for t in tasks_of(pid):
        k = kind_of(t)
        count[k] = count.get(k, 0) + 1
        runs = db.get_runs(t["id"])
        offset = (t["plan"] or {}).get("offset", 0) + (t["plan"] or {}).get("first", 0)
        out.append({"task": t["id"], "kind": k, "folder": STUDY_FOLDERS.get(k, "Studies"), "n": count[k],
                    "title": f"{STUDY_FOLDERS.get(k, 'Study')} {count[k]:02d}", "status": t["status"],
                    "created_at": t["created_at"],
                    "runs": [{"idx": r["idx"], "run": offset + r["idx"], "label": r["label"], "status": r["status"],
                              "file": str(run_file(t["id"], r["idx"]) or "")} for r in runs]})
    return out


PLOT_LABELS = {"utot": "Total displacements |u|", "ux": "Horizontal displacements ux", "uy": "Vertical displacements",
               "mesh": "Model mesh", "plate_m": "Bending moment of the wall", "geometry": "Model geometry",
               "pexcess": "Excess pore pressures", "plate_q": "Shear force of the wall", "plate_n": "Axial force of the wall"}


def _plots_dir(tid: str, idx: int) -> Path | None:
    d = run_folder(tid, idx) / "plots"
    if d.is_dir() and any(d.glob("*.png")):
        return d
    t = db.get_task(tid)
    base = ((t or {}).get("plan") or {}).get("baseline") or {}
    if base.get("task") and base["task"] != tid:  # Run 0 from the full run of the Original File: its images
        run = next((r for r in db.get_runs(tid) if r["idx"] == idx), None)
        if run and not (run.get("inputs") or {}):
            return _plots_dir(base["task"], 0)
    return None


def plots_of(tid: str, idx: int) -> list[dict]:
    """The PLAXIS Output images saved by a run (contours, mesh, moments), as {name, label, url}."""
    d = _plots_dir(tid, idx)
    if not d:
        return []
    out = []
    for f in sorted(d.glob("*.png")):
        k = f.stem.removesuffix("_sim")
        out.append({"name": f.name, "key": k, "label": PLOT_LABELS.get(k, k) + (" (simulated)" if f.stem.endswith("_sim") else ""),
                    "url": f"/api/tasks/{tid}/runs/{idx}/plot/{f.name}"})
    return out


def plot_file(tid: str, idx: int, name: str) -> Path | None:
    d = _plots_dir(tid, idx)
    if not d or "/" in name or "\\" in name or ".." in name:
        return None
    f = d / name
    return f if f.is_file() else None


def run_folder(tid: str, idx: int) -> Path:
    return runs_dir() / tid / f"run_{idx + 1:03d}"


def run_file(tid: str, idx: int) -> Path | None:
    """The PLAXIS file the run saved (with its data folder next to it), or None."""
    d = run_folder(tid, idx)
    if d.is_dir():
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in PLAXIS_EXT:
                return f
    t = db.get_task(tid)
    base = ((t or {}).get("plan") or {}).get("baseline") or {}
    if base.get("task") and base["task"] != tid:  # Run 0 taken from the full run of the Original File: its file
        run = next((r for r in db.get_runs(tid) if r["idx"] == idx), None)
        if run and not (run.get("inputs") or {}):
            return run_file(base["task"], 0)
    # a project opened from a .optimus file: its saved copy
    saved = ((db.get_project(t["project_id"]) or {}).get("setup") or {}).get("saved") if t else None
    if saved and saved.get("folder"):
        for f in Path(saved["folder"]).glob(f"*/*/Run {_run_no(t, idx):03d}/*"):
            if f.is_file() and f.suffix.lower() in PLAXIS_EXT:
                return f
    return None


def _run_no(t: dict, idx: int) -> int:
    return (t["plan"] or {}).get("offset", 0) + (t["plan"] or {}).get("first", 0) + idx


def parse_runs(text: str) -> set[int]:
    """'3, 5-7' -> {3, 5, 6, 7}"""
    out = set()
    for part in re.split(r"[,; ]+", text or ""):
        if not part:
            continue
        a, _, b = part.partition("-")
        try:
            lo, hi = int(a), int(b or a)
        except ValueError:
            raise ProjectFileError(f"'{part}' is not a run number (e.g. 3, 5-7).")
        out.update(range(min(lo, hi), max(lo, hi) + 1))
    return out


# --- save -----------------------------------------------------------------------------------
def snapshot(pid: str) -> dict:
    p = db.get_project(pid)
    tasks = []
    for t in tasks_of(pid):
        tasks.append({**t, "runs": db.get_runs(t["id"])})
    return {"optimus": FORMAT, "saved_at": time.time(), "project": p, "tasks": tasks,
            "messages": db.get_messages(pid)}


def save(pid: str, folder: str = "", name: str = "", plaxis: str = "all", runs: str = "", task: str = "",
         reports: bool = True, results=None) -> dict:
    """Writes the project file and its folders. plaxis: all | none | some (the run numbers in `runs`, of the study
    `task` or of every study). results(tid) -> xlsx bytes, to save each study's results next to its runs."""
    p = db.get_project(pid)
    if not p:
        raise ProjectFileError("Project not found")
    setup = p.get("setup") or {}
    title = (setup.get("info") or {}).get("title") or "OPTIMUS project"
    prev = setup.get("saved") or {}
    name = safe_name(name or prev.get("name") or title)
    base = Path(folder.strip().strip('"')).expanduser() if folder.strip() else (
        Path(prev["folder"]) if prev.get("folder") and name == prev.get("name") else projects_dir() / name)
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ProjectFileError(f"Cannot create the folder {base}: {e}")
    wanted = parse_runs(runs) if plaxis == "some" else set()
    copied, report_files = 0, 0

    model = base / "Model"  # the uploaded model, once
    for f in p.get("files") or []:
        src = Path(f.get("path") or "")
        if src.is_file():
            model.mkdir(exist_ok=True)
            dst = model / src.name
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                shutil.copy2(src, dst)
            data = src.with_suffix(src.suffix + "data")  # PLAXIS data folder: wall.p2dx -> wall.p2dxdata
            if not data.is_dir():
                data = src.parent / (src.stem + "_data")
            if data.is_dir() and not (model / data.name).exists():
                shutil.copytree(data, model / data.name)

    for st in studies(pid):
        sfolder = base / st["folder"] / (f"Study {st['n']:02d} - " + time.strftime("%Y-%m-%d %H%M", time.localtime(st["created_at"])))
        for r in st["runs"]:
            pick = plaxis == "all" or (plaxis == "some" and r["run"] in wanted and (not task or task == st["task"]))
            if not pick or not r["file"]:
                continue
            src = Path(r["file"])
            dst = sfolder / f"Run {r['run']:03d}"
            if src.parent.resolve() == dst.resolve():
                continue
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.parent.iterdir():  # the PLAXIS file and its data folder
                target = dst / f.name
                if f.is_dir():
                    if not target.exists():
                        shutil.copytree(f, target)
                elif not target.exists() or target.stat().st_size != f.stat().st_size:
                    shutil.copy2(f, target)
            copied += 1
        if reports and results and st["status"] == "succeeded" and st["kind"] != "basic":
            try:
                data = results(st["task"])
                if data:
                    sfolder.mkdir(parents=True, exist_ok=True)
                    (sfolder / "results.xlsx").write_bytes(data)
                    report_files += 1
            except Exception:  # noqa: BLE001 - the project file matters more than one results sheet
                pass

    file = base / (name + EXT)
    snap = snapshot(pid)
    snap["project"]["setup"] = {**setup, "saved": {"folder": str(base), "file": str(file), "name": name,
                                                    "at": snap["saved_at"]}}
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, default=str, indent=1), encoding="utf-8")
    tmp.replace(file)
    db.update_project(pid, setup=snap["project"]["setup"])
    db.log("project_saved", {"file": str(file), "plaxis_files": copied}, pid)
    return {"file": str(file), "folder": str(base), "name": name, "plaxis_files": copied, "results": report_files,
            "at": snap["saved_at"]}


def save_as_copy(pid: str, name: str, folder: str = "", **kw) -> dict:
    """Save As with a new name: a new project (its own id) is made from this one and saved; the original stays."""
    snap = snapshot(pid)
    new_pid = _import(snap, new_ids=True, title=name)
    res = save(new_pid, folder=folder, name=name, **kw)
    return {**res, "project": new_pid}


# --- open -----------------------------------------------------------------------------------
def open_file(path: str) -> str:
    """Brings a project back from its .optimus file; returns the project id (the same project when it is still in
    the database: the database copy is kept, it is never older than the file)."""
    f = Path((path or "").strip().strip('"')).expanduser()
    if f.is_dir():
        found = sorted(f.glob("*" + EXT))
        if not found:
            raise ProjectFileError(f"No {EXT} project file in {f}")
        f = found[0]
    if not f.is_file():
        raise ProjectFileError(f"File not found: {f}")
    try:
        snap = json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ProjectFileError(f"{f.name} is not an OPTIMUS project file ({e})")
    if not isinstance(snap, dict) or "project" not in snap:
        raise ProjectFileError(f"{f.name} is not an OPTIMUS project file")
    pid = snap["project"]["id"]
    if db.get_project(pid):
        return pid
    snap["project"]["setup"] = {**(snap["project"].get("setup") or {}),
                                "saved": {**((snap["project"].get("setup") or {}).get("saved") or {}),
                                          "folder": str(f.parent), "file": str(f)}}
    return _import(snap)


def _import(snap: dict, new_ids: bool = False, title: str = "") -> str:
    p = dict(snap["project"])
    pid = db.new_id() if new_ids else p["id"]
    setup = dict(p.get("setup") or {})
    if new_ids:
        setup.pop("saved", None)
    if title:
        setup["info"] = {**(setup.get("info") or {}), "title": title}
    now = time.time()
    tid_map = {t["id"]: (db.new_id() if new_ids else t["id"]) for t in snap.get("tasks") or []}
    with db._lock:
        c = db.conn()
        c.execute("INSERT INTO projects VALUES (?,?,?,?,?,?)",
                  (pid, p.get("objective"), db._dump(p.get("files") or []), db._dump(setup), p.get("created_at") or now, now))
        for t in snap.get("tasks") or []:
            plan = dict(t.get("plan") or {})
            if plan.get("previous"):
                plan["previous"] = [tid_map.get(x, x) for x in plan["previous"]]
            c.execute("INSERT INTO tasks (id, project_id, objective, summary, plan, status, result, error, created_at,"
                      " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (tid_map[t["id"]], pid, t.get("objective"), t.get("summary"), db._dump(plan),
                       "stopped" if t.get("status") in ("queued", "running") else t.get("status"),
                       db._dump(t.get("result")), t.get("error"), t.get("created_at") or now, now))
            for r in t.get("runs") or []:
                c.execute("INSERT INTO runs (task_id, idx, label, inputs, outputs, checks, passed, status, attempts, error,"
                          " file, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (tid_map[t["id"]], r.get("idx"), r.get("label"), db._dump(r.get("inputs") or {}),
                           db._dump(r.get("outputs")), db._dump(r.get("checks")), r.get("passed"),
                           "stopped" if r.get("status") in ("queued", "running", "pending") else r.get("status"),
                           r.get("attempts") or 0, r.get("error"), r.get("file"), now))
            if new_ids:  # the copy keeps the PLAXIS files of its runs
                src = runs_dir() / t["id"]
                if src.is_dir() and not (runs_dir() / tid_map[t["id"]]).exists():
                    shutil.copytree(src, runs_dir() / tid_map[t["id"]])
        for m in snap.get("messages") or []:
            c.execute("INSERT INTO messages (project_id, role, content, attachments, created_at, category) VALUES (?,?,?,?,?,?)",
                      (pid, m.get("role"), m.get("content"), db._dump(m.get("attachments") or []), now, m.get("category") or ""))
        c.commit()
    return pid


# --- lists ----------------------------------------------------------------------------------
def all_projects() -> list[dict]:
    """Every project in the database, the latest first, plus the project files found in the projects folder."""
    rows = db._query("SELECT id FROM projects ORDER BY updated_at DESC")
    out, seen = [], set()
    for r in rows:
        p = db.get_project(r["id"])
        s = p.get("setup") or {}
        info = s.get("info") or {}
        n = db._query("SELECT COUNT(*) AS n FROM tasks WHERE project_id = ?", (p["id"],))[0]["n"]
        saved = s.get("saved") or {}
        out.append({"id": p["id"], "title": info.get("title") or "Project", "user": info.get("user") or "",
                    "plaxis": s.get("plaxis") or "2d", "mode": (s.get("study") or {}).get("mode") or p.get("objective"),
                    "updated_at": p["updated_at"], "created_at": p["created_at"], "studies": n,
                    "files": [f.get("name") for f in p.get("files") or []][:3],
                    "saved_file": saved.get("file") or "", "saved_at": saved.get("at"),
                    "unsaved": bool(not saved.get("at") or p["updated_at"] - saved.get("at", 0) > 2)})
        seen.add(p["id"])
    files = []
    try:
        for f in sorted(projects_dir().glob("*/*" + EXT)) + sorted(projects_dir().glob("*" + EXT)):
            try:
                pid = json.loads(f.read_text(encoding="utf-8"))["project"]["id"]
            except Exception:  # noqa: BLE001
                pid = None
            if pid not in seen:
                files.append({"file": str(f), "title": f.stem, "updated_at": f.stat().st_mtime})
    except OSError:
        pass
    return out + [{"id": None, **x, "only_file": True} for x in files]


def delete_task(tid: str) -> None:
    """Removes one study (its runs, its log and the PLAXIS files of its runs)."""
    with db._lock:
        c = db.conn()
        c.execute("DELETE FROM runs WHERE task_id = ?", (tid,))
        c.execute("DELETE FROM task_log WHERE task_id = ?", (tid,))
        c.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        c.commit()
    shutil.rmtree(runs_dir() / tid, ignore_errors=True)


def delete_project(pid: str) -> None:
    """Removes the project from the list (its saved project folder on disk is kept)."""
    with db._lock:
        c = db.conn()
        for r in c.execute("SELECT id FROM tasks WHERE project_id = ?", (pid,)).fetchall():
            c.execute("DELETE FROM runs WHERE task_id = ?", (r["id"],))
        c.execute("DELETE FROM tasks WHERE project_id = ?", (pid,))
        c.execute("DELETE FROM messages WHERE project_id = ?", (pid,))
        c.execute("DELETE FROM projects WHERE id = ?", (pid,))
        c.commit()


# --- open a file or a folder on this PC ------------------------------------------------------
def open_on_pc(path: Path, select: bool = False) -> bool:
    """Opens the file with its program (a PLAXIS file in PLAXIS) or shows it in Explorer. False when this PC cannot
    (not Windows): the path is then shown to copy."""
    if sys.platform.startswith("win"):
        if select:
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path))  # noqa: S606 - the engineer's own file, on their own PC
        return True
    return False
