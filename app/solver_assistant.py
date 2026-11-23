"""Solver assistant: an offline chat (no API key) that knows PLAXIS and checks this PC.

It answers questions about the PLAXIS connection, the licence, reading the model, meshing, calculations that fail,
results and the run plan. For connection questions it looks at this PC first (plxscripting, PLAXIS Input, what
is open, which ports answer) and says what it found. Replies can carry buttons (actions) the page runs:
connect, restart, stop_plaxis, diagnose, settings, read_model, upload_folder.
"""
import platform
import re

from . import plaxis_launcher as L
from . import settings


def diagnose(full: bool = True) -> dict:
    cfg = settings.load()
    d = {"os": platform.system() + " " + platform.release(), "solver": cfg.get("solver", "simulated"),
         "host": cfg.get("host") or "localhost", "port": int(cfg.get("port") or 10000),
         "password_set": bool(cfg.get("password")), "launched_pid": cfg.get("launched_pid") or 0}
    try:
        d["plxscripting"] = settings.ensure_plxscripting_importable(cfg)
    except ImportError as e:
        d["plxscripting"], d["plxscripting_error"] = None, str(e)
    d["port_answers"] = L._listening(d["host"], d["port"])
    d["open"] = L.running_inputs()
    for p in d["open"]:
        p["ports"] = L.listening_ports([p["pid"]])
    if full:
        d["exe"] = {v: L.find_input_exes(v) for v in ("3d", "2d")}
        d["input_exe"] = cfg.get("input_exe") or ""
        f = L.saved_server_settings()
        d["plaxis_saved"] = {"ports": f["ports"], "passwords": len(f["passwords"])}
    try:
        import pywinauto  # noqa: F401
        d["pywinauto"] = True
    except ImportError:
        d["pywinauto"] = False
    d["last_error"] = L.LAST["error"]
    d["steps"] = L.LAST["steps"][-8:]
    return d


def diagnosis_text(d: dict) -> str:
    ok, bad = "✓", "✗"
    lines = [f"{ok if d.get('plxscripting') else bad} PLAXIS scripting library (plxscripting): "
             + (d["plxscripting"] if d.get("plxscripting") else d.get("plxscripting_error", "not found"))]
    for v, exes in (d.get("exe") or {}).items():
        lines.append(f"{ok if exes else '·'} PLAXIS {v.upper()} Input installed: "
                     + (", ".join(exes) if exes else "not found"))
    if d.get("input_exe"):
        lines.append(f"{ok} PLAXIS you worked with last (started first): {d['input_exe']}")
    if d["open"]:
        for p in d["open"]:
            lines.append(f"{ok} Open now: {p['name']} (process {p['pid']})"
                         + (f", window '{p['title']}'" if p.get("title") and p["title"] != "N/A" else "")
                         + (f", listening on port {', '.join(map(str, p['ports']))}" if p["ports"] else
                            ", scripting server not listening"))
    else:
        lines.append("· No PLAXIS Input is open (the app starts it when it connects)")
    lines.append(f"{ok if d['port_answers'] else '·'} Saved port {d['host']}:{d['port']} "
                 f"{'answers' if d['port_answers'] else 'does not answer'}; password {'saved' if d['password_set'] else 'not set yet'}")
    if d.get("plaxis_saved") and (d["plaxis_saved"]["ports"] or d["plaxis_saved"]["passwords"]):
        lines.append(f"{ok} Found in PLAXIS' own settings: ports {d['plaxis_saved']['ports'] or '-'}, "
                     f"{d['plaxis_saved']['passwords']} password(s) the app will try")
    lines.append(f"{ok if d['pywinauto'] else '·'} Switching the server on in PLAXIS' menu for you: "
                 + ("available" if d["pywinauto"] else "needs pywinauto (python -m pip install pywinauto)"))
    lines.append(f"{ok if d['solver'] == 'plaxis' else '·'} Calculations: "
                 + ("PLAXIS" if d["solver"] == "plaxis" else "simulated until PLAXIS is connected"))
    if d.get("last_error"):
        lines.append(f"Last connection problem: {d['last_error']}")
    if d.get("steps"):
        lines.append("Last steps: " + " | ".join(d["steps"][-4:]))
    return "\n".join(lines)


def _advice(d: dict) -> tuple[str, list[str]]:
    """What to do next, from the diagnosis."""
    if not d.get("plxscripting"):
        return ("The app can't find PLAXIS' Python library on this PC, so it can't talk to PLAXIS. Run the app on the "
                "PC where PLAXIS is installed, or put the folder that contains 'plxscripting' in Settings "
                "(find_plaxis.bat finds it).", ["settings", "diagnose"])
    if not d["open"] and not any((d.get("exe") or {}).values()):
        return ("PLAXIS Input was not found in the usual folders. Open PLAXIS yourself and press Connect: the app "
                "finds the open PLAXIS and switches its scripting server on.", ["connect", "diagnose"])
    if d["open"] and not any(p["ports"] for p in d["open"]):
        t = d["open"][0].get("title") or ""
        if re.search(r"(?i)licen[cs]e|activation|sign.?in|bentley|seequent id", t):
            return (f"PLAXIS is open but shows '{t}'. It is waiting for the licence or sign-in. Answer that window in "
                    "PLAXIS, then press Connect.", ["connect", "diagnose"])
        if d.get("launched_pid") and any(p["pid"] == d["launched_pid"] for p in d["open"]):
            return ("PLAXIS started by the app is open but its scripting server does not answer yet"
                    + (f" (PLAXIS shows '{t}')" if t else "") + ". If PLAXIS shows a message or a start window, "
                    "close it and press Connect. If nothing helps, press Restart PLAXIS.",
                    ["connect", "restart", "diagnose"])
        return ("PLAXIS is open but its scripting server is off. Press Connect: in PLAXIS' window the app closes "
                "the disclaimer, opens Expert › Configure remote scripting server, reads the port and password, "
                "presses Start server and connects"
                + ("" if d["pywinauto"] else " (this needs pywinauto: run  python -m pip install pywinauto  once, "
                   "run.bat does it)") + ".", ["connect", "diagnose"])
    if d["solver"] == "plaxis" and d["port_answers"]:
        return "PLAXIS is connected and calculations use PLAXIS.", ["read_model"]
    return "Press Connect: the app finds or starts PLAXIS, switches its scripting server on and connects.", \
        ["connect", "diagnose"]


# --- knowledge -------------------------------------------------------------------------------------------
TOPICS = [
    (r"connect|server|port|password|not connected|scripting|plxscripting|start plaxis|launch|did not answer|"
     r"refresh|retry|struggl|stuck|hang|freez",
     None),  # answered from the diagnosis
    (r"licen[cs]e|activation|seat|sign.?in",
     "PLAXIS needs a free licence seat before its scripting server answers. When the app starts PLAXIS, a licence or "
     "sign-in window can stop it there: answer it in PLAXIS, then press Connect. If another PC holds the seat, close "
     "PLAXIS there first. The app never closes a PLAXIS you opened yourself; Restart PLAXIS only closes the one it started.",
     ["connect", "diagnose"]),
    (r"p3dat|p2dxdat|data folder|missing (file|folder)|upload",
     "A PLAXIS project is two parts: Zayed_SS2.p3d and the Zayed_SS2.p3dat folder next to it (2D: .p2dx and "
     ".p2dxdat). Add the folder with '+ Data folder' on page 1 or page 2, or zip both together.",
     ["upload_folder"]),
    (r"mesh",
     "Mesh levels used by the run plan: Very coarse, Coarse, Medium, Fine, Very fine (PLAXIS element distribution; "
     "relative element size 0.12 to 0.03). If meshing fails, refine around small structures or check overlapping "
     "geometry in Structures mode (Intersect). A finer mesh is slower; compare at least two levels to see if results "
     "still change.", []),
    (r"collapse|not reached|ultimate state|did not converge|converge|numerical|load advancement|failed calculation|"
     r"calculation fail|phase fail",
     "PLAXIS calculation problems and what usually helps:\n"
     "• 'Soil body seems to collapse' or 'Prescribed ultimate state not fully reached': the phase is near failure. "
     "Check the strengths (phi, c'ref, Rinter), the excavation step and the load; for a safety phase it is normal.\n"
     "• 'Load advancement procedure fails' or no convergence: raise Max steps, lower the tolerated error only if "
     "needed, or make the phase smaller (excavate in two steps).\n"
     "• 'Numerical error' or 'singular matrix': a structure or soil cluster is not connected or not supported; check "
     "the staged construction (all needed parts active) and the boundary conditions.\n"
     "Runs that fail are shown in Results with PLAXIS' message, and the other runs go on.", []),
    (r"safety|fos|msf|factor",
     "Factor of safety comes from a Safety phase (phi/c reduction). PLAXIS reports ΣMsf; the app reads it as FoS for "
     "every run. Add it in Output › Outputs to extract.", []),
    (r"result|output|moment|displacement|anchor force|extract",
     "Choose what each run extracts on page 6 (Output): plate moments and forces, anchor forces, soil displacements "
     "(with 'max' or a location), FoS. Compiled results (page 8) plots each one against every changed criterion.", []),
    (r"logical|customi[sz]ed|sequenc|runs?|scenario|combination",
     "Run plan (page 4): Logical sequencing moves each parameter from its start to its end value in equal parts "
     "(5 runs = 20 % more each run). Customized sequencing lets you type every run's values. 'Every combination' "
     "multiplies criteria (Mesh 4 × Materials 5 = 20 runs); 'together' steps them at once; 'one at a time' varies one "
     "criterion while the others keep the existing model's values.", []),
    (r"time|slow|how long|speed",
     "Each run is a full PLAXIS calculation, so time per run ≈ the time your model takes in PLAXIS. Coarser mesh and "
     "fewer phases are faster. The app saves a working copy, so your own file is never changed.", []),
    (r"simulat",
     "Until PLAXIS is connected, calculations are simulated so you can try the workflow. Once the app connects "
     "(Read my PLAXIS project or Connect), it switches to real PLAXIS calculations by itself.", ["connect"]),
]

HELLO = ("I'm the solver assistant. I can check and fix the PLAXIS connection on this PC, and explain PLAXIS "
         "messages, meshing, calculations, results and the run plan. Ask me anything, or press Check my PLAXIS.")


def answer(message: str, project: dict | None = None) -> dict:
    q = (message or "").strip().lower()
    if not q or re.fullmatch(r"(hi|hello|hey|help|\?)+[!. ]*", q):
        return {"message": HELLO, "actions": ["diagnose", "connect"]}
    if re.search(r"check|diagnos|status|what.*wrong|why", q) and not re.search(r"mesh|collapse|converge|safety", q):
        d = diagnose()
        tip, acts = _advice(d)
        return {"message": tip + "\n\nWhat I checked on this PC:\n" + diagnosis_text(d), "actions": acts,
                "diagnosis": d}
    for pat, text, *acts in TOPICS:
        if re.search(pat, q):
            if text is None:
                d = diagnose(full=False)
                tip, a = _advice(d)
                return {"message": tip + "\n\n" + diagnosis_text(d), "actions": a, "diagnosis": d}
            return {"message": text, "actions": acts[0] if acts else []}
    if project:
        m = (project.get("setup") or {}).get("plaxis_model")
        if m:
            c = m.get("counts", {})
            return {"message": f"I don't have a ready answer for that. Your model: {c.get('points', 0)} points, "
                               f"{c.get('structures', 0)} structures, {c.get('materials', 0)} materials, "
                               f"{c.get('phases', 0)} phases. Try asking about connection, licence, mesh, a "
                               "calculation message, results or the run plan.", "actions": ["diagnose"]}
    return {"message": "I don't have a ready answer for that. Ask me about the PLAXIS connection, the licence, mesh, a "
                       "calculation message, results or the run plan, or press Check my PLAXIS.",
            "actions": ["diagnose"]}

