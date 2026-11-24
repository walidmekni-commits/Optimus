"""HTTP API + web pages. Every step of the workflow writes to the task log."""
import csv
import hashlib
import io
import json
import os
import threading
import time
import re
import shutil
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import compiled, design, geosel, ai, db, engine, exports, geometry, insight, plaxis_model, report, sequence, settings, study
from . import accounts, project_files, units
from .config import backend_name, data_dir, max_runs, runs_dir
from .planner import build_runs, describe_plan, show, validate
from .offline_ai import guess_section
from .schema import OBJECTIVES, SECTIONS, Goal, coerce_target, coerce_variable, norm_section
from . import solvers
from .solvers import get_solver

app = FastAPI(title="OPTIMUS")
STATIC = Path(__file__).resolve().parent.parent / "static"
RUN_IN_BACKGROUND = True
PUBLIC_API = ("/api/auth/", "/api/objectives", "/api/heartbeat")


@app.middleware("http")
async def _sign_in(request: Request, call_next):
    """With an account, every /api call needs the session cookie (the page itself shows the sign-in screen)."""
    path = request.url.path
    if path.startswith("/api/") and not path.startswith(PUBLIC_API) and accounts.required() \
            and not accounts.user(request.cookies.get(accounts.COOKIE)):
        return JSONResponse({"detail": "Sign in to OPTIMUS first.", "signin": True}, status_code=401)
    return await call_next(request)


# --- sign-in --------------------------------------------------------------------------------
def _with_cookie(data: dict, token: str, remember: bool = True) -> JSONResponse:
    r = JSONResponse(data)
    r.set_cookie(accounts.COOKIE, token, httponly=True, samesite="strict",
                 max_age=30 * 86400 if remember else None)
    return r


def _auth_call(f, *a):
    try:
        return f(*a)
    except accounts.AuthError as e:
        raise HTTPException(400, str(e))


@app.get("/api/auth/status")
def auth_status(request: Request):
    return accounts.status(request.cookies.get(accounts.COOKIE))


@app.post("/api/auth/setup")
def auth_setup(body: dict, request: Request):
    """The first account, or another one when signed in."""
    st = accounts.status(request.cookies.get(accounts.COOKIE))
    if st["accounts"] and not st["user"]:
        raise HTTPException(403, "Sign in first to add an account.")
    r = _auth_call(accounts.create, body.get("name", ""), body.get("email", ""), body.get("password", ""))
    return _with_cookie({"user": r["user"], "recovery_key": r["recovery_key"]}, r["token"])


@app.post("/api/auth/login")
def auth_login(body: dict):
    tok = _auth_call(accounts.login, body.get("email", ""), body.get("password", ""), bool(body.get("remember")))
    return _with_cookie({"user": accounts.user(tok)}, tok, bool(body.get("remember")))


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    accounts.logout(request.cookies.get(accounts.COOKIE))
    r = JSONResponse({"ok": True})
    r.delete_cookie(accounts.COOKIE)
    return r


@app.post("/api/auth/forgot")
def auth_forgot(body: dict):
    return accounts.forgot(body.get("email", ""))


@app.post("/api/auth/reset")
def auth_reset(body: dict):
    tok = _auth_call(accounts.reset, body.get("email", ""), body.get("code", ""), body.get("password", ""))
    return _with_cookie({"user": accounts.user(tok)}, tok, False)


@app.post("/api/auth/password")
def auth_password(body: dict, request: Request):
    _auth_call(accounts.change_password, request.cookies.get(accounts.COOKIE), body.get("old", ""), body.get("new", ""))
    return {"ok": True}


@app.post("/api/auth/skip")
def auth_skip(request: Request):
    """Use OPTIMUS without signing in: only before any account exists, or when signed in."""
    st = accounts.status(request.cookies.get(accounts.COOKIE))
    if st["accounts"] and not st["user"]:
        raise HTTPException(403, "Sign in first.")
    accounts.set_login_off(True)
    return accounts.status(request.cookies.get(accounts.COOKIE))


@app.post("/api/auth/on")
def auth_on(request: Request):
    st = accounts.status(request.cookies.get(accounts.COOKIE))
    if not st["accounts"]:
        raise HTTPException(400, "Create an account first.")
    if not st["user"]:
        raise HTTPException(403, "Sign in first.")
    accounts.set_login_off(False)
    return accounts.status(request.cookies.get(accounts.COOKIE))


# --- the desktop program: the server stops by itself when no window is open and nothing runs --------
LAST_BEAT = {"t": time.time()}


@app.post("/api/open-window")
def open_window(request: Request):
    """From a browser tab: opens OPTIMUS in its own program window on this PC (the tab can then be closed)."""
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(403, "Only on this PC.")
    from .launch import open_app_window
    try:
        how = open_app_window(f"http://127.0.0.1:{request.url.port or 8000}")
    except OSError as e:
        raise HTTPException(400, f"Could not open the window: {e}")
    return {"opened": how}


@app.post("/api/heartbeat")
def heartbeat():
    LAST_BEAT["t"] = time.time()
    return {"ok": True}


def _idle_watch() -> None:
    idle = float(os.environ.get("OPTIMUS_IDLE_MINUTES", "20")) * 60
    while True:
        time.sleep(30)
        running = db._query("SELECT COUNT(*) AS n FROM tasks WHERE status IN ('queued', 'running')")[0]["n"]
        if time.time() - LAST_BEAT["t"] > idle and not running and not engine.BUSY:
            db.log("server_idle_stop", {"minutes": idle / 60})
            os._exit(0)


@app.on_event("startup")
def _startup() -> None:
    # runs left "running" by a server that stopped (PC shut down, crash): shown as stopped, Restart goes on
    for r in db._query("SELECT id FROM tasks WHERE status IN ('queued', 'running')"):
        db.update_task(r["id"], status="stopped", error="OPTIMUS was closed during the calculation: press Restart to "
                                                       "calculate the remaining runs.")
        for run in db.get_runs(r["id"]):
            if run["status"] in ("queued", "pending", "running"):
                db.update_run(run["id"], status="stopped")
    if os.environ.get("OPTIMUS_DESKTOP") == "1":
        threading.Thread(target=_idle_watch, daemon=True).start()


def _save_uploads(pid: str, files: list[UploadFile], sub: str) -> list[dict]:
    out = []
    folder = data_dir() / "projects" / pid / sub
    folder.mkdir(parents=True, exist_ok=True)
    for f in files or []:
        if not f.filename:
            continue
        name = re.sub(r"[^\w.\- ]", "_", Path(f.filename).name)
        dest = folder / name
        with dest.open("wb") as fh:
            shutil.copyfileobj(f.file, fh)
        out.append({"name": name, "path": str(dest), "size": dest.stat().st_size})
    return out


def _project_or_404(pid: str) -> dict:
    p = db.get_project(pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


def _task_or_404(tid: str) -> dict:
    t = db.get_task(tid)
    if not t:
        raise HTTPException(404, "Task not found")
    return t


def _with_section(v: dict) -> dict:
    v = coerce_variable(v).model_dump()
    v["section"] = v["section"] or guess_section(v)
    return v


def _clean_setup(setup: dict) -> dict:
    setup = {k: v for k, v in setup.items() if k != "current_section"}
    return {
        **setup,
        "variables": [_with_section(v) for v in setup.get("variables") or []],
        "targets": [coerce_target(t).model_dump() for t in setup.get("targets") or []],
        "goal": Goal.model_validate(setup["goal"]).model_dump() if setup.get("goal") else None,
    }


# --- page 1: input files + objective --------------------------------------------------
@app.get("/api/objectives")
def objectives():
    from .config import ai_mode, pdf_import_enabled
    return {"objectives": OBJECTIVES, "sections": SECTIONS, "backend": backend_name(), "max_runs": max_runs(),
            "ai_mode": ai_mode(), "pdf_import": pdf_import_enabled(), "dwg_converter": geometry.dwg_converter()}


@app.get("/api/samples/cross_section.csv")
def csv_template():
    """A filled-in CSV cross section (line name, x, level), to copy when only a PDF of the drawing exists."""
    from .config import APP_DIR
    return FileResponse(APP_DIR / "samples" / "cross_section_example.csv", media_type="text/csv",
                        filename="cross_section_template.csv")


@app.post("/api/projects")
def create_project(objective: str = Form(...), title: str = Form(""), user: str = Form(""), date: str = Form(""),
                   source: str = Form("upload"), local_path: str = Form(""), plaxis_version: str = Form(""),
                   discipline: str = Form("geotechnical"),
                   files: list[UploadFile] = File(default=[])):
    if objective not in OBJECTIVES:
        raise HTTPException(400, "Unknown objective")
    if not title.strip():
        raise HTTPException(400, "Give the project a title")
    local_path = local_path.strip().strip('"')
    if source == "path" and not Path(local_path).is_file():
        raise HTTPException(400, f"File not found on this PC: {local_path or '(empty)'}")
    pid = db.create_project(objective, [])
    saved = []
    if source == "path":
        saved.append({"name": Path(local_path).name, "path": local_path, "source": "path",
                      "size": Path(local_path).stat().st_size})
    elif source == "open":
        saved.append({"name": "Project open in PLAXIS", "path": "", "source": "open"})
    saved += _save_uploads(pid, files, "input")
    try:
        description = get_solver(backend_name()).describe(saved)
    except Exception as e:  # noqa: BLE001
        description = f"Could not read the model: {e}"
    project = db.get_project(pid)
    info = {"title": title.strip(), "user": user.strip(), "date": date.strip(),
            "discipline": "structural" if discipline.strip().lower().startswith("struct") else "geotechnical"}
    version = _guess_version(plaxis_version, saved)
    db.update_project(pid, files=saved, setup={**project["setup"], "model_description": description, "info": info,
                                               "plaxis": version})
    db.log("request", {**info, "objective": OBJECTIVES[objective]["title"],
                       "files": [f["name"] for f in saved]}, pid)
    return project_view(pid)


def _guess_version(chosen: str, files: list[dict]) -> str:
    """PLAXIS 2D or 3D: as chosen on page 1, else from the file type (.p2dx = 2D, .p3d = 3D)."""
    if chosen in ("2d", "3d"):
        return chosen
    for f in files:
        n = f.get("name", "").lower()
        if n.endswith((".p2dx", ".p2d")):
            return "2d"
        if n.endswith((".p3d", ".p3dxml")):
            return "3d"
    return "2d"


class VersionIn(BaseModel):
    version: str


@app.put("/api/projects/{pid}/plaxis-version")
def set_version(pid: str, body: VersionIn):
    p = _project_or_404(pid)
    if body.version not in ("2d", "3d"):
        raise HTTPException(400, "version must be 2d or 3d")
    setup = {**p["setup"], "plaxis": body.version}
    for v in setup.get("variables") or []:  # mesh levels differ between PLAXIS 2D and 3D
        if v.get("plaxis_ref", "").lower() == "mesh.size" and v.get("kind") == "values":
            v["values"] = plaxis_model.mesh_values(body.version)
    db.update_project(pid, setup=setup)
    db.log("request", {"plaxis": body.version.upper()}, pid)
    return {"plaxis": body.version}


@app.get("/api/projects/{pid}")
def project_view(pid: str):
    p = _project_or_404(pid)
    for sec in p["setup"].get("sections") or []:  # sections read by an earlier version
        sec.setdefault("quality", geometry.quality(sec))
    rows = db._query("SELECT id FROM tasks WHERE project_id = ? ORDER BY created_at DESC LIMIT 20", (pid,))
    tasks = next((r for r in rows if not (db.get_task(r["id"])["plan"] or {}).get("basic")), None)  # not the basic run
    return {"project": p, "objective": OBJECTIVES[p["objective"]],
            "messages": [{"role": m["role"], "content": m["content"], "category": m.get("category") or "",
                          "attachments": [a["name"] for a in m["attachments"] or []]}
                         for m in db.get_messages(pid)],
            "latest_task": task_view(tasks["id"]) if tasks else None}


# --- PLAXIS connection settings -------------------------------------------------------
class SettingsIn(BaseModel):
    solver: str | None = None
    anthropic_api_key: str | None = None
    host: str | None = None
    port: int | None = None
    password: str | None = None
    plxscripting_path: str | None = None
    runs_dir: str | None = None
    projects_dir: str | None = None
    smtp_host: str | None = None
    smtp_port: str | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None


@app.get("/api/settings")
def get_settings():
    return {**settings.public(), "runs_dir_effective": str(runs_dir()), "projects_dir_effective": str(project_files.projects_dir())}


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    if body.solver and body.solver not in ("simulated", "plaxis"):
        raise HTTPException(400, "solver must be simulated or plaxis")
    return {**settings.public(settings.save(body.model_dump())), "runs_dir_effective": str(runs_dir()),
            "projects_dir_effective": str(project_files.projects_dir())}


@app.post("/api/settings/test-email")
def test_email(body: dict):
    from .notify import send_mail
    to = (body.get("to") or "").strip()
    if not to:
        raise HTTPException(400, "Give the email address to send the test to.")
    try:
        ok = send_mail([to], "OPTIMUS test email", "Email from OPTIMUS works: password reset codes will arrive here.\n",
                       raise_errors=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"The email could not be sent: {e}")
    if not ok:
        raise HTTPException(400, "Email is not set up: give the SMTP server first.")
    return {"ok": True}


@app.post("/api/settings/test")
def test_connection():
    try:
        from .solvers import PlaxisSolver
        return {"ok": True, "message": PlaxisSolver().test()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}


@app.post("/api/plaxis/mark-phases")
def mark_phases():
    """All phases blue: every phase of the project open in PLAXIS Input is marked for calculation (the runs do this
    themselves before each calculation)."""
    try:
        from .solvers import PlaxisSolver
        n = PlaxisSolver().mark_open_phases()
        return {"ok": True, "message": f"All {n} phases are marked for calculation (blue) in PLAXIS."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"Could not mark the phases in PLAXIS: {e}"}


# --- page 2: AI assistant, variable parameters, target output --------------------------
class SetupIn(BaseModel):
    variables: list[dict]
    targets: list[dict]
    goal: dict | None = None


@app.put("/api/projects/{pid}/setup")
def customize(pid: str, body: SetupIn):
    p = _project_or_404(pid)
    try:
        setup = _clean_setup({**p["setup"], **body.model_dump()})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Invalid table values: {e}")
    if {k: setup[k] for k in ("variables", "targets", "goal")} != {k: p["setup"].get(k) for k in ("variables", "targets", "goal")}:
        db.update_project(pid, setup=setup)
        db.log("customize", {"variables": len(setup["variables"]), "targets": len(setup["targets"])}, pid)
    return setup


@app.post("/api/projects/{pid}/chat")
def chat(pid: str, message: str = Form(""), section: str = Form("assistant"), category: str = Form(""),
         files: list[UploadFile] = File(default=[])):
    """category = the page the user writes from: a Variables section (geometry, mesh, ...) or "output"."""
    p = _project_or_404(pid)
    attachments = [{**a, "section": section} for a in _save_uploads(pid, files, "uploads")]
    if not message and not attachments:
        raise HTTPException(400, "Write a message or upload a file")
    if attachments and not message:
        message = {"variables": "Identify the variable parameters from this file.",
                   "targets": "Identify the target outputs from this file."}.get(section, "Please use this file.")
    db.add_message(pid, "user", message, attachments, category)
    db.log("request", {"message": message, "section": section, "page": category,
                       "files": [a["name"] for a in attachments]}, pid)
    try:
        turn = ai.converse(p["objective"], p["setup"].get("model_description", ""),
                           {**p["setup"], "current_section": category}, db.get_messages(pid))
    except Exception as e:  # noqa: BLE001
        db.log("ai_error", {"error": str(e)}, pid)
        raise HTTPException(502, f"AI service error: {e}")
    before = {v["name"]: v.get("section") for v in p["setup"].get("variables") or []}
    for v in turn.variables:  # new variables go under the section the user wrote from
        v.section = before.get(v.name) or v.section or (category if category in SECTIONS else None)
    setup = _clean_setup({**p["setup"], "variables": [v.model_dump() for v in turn.variables],
                          "targets": [t.model_dump() for t in turn.targets],
                          "goal": turn.goal.model_dump() if turn.goal else None,
                          "ready": turn.status == "ready"})
    db.update_project(pid, setup=setup)
    db.add_message(pid, "assistant", turn.message, None, category)
    db.log("clarifying_question" if turn.status == "clarify" else "parameters_identified",
           {"message": turn.message, "variables": [v.name for v in turn.variables],
            "targets": [t.name for t in turn.targets]}, pid)
    return {"type": turn.status, "message": turn.message, "setup": setup}


# --- Geometry: cross sections (simplified drawing with node coordinates and spacings) -------------------
def _raw_path(pid: str, sid: str) -> Path:
    return data_dir() / "projects" / pid / "sections" / f"{sid}.raw.json"


def _touch(s: dict) -> dict:
    """The geometry changed: score it again; an earlier confirmation by the engineer no longer holds."""
    s["quality"] = geometry.quality(s)
    s.pop("confirmed", None)
    return s


def _section_payload(sec: dict, labels=1, dims=1, numbers=0) -> dict:
    if "quality" not in sec:
        sec["quality"] = geometry.quality(sec)
    return {"section": sec, "svg": geometry.svg(sec, bool(labels), bool(dims), bool(numbers))}


JOBS: dict[str, dict] = {}


@app.post("/api/projects/{pid}/sections")
def add_sections(pid: str, files: list[UploadFile] = File(default=[]), wait: int = 0):
    """Each file can hold several cross sections (e.g. a PDF sheet with sections A-A and B-B): each one is added.
    Reading runs in the background; the page follows it through /api/jobs/{id} (wait=1 reads straight away)."""
    _project_or_404(pid)
    saved = _save_uploads(pid, files, "sections")
    jid = db.new_id()
    JOBS[jid] = {"id": jid, "status": "running", "message": "Reading " + ", ".join(f["name"] for f in saved) + "…",
                 "added": [], "errors": [], "started": time.time()}
    if wait or not RUN_IN_BACKGROUND:
        _read_sections(pid, saved, JOBS[jid])
        return {**JOBS[jid], "sections": _project_or_404(pid)["setup"].get("sections") or []}
    threading.Thread(target=_read_sections, args=(pid, saved, JOBS[jid]), daemon=True).start()
    return JOBS[jid]


@app.post("/api/jobs/{jid}/stop")
def stop_job(jid: str):
    """Stop waiting (e.g. for PLAXIS to start). PLAXIS itself keeps running."""
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404, "Unknown job")
    j["stop"] = True
    return {"ok": True}


@app.get("/api/jobs/{jid}")
def job(jid: str):
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404, "Unknown job")
    out = {**j, "seconds": round(time.time() - j["started"])}
    if j["status"] != "running":
        out["sections"] = (db.get_project(j["pid"])["setup"].get("sections") or []) if j.get("pid") else []
    return out


def _read_sections(pid: str, saved: list[dict], job: dict) -> None:
    job["pid"] = pid
    try:
        _read_sections_inner(pid, saved, job)
    except Exception as e:  # noqa: BLE001
        job["errors"].append(f"{type(e).__name__}: {e}")
    job["status"] = "done"
    job["message"] = (f"Added {len(job['added'])} cross section(s): {', '.join(job['added'])}." if job["added"]
                      else "No cross section added.")


def _read_sections_inner(pid: str, saved: list[dict], job: dict) -> None:
    added, errors = job["added"], job["errors"]

    def progress(msg):
        job["message"] = msg

    for f in saved:
        progress(f"Reading {f['name']}…")
        try:
            geometry.check_allowed(Path(f["path"]), f["name"])  # CAD only for now (PDF_IMPORT=1 re-enables PDFs)
        except geometry.GeometryError as e:
            errors.append(str(e))
            continue
        try:
            drafts = geometry.read_file(Path(f["path"]), progress)
        except geometry.GeometryError as e:
            errors.append(str(e))
            continue
        except Exception as e:  # noqa: BLE001  unexpected content: look at the drawing as a picture instead
            from .config import pdf_import_enabled
            try:
                if not pdf_import_enabled():
                    raise e  # CAD only: a damaged CAD file is reported, not read as a picture
                drafts = geometry.read_picture(Path(f["path"]), progress)
            except Exception:  # noqa: BLE001
                errors.append(f"{f['name']}: could not read it ({type(e).__name__}: {e})")
                continue
        for k, d in enumerate(drafts, 1):
            progress(f"{f['name']}: simplifying cross section {k} of {len(drafts)}…")
            raw = d.pop("raw")
            ch = d.pop("chainage", None)
            sec = {**d, "id": db.new_id(), "file": f["name"], "y": ch or 0.0,
                   "chainage_from": "drawing" if ch is not None else None}
            try:
                geometry.rebuild(sec, raw)
                sec["layers"] = geometry.soil_layers(sec)
            except geometry.GeometryError as e:
                errors.append(f"{sec['name']}: {e}")
                continue
            if geometry.classify_with_ai(sec):
                progress(f"{sec['name']}: the AI checked soil / structure / water lines")
            _touch(sec)
            raw = _second_reading(Path(f["path"]), sec, raw, progress)
            _raw_path(pid, sec["id"]).write_text(json.dumps(raw))
            p = _project_or_404(pid)  # re-read: the user may edit other sections meanwhile
            db.update_project(pid, setup={**p["setup"], "sections": list(p["setup"].get("sections") or []) + [sec]})
            added.append(sec["name"])
    db.log("cross_section", {"added": added, "errors": errors}, pid)


def _read_again(path: Path, page, how: str) -> tuple[dict, list]:
    """Reads one page of the file again, with Claude Fable (how="ai") or as a picture; the largest drawing wins."""
    pages = [page] if page else None
    if how == "ai":
        polys, depth, warn = geometry.read_with_ai(geometry._only_pages(path, pages))
        drafts = [{"kind": "ai", "source": "ai", "raw": [[[list(q) for q in pl[0]], pl[1], pl[2]] for pl in polys],
                   "y_is_depth": depth, "unit": 1.0, "warnings": warn}]
    else:
        drafts = geometry._trace_picture(path, pages)

    def size(d):
        ps = [q for r in d["raw"] for q in r[0]]
        return (max(q[0] for q in ps) - min(q[0] for q in ps)) * (max(q[1] for q in ps) - min(q[1] for q in ps)) if ps else 0
    d = max(drafts, key=size)
    raw = d.pop("raw")
    for k in ("chainage", "name", "off"):
        d.pop(k, None)
    return d, raw


def _use_reading(s: dict, d: dict, raw: list) -> None:
    for k in ("source", "scale"):
        s.pop(k, None)
    s.update({k: v for k, v in d.items() if k in ("kind", "source", "y_is_depth", "unit", "scale", "warnings")})
    s["groups"], s["off"], s["roles"] = {}, [], {}
    for k in ("design", "design_applied"):
        s.pop(k, None)
    geometry.rebuild(s, raw)
    s["layers"] = geometry.soil_layers(s)
    _touch(s)


RANK = {"low": 0, "check": 1, "high": 2}


def _second_reading(path: Path, sec: dict, raw: list, progress) -> list:
    """Tier 2: a PDF or picture that read badly is read again by Claude Fable (when the API key is set). The
    better of the two readings is kept, and the engineer is told."""
    from .config import ai_mode
    if sec["quality"]["level"] != "low" or sec.get("kind") not in ("pdf", "ai") or sec.get("source") == "ai":
        return raw
    if ai_mode() != "claude":
        sec.setdefault("warnings", []).append(
            "This reading looks poor. With a Claude API key in Settings, Claude Fable reads such pages again "
            "automatically; otherwise check it, or use Read as a picture.")
        return raw
    progress(f"{sec['name']}: the reading looks poor, Claude Fable is reading the page again…")
    try:
        d, raw2 = _read_again(path, sec.get("page"), "ai")
        trial = json.loads(json.dumps(sec))
        _use_reading(trial, d, raw2)
    except Exception:  # noqa: BLE001  keep the first reading
        return raw
    if RANK[trial["quality"]["level"]] > RANK[sec["quality"]["level"]]:
        trial.setdefault("warnings", []).append("The first reading looked poor, so Claude Fable read the page "
                                                "again and its reading is used. Confirm it against the drawing.")
        sec.clear()
        sec.update(trial)
        return raw2
    return raw


class SectionIn(BaseModel):
    name: str | None = None
    y: float | None = None
    scale: float | None = None          # PDF: drawing scale 1:N
    unit: float | None = None           # DXF / CSV: metres per drawing unit
    groups: dict[str, bool] | None = None   # layers / line groups to use
    roles: dict[str, str] | None = None     # layer -> soil / structure / water / ignore ("" = automatic)
    redraw: bool = False                # draw again from the file (replaces hand edits)
    reclassify: bool = False            # identify soil / structure lines again
    model: bool = False                 # make PLAXIS model geometry (straighten, close boxes, split crossings)


def _section(p: dict, sid: str) -> dict:
    s = next((s for s in p["setup"].get("sections") or [] if s["id"] == sid), None)
    if not s:
        raise HTTPException(404, "Cross section not found")
    return s


@app.patch("/api/projects/{pid}/sections/{sid}")
def edit_section(pid: str, sid: str, body: SectionIn, labels: int = 1, dims: int = 1, numbers: int = 0):
    p = _project_or_404(pid)
    s = _section(p, sid)
    if body.name is not None and body.name.strip():
        s["name"] = body.name.strip()
    if body.y is not None:
        s["y"] = body.y
        s["chainage_from"] = "user"
    redraw = body.redraw
    if body.scale and body.scale > 0 and s.get("kind") == "pdf":
        s["scale"], s["unit"], redraw = body.scale, geometry.PT * body.scale, True
        s["warnings"] = [w for w in s.get("warnings", []) if "scale" not in w]
    if body.unit and body.unit > 0 and s.get("kind") != "pdf":
        s["unit"], redraw = body.unit, True
        s["warnings"] = [w for w in s.get("warnings", []) if "units" not in w]
    if body.groups:
        s.setdefault("groups", {}).update(body.groups)
        redraw = True
    if body.roles:
        roles = s.setdefault("roles", {})
        for g, r in body.roles.items():
            if r in geometry.ROLES:
                roles[g] = r
                s.setdefault("groups", {})[g] = r != "ignore"
            else:
                roles.pop(g, None)
        if s.get("kind") == "dxf":
            geometry.learn_roles(body.roles)
        redraw = True
    geometry_changed = redraw or body.model or body.reclassify
    if redraw:
        rp = _raw_path(pid, sid)
        if not rp.exists():
            raise HTTPException(409, "The original file of this section is not available any more.")
        try:
            geometry.rebuild(s, json.loads(rp.read_text()))
        except geometry.GeometryError as e:
            raise HTTPException(400, str(e))
        geometry.classify_with_ai(s)
    if body.model:
        try:
            geometry.model_geometry(s)
        except geometry.GeometryError as e:
            raise HTTPException(400, str(e))
    elif body.reclassify and not redraw:
        s["segments"] = geometry.keep_fixed(s, [[a, b, t] for (a, b, *_x), t in
                         zip(s["segments"], geometry.classify(s["nodes"], [x[:2] for x in s["segments"]]))])
        geometry.classify_with_ai(s)
    s["layers"] = geometry.soil_layers(s)
    if geometry_changed:
        _touch(s)
    db.update_project(pid, setup=p["setup"])
    return _section_payload(s, labels, dims, numbers)


class GeometryIn(BaseModel):
    nodes: list[list]
    segments: list[list]


@app.put("/api/projects/{pid}/sections/{sid}/geometry")
def edit_geometry(pid: str, sid: str, body: GeometryIn, labels: int = 1, dims: int = 1, numbers: int = 0):
    """Hand edits from the live coordinate table: saves and returns the new drawing (spacings recomputed)."""
    p = _project_or_404(pid)
    s = _section(p, sid)
    try:
        geometry.set_geometry(s, body.nodes, body.segments)
    except geometry.GeometryError as e:
        raise HTTPException(400, str(e))
    s["layers"] = geometry.soil_layers(s)
    _touch(s)
    db.update_project(pid, setup=p["setup"])
    return _section_payload(s, labels, dims, numbers)


class RereadIn(BaseModel):
    how: str = "ai"   # ai: Claude Fable reads the drawing; picture: trace the lines from the picture of the page


@app.post("/api/projects/{pid}/sections/{sid}/reread")
def reread_section(pid: str, sid: str, body: RereadIn, labels: int = 1, dims: int = 1, numbers: int = 0):
    """When the lines read from the file are not right: read the same page again, as a picture."""
    p = _project_or_404(pid)
    s = _section(p, sid)
    path = data_dir() / "projects" / pid / "sections" / (s.get("file") or "")
    if not s.get("file") or not path.exists():
        raise HTTPException(409, "The original file of this section is not available any more. Upload it again.")
    from .config import ai_mode, pdf_import_enabled
    if not pdf_import_enabled():
        raise HTTPException(400, "Reading drawings as pictures or with Claude Fable is switched off for now (CAD only). "
                                 "Fix the CAD layers in Extracted lines, or set PDF_IMPORT=1 in .env.")
    if body.how == "ai" and ai_mode() != "claude":
        raise HTTPException(400, "Reading with Claude Fable needs the Claude API key in Settings. "
                                 "Without it, use Read as a picture.")
    try:
        d, raw = _read_again(path, s.get("page"), "ai" if body.how == "ai" else "picture")
        _use_reading(s, d, raw)
    except geometry.GeometryError as e:
        raise HTTPException(400, str(e))
    _raw_path(pid, sid).write_text(json.dumps(raw))
    db.update_project(pid, setup=p["setup"])
    return _section_payload(s, labels, dims, numbers)


# --- Design consultant: the section as a whole model, with checks and options ------------------------------
def _design_of(s: dict) -> dict:
    d = s.get("design")
    if d and d.get("type") == "general":
        return design.general(s)  # always the current lines: hand edits show straight away
    return d or design.interpret(s)


def _design_payload(s: dict) -> dict:
    p = _design_of(s)
    assumed = p.get("assumed", s.get("design_assumed", []))
    params = design.normalise(p)
    opts = design.options(params)
    return {"params": params, "assumed": assumed, "remarks": design.remarks(params), "stages": design.stages(params),
            "svg": design.svg(params, s["name"], s.get("y")),
            "options": {k: {"label": design.OPTIONS[k][0], "about": design.OPTIONS[k][1], "params": v}
                        for k, v in opts.items()},
            "saved": bool(s.get("design")), "applied": bool(s.get("design_applied"))}


@app.get("/api/projects/{pid}/sections/{sid}/design")
def get_design(pid: str, sid: str, fresh: int = 0, template: str = ""):
    """The section read as a model. template = retaining_wall | general switches the kind of problem."""
    p = _project_or_404(pid)
    s = _section(p, sid)
    if fresh or template:
        s.pop("design", None)
        s.pop("design_applied", None)
        d = design.interpret(s, template or None)
        s["design_assumed"] = d.get("assumed", [])
        if template:
            s["design"] = design.normalise(d)
        db.update_project(pid, setup=p["setup"])
    elif not s.get("design"):
        s["design_assumed"] = design.interpret(s).get("assumed", [])
    return _design_payload(s)


class DesignIn(BaseModel):
    params: dict
    apply: bool = False


@app.put("/api/projects/{pid}/sections/{sid}/design")
def put_design(pid: str, sid: str, body: DesignIn):
    """Saves the consultant model. With apply, it also becomes the section's PLAXIS geometry (nodes and lines)."""
    p = _project_or_404(pid)
    s = _section(p, sid)
    try:
        s["design"] = design.normalise(body.params)
    except (TypeError, ValueError, KeyError) as e:
        raise HTTPException(400, f"Check the values: {e}")
    if body.apply:
        nodes, segs = design.to_geometry(s["design"])
        s["nodes"], s["segments"] = nodes, segs
        s["model"], s["edited"], s["design_applied"] = True, True, True
        s["layers"] = geometry.soil_layers(s)
        s.pop("fixed", None)
        _touch(s)
    db.update_project(pid, setup=p["setup"])
    return _design_payload(s) | {"section": s}


class ConfirmIn(BaseModel):
    confirmed: bool = True
    checks: list[str] = []
    by: str = ""


@app.post("/api/projects/{pid}/sections/{sid}/confirm")
def confirm_section(pid: str, sid: str, body: ConfirmIn):
    """Tier 3: the engineer confirms the geometry against the drawing. Any later change to it clears this."""
    p = _project_or_404(pid)
    s = _section(p, sid)
    if body.confirmed:
        s["confirmed"] = {"at": time.strftime("%Y-%m-%d %H:%M"), "checks": body.checks, "by": body.by.strip()}
    else:
        s.pop("confirmed", None)
    db.update_project(pid, setup=p["setup"])
    db.log("cross_section", {"confirmed" if body.confirmed else "unconfirmed": s["name"]}, pid)
    return {"section": s}


@app.get("/api/layer-roles")
def get_layer_roles():
    return geometry.layer_library()


@app.put("/api/layer-roles")
def put_layer_roles(body: dict[str, str]):
    return geometry.save_layer_library(body)


class Model3dIn(BaseModel):
    items: list[dict]
    mode: str = "linear"


@app.post("/api/projects/{pid}/model3d")
def model3d(pid: str, body: Model3dIn):
    """Solid 3D model along the job: each section's consultant model placed at its chainage, with the chosen
    transition between them."""
    p = _project_or_404(pid)
    items = []
    for it in body.items:
        try:
            s = _section(p, it.get("sid", ""))
        except HTTPException:
            continue
        items.append({"name": s["name"], "y": float(it.get("y", s.get("y") or 0)),
                      "design": _design_of(s)})
    return design.model3d(items, "step" if body.mode == "step" else "linear")


@app.delete("/api/projects/{pid}/sections/{sid}")
def delete_section(pid: str, sid: str):
    p = _project_or_404(pid)
    _section(p, sid)
    p["setup"]["sections"] = [s for s in p["setup"]["sections"] if s["id"] != sid]
    db.update_project(pid, setup=p["setup"])
    _raw_path(pid, sid).unlink(missing_ok=True)
    db.log("cross_section", {"removed": sid}, pid)
    return {"sections": p["setup"]["sections"]}


@app.get("/api/projects/{pid}/sections/{sid}.svg")
def section_svg(pid: str, sid: str, labels: int = 1, dims: int = 1, numbers: int = 0, download: int = 0):
    s = _section(_project_or_404(pid), sid)
    fname = re.sub(r"[^\w.-]", "_", s["name"]) + ".svg"
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'} if download else {}
    return Response(geometry.svg(s, bool(labels), bool(dims), bool(numbers)), media_type="image/svg+xml",
                    headers=headers)


# --- PLAXIS model: the geometry from the PLAXIS project's own coordinates, and what to optimize ---------------
def _version(p: dict) -> str:
    return (p["setup"] or {}).get("plaxis") or "2d"


def _picked_names(m: dict, picked: list[str]) -> list[str]:
    """Points and objects (structures, loads, lines) of the picked items, drawn in orange."""
    names = []
    for x in plaxis_model.candidates(m):
        if x["key"] not in picked:
            continue
        if x["key"].startswith("move:"):
            names.append(x["key"][5:].rsplit(".", 1)[0])
        elif x["key"].startswith(("pt:", "geo:")):
            names.append(x["plaxis_ref"].rsplit(".", 1)[0])
    return list(dict.fromkeys(names))


def _model_payload(p: dict, labels: int = 1, view: str = "iso") -> dict:
    m = (p["setup"] or {}).get("plaxis_model")
    if not m:
        return {"model": None, "version": _version(p)}
    picked = [v.get("item") for v in p["setup"].get("variables") or [] if v.get("item")]
    return {"model": m, "version": m["version"], "view": view,
            "svg": plaxis_model.svg(m, bool(labels), _picked_names(m, picked), view),
            "objects": plaxis_model.objects(m), "candidates": plaxis_model.candidates(m), "selected": picked}


def _save_model(pid: str, m: dict) -> dict:
    p = _project_or_404(pid)
    chosen = _version(p)
    if m["version"] != chosen:
        m["notes"] = [f"PLAXIS says this is a PLAXIS {m['version'].upper()} project; the app was set to PLAXIS "
                      f"{chosen.upper()}, so it switched to {m['version'].upper()}."] + m.get("notes", [])
    db.update_project(pid, setup={**p["setup"], "plaxis_model": m, "plaxis": m["version"]})
    db.log("plaxis_model", {"source": m["source"], "version": m["version"], **m["counts"]}, pid)
    return _model_payload(_project_or_404(pid))


@app.post("/api/projects/{pid}/plaxis-model/read")
def read_plaxis_model(pid: str, demo: int = 0, wait: int = 0, restart: int = 0):
    """Reads points, lines, soil layers, structures, loads, materials and phases from the user's PLAXIS project.
    The app connects to PLAXIS by itself (starting PLAXIS Input when needed), whatever the solver setting, and
    switches to real calculations once connected. demo=1 loads the example model instead (only when asked).
    Runs in the background: the page follows it through /api/jobs/{id}."""
    p = _project_or_404(pid)
    if demo:
        return _save_model(pid, plaxis_model.demo(_version(p)))
    jid = db.new_id()
    JOBS[jid] = {"id": jid, "status": "running", "message": "Connecting to PLAXIS…", "errors": [], "added": [],
                 "started": time.time(), "pid": pid, "restart": bool(restart)}
    if wait or not RUN_IN_BACKGROUND:
        _read_model_job(pid, JOBS[jid])
        return JOBS[jid]
    threading.Thread(target=_read_model_job, args=(pid, JOBS[jid]), daemon=True).start()
    return JOBS[jid]


def _read_model_job(pid: str, job: dict) -> None:
    def progress(msg):
        job["message"] = msg
    p = _project_or_404(pid)
    progress("Waiting for PLAXIS to be free (another run is using it)")
    with engine._run_lock:  # one PLAXIS session at a time: never read the model while a run calculates
        engine.BUSY.update(what="reading the model", task=None, since=time.time())
        try:
            _read_model_locked(pid, p, job, progress)
        finally:
            engine.BUSY.clear()
    job["status"] = "done"


def _read_model_locked(pid, p, job, progress) -> None:
    try:
        from .solvers import PlaxisSolver
        m = PlaxisSolver.__new__(PlaxisSolver)  # no settings check here: read_model connects (and starts PLAXIS)
        m._s = m._g = None
        model = PlaxisSolver.read_model(m, p["files"], _version(p), progress,
                                        should_stop=lambda: bool(job.get("stop")), restart=job.get("restart", False))
        job["model"] = _save_model(pid, model)
        job["message"] = f"Read {model['counts']['points']} points, {model['counts']['structures']} structures and " \
                         f"{model['counts']['materials']} materials from PLAXIS. Calculations now use PLAXIS."
    except Exception as e:  # noqa: BLE001
        job["errors"].append(f"Could not read your PLAXIS model: {e}")
        job["message"] = job["errors"][-1]
        db.log("plaxis_model", {"error": str(e)}, pid)


# --- Solver assistant (offline chat about PLAXIS and the connection) ----------------------------------------
@app.post("/api/solver/assistant")
def solver_chat(body: dict):
    from . import solver_assistant
    p = db.get_project(body.get("project_id") or "") if body.get("project_id") else None
    r = solver_assistant.answer(body.get("message", ""), p)
    db.log("solver_assistant", {"message": body.get("message", ""), "answer": r["message"][:300]},
           p["id"] if p else None)
    return r


@app.get("/api/solver/diagnose")
def solver_diagnose():
    from . import solver_assistant
    d = solver_assistant.diagnose()
    return {"diagnosis": d, "text": solver_assistant.diagnosis_text(d)}


@app.post("/api/solver/connect")
def solver_connect(version: str = "3d", restart: int = 0, wait: int = 0):
    """Connects to PLAXIS (finding or starting it) without reading a model. Followed through /api/jobs/{id}."""
    jid = db.new_id()
    JOBS[jid] = {"id": jid, "status": "running", "message": "Connecting to PLAXIS…", "errors": [], "added": [],
                 "started": time.time()}

    def work(job):
        from . import plaxis_launcher
        try:
            plaxis_launcher.connect(version if version in ("2d", "3d") else "3d",
                                    lambda m: job.update(message=m), should_stop=lambda: bool(job.get("stop")),
                                    restart=bool(restart))
            cfg = settings.load()
            job["message"] = f"Connected to PLAXIS on port {cfg['port']}. Calculations now use PLAXIS."
        except Exception as e:  # noqa: BLE001
            job["errors"].append(str(e))
            job["message"] = str(e)
        job["status"] = "done"
    if wait or not RUN_IN_BACKGROUND:
        work(JOBS[jid])
        return JOBS[jid]
    threading.Thread(target=work, args=(JOBS[jid],), daemon=True).start()
    return JOBS[jid]


@app.put("/api/solver/mode")
def solver_mode(body: dict):
    """connect_mode: auto (the app finds / starts PLAXIS and starts its server) or manual (the engineer presses
    Start server in PLAXIS and types the port and password here). Port and password are saved when given."""
    mode = body.get("mode") if body.get("mode") in ("auto", "manual") else None
    new = {"connect_mode": mode}
    if body.get("port"):
        new.update(port=int(body["port"]), host="localhost")
    if body.get("password"):
        new["password"] = str(body["password"]).strip()
    if body.get("use_plaxis"):  # Connect pressed: the calculations run in PLAXIS from now on (no simulation)
        new["solver"] = "plaxis"
    s = settings.save(new)
    return {"mode": s.get("connect_mode") or "auto", "port": s["port"], "password_set": bool(s.get("password"))}


@app.post("/api/solver/stop-plaxis")
def solver_stop_plaxis():
    from . import plaxis_launcher
    return {"message": plaxis_launcher.stop_launched()}


@app.post("/api/projects/{pid}/model-files")
def add_model_files(pid: str, files: list[UploadFile] = File(default=[]), paths: list[str] = Form(default=[])):
    """Adds PLAXIS project files on page 2 (no need to go back to page 1): the .p3d / .p2dx, its .p3dat /
    .p2dxdat data folder (folder upload keeps the sub-folders), or a zip of either. Saved next to the files
    uploaded before, so a .p3d and its data folder end up side by side."""
    p = _project_or_404(pid)
    root = data_dir() / "projects" / pid / "input"
    root.mkdir(parents=True, exist_ok=True)
    added, folders, n = [], {}, 0
    for i, f in enumerate(files):
        if not f.filename:
            continue
        rel = (paths[i] if i < len(paths) and paths[i] else f.filename).replace("\\", "/")
        parts = [re.sub(r"[^\w.\- ]", "_", x) for x in rel.split("/") if x not in ("", ".", "..")]
        if not parts:
            continue
        dest = root.joinpath(*parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as fh:
            shutil.copyfileobj(f.file, fh)
        n += 1
        if len(parts) > 1:
            folders[parts[0]] = folders.get(parts[0], 0) + 1
            continue
        low = dest.name.lower()
        if low.endswith(".zip"):
            with zipfile.ZipFile(dest) as z:
                names = z.namelist()
                if not any(x.lower().endswith(tuple(PLAXIS_EXT_ALL)) for x in names):
                    z.extractall(root)  # only the data folder: unpack it next to the project file
                    folders[dest.stem + " (unzipped)"] = len(names)
                    continue
        added.append({"name": dest.name, "path": str(dest), "size": dest.stat().st_size, "source": "upload"})
    items = [{"name": f"{k} (folder, {v} files)", "path": str(root / k.split(" (")[0]), "source": "upload", "folder": True}
             for k, v in folders.items()]
    plx = [a for a in added if a["name"].lower().endswith(tuple(PLAXIS_EXT_ALL) + (".zip",))]
    other = [a for a in added if a not in plx]
    old = [f for f in p["files"] if f["path"] not in {a["path"] for a in added + items}]
    files_now = plx + old + other + items  # a new project file / zip is read first
    db.update_project(pid, files=files_now)
    db.log("request", {"added_model_files": [a["name"] for a in added + items]}, pid)
    return {"files": files_now, "saved": n}


PLAXIS_EXT_ALL = (".p3d", ".p2dx", ".p3dxml")


@app.post("/api/projects/{pid}/plaxis-model/upload")
def upload_plaxis_model(pid: str, file: UploadFile = File(...)):
    """model.json written by app/plaxis_model.py run with PLAXIS' own Python."""
    _project_or_404(pid)
    try:
        m = plaxis_model.from_json(file.file.read().decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(400, str(e))
    return _save_model(pid, m)


@app.get("/api/projects/{pid}/plaxis-model")
def get_plaxis_model(pid: str, labels: int = 1, view: str = "iso"):
    return _model_payload(_project_or_404(pid), labels, view)


@app.get("/api/plaxis-model-script")
def plaxis_model_script():
    return FileResponse(Path(__file__).resolve().parent / "plaxis_model.py", media_type="text/x-python",
                        filename="plaxis_model.py")


class SelectionIn(BaseModel):
    items: list[str]


@app.put("/api/projects/{pid}/plaxis-model/selection")
def select_items(pid: str, body: SelectionIn, labels: int = 1, view: str = "iso"):
    """The items ticked in the picker become variables (with their PLAXIS ref and current value); unticked ones go."""
    p = _project_or_404(pid)
    m = p["setup"].get("plaxis_model")
    if not m:
        raise HTTPException(409, "Read the PLAXIS model first.")
    cands = {c["key"]: c for c in plaxis_model.candidates(m)}
    want = [k for k in body.items if k in cands]
    variables = [v for v in p["setup"].get("variables") or [] if not v.get("item") or v["item"] in want]
    have = {v.get("item") for v in variables}
    used = {v["name"] for v in variables} | {t["name"] for t in p["setup"].get("targets") or []}
    n = 1
    for k in want:
        if k in have:
            continue
        while f"p{n}" in used:
            n += 1
        used.add(f"p{n}")
        variables.append(plaxis_model.variable_from(cands[k], f"p{n}", m["version"]))
    setup = _clean_setup({**p["setup"], "variables": variables})
    db.update_project(pid, setup=setup)
    db.log("customize", {"picked": len(want), "variables": len(setup["variables"])}, pid)
    return _model_payload(_project_or_404(pid), labels, view) | {"setup": setup}


@app.post("/api/projects/{pid}/run-geometry")
def run_geometry(pid: str, body: dict, labels: int = 0):
    """The PLAXIS model as one run changes it: drawing (moved points in orange) and the list of changes."""
    p = _project_or_404(pid)
    m = p["setup"].get("plaxis_model")
    if not m:
        raise HTTPException(409, "Read the PLAXIS model first.")
    variables = [coerce_variable(v).model_dump() for v in body.get("variables") or p["setup"].get("variables") or []]
    run, changes, moved = plaxis_model.apply_inputs(m, variables, body.get("inputs") or {})
    return {"svg": plaxis_model.svg(run, bool(labels), moved), "changes": changes, "moved": moved}


# --- Run plan (runs per criterion, logical / customized sequencing) and the compiled scenarios ----------------
def _sequence_view(p: dict) -> dict:
    setup = p["setup"]
    variables = [coerce_variable(v) for v in setup.get("variables") or []]
    cfg = sequence.default_config(variables, setup.get("sequence"))
    runs, how, errors = sequence.build(variables, cfg) if variables else ([], "", ["Pick the items to optimize first."])
    plan_stub = {"sequence": cfg}
    vmap = {v.name: v for v in variables}
    rows = [{"run": sequence.run_no(i, plan_stub), "label": r["label"], "inputs": r["inputs"],
             "title": f"Run {sequence.run_no(i, plan_stub)}: {r['label']}",
             "values": {n: sequence.value_text(vmap[n], val) for n, val in r["inputs"].items() if n in vmap}}
            for i, r in enumerate(runs)]
    over = len(runs) > max_runs()
    if over:
        errors = errors + [f"That is {len(runs)} runs, more than the limit of {max_runs()}. Use fewer runs per "
                           "criterion, or combine the criteria together / one at a time."]
    steps = {}
    for sec in cfg["criteria"]:
        vs = [v for v in variables if norm_section(v.section, v.plaxis_ref) == sec]
        c = cfg["criteria"][sec]
        steps[sec] = {v.name: sequence.logical_steps(v, int(c["runs"]), cfg["ends"].get(v.name) or {}) for v in vs}
    shown = {sec: {n: [sequence.value_text(vmap[n], x) for x in xs] for n, xs in d.items()} for sec, d in steps.items()}
    raw = {sec: {n: [f"{x[0]}: {x[1]:g}" if isinstance(x, tuple) else (f"{x:g}" if isinstance(x, float) else str(x))
                     for x in xs] for n, xs in d.items()} for sec, d in steps.items()}
    return {"config": cfg, "combine_options": sequence.COMBINE, "rows": rows, "how": how, "errors": errors,
            "total": len(runs), "logical": shown, "logical_raw": raw,
            "variables": [{**v.model_dump(), "name": v.name} for v in variables]}


@app.get("/api/projects/{pid}/sequence")
def get_sequence(pid: str):
    return _sequence_view(_project_or_404(pid))


@app.put("/api/projects/{pid}/sequence")
def put_sequence(pid: str, body: dict):
    p = _project_or_404(pid)
    variables = [coerce_variable(v) for v in p["setup"].get("variables") or []]
    try:
        cfg = sequence.default_config(variables, body)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"Check the run plan: {e}")
    db.update_project(pid, setup={**p["setup"], "sequence": cfg})
    return _sequence_view(_project_or_404(pid))


@app.get("/api/outputs")
def output_catalog(version: str = "2d"):
    """The PLAXIS results the app can extract at every run (Output page)."""
    return plaxis_model.outputs(version)


# --- Output page: Results and Report -------------------------------------------------------------------------
@app.put("/api/projects/{pid}/output")
def set_output(pid: str, body: dict):
    p = _project_or_404(pid)
    opts = {k: bool(body.get(k, v)) for k, v in report.output_options(p["setup"]).items()}
    db.update_project(pid, setup={**p["setup"], "output": opts})
    return opts


# --- the four studies (sensitivity, optimization, modification, scenarios) -------------------------------------
def _study_model(p: dict) -> dict:
    m = (p["setup"] or {}).get("plaxis_model")
    if not m:
        raise HTTPException(409, "Read the PLAXIS model first.")
    return m


# --- the basic model run from the first page (no visualization): the existing design, every PLAXIS result ---------
@app.post("/api/projects/{pid}/basic")
def basic_run(pid: str):
    """Step 2 of the first page: the uploaded PLAXIS model calculated completely in PLAXIS (never simulated). Only the
    example model (no PLAXIS file) is simulated."""
    p = _project_or_404(pid)
    example = not (p.get("files") or [])
    if not example and not engine.BUSY:
        try:
            from .solvers import PlaxisSolver
            PlaxisSolver().test()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(409, f"PLAXIS is not connected ({e}). In PLAXIS Input: Expert › Configure remote "
                                     "scripting server › Start server, then paste the password and press Connect.")
    plan_data = _basic_plan(p, example)
    tid = db.create_task(pid, "reporting", "Basic model: one PLAXIS run of the existing design.", plan_data)
    db.add_runs(tid, [{"label": "Existing design", "inputs": {}}])
    db.log("task_definition", {"summary": "Basic model run", "runs": 1}, pid, tid)
    db.update_task(tid, status="queued")
    engine.start(tid, RUN_IN_BACKGROUND)
    return basic_view(pid)


def _basic_plan(p: dict, example: bool, targets: list[dict] | None = None) -> dict:
    m = (p["setup"] or {}).get("plaxis_model") or {}
    # V26.1.9: the full run reads the main results of V26.1.8 only; the results added for the missing features
    # (interfaces, flow, trough, beams) are chosen in the studies, so the full run of a 3D model stays as long as before
    outs = [o for o in plaxis_model.outputs(p["setup"].get("plaxis") or "2d") if o["key"] != "file" and o["key"] in plaxis_model.BASIC_KEYS]
    targets = targets or ([{"name": f"T{i}", "label": o["label"], "result": o["result"], "phase": study._phase_for(o, m),
                            "operator": "report", "value": None, "unit": o["unit"], "key": o["key"]} for i, o in enumerate(outs, 1)]
                          + _stage_targets(outs, m))
    s = study.clean({"mode": "reporting", "outputs": [t["key"] for t in targets if t.get("key")]})
    return {"variables": [], "targets": targets, "goal": None, "output": report.output_options(p["setup"]),
            "plaxis": p["setup"].get("plaxis") or "2d", "study": s, "first": 0, "offset": 0, "previous": [],
            "basic": True, "backend": "simulated" if example else "plaxis", "example": example}


def _stage_targets(outs: list[dict], m: dict) -> list[dict]:
    """Every main result at every stage of the model (the summary tables of the control parameters): the factor of
    safety in the safety phases, the other results in the other phases (not the initial phase)."""
    out, i = [], 0
    for ph in (m.get("phases") or [])[1:]:
        safe = "safety" in f"{ph.get('type', '')} {ph.get('id', '')}".lower()
        for o in outs:
            if (o["key"] == "fos") != safe:
                continue
            i += 1
            out.append({"name": f"S{i}", "label": f"{o['label']} @ {ph.get('id') or ph['name']}", "base_label": o["label"],
                        "result": o["result"], "phase": ph["name"], "stage": True,
                        "phase_label": ph.get("id") or ph["name"], "operator": "report", "value": None, "unit": o["unit"],
                        "key": o["key"]})
    return out


def _stages_view(t: dict, outs: dict, setup: dict, m: dict) -> dict:
    """The summary table: each main result at each stage, with what is missing (not read, or no such item)."""
    st = [tg for tg in t["plan"]["targets"] if tg.get("stage")]
    phases = [{"name": ph["name"], "label": ph.get("id") or ph["name"], "safety": "safety" in f"{ph.get('type', '')} {ph.get('id', '')}".lower()}
              for ph in (m.get("phases") or [])[1:]]
    has = {k: bool(v) for k, v in {
        "plate": [s for s in m.get("structures") or [] if s.get("kind") == "plate"],
        "anchor": [s for s in m.get("structures") or [] if "anchor" in (s.get("kind") or "")],
        "pile": [s for s in m.get("structures") or [] if "embedded" in (s.get("kind") or "") or "pile" in (s.get("kind") or "")],
    }.items()}
    need = {"wall_ux": "plate", "plate_m": "plate", "plate_q": "plate", "plate_n": "plate", "anchor": "anchor", "pile_n": "pile"}
    rows = {}
    for tg in st:
        v, u = units.to_display(outs.get(tg["name"]), tg["unit"], setup)
        r = rows.setdefault(tg["key"], {"key": tg["key"], "label": tg.get("base_label") or tg["label"], "unit": u, "values": {},
                                        "absent": bool(need.get(tg["key"]) and not has[need[tg["key"]]])})
        r["values"][tg["phase"]] = v
    return {"phases": phases, "rows": list(rows.values()), "read": bool(st)}


def _basic_from_cache(pid: str) -> dict | None:
    """The same PLAXIS file was calculated fully before (another project, before an update or a restart): its run is
    taken over instead of calculating it again."""
    from . import basic_cache
    p = db.get_project(pid)
    try:
        c = basic_cache.get(basic_cache.model_hash(p.get("files") or []))
    except OSError:
        c = None
    if not c:
        return None
    plan_data = {**_basic_plan(p, False, c["targets"]), "cached": {"at": c["at"], "file": c.get("file") or ""}}
    tid = db.create_task(pid, "reporting", "Basic model: taken from the earlier full PLAXIS run of the same file.", plan_data)
    db.add_runs(tid, [{"label": "Existing design", "inputs": {}}])
    run = db.get_runs(tid)[0]
    db.update_run(run["id"], status="succeeded", outputs=c["outputs"], checks={}, passed=True, attempts=0)
    db.update_task(tid, status="succeeded")
    if c.get("model") and not ((p["setup"] or {}).get("plaxis_model") or {}).get("source") == "plaxis":
        db.update_project(pid, setup={**p["setup"], "plaxis_model": c["model"], "plaxis": c["model"].get("version") or p["setup"].get("plaxis")})
    db.log("basic_cached", {"file": c.get("file"), "calculated_at": c["at"]}, pid, tid)
    return db.get_task(tid)


@app.post("/api/projects/{pid}/basic/cancel")
def basic_cancel(pid: str):
    """Stops waiting for the basic model run (e.g. PLAXIS hangs): the run is marked stopped and can be run again.
    A calculation already inside PLAXIS finishes there (stop it in PLAXIS if needed)."""
    _project_or_404(pid)
    rows = db._query("SELECT id FROM tasks WHERE project_id = ? ORDER BY created_at DESC LIMIT 50", (pid,))
    for r in rows:
        t = db.get_task(r["id"])
        if (t["plan"] or {}).get("basic") and t["status"] in ("queued", "running"):
            engine.ask_stop(t["id"])
            db.update_task(t["id"], status="stopped", error="Cancelled by the engineer.")
            for run in db.get_runs(t["id"]):
                if run["status"] in ("queued", "pending", "running"):
                    db.update_run(run["id"], status="stopped")
    return basic_view(pid)


@app.get("/api/projects/{pid}/basic")
def basic_view(pid: str):
    """The latest basic model run: its status and every result by output key (the "current" values)."""
    _project_or_404(pid)
    rows = db._query("SELECT id FROM tasks WHERE project_id = ? ORDER BY created_at DESC LIMIT 50", (pid,))
    t = next((db.get_task(r["id"]) for r in rows if (db.get_task(r["id"])["plan"] or {}).get("basic")), None)
    if not t:
        t = _basic_from_cache(pid)
    if not t:
        return {"status": None, "values": {}, "results": []}
    limit = float(os.environ.get("PLAXIS_BASIC_TIMEOUT_MIN", "180")) * 60  # no answer from PLAXIS for that long
    if t["status"] in ("queued", "running") and time.time() - (t.get("created_at") or time.time()) > limit:
        engine.ask_stop(t["id"])
        db.update_task(t["id"], status="failed", error=f"No result from PLAXIS after {limit / 60:.0f} min: check PLAXIS "
                                                        "(a dialog waiting for an answer, a calculation stuck), then run again.")
        t = db.get_task(t["id"])
    run = (db.get_runs(t["id"]) or [{}])[0]
    outs = run.get("outputs") or {}
    setup = (db.get_project(pid) or {}).get("setup") or {}
    prog = engine.PROGRESS.get(t["id"]) or {}
    busy = dict(engine.BUSY)
    waiting = t["status"] in ("queued", "running") and busy and busy.get("task") != t["id"]
    res = [{"key": tg["key"], "label": tg["label"], **dict(zip(("value", "unit"), units.to_display(outs.get(tg["name"]), tg["unit"], setup)))}
           for tg in t["plan"]["targets"] if not tg.get("stage")]
    stage_labels = {f"{tg['label']}" for tg in t["plan"]["targets"] if tg.get("stage")}
    return {"task": t["id"], "status": t["status"], "run_status": run.get("status"), "example": bool(t["plan"].get("example")),
            "missing": [x for x in outs.get("_missing") or [] if x.split(": ")[0] not in stage_labels],
            "stages": _stages_view(t, outs, setup, setup.get("plaxis_model") or {}),
            "plots": project_files.plots_of(t["id"], 0),
            "cached": t["plan"].get("cached"),
            "backend": t["plan"].get("backend") or "", "elapsed": round(time.time() - (t.get("created_at") or time.time())),
            "progress": (f"Waiting: PLAXIS is busy with {busy.get('what')} (since {round(time.time() - busy.get('since', time.time()))} s)"
                         if waiting else prog.get("message") or ""),
            "step_seconds": round(time.time() - prog["since"]) if prog.get("since") else None,
            "pct": 100 if t["status"] == "succeeded" else prog.get("pct", 0), "error": run.get("error") or t.get("error"),
            "values": {r["key"]: r["value"] for r in res if isinstance(r["value"], (int, float))}, "results": res}


@app.get("/api/projects/{pid}/study")
def get_study(pid: str):
    p = _project_or_404(pid)
    m = (p["setup"] or {}).get("plaxis_model")
    s = study.clean((p["setup"] or {}).get("study"), None if (p["setup"] or {}).get("study") else
                    (p["objective"] if p["objective"] in study.MODES else "sensitivity"))
    out = {"study": s, "modes": study.MODES}
    if m:
        out |= {"catalog": _catalog_units(study.catalog(m, s), p["setup"]), **_study_preview(m, s)}
    return out


def _catalog_units(cat: dict, setup: dict) -> dict:
    """Inputs labelled in the model's units; results (control parameters) in the chosen units."""
    for x in cat.get("params") or []:
        x["unit"] = units.input_label(x.get("unit") or "", setup)
    for o in cat.get("outputs") or []:
        o["unit"] = units.to_display(None, o.get("unit") or "", setup)[1]
    cat["units"] = {"plaxis": units.plaxis_of(setup), "display": units.display_of(setup), "chosen": units.clean(setup.get("units")),
                    "choices": units.CHOICES}
    return cat


def _study_preview(m: dict, s: dict) -> dict:
    b = study.build(m, s)
    return {"rows": b["rows"], "errors": b["errors"], "warnings": b.get("warnings") or [], "total": len(b["runs"]), "deleted": b.get("deleted", 0),
            "vars": [{k: v.get(k) for k in ("name", "item", "label", "unit")} for v in b["variables"]],
            "too_many": len(b["runs"]) > max_runs(), "max_runs": max_runs()}


@app.put("/api/projects/{pid}/study")
def put_study(pid: str, body: dict):
    p = _project_or_404(pid)
    s = study.clean(body)
    db.update_project(pid, setup={**p["setup"], "study": s})
    m = (p["setup"] or {}).get("plaxis_model")
    # V26.1.9: the study's PLAXIS features (new structures, points, dynamics) add inputs and results to the catalog
    return {"study": s, **({**_study_preview(m, s), "catalog": _catalog_units(study.catalog(m, s), p["setup"])} if m else {})}


@app.post("/api/projects/{pid}/study/run")
def run_study(pid: str):
    """Builds the runs of the study and starts them straight away."""
    p = _project_or_404(pid)
    m = _study_model(p)
    s = study.clean(p["setup"].get("study"))
    b = study.build(m, s)
    if b["errors"]:
        raise HTTPException(400, " ".join(b["errors"]))
    if len(b["runs"]) > max_runs():
        raise HTTPException(400, f"That is {len(b['runs'])} runs, more than the limit of {max_runs()}.")
    targets = b["targets"]
    if s["mode"] == "reporting" and not (s.get("time") or {}).get("on"):  # QA V26.1.8: every result at every stage too
        outs = {o["key"]: o for o in plaxis_model.outputs(p["setup"].get("plaxis") or "2d")}
        chosen = [outs[t["key"]] for t in targets if t.get("key") in outs]
        targets = targets + [{**x, "name": f"S{i}"} for i, x in enumerate(_stage_targets(chosen, m), 1)]
    return _start_study_task(p, s, b["variables"], _targets_to_plaxis(targets, p["setup"]), b["runs"], b["first"], 0, [])


TARGET_NUMS = ("value", "ref", "lo", "hi", "measured")


def _targets_to_plaxis(targets: list[dict], setup: dict) -> list[dict]:
    out = []
    for t in targets:
        t = dict(t)
        for k in TARGET_NUMS:
            if isinstance(t.get(k), (int, float)) and not isinstance(t.get(k), bool):
                t[k] = units.to_plaxis(t[k], t.get("unit") or "", setup)
        out.append(t)
    return out


def _targets_to_display(targets: list[dict], setup: dict) -> list[dict]:
    out = []
    for t in targets:
        t = dict(t)
        f, lab = units.to_display(1.0, t.get("unit") or "", setup)
        for k in TARGET_NUMS:
            if isinstance(t.get(k), (int, float)) and not isinstance(t.get(k), bool):
                t[k] = t[k] * f
        t["unit"] = lab
        out.append(t)
    return out


def _baseline_from_basic(p: dict, targets: list[dict]) -> dict | None:
    """Run 0 (the existing design) is the Original File: its results come from the full PLAXIS run on the first page
    when that run has every result the study reads. None: Run 0 is calculated like the other runs."""
    rows = db._query("SELECT id FROM tasks WHERE project_id = ? ORDER BY created_at DESC LIMIT 50", (p["id"],))
    bt = next((db.get_task(r["id"]) for r in rows if (db.get_task(r["id"])["plan"] or {}).get("basic")), None)
    if not bt or bt["status"] != "succeeded" or bt["plan"].get("example"):
        return None
    outs = (db.get_runs(bt["id"]) or [{}])[0].get("outputs") or {}
    # QA V26.1.8: matched by result AND phase (a stage value is not the value of the last phase), and a result the
    # full run found absent from the model ("not in this model", e.g. no safety phase) is taken over as absent instead
    # of calculating Run 0 again for 10 minutes.
    order = sorted(bt["plan"]["targets"], key=lambda t: not t.get("stage"))  # the main results win over a stage value
    by_key = {(t["result"], t.get("phase") or ""): outs.get(t["name"]) for t in order}
    # a result the full run tried to read and PLAXIS did not give: the same model gives the same answer again
    # (not a connection problem: then Run 0 is calculated again)
    transient = ("syntax of request", "did not answer", "connection", "no active project", "timed out", "refused")
    absent = {m.split(": ")[0] for m in outs.get("_missing") or [] if not m.startswith("PLAXIS Output images")
              and not any(x in m.lower() for x in transient)}
    labels = {(t["result"], t.get("phase") or ""): t["label"] for t in bt["plan"]["targets"]}
    got, missing = {}, []
    for t in targets:
        if t["result"].lower() == "file":
            got[t["name"]] = None
            continue
        k = (t["result"], t.get("phase") or "")
        v = by_key.get(k)
        if isinstance(v, (int, float)):
            got[t["name"]] = v
        elif labels.get(k) in absent:
            got[t["name"]] = None
            why = next((m.split(": ", 1)[1] for m in outs.get("_missing") or [] if m.split(": ")[0] == labels.get(k)), "")
            missing.append(f"{t['label']}: {why or 'not read'} (full PLAXIS run of the Original File)")
        else:
            return None  # a result the full run did not read (e.g. a minimum over the nodes): Run 0 is calculated
    if missing:
        got["_missing"] = missing
    return {"outputs": got, "task": bt["id"]}


def _start_study_task(p, s, variables, targets, runs, first, offset, previous):
    plan_data = {"variables": variables, "targets": targets, "goal": None, "output": report.output_options(p["setup"]),
                 "plaxis": p["setup"].get("plaxis") or "2d", "study": s, "first": first, "offset": offset,
                 "previous": previous}
    m = (p["setup"] or {}).get("plaxis_model")
    if m:  # V26.1.9: what PLAXIS adds in every run (safety phases after chosen stages, new structures, a dynamic phase)
        from . import features
        plan_data["features"] = features.solver_options(m, s)
    # V26.1.8: a Soft Soil Creep study calculates every run, Run 0 included, with gravity loading in the initial
    # phase: the full run of the Original File (K0 procedure) is then not comparable and is not taken over
    if any(not (r.get("inputs") or {}) for r in runs) and not study.needs_gravity_initial({"variables": variables}, runs):
        base = _baseline_from_basic(p, targets)
        if base:
            plan_data["baseline"] = base
    title = study.MODES[s["mode"]]["title"]
    tid = db.create_task(p["id"], s["mode"] if s["mode"] in OBJECTIVES else "optimize",
                         f"{title}: {len(runs)} PLAXIS run(s).", plan_data)
    db.add_runs(tid, runs)
    db.log("task_definition", {"summary": title, "runs": len(runs)}, p["id"], tid)
    db.update_task(tid, status="queued")
    engine.start(tid, RUN_IN_BACKGROUND)
    return task_view(tid)


def _chain_runs(t: dict) -> list[dict]:
    """Runs of the earlier tasks this one adds to (Design optimization › Add new runs), numbered as shown."""
    out = []
    for prev in t["plan"].get("previous") or []:
        pt = db.get_task(prev)
        if not pt:
            continue
        rs = db.get_runs(prev)
        for i, r in enumerate(rs):
            r["no"] = pt["plan"].get("offset", 0) + pt["plan"].get("first", 0) + i
        out += rs
    return out


@app.post("/api/tasks/{tid}/more")
def more_runs(tid: str, body: dict):
    """Design optimization: n new runs in the gaps between the runs already made; earlier runs are kept."""
    t = _task_or_404(tid)
    if t["status"] not in ("succeeded", "failed"):
        raise HTTPException(409, "Wait until the runs have finished.")
    n = max(1, min(int(body.get("n") or 3), max_runs()))
    own = db.get_runs(tid)
    for i, r in enumerate(own):
        r["no"] = t["plan"].get("offset", 0) + t["plan"].get("first", 0) + i
    prev_runs = _chain_runs(t) + own
    runs = study.more_runs(t["plan"], prev_runs, n)
    if not runs:
        raise HTTPException(400, "Nothing to add.")
    p = _project_or_404(t["project_id"])
    offset = t["plan"].get("offset", 0) + t["plan"].get("first", 0) + len(db.get_runs(tid))
    return _start_study_task(p, t["plan"]["study"], t["plan"]["variables"], t["plan"]["targets"], runs, 0, offset,
                             (t["plan"].get("previous") or []) + [tid])


@app.post("/api/tasks/{tid}/stop")
def stop_task(tid: str):
    """Stop: the run in progress finishes (PLAXIS is not interrupted in the middle of a phase), the others wait."""
    t = _task_or_404(tid)
    if t["status"] not in ("queued", "running"):
        raise HTTPException(409, "Nothing is calculating.")
    engine.ask_stop(tid)
    db.log("stop_asked", {}, t["project_id"], tid)
    return {"ok": True}


@app.post("/api/tasks/{tid}/restart")
def restart_task(tid: str, body: dict | None = None):
    """Restart: calculates the runs that were stopped or failed again (all runs with {"all": true}; every run from
    Run n on with {"from": n}, the runs before it keep their results)."""
    t = _task_or_404(tid)
    if t["status"] in ("queued", "running"):
        raise HTTPException(409, "The runs are still calculating. Stop them first.")
    again = bool((body or {}).get("all"))
    start = (body or {}).get("from")
    first = t["plan"].get("offset", 0) + t["plan"].get("first", 0)
    for i, r in enumerate(db.get_runs(tid)):
        if start is not None:
            if first + i >= int(start):
                db.update_run(r["id"], status="pending", error=None, attempts=0, outputs=None, checks=None, passed=None)
            continue
        if again or r["status"] != "succeeded":
            db.update_run(r["id"], status="pending", error=None, attempts=0, **({"outputs": None, "checks": None, "passed": None} if again else {}))
    engine._STOP.discard(tid)
    db.update_task(tid, status="queued", error=None)
    db.log("restart", {"all": again, "from": start}, t["project_id"], tid)
    engine.start(tid, RUN_IN_BACKGROUND)
    return task_view(tid)


def _study_results(tid: str, swap: int | None = None):
    """swap=1 / 0 turns the axes of the sensitivity figures (default: as saved with the study)."""
    t = _task_or_404(tid)
    if swap is not None:
        t["plan"]["study"] = {**(t["plan"].get("study") or {}), "swap": bool(swap)}
    runs = db.get_runs(tid)
    prev = _chain_runs(t)
    setup = (db.get_project(t["project_id"]) or {}).get("setup") or {}
    if units.display_of(setup) != units.plaxis_of(setup):  # results shown in the chosen units
        tg = {x["name"]: x for x in t["plan"]["targets"]}
        for r in runs + prev:
            r["outputs"] = {k: (units.to_display(v, tg[k].get("unit") or "", setup)[0] if k in tg else v)
                            for k, v in (r.get("outputs") or {}).items()}
        t["plan"]["targets"] = _targets_to_display(t["plan"]["targets"], setup)
    out = study.results(t, runs, prev)
    allruns = prev + runs
    for row, r in zip(out["rows"], allruns):  # a failed run: why, and what to do
        if r.get("status") == "failed":
            row["error"] = r.get("error") or ""
            row.update(insight.diagnose(r, t["plan"]["variables"]))
    if t["status"] not in ("queued", "running") and (t["plan"].get("study") or {}).get("mode", t["objective"]) not in ("design_change", "reporting"):
        try:  # the effect of each variable on the control parameters, and combinations for the next runs
            ip = {**t["plan"], "targets": [x for x in t["plan"]["targets"] if not x.get("stage")]}  # not the time values
            out["effects"] = insight.effects(ip, allruns)
            out["proposals"] = insight.proposals(ip, allruns)
        except Exception as e:  # noqa: BLE001 - the insight must never stop the results
            db.log("insight", {"error": str(e)}, t["project_id"], tid)
    busy = t["status"] in ("queued", "running")
    figs = exports.figures(study.chart_specs(t, out, allruns)) if not busy and any(
        r["status"] == "succeeded" for r in allruns) else []
    return t, out, allruns, figs


@app.get("/api/tasks/{tid}/study")
def study_results(tid: str, swap: int | None = None):
    t, out, allruns, figs = _study_results(tid, swap)
    m = ((db.get_project(t["project_id"]) or {}).get("setup") or {}).get("plaxis_model")
    for row, r in zip(out["rows"], allruns):  # for "Individual runs": the inputs, the results and the moved points
        row["inputs"], row["outputs"] = r.get("inputs") or {}, r.get("outputs") or {}
        row["task"], row["idx"] = r.get("task_id"), r.get("idx")
        row["has_file"] = bool(r.get("status") == "succeeded" and project_files.run_file(r.get("task_id"), r.get("idx")))
        try:  # one run the geometry view cannot draw must not stop the live results
            row["moves"] = geosel.displace(m, t["plan"]["variables"], row["inputs"]) if m else {}
        except Exception:  # noqa: BLE001
            row["moves"] = {}
    try:  # the chapter "Calculation quality and checks" and the key findings, as in the Word report
        pl = [x for r in allruns if r.get("status") == "succeeded" and r.get("task_id") is not None
              for x in project_files.plots_of(r["task_id"], r["idx"])]
        out["quality"] = exports.quality(t["plan"], out, allruns, m or {}, sum(1 for x in pl if x["key"] != "mesh"),
                                         any(x["key"] == "mesh" for x in pl), exports.vis_of(t["plan"]))
        out["findings"] = exports.key_findings(out, allruns)
    except Exception as e:  # noqa: BLE001
        db.log("quality", {"error": str(e)}, t["project_id"], tid)
    prog = engine.PROGRESS.get(tid) or {}
    pct = 100.0 if t["status"] == "succeeded" else (100.0 * (out["done"] + prog.get("pct", 0) / 100) / out["total"]
                                                    if out.get("total") else 0.0)
    cur = next((r for r in out["rows"] if r["status"] == "running"), None)
    return {**out, "status": t["status"], "figures": exports.figures_b64(figs), "error": t.get("error"),
            "pct": round(min(100.0, pct), 1), "progress": (f"Run {cur['run']}: {prog['message']}" if cur and prog.get("message") else ""),
            "targets": t["plan"]["targets"], "variables": t["plan"]["variables"]}


def _fname(t: dict, ext: str) -> str:
    title = ((db.get_project(t["project_id"])["setup"] or {}).get("info") or {}).get("title") or "PLAXIS study"
    safe = "".join(ch if (ch.isascii() and ch.isalnum()) or ch in " -_" else "_" for ch in title).strip() or "study"
    return f"{safe} - {study.MODES.get(t['plan'].get('study', {}).get('mode'), {}).get('title', 'results')}.{ext}"


@app.get("/api/tasks/{tid}/results.xlsx")
def results_xlsx(tid: str, swap: int | None = None):
    t, out, allruns, figs = _study_results(tid, swap)
    data = exports.excel(db.get_project(t["project_id"]), t, out, allruns, figs)
    return StreamingResponse(io.BytesIO(data), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{_fname(t, "xlsx")}"'})


@app.get("/api/tasks/{tid}/report.docx")
def results_docx(tid: str, swap: int | None = None):
    t, out, allruns, figs = _study_results(tid, swap)
    data = exports.word(db.get_project(t["project_id"]), t, out, allruns, figs)
    return StreamingResponse(io.BytesIO(data), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                             headers={"Content-Disposition": f'attachment; filename="{_fname(t, "docx")}"'})


# --- task summary + approve / edit / cancel ---------------------------------------------
@app.post("/api/projects/{pid}/plan")
def plan(pid: str):
    p = _project_or_404(pid)
    setup = p["setup"]
    variables = [coerce_variable(v) for v in setup.get("variables") or []]
    targets = [coerce_target(t) for t in setup.get("targets") or []]
    goal = Goal.model_validate(setup["goal"]) if setup.get("goal") else None
    seq_cfg = setup.get("sequence") if (setup.get("sequence") or {}).get("enabled") else None
    errors = validate(p["objective"], variables, targets, sequenced=bool(seq_cfg))
    waiting = [s["name"] for s in setup.get("sections") or [] if not s.get("confirmed")]
    if waiting:
        errors.append("Confirm the geometry of cross section(s) " + ", ".join(waiting) + " first (Confirm geometry "
                      "in each section window): an engineer checks every reading before it goes to PLAXIS.")
    if seq_cfg and not errors:
        runs, how, seq_err = sequence.build(variables, seq_cfg)
        errors += seq_err
        seq_cfg = sequence.default_config(variables, seq_cfg)
    else:
        runs, how = ([], "") if errors else build_runs(p["objective"], variables)
    if runs and len(runs) > max_runs():
        errors.append(f"That is {len(runs)} runs, more than the limit of {max_runs()}. "
                      "Use fewer values or larger steps.")
    if errors:
        db.log("clarifying_question", {"errors": errors}, pid)
        return {"type": "clarify", "message": "Before running: " + " ".join(errors)}
    summary = describe_plan(p["objective"], variables, targets, goal, runs, how)
    plan_data = {"variables": setup["variables"], "targets": setup["targets"], "goal": setup.get("goal"),
                 "output": report.output_options(setup), "plaxis": setup.get("plaxis") or "2d"}
    if seq_cfg:
        plan_data["sequence"] = seq_cfg
    tid = db.create_task(pid, p["objective"], summary, plan_data)
    db.add_runs(tid, runs)
    db.log("task_definition", {"summary": summary, "runs": len(runs)}, pid, tid)
    return {"type": "task_summary", "task": task_view(tid)}


def _decision(tid: str, allowed: tuple, decision: str) -> dict:
    t = _task_or_404(tid)
    if t["status"] not in allowed:
        raise HTTPException(409, f"Task is {t['status']}")
    db.log("approval", {"decision": decision}, t["project_id"], tid)
    return t


@app.post("/api/tasks/{tid}/approve")
def approve(tid: str):
    _decision(tid, ("awaiting_approval",), "approve")
    db.update_task(tid, status="queued")
    engine.start(tid, RUN_IN_BACKGROUND)
    return task_view(tid)


@app.post("/api/tasks/{tid}/cancel")
def cancel(tid: str):
    _decision(tid, ("awaiting_approval", "failed"), "cancel")
    db.update_task(tid, status="cancelled")
    return task_view(tid)


@app.post("/api/tasks/{tid}/edit")
def edit(tid: str):
    """Back to the setup page (AI assistant + tables) to change things, then Review & run again."""
    _decision(tid, ("awaiting_approval", "failed", "succeeded"), "edit")
    t = db.get_task(tid)
    if t["status"] != "succeeded":
        db.update_task(tid, status="replaced")
    return task_view(tid)


@app.post("/api/tasks/{tid}/retry")
def retry(tid: str):
    """Re-runs only the runs that failed."""
    _decision(tid, ("failed",), "retry")
    db.update_task(tid, status="queued")
    engine.start(tid, RUN_IN_BACKGROUND)
    return task_view(tid)


# --- results ---------------------------------------------------------------------------
@app.get("/api/tasks/{tid}")
def task_view(tid: str):
    t = _task_or_404(tid)
    runs = db.get_runs(tid)
    for r in runs:
        r["inputs_display"] = {k: show(tuple(v) if isinstance(v, list) else v) for k, v in (r["inputs"] or {}).items()}
        r["has_file"] = any(isinstance(v, str) and Path(v).exists() for v in (r["outputs"] or {}).values())
        r["run_no"] = sequence.run_no(r["idx"], t["plan"] or {})
        r["title"] = f"Run {r['run_no']}: {r['label']}"
    done = sum(r["status"] in ("succeeded", "failed") for r in runs)
    return {**t, "runs": runs, "progress": {"done": done, "total": len(runs)}}


@app.get("/api/tasks/{tid}/results.csv")
def results_csv(tid: str):
    t = _task_or_404(tid)
    runs = task_view(tid)["runs"]
    vnames = [v["name"] for v in t["plan"]["variables"]]
    tnames = [x["name"] for x in t["plan"]["targets"]]
    buf = io.StringIO()
    w = csv.writer(buf)
    info = (db.get_project(t["project_id"])["setup"] or {}).get("info") or {}
    w.writerow(["Project", info.get("title", "")])
    w.writerow(["User", info.get("user", "")])
    w.writerow(["Date", info.get("date", "")])
    w.writerow(["Objective", OBJECTIVES[t["objective"]]["title"]])
    w.writerow([])
    w.writerow(["run", "label", *vnames, *tnames, "meets criteria", "status", "attempts", "error"])
    for r in runs:
        w.writerow([r["run_no"], r["label"], *[r["inputs_display"].get(n, "existing") for n in vnames],
                    *[(r["outputs"] or {}).get(n, "") for n in tnames],
                    {1: "yes", 0: "no"}.get(r["passed"], ""), r["status"], r["attempts"], r["error"] or ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="results_{tid}.csv"'})


@app.get("/api/tasks/{tid}/compiled")
def compiled_results(tid: str):
    """Summary of every run and figures of each output against each changed parameter."""
    t = _task_or_404(tid)
    runs = task_view(tid)["runs"]
    for r in runs:
        r["inputs"] = {k: tuple(v) if isinstance(v, list) else v for k, v in (r["inputs"] or {}).items()}
    return compiled.build(t, runs)


@app.get("/api/tasks/{tid}/report.html")
def task_report(tid: str):
    t = _task_or_404(tid)
    return HTMLResponse(report.build(db.get_project(t["project_id"]), t, task_view(tid)["runs"]))


@app.get("/api/tasks/{tid}/files.zip")
def run_files(tid: str):
    _task_or_404(tid)
    root = runs_dir() / tid
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if root.exists():
            for f in root.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(root))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="runs_{tid}.zip"'})


# --- projects on disk: list, Save / Save As, Open, the PLAXIS file of each run ----------------------------
@app.get("/api/projects")
def list_projects():
    return {"projects": project_files.all_projects(), "folder": str(project_files.projects_dir())}


@app.delete("/api/tasks/{tid}")
def delete_study_task(tid: str):
    """Deletes one study of the project (not while it runs)."""
    t = _task_or_404(tid)
    if t["status"] in ("queued", "running"):
        raise HTTPException(409, "This study is still running: stop it first.")
    project_files.delete_task(tid)
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    _project_or_404(pid)
    project_files.delete_project(pid)
    return {"ok": True}


def _results_xlsx_bytes(tid: str) -> bytes:
    t, out, allruns, figs = _study_results(tid)
    return exports.excel(db.get_project(t["project_id"]), t, out, allruns, figs)


@app.post("/api/projects/{pid}/save")
def save_project(pid: str, body: dict):
    """Save (same file), or Save As: a new name and/or folder (as_copy: a copy with its own name; this project stays).
    plaxis: all | some | none: the PLAXIS files of every run, of the runs listed in `runs` (e.g. "3, 5-7"), or none."""
    _project_or_404(pid)
    kw = {"plaxis": body.get("plaxis") or "all", "runs": body.get("runs") or "", "task": body.get("task") or "",
          "reports": body.get("reports", True) is not False, "results": _results_xlsx_bytes}
    if kw["plaxis"] not in ("all", "some", "none"):
        raise HTTPException(400, "plaxis must be all, some or none")
    try:
        if body.get("as_copy"):
            if not (body.get("name") or "").strip():
                raise HTTPException(400, "Give the new project a name.")
            return project_files.save_as_copy(pid, body["name"].strip(), body.get("folder") or "", **kw)
        return project_files.save(pid, body.get("folder") or "", body.get("name") or "", **kw)
    except project_files.ProjectFileError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(400, f"Could not save: {e}")


@app.post("/api/projects/open")
def open_project_file(body: dict):
    try:
        pid = project_files.open_file(body.get("path") or "")
    except project_files.ProjectFileError as e:
        raise HTTPException(400, str(e))
    return project_view(pid)


@app.get("/api/projects/{pid}/studies")
def project_studies(pid: str):
    p = _project_or_404(pid)
    return {"studies": project_files.studies(pid), "saved": (p["setup"] or {}).get("saved") or {},
            "default_folder": str(project_files.projects_dir() / project_files.safe_name(
                ((p["setup"] or {}).get("info") or {}).get("title") or ""))}


@app.post("/api/tasks/{tid}/runs/{idx}/open")
def open_run_file(tid: str, idx: int, body: dict | None = None):
    """Opens the PLAXIS file of one run on this PC (in PLAXIS), or shows it in its folder."""
    _task_or_404(tid)
    f = project_files.run_file(tid, idx)
    if not f:
        raise HTTPException(404, "This run has no PLAXIS file (not calculated in PLAXIS, or its runs folder was emptied).")
    try:
        done = project_files.open_on_pc(f, select=(body or {}).get("action") == "folder")
    except OSError as e:
        raise HTTPException(400, f"Could not open {f}: {e}")
    return {"path": str(f), "opened": done}


@app.get("/api/projects/{pid}/geometry.png")
def geometry_image(pid: str):
    p = _project_or_404(pid)
    png = exports.geometry_png((p["setup"] or {}).get("plaxis_model") or {})
    if not png:
        raise HTTPException(404, "The model is not read yet.")
    return StreamingResponse(io.BytesIO(png), media_type="image/png")


@app.get("/api/tasks/{tid}/plots")
def task_plots(tid: str):
    """The PLAXIS Output images of every calculated run of the study (Run 0 included)."""
    _task_or_404(tid)
    out = []
    for r in db.get_runs(tid):
        for pl in project_files.plots_of(tid, r["idx"]):
            out.append({**pl, "idx": r["idx"], "base": not (r.get("inputs") or {})})
    return {"plots": out}


@app.get("/api/tasks/{tid}/runs/{idx}/plots")
def run_plots(tid: str, idx: int):
    _task_or_404(tid)
    return {"plots": project_files.plots_of(tid, idx)}


class MorePlots(BaseModel):
    keys: list[str] = []


@app.post("/api/tasks/{tid}/runs/{idx}/plots")
def run_plots_more(tid: str, idx: int, body: MorePlots):
    """Export More Output Results: more PLAXIS Output images of one calculated run (simulated without PLAXIS)."""
    t = _task_or_404(tid)
    run = next((r for r in db.get_runs(tid) if r["idx"] == idx), None)
    if not run or run.get("status") != "succeeded":
        raise HTTPException(404, "That run has no calculated result.")
    keys = [k for k in body.keys if k in project_files.PLOT_LABELS and k != "geometry"]
    if not keys:
        raise HTTPException(400, "Choose at least one output item.")
    d = project_files.run_folder(tid, idx)
    d.mkdir(parents=True, exist_ok=True)
    missing = []
    if (t["plan"].get("backend") or backend_name()) == "plaxis":
        f = project_files.run_file(tid, idx)
        if not f:
            raise HTTPException(409, "The PLAXIS file of this run was not saved, so PLAXIS Output cannot open it again.")
        try:
            got = solvers.PlaxisSolver().export_more(f, keys, d)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"PLAXIS Output could not export the images: {e}") from e
    else:
        s = (int(hashlib.sha1(f"{tid}{idx}".encode()).hexdigest()[:6], 16) % 100) / 100
        got = solvers._sim_plots([k for k in keys if k in solvers.SIM_KEYS], s, d)
    made = {Path(x).stem.removesuffix("_sim") for x in got}
    missing = [project_files.PLOT_LABELS[k] for k in keys if k not in made]
    db.log("export_plots", {"run": idx, "keys": keys, "missing": missing}, t["project_id"], tid)
    return {"plots": project_files.plots_of(tid, idx), "missing": missing}


@app.get("/api/tasks/{tid}/plots.zip")
def plots_zip(tid: str):
    """Every PLAXIS Output image of the study, to share (one folder per run)."""
    import zipfile
    _task_or_404(tid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in db.get_runs(tid):
            for pl in project_files.plots_of(tid, r["idx"]):
                f = project_files.plot_file(tid, r["idx"], pl["name"])
                if f:
                    z.write(f, f"Run {r['idx']:03d}/{pl['label']}.png")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="output-images-{tid[:8]}.zip"'})


@app.get("/api/tasks/{tid}/runs/{idx}/plot/{name}")
def run_plot(tid: str, idx: int, name: str):
    _task_or_404(tid)
    f = project_files.plot_file(tid, idx, name)
    if not f:
        raise HTTPException(404, "No such image for this run.")
    return FileResponse(f, media_type="image/png")


@app.get("/api/tasks/{tid}/runs/{idx}/file.zip")
def download_run_file(tid: str, idx: int):
    _task_or_404(tid)
    f = project_files.run_file(tid, idx)
    if not f:
        raise HTTPException(404, "This run has no PLAXIS file.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for x in f.parent.rglob("*"):
            if x.is_file():
                z.write(x, x.relative_to(f.parent))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{f.stem}.zip"'})


@app.put("/api/projects/{pid}/units")
def put_units(pid: str, body: dict):
    p = _project_or_404(pid)
    u = units.clean(body)
    db.update_project(pid, setup={**p["setup"], "units": u})
    setup = {**p["setup"], "units": u}
    return {"units": u, "plaxis": units.plaxis_of(setup), "display": units.display_of(setup)}


@app.get("/api/units")
def units_choices():
    return {"choices": units.CHOICES, "default": units.DEFAULT, "custom_default": units.CUSTOM_DEFAULT}


@app.get("/api/log")
def task_log(project_id: str | None = None, limit: int = 300):
    return db.get_log(project_id, limit)


@app.get("/")
def index():
    # never cached: a new version of the app shows at once (no old buttons after an update)
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/view3d")
def view3d():
    return FileResponse(STATIC / "view3d.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
