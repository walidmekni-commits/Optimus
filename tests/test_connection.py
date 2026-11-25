"""Automatic PLAXIS connection (open PLAXIS found, server switched on, retry / stop) and the solver assistant."""
import pytest

from app import plaxis_launcher as L, settings
from tests.test_workflow import client  # noqa: F401  (fixture)


@pytest.fixture
def pc(monkeypatch, tmp_path):
    """A fake Windows PC: no PLAXIS open, nothing listening, plxscripting found."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ensure_plxscripting_importable", lambda cfg: "here")
    monkeypatch.setattr(L, "saved_server_settings", lambda: {"ports": [], "passwords": ["fromplaxis"], "where": ["x"]})
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    monkeypatch.setattr(L, "window_title", lambda pid: "")
    state = {"listen": set(), "open": [], "ports": {}, "accept": None, "gui": [], "started": None}
    monkeypatch.setattr(L, "_listening", lambda h, p: p in state["listen"])
    monkeypatch.setattr(L, "running_inputs", lambda: state["open"])
    monkeypatch.setattr(L, "listening_ports", lambda pids: [p for pid in pids for p in state["ports"].get(pid, [])])
    monkeypatch.setattr(L, "_win", lambda: True)
    monkeypatch.setattr(L, "gui", lambda action, pid=0, port=0, timeout=90: {"ok": True})
    monkeypatch.setattr(L, "exe_path", lambda pid: r"C:\Program Files\Seequent\PLAXIS 3D 2024\Plaxis3DInput.exe")

    def new_server(h, p, pw):
        if p in state["listen"] and pw == state["accept"]:
            return ("s", "g")
        raise ConnectionError("bad password")
    monkeypatch.setattr(L, "_new_server", new_server)
    return state


def test_open_plaxis_on_another_port_with_password_from_its_settings(pc):
    pc["open"] = [{"pid": 42, "name": "Plaxis3DInput.exe", "title": "PLAXIS 3D"}]
    pc["ports"] = {42: [10001]}
    pc["listen"] = {10001}
    pc["accept"] = "fromplaxis"
    assert L.connect("3d") == ("s", "g")
    cfg = settings.load()
    assert cfg["port"] == 10001 and cfg["password"] == "fromplaxis" and cfg["solver"] == "plaxis"


def test_open_plaxis_without_server_gets_it_switched_on(pc, monkeypatch):
    """As the engineer does by hand: Expert > Configure remote scripting server, read port + password, Start."""
    pc["open"] = [{"pid": 7, "name": "Plaxis3DInput.exe", "title": "PLAXIS 3D Advanced: Zayed_SS2.p3d"}]

    def gui(pid, progress=None, want_port=0):
        pc["gui"].append(pid)
        pc["listen"].add(10000)
        pc["accept"] = "xRmPezxT2T~+zP<2"
        return {"ok": True, "port": 10000, "password": "xRmPezxT2T~+zP<2"}
    monkeypatch.setattr(L, "gui_start_server", gui)
    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start a second PLAXIS"))
    assert L.connect("3d") == ("s", "g")
    cfg = settings.load()
    assert pc["gui"] == [7] and cfg["port"] == 10000 and cfg["password"] == "xRmPezxT2T~+zP<2"
    assert cfg["input_exe"].endswith("PLAXIS 3D 2024\\Plaxis3DInput.exe")  # this version is started next time


def test_open_plaxis_that_cannot_be_switched_on_says_what_to_do(pc, monkeypatch):
    pc["open"] = [{"pid": 7, "name": "Plaxis3DInput.exe", "title": "PLAXIS 3D 2025"}]
    monkeypatch.setattr(L, "gui_start_server", lambda *a, **k: {"ok": False, "error": "pywinauto is not installed"})
    with pytest.raises(L.ConnectError, match="pywinauto is not installed.*press Try again"):
        L.connect("3d")


def test_waiting_can_be_stopped_and_a_version_that_quits_is_replaced(pc, monkeypatch):
    monkeypatch.setattr(L, "find_input_exe", lambda v: r"C:\P\PLAXIS 3D 2025\Plaxis3DInput.exe")
    monkeypatch.setattr(L, "find_input_exes", lambda v: [r"C:\P\PLAXIS 3D 2025\Plaxis3DInput.exe",
                                                         r"C:\P\PLAXIS 3D 2024\Plaxis3DInput.exe"])
    monkeypatch.setattr(L, "gui_start_server", lambda *a, **k: {"ok": False, "error": "not yet"})

    class Proc:
        pid, returncode = 99, None

        def poll(self):
            return None
    monkeypatch.setattr(L.subprocess, "Popen", lambda args, cwd=None: Proc())
    with pytest.raises(L.Stopped):
        L.connect("3d", should_stop=lambda: settings.load().get("launched_pid") == 99)

    settings.save({"launched_pid": 0})
    started = []

    class Quits(Proc):  # 2025 has no licence here: closes with exit code 1
        returncode = 1

        def poll(self):
            return 1

    def popen(args, cwd=None):
        started.append(args[0])
        if "2025" in args[0]:
            return Quits()
        pc["listen"].add(int(args[1].split("=")[1]))
        pc["accept"] = args[2].split("=", 1)[1]
        return Proc()
    monkeypatch.setattr(L.subprocess, "Popen", popen)
    assert L.connect("3d") == ("s", "g")
    assert [("2025" in a) for a in started] == [True, False]
    assert "2024" in settings.load()["input_exe"]


def test_try_again_waits_for_the_plaxis_the_app_started(pc, monkeypatch):
    settings.save({"launched_pid": 55, "port": 10000, "password": "pw"})
    pc["open"] = [{"pid": 55, "name": "Plaxis3DInput.exe", "title": ""}]
    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start a second PLAXIS"))
    monkeypatch.setattr(L, "gui_start_server", lambda *a, **k: pytest.fail("not on the app's own PLAXIS so soon"))
    calls = {"n": 0}

    def later(h, p):
        calls["n"] += 1
        return calls["n"] > 3 and p == 10000
    monkeypatch.setattr(L, "_listening", later)
    pc["listen"], pc["accept"] = {10000}, "pw"
    assert L.connect("3d") == ("s", "g")


def test_solver_assistant_chat(client, monkeypatch):
    r = client.post("/api/solver/assistant", json={"message": "hello"}).json()
    assert "solver assistant" in r["message"] and "connect" in r["actions"]
    r = client.post("/api/solver/assistant", json={"message": "Soil body seems to collapse in phase 3"}).json()
    assert "near failure" in r["message"]
    monkeypatch.setattr(settings, "ensure_plxscripting_importable", lambda cfg: (_ for _ in ()).throw(ImportError("no plx")))
    r = client.post("/api/solver/assistant", json={"message": "plaxis is not connected, why?"}).json()
    assert "Python library" in r["message"] and "settings" in r["actions"] and "diagnosis" in r
    d = client.get("/api/solver/diagnose").json()
    assert "plxscripting" in d["text"]


def test_connect_job_and_stop(client, monkeypatch):
    seen = {}

    def fake(version, progress=lambda m: None, should_stop=lambda: False, restart=False):
        seen.update(version=version, restart=restart)
        raise L.ConnectError("PLAXIS shows: Licence")
    monkeypatch.setattr(L, "connect", fake)
    j = client.post("/api/solver/connect?version=2d&restart=1&wait=1").json()
    assert j["status"] == "done" and "Licence" in j["errors"][0] and seen == {"version": "2d", "restart": True}
    assert client.post(f"/api/jobs/{j['id']}/stop").json() == {"ok": True}
    assert client.post("/api/jobs/nope/stop").status_code == 404


def test_window_automation_reads_password_and_starts_server(monkeypatch):
    """app/plaxis_gui.py against a stand-in of PLAXIS' windows (disclaimer + Configure remote scripting server)."""
    from app import plaxis_gui as G

    class W:
        def __init__(self, text, cls, kids=(), on_click=None):
            self.text, self.cls, self.kids, self.on_click, self.handle = text, cls, list(kids), on_click, 1

        def window_text(self):
            return self.text

        def class_name(self):
            return self.cls

        def descendants(self):
            return self.kids

        def is_visible(self):
            return True

        def click(self):
            if self.on_click:
                self.on_click()

        def set_edit_text(self, t):
            self.text = t

        def set_focus(self):
            pass

        def rectangle(self):
            class R:
                left = top = 0
                right = bottom = 100
            return R()

    shown = {"disclaimer": True}
    state = W("The server is not running", "TLabel")
    port = W("10000", "TSpinEdit")
    pw = W("xRmPezxT2T~+zP<2", "TEdit")
    dlg = W("Configure remote scripting server", "TFrmServer", [
        state, port, pw, W("Show password", "TCheckBox"),
        W("Start server", "TButton", on_click=lambda: setattr(state, "text", "The server is running")),
        W("Close", "TButton")])
    disc = W("Plaxis disclaimer", "TFrmDisclaimer",
             [W("OK", "TButton", on_click=lambda: shown.update(disclaimer=False))])
    main = W("PLAXIS 3D Advanced: Zayed_SS2.p3d *", "TMainForm")
    monkeypatch.setattr(G, "windows_of", lambda pid: ([disc] if shown["disclaimer"] else []) + [main, dlg])
    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    r = G.start_server(4242)
    assert r == {"port": 10000, "password": "xRmPezxT2T~+zP<2"}
    assert not shown["disclaimer"] and state.text == "The server is running"
    assert any("password read from PLAXIS" in x for x in G.LOG)

    assert G.crash_report(4242) is None
    closed = []
    crash = W("Plaxis3DInput.exe", "TMEDialog", [W("An error occurred in the application.", "TLabel"),
              W("Access violation at address 00007FF8FD4E6918.", "TLabel"),
              W("Close application", "TButton", on_click=lambda: closed.append(1))])
    monkeypatch.setattr(G, "windows_of", lambda pid: [crash])
    assert "Access violation" in G.crash_report(4242) and closed == [1]
    monkeypatch.setattr(G, "windows_of", lambda pid: [main, dlg])

    pw.text, state.text = "", "The server is not running"  # no password set yet: a strong one is typed
    r = G.start_server(4242)
    assert len(r["password"]) == 16 and pw.text == r["password"]


def test_plain_start_crash_window_then_next_version(pc, monkeypatch):
    """PLAXIS 3D 2025 crashes (EAccessViolation window): the app closes it, remembers it and starts 2024 plainly
    (no start-up options), then switches the server on through PLAXIS' menu."""
    v25, v24 = r"C:\P\PLAXIS 3D 2025\Plaxis3DInput.exe", r"C:\P\PLAXIS 3D 2024\Plaxis3DInput.exe"
    monkeypatch.setattr(L, "_gui_available", lambda: True)
    monkeypatch.setattr(L, "GUI_FIRST", 0)
    monkeypatch.setattr(L, "find_input_exe", lambda v: v25)
    monkeypatch.setattr(L, "find_input_exes", lambda v: [v25, v24])
    killed, started = [], []
    monkeypatch.setattr(L, "_kill", lambda pid: killed.append(pid))

    class Proc:
        returncode = None

        def __init__(self, pid):
            self.pid = pid

        def poll(self):
            return None

    def popen(args, cwd=None):
        started.append(args)
        return Proc(25 if "2025" in args[0] else 24)
    monkeypatch.setattr(L.subprocess, "Popen", popen)
    monkeypatch.setattr(L, "gui", lambda action, pid=0, port=0, timeout=90:
                        {"ok": True, "crashed": pid == 25, "crash": "EAccessViolation"} if action == "dismiss" else {"ok": True})

    def start(pid, progress=None, want_port=0):
        pc["listen"].add(10000)
        pc["accept"] = "Pw-strong1"
        return {"ok": True, "port": 10000, "password": "Pw-strong1"}
    monkeypatch.setattr(L, "gui_start_server", start)
    assert L.connect("3d") == ("s", "g")
    # V26.1.9: each PLAXIS is started first with the F.2.2 server options, then as a double-click when it closes
    assert [a[0] for a in started] == [v25, v25, v24] and len(started[0]) == 3 and started[1] == [v25]
    assert started[2][1].startswith("--AppServerPort=") and killed == [25, 25]
    cfg = settings.load()
    assert cfg["input_exe"] == v24 and cfg["bad_exes"] == [v25] and cfg["plain_exes"] == [v25]
    assert "options" in cfg["connected_how"]
    assert L._candidates("3d") == [v24, v25]  # next time 2024 first


def test_manual_mode_password_typed_in_the_app(pc, client, monkeypatch):
    """The engineer may still paste the password into the app; when it no longer answers, OPTIMUS does not ask for
    it again but connects by itself (V26.1.9: Walid never opens the scripting server or copies the password)."""
    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: pytest.fail("PLAXIS is open: no second one"))
    r = client.put("/api/solver/mode", json={"mode": "manual", "port": 10000, "password": "xRmPezxT2T~+zP<2"}).json()
    assert r == {"mode": "manual", "port": 10000, "password_set": True}
    pc["listen"], pc["accept"] = {10000}, "xRmPezxT2T~+zP<2"
    assert L.connect("3d") == ("s", "g")  # the pasted password
    pc["listen"], pc["accept"] = set(), None
    pc["open"] = [{"pid": 7, "name": "Plaxis3DInput.exe", "title": "PLAXIS 3D"}]
    menu = []

    def gui(pid, progress=None, want_port=0):
        menu.append(pid)
        pc["listen"], pc["accept"] = {10000}, "NewPw-from-PLAXIS"
        return {"ok": True, "port": 10000, "password": "NewPw-from-PLAXIS"}
    monkeypatch.setattr(L, "gui_start_server", gui)
    assert L.connect("3d") == ("s", "g")  # server not started: switched on and its password read by OPTIMUS
    assert menu == [7] and settings.load()["password"] == "NewPw-from-PLAXIS"
    assert client.put("/api/solver/mode", json={"mode": "auto"}).json()["mode"] == "auto"
    assert client.get("/api/settings").json()["connect_mode"] == "auto"


def test_browser_window_titled_plaxis_is_not_taken_for_plaxis(monkeypatch):
    monkeypatch.setattr(L, "_win", lambda: True)
    monkeypatch.setattr(L, "_run", lambda args, timeout=30: "")
    monkeypatch.setattr(L, "gui", lambda action, **k: {"windows": [
        {"pid": 5, "title": "30 – PLAXIS AI Design Automation - Microsoft Edge"},
        {"pid": 6, "title": "PLAXIS 3D Advanced: Zayed_SS2.p3d"}]})
    monkeypatch.setattr(L, "image_name", lambda pid: {5: "msedge.exe", 6: "Plaxis3DInput.exe"}[pid])
    assert [p["pid"] for p in L.running_inputs()] == [6]
