"""Automation engine: perform each PLAXIS run -> succeeded? -> retries left (max 3)? -> results, or error + email."""
import threading
import time

from . import db
from .config import backend_name, max_retries, retry_delay, runs_dir
from .notify import error_recipients, send_mail
from .planner import check, pick_best, show
from .sequence import run_no
from .schema import Goal, coerce_target, coerce_variable
from .solvers import get_solver

_run_lock = threading.Lock()  # one PLAXIS session at a time (the runs and reading the model)
PROGRESS: dict = {}  # task id -> {"message", "since"}: what the run in progress is doing
BUSY: dict = {}  # what holds PLAXIS now: {"what", "task", "since"}


def _plan_objects(plan: dict):
    variables = [coerce_variable(v) for v in plan["variables"]]
    targets = [coerce_target(t) for t in plan["targets"]]
    goal = Goal.model_validate(plan["goal"]) if plan.get("goal") else None
    return variables, targets, goal


def _decode(inputs: dict) -> dict:
    """Runs store tuples (label, number) as lists in JSON."""
    return {k: tuple(v) if isinstance(v, list) else v for k, v in (inputs or {}).items()}


_STOP: set[str] = set()


def ask_stop(task_id: str) -> None:
    _STOP.add(task_id)


def _stop_asked(task_id: str) -> bool:
    return task_id in _STOP


def run_task(task_id: str) -> None:
    PROGRESS[task_id] = {"message": "Waiting for PLAXIS to be free", "since": time.time()}
    with _run_lock:
        BUSY.update(what="a PLAXIS run", task=task_id, since=time.time())
        PROGRESS[task_id] = {"message": "Starting the run", "since": time.time(), "pct": 2}
        try:
            _run_task(task_id)
        finally:
            BUSY.clear()
            PROGRESS.pop(task_id, None)


def _run_task(task_id: str) -> None:
    task = db.get_task(task_id)
    project = db.get_project(task["project_id"])
    pid = project["id"]
    plan = task["plan"]
    variables, targets, goal = _plan_objects(plan)
    db.update_task(task_id, status="running", error=None)

    try:
        solver = get_solver(plan.get("backend") or backend_name())  # the basic model run is always in PLAXIS
    except Exception as e:  # noqa: BLE001
        return _fail(task, pid, f"Could not start the solver: {e}", [])
    solver.want_model = bool(plan.get("basic"))
    from .study import needs_gravity_initial
    solver.gravity_initial = needs_gravity_initial(plan, db.get_runs(task_id))  # V26.1.8: Soft Soil Creep → gravity loading in every run
    # V26.1.9: what PLAXIS adds in every run of the study: safety phases, new structures, a dynamic phase
    for k, v in (plan.get("features") or {}).items():  # set when the study's runs were made (main._start_study_task)
        setattr(solver, k, v)
    tm = (plan.get("study") or {}).get("time") or {}
    solver.longterm = float(tm.get("longterm") or 0) if tm.get("on") or (plan.get("study") or {}).get("series") else 0.0  # V26.1.8  # the full run of the uploaded model also reads its geometry
    # PLAXIS Output images of each run, as chosen in "What do you prefer to visualize?" (the full run: the main ones)
    rep = ((plan.get("study") or {}).get("report") or {})
    solver.want_plots = (["utot", "ux", "uy", "mesh", "plate_m"] if plan.get("basic") else
                         [k for k in (rep.get("plots") or []) if k] if "output" in (rep.get("vis") or []) or "mesh" in (rep.get("vis") or []) else [])
    if plan.get("basic") is None and "mesh" in (rep.get("vis") or []) and "mesh" not in solver.want_plots:
        solver.want_plots.append("mesh")
    solver.progress = lambda msg, pct=None: PROGRESS.__setitem__(task_id, {"message": msg, "since": time.time(),
                                                                           "pct": pct if pct is not None else PROGRESS.get(task_id, {}).get("pct", 0)})

    # the basic model run is calculated once: no automatic retry (a failure is shown, Run PLAXIS again to repeat it)
    retries = 0 if plan.get("basic") else max_retries()
    for run in db.get_runs(task_id):
        if run["status"] == "succeeded":
            continue
        if _stop_asked(task_id):
            break
        inputs = _decode(run["inputs"])
        if not inputs and (plan.get("baseline") or {}).get("outputs") is not None:
            # Run 0 = the Original File: its results come from the full PLAXIS run of the first page, not run again
            outputs = dict(plan["baseline"]["outputs"])
            checks, passed = check(targets, outputs)
            db.update_run(run["id"], status="succeeded", outputs=outputs, checks=checks, passed=passed, attempts=0)
            db.log("attempt", {"run": run["label"], "from": "full PLAXIS run of the Original File", "outputs": outputs}, pid, task_id)
            continue
        run_dir = runs_dir() / task_id / f"run_{run['idx'] + 1:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        attempts = run["attempts"] or 0
        db.update_run(run["id"], status="running", error=None)
        PROGRESS[task_id] = {"message": "Starting the run", "since": time.time(), "pct": 0}
        for i in range(retries + 1):
            attempts += 1
            try:
                outputs = solver.run(project["files"], inputs, variables, targets, run_dir)
                checks, passed = check(targets, outputs)
                db.update_run(run["id"], status="succeeded", outputs=outputs, checks=checks, passed=passed,
                              attempts=attempts)
                db.log("attempt", {"run": run["label"], "attempt": attempts, "ok": True,
                                   "outputs": outputs, "passed": passed}, pid, task_id)
                break
            except Exception as e:  # noqa: BLE001 - any failure counts as a failed attempt
                err = f"{type(e).__name__}: {e}"
                db.log("attempt", {"run": run["label"], "attempt": attempts, "ok": False, "error": err},
                       pid, task_id)
                from .solvers import deterministic
                if i < retries and not _stop_asked(task_id) and not deterministic(err):
                    delay = retry_delay() * (2 ** i)
                    db.log("retry_scheduled", {"run": run["label"], "in_seconds": delay,
                                               "retries_left": retries - i}, pid, task_id)
                    db.update_run(run["id"], attempts=attempts, error=err)
                    time.sleep(delay)
                else:
                    kept = getattr(solver, "last_applied", None)  # QA V26.1.9 D8: what the failed run set
                    db.update_run(run["id"], status="failed", attempts=attempts, error=err,
                                  **({"outputs": kept} if kept else {}))

    if _stop_asked(task_id):  # Stop pressed: the run in progress finished, the others wait for Restart
        for r in db.get_runs(task_id):
            if r["status"] in ("queued", "pending", "running"):
                db.update_run(r["id"], status="stopped")
        db.update_task(task_id, status="stopped", error="Stopped by the engineer. Press Restart to calculate the remaining runs.")
        db.log("stopped", {}, pid, task_id)
        return
    runs = db.get_runs(task_id)
    for r in runs:
        r["inputs"] = _decode(r["inputs"])
    failed = [r for r in runs if r["status"] == "failed"]
    result = _summarize(task["objective"], runs, variables, targets, goal, plan)
    db.update_task(task_id, result=result)
    db.log("result", result, pid, task_id)
    db.add_message(pid, "assistant", result["text"])
    if failed:
        msg = f"{len(failed)} of {len(runs)} run(s) failed after {retries + 1} attempt(s) each."
        return _fail(task, pid, msg, failed)
    if plan.get("basic") and not plan.get("example"):
        _remember_basic(pid, project, plan, solver, runs)
    db.update_task(task_id, status="succeeded")


def _remember_basic(pid: str, project: dict, plan: dict, solver, runs: list[dict]) -> None:
    """The full run of the uploaded model: its model is kept with the project (no second opening in PLAXIS), and the
    run is remembered by the PLAXIS file's content (the same file is not calculated again)."""
    from . import basic_cache
    model = getattr(solver, "model_read", None)
    try:
        if model:
            p = db.get_project(pid)
            db.update_project(pid, setup={**(p["setup"] or {}), "plaxis_model": model,
                                          "plaxis": model.get("version") or (p["setup"] or {}).get("plaxis")})
            db.log("plaxis_model", {"source": "basic run", "version": model.get("version")}, pid)
        key = basic_cache.model_hash(project["files"])
        name = next((f.get("name") for f in project["files"] or [] if str(f.get("name", "")).lower().endswith(basic_cache.PLAXIS_EXT)), "")
        outs = {k: v for k, v in (runs[0].get("outputs") or {}).items() if not k.startswith("_") or k == "_missing"} if runs else {}
        basic_cache.put(key, name, plan["targets"], outs, model)
    except Exception as e:  # noqa: BLE001 - remembering is a convenience, never a failure of the run
        db.log("basic_cache", {"error": str(e)}, pid)


def _fail(task: dict, pid: str, message: str, failed_runs: list[dict]) -> None:
    db.update_task(task["id"], status="failed", error=message)
    lines = [f"- {r['label']}: {r['error']}" for r in failed_runs[:20]]
    body = (f"The PLAXIS automation task failed.\n\nWhat it was doing: {task['summary']}\n\n{message}\n"
            + ("\n".join(lines) + "\n" if lines else "") + f"\nTask id: {task['id']}\n")
    info = (db.get_project(pid)["setup"] or {}).get("info") or {}
    title = info.get("title") or "PLAXIS automation"
    body = f"Project: {title}\nUser: {info.get('user', '')}\nDate: {info.get('date', '')}\n\n" + body
    recipients = error_recipients()
    sent = send_mail(recipients, f"{title}: automation task failed", body)
    db.log("error_alert", {"error": message, "emailed": recipients, "delivered": sent}, pid, task["id"])
    db.add_message(pid, "assistant", f"The task failed: {message}")


def _summarize(objective, runs, variables, targets, goal, task_plan=None) -> dict:
    task_plan = task_plan or {}
    ok = [r for r in runs if r["status"] == "succeeded"]
    passing = [r for r in ok if r["passed"]]
    checked = any(t.operator != "report" for t in targets)
    best = pick_best(runs, goal, targets) if objective in ("optimize", "new_design") else None
    parts = [f"Finished {len(ok)} of {len(runs)} run(s)."]
    if checked:
        parts.append(f"{len(passing)} met all target criteria.")
    if objective in ("optimize", "new_design"):
        if best:
            ins = ", ".join(f"{k} = {show(v)}" for k, v in best["inputs"].items()) or "the existing design"
            if task_plan.get("sequence"):
                ins = f"Run {run_no(best['idx'], task_plan)}: {best['label']}"
            outs = ", ".join(f"{k} = {v}" for k, v in (best["outputs"] or {}).items() if not k.startswith("_"))
            parts.append(f"Best design: {ins} ({outs}).")
        else:
            parts.append("No design met all criteria. Try widening the ranges or relaxing a target.")
    if objective == "design_change" and len(ok) == 2:
        a, b = ok[0]["outputs"], ok[1]["outputs"]
        diffs = [f"{k}: {a.get(k)} → {b.get(k)}" for k in b if isinstance(b.get(k), (int, float))]
        parts.append("Existing → changed: " + "; ".join(diffs) + ".")
        parts.append("The changed design " + ("meets" if ok[1]["passed"] else "does NOT meet") + " the criteria.")
    return {"text": " ".join(parts), "best_run": best["idx"] if best else None,
            "passing": len(passing), "succeeded": len(ok), "total": len(runs)}


def start(task_id: str, background: bool = True) -> None:
    if background:
        threading.Thread(target=run_task, args=(task_id,), daemon=True).start()
    else:
        run_task(task_id)
