"""Connects to PLAXIS Input by itself, for any user of the app on their own PC: no server set-up by hand.

In this order:
  1. The saved port: when PLAXIS answers there, connect.
  2. A PLAXIS Input that is already open (found with tasklist and by its window title): connect on its port when
     its scripting server runs; otherwise do in its window what the engineer does by hand (app/plaxis_gui.py):
     close the "Plaxis disclaimer", Expert › Configure remote scripting server, read the port and the password,
     Start server, Close, then connect with them.
  3. Nothing open: start PLAXIS 2D / 3D Input (the version the user worked with last, else the newest install)
     with its server on (Reference Manual F.2.2: Plaxis3DInput.exe --AppServerPort=N --AppServerPassword=X),
     close the disclaimer, and if the server does not answer, switch it on through the menu as in 2. When a
     version closes by itself (e.g. no licence for it), the next installed version is tried.

The port, password and PLAXIS program that worked are saved in Settings, so the next connection is instant.
"""
import csv
import inspect
import io
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time

from . import settings

EXE_NAMES = {
    "3d": ("plaxis3dinput.exe",),
    "2d": ("plaxis2dxinput.exe", "plaxis2dinput.exe"),
}
ALL_INPUT_EXES = tuple(n for v in EXE_NAMES.values() for n in v)
GHOST_WAIT = 4  # × 5 s: time given to an open PLAXIS process to show its window before it is counted as left over
START_TIMEOUT = 600  # seconds: a cold PLAXIS start with a licence check can take a while; the user can stop sooner
GUI_AFTER = 25       # seconds without an answer before the app switches the server on through PLAXIS' menu
GUI_FIRST = 8        # started without options: first try of the menu (again every 20 s while PLAXIS opens)
LAST = {"error": "", "steps": []}  # for the solver assistant
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConnectError(RuntimeError):
    pass


class Stopped(ConnectError):
    pass


def _win() -> bool:
    return os.name == "nt"


def _run(args: list[str], timeout: float = 30) -> str:
    try:
        kw = {"creationflags": 0x08000000} if _win() else {}  # CREATE_NO_WINDOW
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, errors="replace", **kw).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# --- what is on this PC -----------------------------------------------------------------------------------
def find_input_exes(version: str) -> list[str]:
    """PLAXIS 2D / 3D Input programs on this PC, newest install first."""
    names = EXE_NAMES.get(version, EXE_NAMES["3d"])
    found = []
    roots = [r for r in settings.SEARCH_ROOTS + [r"C:\Program Files", r"D:\Program Files"] if r and os.path.isdir(r)]
    for root in dict.fromkeys(roots):
        base = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, files in os.walk(root):
            for f in files:
                if f.lower() in names:
                    found.append(os.path.join(dirpath, f))
            if dirpath.count(os.sep) - base >= 4:
                dirnames[:] = []
    return sorted(dict.fromkeys(found), key=lambda p: (_ver_key(p), os.path.getmtime(p)), reverse=True)


def _ver_key(path: str):
    m = re.search(r"(20\d\d)(?:\.(\d+))?|V(\d+)", path)
    if not m:
        return (0, 0)
    return (int(m.group(1) or 0) or int(m.group(3) or 0), int(m.group(2) or 0))


def find_input_exe(version: str) -> str | None:
    exes = find_input_exes(version)
    return exes[0] if exes else None


def _candidates(version: str) -> list[str]:
    """Programs to start: the one that worked last (or that the user had open), then the newest installs."""
    names = EXE_NAMES.get(version, EXE_NAMES["3d"])
    saved = settings.load().get("input_exe") or ""
    first = [saved] if saved and os.path.basename(saved).lower() in names and os.path.isfile(saved) else []
    one = find_input_exe(version)
    exes = list(dict.fromkeys(first + ([one] if one else []) + find_input_exes(version)))
    bad = set(settings.load().get("bad_exes") or [])  # versions that crashed or closed here: try them last
    return [e for e in exes if e not in bad] + [e for e in exes if e in bad]


def _mark_bad(exe: str) -> None:
    bad = list(dict.fromkeys((settings.load().get("bad_exes") or []) + [exe]))
    s = {"bad_exes": bad}
    if settings.load().get("input_exe") == exe:
        s["input_exe"] = ""
    settings.save(s)


def _gui_available() -> bool:
    """Window automation (pywinauto) can switch the server on through PLAXIS' menu."""
    if not _win():
        return False
    import importlib.util
    return importlib.util.find_spec("pywinauto") is not None


def exe_path(pid: int) -> str:
    """Full path of a running program (to remember which PLAXIS version the user works with)."""
    if not _win() or not pid:
        return ""
    out = _run(["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {int(pid)}).Path"], 20).strip()
    if not out:
        out = _run(["wmic", "process", "where", f"processid={int(pid)}", "get", "ExecutablePath", "/value"], 20)
        out = out.split("=", 1)[1].strip() if "=" in out else ""
    return out


def running_inputs() -> list[dict]:
    """PLAXIS Input programs open on this PC: [{pid, name, title}]. tasklist by program name (fast), plus any
    window titled PLAXIS (in case the program has another name)."""
    if not _win():
        return []
    out = {}
    for name in ALL_INPUT_EXES:
        for row in csv.reader(io.StringIO(_run(["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {name}"]))):
            if len(row) >= 2 and row[0].lower() in ALL_INPUT_EXES:
                try:
                    out[int(row[1])] = {"pid": int(row[1]), "name": row[0], "title": ""}
                except ValueError:
                    pass
    for w in gui("windows").get("windows") or []:
        t = w.get("title", "")
        if re.search(r"(?i)output|calculat|disclaimer|remote scripting", t):
            if w["pid"] in out and not out[w["pid"]]["title"]:
                out[w["pid"]]["title"] = t
            continue
        if w["pid"] in out:
            out[w["pid"]]["title"] = out[w["pid"]]["title"] or t
            continue
        # a window titled PLAXIS counts only when its program is PLAXIS Input (not a browser showing this app)
        name = image_name(w["pid"])
        if re.search(r"(?i)^plaxis.*input.*\.exe$", name):
            out[w["pid"]] = {"pid": w["pid"], "name": name, "title": t}
    # re-test of d5016d6 (C1b): PLAXIS 3D 2024 runs helper processes of Plaxis3DInput.exe without a window; the one
    # with the PLAXIS Input window comes first, so the window automation acts on it
    return sorted(out.values(), key=lambda p: not (p["title"] and not re.search(r"(?i)output|calculat", p["title"])))


def _has_window(p: dict) -> bool:
    """The process shows a PLAXIS Input window (its title read from the visible windows), not only helpers."""
    return bool(str(p.get("title") or "").strip())  # its disclaimer or crash window counts too


def image_name(pid: int) -> str:
    """Program name of a process, e.g. Plaxis3DInput.exe."""
    for row in csv.reader(io.StringIO(_run(["tasklist", "/FO", "CSV", "/NH", "/FI", f"PID eq {int(pid)}"]))):
        if len(row) >= 2 and row[1].strip() == str(pid):
            return row[0]
    return ""


def window_title(pid: int) -> str:
    if not _win() or not pid:
        return ""
    for row in csv.reader(io.StringIO(_run(["tasklist", "/V", "/FO", "CSV", "/NH", "/FI", f"PID eq {pid}"]))):
        if len(row) >= 9 and row[1].strip() == str(pid):
            return "" if row[-1] in ("N/A", "") else row[-1]
    return ""


def alive(pid) -> bool:
    if not pid:
        return False
    if _win():
        return str(pid) in _run(["tasklist", "/FO", "CSV", "/NH", "/FI", f"PID eq {pid}"])
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def listening_ports(pids) -> list[int]:
    """TCP ports the given processes listen on (netstat -ano)."""
    pids = {str(p) for p in pids if p}
    if not _win() or not pids:
        return []
    ports = []
    for line in _run(["netstat", "-ano", "-p", "TCP"]).splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() in ("LISTENING", "ABHÖREN", "ECOUTE", "ÉCOUTE") and parts[4] in pids:
            try:
                ports.append(int(parts[1].rsplit(":", 1)[1]))
            except (IndexError, ValueError):
                pass
    return sorted(set(ports))


def saved_server_settings() -> dict:
    """Ports and passwords of PLAXIS' remote scripting server remembered by PLAXIS itself on this PC (its settings
    files under AppData and its registry keys). Best effort: PLAXIS versions store them differently."""
    found = {"ports": [], "passwords": [], "where": []}
    key = re.compile(r"(?i)(app\s*server|remote\s*scripting|scripting\s*server|server)[\w ]{0,20}?(port|password)"
                     r"[\"']?\s*(?:[:=]|\">|'>|>)\s*[\"']?([^\"'<\s,;}]+)")
    roots = []
    for env in ("APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        base = os.environ.get(env, "")
        for vendor in ("Seequent", "Bentley", "Plaxis", "PLAXIS"):
            d = os.path.join(base, vendor)
            if base and os.path.isdir(d):
                roots.append(d)
    for root in dict.fromkeys(roots):
        depth0 = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, files in os.walk(root):
            if dirpath.count(os.sep) - depth0 >= 5:
                dirnames[:] = []
            for f in files:
                if not f.lower().endswith((".ini", ".xml", ".json", ".config", ".cfg", ".settings", ".txt")):
                    continue
                path = os.path.join(dirpath, f)
                try:
                    if os.path.getsize(path) > 2_000_000:
                        continue
                    text = open(path, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                for m in key.finditer(text):
                    _add(found, m.group(2), m.group(3), path)
    if _win():
        try:
            import winreg
            for vendor in ("Seequent", "Bentley", "Plaxis", "PLAXIS"):
                _reg_walk(winreg, winreg.HKEY_CURRENT_USER, rf"Software\{vendor}", found, 0)
        except ImportError:
            pass
    return found


def _add(found, kind, value, where):
    kind = kind.lower()
    if kind == "port" and value.isdigit() and 1000 <= int(value) < 65536:
        found["ports"].append(int(value))
    elif kind == "password" and value:
        found["passwords"].append(value)
    else:
        return
    found["where"].append(where)


def _reg_walk(winreg, hive, path, found, depth):
    if depth > 6:
        return
    try:
        k = winreg.OpenKey(hive, path)
    except OSError:
        return
    with k:
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(k, i)
            except OSError:
                break
            i += 1
            low = name.lower()
            if ("server" in low or "script" in low) and ("port" in low or "password" in low):
                _add(found, "port" if "port" in low else "password", str(value), "registry " + path)
        j = 0
        while True:
            try:
                sub = winreg.EnumKey(k, j)
            except OSError:
                break
            j += 1
            _reg_walk(winreg, hive, path + "\\" + sub, found, depth + 1)


# --- talking to PLAXIS ---------------------------------------------------------------------------------------
def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def _free_port(start: int) -> int:
    for p in range(start, start + 200):
        if not _listening("localhost", p):
            return p
    return start


def _new_server(host, port, password):
    from plxscripting.easy import new_server
    kw = {"password": password}
    try:
        if "timeout" in inspect.signature(new_server).parameters:
            kw["timeout"] = 8.0
    except (TypeError, ValueError):
        pass
    return new_server(host, port, **kw)


# QA V26.1.9 C1: PLAXIS answers on its port whatever the password; with a wrong one every command comes back
# undecipherable ('failed to decrypt'), and a read gave an empty model. A harmless command proves the password.
WRONG_PW = re.compile(r"(?i)decrypt|encrypt|password|authenticat|unauthori[sz]|forbidden|padding|mac check|invalid token|"
                      r"expecting value|json|decode")


LOCKED = re.compile(r"(?i)no active desktop|desktop is locked|workstation is locked|screen is locked")


class WrongPassword(Exception):
    pass


def _ok(s_i, version, g_i=None):
    """A harmless command that fails when the password is wrong (a PLAXIS with no project open still passes)."""
    try:
        _ = s_i.is_3d
    except AttributeError:
        pass
    except Exception as e:  # noqa: BLE001
        if WRONG_PW.search(f"{type(e).__name__}: {e}"):
            raise WrongPassword(f"PLAXIS did not accept the password ({e})") from e
    if g_i is None:
        return
    for probe in (lambda: list(g_i.Phases), lambda: g_i.Project.Title.value, lambda: list(g_i.Points)):
        try:
            probe()
            return
        except Exception as e:  # noqa: BLE001
            if WRONG_PW.search(f"{type(e).__name__}: {e}"):
                raise WrongPassword(f"PLAXIS did not accept the password ({e})") from e
            continue  # e.g. no project open yet: the password itself was accepted


def _try(host, port, passwords, version):
    """(s_i, g_i, password) with the first password PLAXIS accepts, else None (last error in LAST)."""
    for pw in dict.fromkeys(p for p in passwords if p is not None):
        try:
            s_i, g_i = _new_server(host, port, pw)
            _ok(s_i, version, g_i)
            return s_i, g_i, pw
        except Exception as e:  # noqa: BLE001
            LAST["error"] = f"{type(e).__name__}: {e}"
    return None


def _connected(s_i, g_i, host, port, pw, step):
    settings.save({"solver": "plaxis", "host": host, "port": port, "password": pw, "connected_how": step})
    LAST["error"] = ""
    _step(step)
    return s_i, g_i


def _step(msg):
    LAST["steps"] = (LAST["steps"] + [time.strftime("%H:%M:%S ") + msg])[-30:]


def gui(action: str, pid: int = 0, port: int = 0, timeout: float = 90) -> dict:
    """Runs app/plaxis_gui.py (window automation of PLAXIS Input) in its own process; its JSON answer."""
    if not _win():
        return {"ok": False, "error": "only possible on Windows"}
    args = [sys.executable, "-m", "app.plaxis_gui", "--action", action, "--pid", str(int(pid or 0))]
    if port:
        args += ["--port", str(int(port))]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=APP_ROOT, errors="replace",
                           creationflags=0x08000000)
        line = [x for x in r.stdout.splitlines() if x.startswith("{")][-1:]
        return json.loads(line[0]) if line else {"ok": False, "error": (r.stderr or r.stdout or "no answer")[-400:]}
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def gui_start_server(pid: int, progress=lambda m: None, want_port: int = 0) -> dict:
    """In the PLAXIS Input window of process pid: close the disclaimer, Expert › Configure remote scripting server,
    read port and password, Start server, Close. {"ok", "port", "password"} or {"ok": False, "error"}."""
    progress("Switching on PLAXIS' scripting server for you (Expert › Configure remote scripting server)…")
    r = gui("start", pid, want_port, timeout=180)  # the menu routes wait for a slow dialog
    _step(("password read from PLAXIS' window (server already on): " if r.get("ok") and r.get("was_on") else
           "server switched on in PLAXIS: " if r.get("ok") else "menu switch not possible: ")
          + ("" if r.get("ok") else str(r.get("error")) + " | ") + ", ".join(r.get("log") or []))
    return r


def stop_launched() -> str:
    """Closes the PLAXIS Input that the app started itself (never one the engineer opened)."""
    pid = settings.load().get("launched_pid")
    if not pid or not alive(pid):
        return "No PLAXIS started by the app is running."
    if _win():
        _run(["taskkill", "/PID", str(pid), "/T", "/F"])
    else:
        try:
            os.kill(int(pid), 15)
        except OSError:
            pass
    settings.save({"launched_pid": 0})
    _step(f"closed PLAXIS (process {pid}) started by the app")
    return f"Closed the PLAXIS Input the app had started (process {pid})."


# --- the connection ----------------------------------------------------------------------------------------
def connect(version: str = "3d", progress=lambda msg: None, should_stop=lambda: False, restart: bool = False):
    """(s_i, g_i) of PLAXIS Input, found or started by the app. Switches the app to real PLAXIS calculations."""
    cfg = settings.load()
    try:
        settings.ensure_plxscripting_importable(cfg)
    except ImportError as e:
        LAST["error"] = str(e)
        raise ConnectError(f"PLAXIS' Python scripting library (plxscripting) was not found on this PC: {e} "
                           "Is PLAXIS installed here? The app must run on the PC that has PLAXIS.") from e
    host, port, pw = cfg.get("host") or "localhost", int(cfg.get("port") or 10000), cfg.get("password") or ""
    local = host in ("localhost", "127.0.0.1")
    manual = cfg.get("connect_mode") == "manual"  # the engineer presses Start server in PLAXIS and gives the password
    if restart and not manual:
        progress(stop_launched())
        time.sleep(2)

    def stop_check():
        if should_stop():
            _step("stopped by the user")
            raise Stopped("Stopped. PLAXIS keeps running; press Try again to connect to it.")

    found = None

    def passwords(saved):
        nonlocal found
        if found is None:
            found = saved_server_settings() if local else {"ports": [], "passwords": [], "where": []}
        return [saved, ""] + found["passwords"]

    # 1. the saved port
    if _listening(host, port):
        progress(f"PLAXIS answers on port {port}, connecting…")
        r = _try(host, port, passwords(pw), version)
        if r:
            return _connected(r[0], r[1], host, port, r[2], f"connected on saved port {port}")
        if not local:
            raise ConnectError(f"PLAXIS on {host}:{port} did not accept the password ({LAST['error']}). Copy the "
                               "password from PLAXIS (Expert › Configure remote scripting server, tick Show password) "
                               "into the PLAXIS password box and press Connect.")
        progress(f"Something answers on port {port} but does not accept the saved password; connecting by itself…")
    elif not local:
        raise ConnectError(f"Nothing answers on {host}:{port}. Start the remote scripting server in PLAXIS there.")
    elif manual:  # V26.1.9: a pasted password that no longer answers is not asked again: OPTIMUS connects by itself
        progress(f"Nothing answers on port {port}; OPTIMUS finds or starts PLAXIS itself…")
    stop_check()

    # 2. a PLAXIS Input that is already open
    progress("Looking for an open PLAXIS Input…")
    open_now = running_inputs()
    LAST["ghosts"] = []
    launched = int(cfg.get("launched_pid") or 0)
    for proc in open_now:
        ports = listening_ports([proc["pid"]])
        for pt in ports + [p for p in (found or {}).get("ports", []) if p not in ports]:
            if _listening("localhost", pt):
                progress(f"{proc['name']} is open and listens on port {pt}, connecting…")
                r = _try("localhost", pt, passwords(pw), version)
                if r:
                    _remember_exe(proc["pid"])
                    return _connected(r[0], r[1], "localhost", pt, r[2], f"connected to open PLAXIS on port {pt}")
    stop_check()
    # re-test of b5968d9 (C2): PLAXIS Input processes left without a window (and without a server) by an earlier
    # session are not an open PLAXIS: only a process with a PLAXIS Input window counts. A PLAXIS still opening gets a
    # little time to show its window; the one OPTIMUS started itself counts as opening for START_TIMEOUT.
    opening = bool(launched) and time.time() - float(cfg.get("launched_at") or 0) < START_TIMEOUT
    windowed = [p for p in open_now if _has_window(p)]
    if open_now and not windowed and not opening:
        for _ in range(GHOST_WAIT):
            stop_check()
            time.sleep(5)
            open_now = running_inputs()
            windowed = [p for p in open_now if _has_window(p)]
            if windowed:
                break
    ghosts = [p for p in open_now if not _has_window(p) and not (opening and p["pid"] == launched)]
    if ghosts and not windowed:
        for g in ghosts:
            if g["pid"] == launched:  # OPTIMUS' own PLAXIS that never opened: ended
                _kill(g["pid"])
        LAST["ghosts"] = [g["pid"] for g in ghosts if g["pid"] != launched]
        _step(f"{len(ghosts)} PLAXIS Input process(es) without a window ignored: {', '.join(str(g['pid']) for g in ghosts)}")
        progress(f"{len(ghosts)} PLAXIS Input process(es) without a window (left from an earlier session) are "
                 "ignored; starting a new PLAXIS…")
        open_now = [p for p in open_now if p not in ghosts]
    # QA V26.1.9 C1: a PLAXIS that is open but refuses the password is never replaced by a second, empty PLAXIS:
    # its server's port and password are read in its own window (also the one OPTIMUS started itself)
    others = [p for p in windowed if p["pid"] != launched] or \
             ([p for p in windowed if p["pid"] == launched] if _listening("localhost", port) else [])
    if others:
        proc = others[0]
        _step(f"{proc['name']} open ({proc['title'] or 'no title'}) without a usable scripting server")
        r = _start_in_window(proc["pid"], version, progress, stop_check)
        if r:
            _remember_exe(proc["pid"])
            return r
        why = LAST["error"]
        if LOCKED.search(str(why)):  # re-test of 9be0a91: the screen was locked, no click is possible in PLAXIS
            raise ConnectError(
                f"PLAXIS Input is open{' (' + proc['title'] + ')' if proc['title'] else ''} but the PC screen is locked, "
                "so OPTIMUS cannot use PLAXIS' menu to switch its scripting server on. Unlock the PC screen (keep it "
                "unlocked while OPTIMUS connects), then press Try again.")
        raise ConnectError(
            f"PLAXIS Input is open{' (' + proc['title'] + ')' if proc['title'] else ''} but the app could not switch "
            f"its scripting server on ({why}). In PLAXIS close any open message, then press Try again.")

    # 3. start PLAXIS with the server on (or keep waiting for the one the app started before)
    if launched and any(p["pid"] == launched for p in open_now) and (opening or any(p["pid"] == launched for p in windowed)):
        progress("PLAXIS started by the app is still opening, waiting for it…")
        r = _wait(port, pw, version, progress, stop_check, launched, START_TIMEOUT, "PLAXIS Input")
        if r:
            return r
        raise _timeout_error(None, port, launched)
    exes = _candidates(version)
    if not exes:
        raise ConnectError(f"PLAXIS {version.upper()} Input was not found on this PC (searched Program Files and ProgramData "
                           "under Seequent, Bentley and Plaxis). Open PLAXIS yourself and press Try again: the app then "
                           "finds it and switches its scripting server on.")
    if _listening("localhost", port):
        port = _free_port(port + 1)
    new_pw = pw or _strong_password()
    tried = []
    gui_ok = _gui_available()
    plain_exes = set(cfg.get("plain_exes") or [])
    for exe in exes:
        # V26.1.9: first as the PLAXIS Reference Manual F.2.2 says, with the server on from the start
        # (--AppServerPort / --AppServerPassword, chosen by OPTIMUS); a PLAXIS that closes with these options (seen with
        # PLAXIS 2025) is started as a double-click does and its server is switched on through its menu; what worked
        # is remembered for this program
        ways = ["plain"] if exe in plain_exes and gui_ok else (["options", "plain"] if gui_ok else ["options"])
        quit_why = None
        for way in ways:
            stop_check()
            plain = way == "plain"
            progress(f"Starting {_exe_label(exe)}…" if plain else
                     f"Starting {_exe_label(exe)} with its scripting server on port {port}…")
            try:
                args = [exe] if plain else [exe, f"--AppServerPort={port}", f"--AppServerPassword={new_pw}"]
                proc = subprocess.Popen(args, cwd=os.path.dirname(exe))
            except OSError as e:
                quit_why = f"{_exe_label(exe)}: {e}"
                break
            pid = getattr(proc, "pid", 0) or 0
            settings.save({"host": "localhost", "port": port, "password": new_pw, "launched_pid": pid,
                           "launched_at": time.time()})
            _step(f"started {exe} (process {pid}) on port {port}" + (" (as a double-click)" if plain else " (F.2.2 options)"))
            try:
                r = _wait(port, new_pw, version, progress, stop_check, pid, START_TIMEOUT,
                          f"{_exe_label(exe)}", proc=proc)
            except _Quit as q:
                # a small starter program may hand over to the real PLAXIS: look for it before trying another way
                later = [p for p in running_inputs() if p["pid"] != pid]
                if later:
                    settings.save({"launched_pid": later[0]["pid"]})
                    r = _start_in_window(later[0]["pid"], version, progress, stop_check) or \
                        _wait(port, new_pw, version, progress, stop_check, later[0]["pid"], START_TIMEOUT, "PLAXIS Input")
                    if r:
                        settings.save({"input_exe": exe, "connected_how": f"started {_exe_label(exe)} ({way})"})
                        return r
                quit_why = f"{_exe_label(exe)} {q.code if isinstance(q.code, str) else f'closed by itself (exit code {q.code})'}"
                _step(quit_why + f" when started {'as a double-click' if plain else 'with the F.2.2 options'}")
                if not plain and "plain" in ways:
                    settings.save({"plain_exes": sorted(plain_exes | {exe})})
                    progress(f"{quit_why} with the server options; starting it as a double-click…")
                continue
            if r:
                settings.save({"input_exe": exe, "connected_how": f"started {_exe_label(exe)} ({way})"})
                return r
            raise _timeout_error(exe, port, pid)
        if quit_why:
            tried.append(quit_why)
            _mark_bad(exe)
            if exe != exes[-1]:
                progress(f"{quit_why}. Trying the next PLAXIS on this PC…")
    LAST["error"] = "; ".join(tried)
    raise ConnectError("PLAXIS did not start: " + "; ".join(tried) + ". Is a PLAXIS licence available? Open PLAXIS "
                       "once by hand (it may ask to accept the disclaimer or sign in), then press Try again: the app "
                       "connects to the open PLAXIS.")


class _Quit(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _exe_label(exe: str) -> str:
    m = re.search(r"(?i)(PLAXIS[^\\/]*)[\\/][^\\/]+$", exe or "")
    return m.group(1) if m else os.path.basename(exe or "PLAXIS")


def _strong_password() -> str:
    from .plaxis_gui import strong_password
    return strong_password()


def _remember_exe(pid):
    path = exe_path(pid)
    if path:
        settings.save({"input_exe": path})


def _start_in_window(pid, version, progress, stop_check):
    """Switch the server on in PLAXIS' window (reading its port and password), then connect."""
    r = gui_start_server(pid, progress)
    if not r.get("ok"):
        LAST["error"] = str(r.get("error"))
        return None
    port, pw = int(r["port"]), r.get("password") or ""
    settings.save({"host": "localhost", "port": port, "password": pw})
    for _ in range(10):
        stop_check()
        if _listening("localhost", port):
            got = _try("localhost", port, [pw], version)
            if got:
                return _connected(got[0], got[1], "localhost", port, pw, f"connected on port {port} ("
                                  + ("password read from PLAXIS' window" if r.get("was_on") else
                                     "server switched on in PLAXIS' window") + ")")
        time.sleep(1)
    LAST["error"] = f"PLAXIS' server was switched on but did not answer on port {port} ({LAST['error']})"
    return None


def _wait(port, pw, version, progress, stop_check, pid, timeout, what, proc=None):
    t0, title, last_dismiss, next_gui = time.time(), "", -99, GUI_FIRST if _gui_available() else GUI_AFTER
    while time.time() - t0 < timeout:
        stop_check()
        time.sleep(3)
        secs = int(time.time() - t0)
        if proc is not None and getattr(proc, "poll", None) and proc.poll() is not None:
            raise _Quit(proc.returncode)
        if _win() and pid and secs - last_dismiss >= 5:
            last_dismiss = secs  # the "Plaxis disclaimer" window stops PLAXIS until it is closed
            d = gui("dismiss", pid, timeout=30)
            if d.get("crashed"):
                _kill(pid)
                raise _Quit("crashed while starting" + (f" ({d.get('crash')})" if d.get("crash") else ""))
            if d.get("dismissed"):
                _step("closed the PLAXIS disclaimer")
                progress("Closed the PLAXIS disclaimer window…")
        if secs // 15 != (secs - 3) // 15:
            title = window_title(pid) or title
        progress(f"Waiting for {what} ({secs} s){' · PLAXIS shows: ' + title if title else ''}…")
        for pt in [port] + [p for p in listening_ports([pid]) if p != port]:
            if _listening("localhost", pt):
                r = _try("localhost", pt, [pw, ""], version)
                if r:
                    return _connected(r[0], r[1], "localhost", pt, r[2], f"connected on port {pt} after {secs} s")
        if secs >= next_gui and pid and _win():
            next_gui = secs + 20  # switch the server on through PLAXIS' menu; again later if PLAXIS was not ready
            r = _start_in_window(pid, version, progress, stop_check)
            if r:
                return r
    return None


def _kill(pid):
    if _win():
        _run(["taskkill", "/PID", str(pid), "/T", "/F"])


def _timeout_error(exe, port, pid):
    title = window_title(pid)
    LAST["error"] = LAST["error"] or f"no answer on port {port}"
    return ConnectError(
        f"PLAXIS Input was started{' (' + exe + ')' if exe else ''} but its scripting server did not answer on port "
        f"{port}{'. PLAXIS shows: ' + title if title else ''}. If PLAXIS is waiting for you (licence, a message or "
        "the start window), answer it and press Try again. Otherwise press Restart PLAXIS." + _ghost_note())


def _ghost_note() -> str:
    g = LAST.get("ghosts") or []
    return (f" {len(g)} PLAXIS Input process(es) without a window were left from an earlier session (process "
            f"{', '.join(map(str, g))}): if PLAXIS does not start, end them in Task Manager (Details, Plaxis3DInput.exe "
            "or Plaxis2DInput.exe without a window) and press Try again.") if g else ""
