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
    import time as _t
    settings.save({"launched_pid": 55, "launched_at": _t.time(), "port": 10000, "password": "pw"})  # started just now
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
    assert r == {"port": 10000, "password": "xRmPezxT2T~+zP<2", "pid": 4242}
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


def test_wrong_password_is_found_and_the_open_plaxis_is_used(pc, client, monkeypatch):
    """QA V26.1.9 C1: PLAXIS answers on its port whatever the password, but every command then fails to decrypt.
    OPTIMUS must not say 'connected', must not start a second (empty) PLAXIS, and reads the password in PLAXIS."""
    class G:
        def __init__(self, good):
            self.good = good

        @property
        def Phases(self):
            if not self.good:
                raise RuntimeError("Failed to decrypt the response from the server")
            return ["InitialPhase"]

    def new_server(h, p, pw):
        if p in pc["listen"]:
            return ("s", G(pw == pc["accept"]))
        raise ConnectionError("nothing there")
    monkeypatch.setattr(L, "_new_server", new_server)
    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: pytest.fail("no second PLAXIS while one is open"))
    client.put("/api/solver/mode", json={"mode": "manual", "port": 10203, "password": "wrong"})
    pc["listen"], pc["accept"] = {10203}, "RealPw-1"
    pc["open"], pc["ports"] = [{"pid": 7, "name": "Plaxis3DInput.exe", "title": "PLAXIS 3D: UA-22"}], {7: [10203]}
    menu = []
    monkeypatch.setattr(L, "gui_start_server", lambda pid, progress=None, want_port=0: menu.append(pid) or
                        {"ok": True, "port": 10203, "password": "RealPw-1"})
    s_i, g_i = L.connect("3d")
    assert g_i.good and menu == [7] and settings.load()["password"] == "RealPw-1"


def test_an_empty_read_never_replaces_the_model(client):
    from app import main
    from tests.test_workflow import new_project
    pid = new_project(client, "reporting")
    assert client.post(f"/api/projects/{pid}/plaxis-model/read?demo=1").status_code == 200
    empty = {"version": "3d", "source": "plaxis", "counts": {"points": 0, "structures": 0, "materials": 0}, "notes": []}
    with pytest.raises(main.HTTPException):
        main._save_model(pid, empty)
    assert client.get(f"/api/projects/{pid}/plaxis-model").json()["model"]["points"]


def test_expert_menu_drawn_by_plaxis_itself(monkeypatch):
    """PLAXIS 3D 2024 (Walid 25 Nov 2026): no Win32 menu bar; Expert opens but the drop-down's items may not be
    readable. OPTIMUS presses the item by UI Automation, or else Down x7 + Enter, each checked by the dialog."""
    from app import plaxis_gui as G

    class M:
        handle = 7

        def menu(self):
            return None

        def set_focus(self):
            pass

    class El:
        def __init__(self, name, on=None):
            self.name, self.on = name, on

        def window_text(self):
            return self.name

        def invoke(self):
            if self.on:
                self.on()

        def click_input(self):
            self.invoke()

    dialog = {"open": False}
    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    monkeypatch.setattr(G, "_dialog", lambda pid: "DLG" if dialog["open"] else None)
    keys = []
    monkeypatch.setattr(G, "_keys", lambda k: (keys.append(k), dialog.update(open="{ENTER}" in k)))
    readable = {"on": True}

    def named(pid, handle, pattern):
        items = [El("Expert")] + ([El("Configure remote scripting server...", lambda: dialog.update(open=True))]
                                  if readable["on"] else [El("")])
        found = [e for e in items if e.name and __import__("re").search(pattern, e.name)]
        return found, [e.name for e in items if e.name]
    monkeypatch.setattr(G, "_uia_named", named)
    G.LOG.clear()
    assert G.open_dialog(1, M()) == "DLG" and keys == []
    assert any("uia: pressing 'Configure remote scripting server...'" in x for x in G.LOG)

    dialog["open"], readable["on"] = False, False
    G.LOG.clear()
    assert G.open_dialog(1, M()) == "DLG"
    assert keys == ["{ESC}", "{DOWN}" * 7 + "{ENTER}"]
    assert any("item not readable" in x for x in G.LOG)

    dialog["open"] = False
    monkeypatch.setattr(G, "_keys", lambda k: keys.append(k))  # nothing works: a clear error with each way
    with pytest.raises(RuntimeError) as e:
        G.open_dialog(1, M())
    assert "no Win32 menu bar" in str(e.value) and "keys: no dialog" in str(e.value)


def test_server_already_on_with_unreadable_password(monkeypatch):
    """Ashraf C1 re-test: the PLAXIS opened by hand already runs its server, password unknown. The dialog is found by
    its controls (title may differ); the server is stopped, a new strong password typed, then started again."""
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

    state = W("The server is running", "TLabel")
    pw = W("****************", "TEdit")
    clicks = []
    dlg = W("Server", "TFrmServer", [
        state, W("10203", "TSpinEdit"), pw, W("Show password", "TCheckBox"),
        W("Stop server", "TButton", on_click=lambda: (clicks.append("stop"), setattr(state, "text", "The server is not running"))),
        W("Start server", "TButton", on_click=lambda: (clicks.append("start"), setattr(state, "text", "The server is running"))),
        W("Close", "TButton")])
    main = W("PLAXIS 3D Advanced: UA-22_converted_2.p3d", "TMainForm")
    monkeypatch.setattr(G, "windows_of", lambda pid: [main, dlg])
    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    assert G._dialog(32560) is dlg
    r = G.start_server(32560)
    assert r["port"] == 10203 and len(r["password"]) == 16 and pw.text == r["password"]
    assert clicks == ["stop", "start"] and state.text == "The server is running"


def test_helper_process_without_window_points_to_the_input_window(monkeypatch):
    """Re-test of d5016d6 (C1b): the chosen PLAXIS process is a helper without a window; the automation acts on the
    process of the TfrmMain window of the same program. running_inputs lists the windowed process first."""
    import sys
    import types
    from app import plaxis_gui as G

    class W:
        def __init__(self, pid, text, cls):
            self.pid, self.text, self.cls = pid, text, cls

        def window_text(self):
            return self.text

        def class_name(self):
            return self.cls

        def process_id(self):
            return self.pid

        def rectangle(self):
            class R:
                left = top = 0
                right = bottom = 100
            return R()
    wins = [W(8720, "PLAXIS 3D Advanced: UA-22.p3d --- SERVER ACTIVE on port 10203 (SECURED)", "TfrmMain"),
            W(8720, "PLAXIS", "TApplication"), W(999, "PLAXIS Output", "TfrmMain")]
    desk = types.SimpleNamespace(windows=lambda visible_only=True: wins)
    fake = types.ModuleType("pywinauto")
    fake.Desktop = lambda backend="win32": desk
    app_mod = types.ModuleType("pywinauto.application")
    app_mod.process_module = lambda pid: {32560: r"C:\P\Plaxis3DInput.exe", 8720: r"C:\P\Plaxis3DInput.exe",
                                          999: r"C:\P\Plaxis3DOutput.exe"}[pid]
    monkeypatch.setitem(sys.modules, "pywinauto", fake)
    monkeypatch.setitem(sys.modules, "pywinauto.application", app_mod)
    monkeypatch.setattr(G, "windows_of", lambda pid: [w for w in wins if w.pid == pid])
    G.LOG.clear()
    assert G.input_process(32560) == 8720 and "using process 8720" in G.LOG[-1]
    assert G.input_process(8720) == 8720
    assert G._cls(G.main_window(8720)) == "TfrmMain"

    monkeypatch.setattr(L, "_win", lambda: True)
    monkeypatch.setattr(L, "_run", lambda args: '"Plaxis3DInput.exe","32560"\n"Plaxis3DInput.exe","8720"\n'
                        if "imagename eq plaxis3dinput.exe" in " ".join(args).lower() else "")
    monkeypatch.setattr(L, "gui", lambda action, pid=0, port=0, timeout=90: {"ok": True, "windows": [
        {"pid": 8720, "title": wins[0].text, "class": "TfrmMain"}]})
    assert [p["pid"] for p in L.running_inputs()] == [8720, 32560]


def test_win32_expert_filled_only_when_opened(monkeypatch):
    """PLAXIS fills its Expert drop-down when it opens: OPTIMUS opens it with a click, reads it again, clicks the item."""
    from app import plaxis_gui as G
    st = {"open": False, "dialog": False}

    class It:
        def __init__(self, t, sub=None, on=None):
            self.t, self.sub, self.on = t, sub, on

        def text(self):
            return self.t

        def sub_menu(self):
            return self.sub

        def click_input(self):
            if self.on:
                self.on()

    item = It("Configure remote scripting server...", on=lambda: st.update(dialog=True))

    class Sub:
        def items(self):
            return [It("Examine commands..."), item] if st["open"] else []
    expert = It("&Expert", Sub(), on=lambda: st.update(open=True))

    class Menu:
        def item_count(self):
            return 2

        def items(self):
            return [It("&File"), expert]

    class Main:
        handle = 3

        def menu(self):
            return Menu()

        def set_focus(self):
            pass
    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    monkeypatch.setattr(G, "_dialog", lambda pid: "DLG" if st["dialog"] else None)
    monkeypatch.setattr(G, "_keys", lambda k: None)
    G.LOG.clear()
    assert G.open_dialog(1, Main()) == "DLG"
    assert any(x.startswith("win32: clicking Configure remote scripting") for x in G.LOG)


def test_locked_screen_is_said_plainly(pc, monkeypatch):
    """Re-test of 9be0a91: with the PC screen locked every window route fails ('no active desktop'): OPTIMUS says to
    unlock the screen, not to close a message in PLAXIS."""
    monkeypatch.setattr(L, "running_inputs", lambda: [{"pid": 8720, "name": "Plaxis3DInput.exe", "title": "PLAXIS 3D"}])
    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: pytest.fail("PLAXIS is open: no second one"))

    def start(pid, progress=None, want_port=0):
        L.LAST["error"] = "RuntimeError: There is no active desktop required for moving mouse cursor!"
        return {"ok": False, "error": L.LAST["error"]}
    monkeypatch.setattr(L, "gui_start_server", start)
    with pytest.raises(L.ConnectError) as e:
        L.connect("3d")
    assert "Unlock the PC screen" in str(e.value) and "close any open message" not in str(e.value)


def test_windowless_plaxis_processes_left_over_are_ignored(pc, monkeypatch):
    """Re-test of b5968d9 (C2): three Plaxis3DInput.exe left from yesterday without a window or a server: OPTIMUS
    does not take them for an open PLAXIS (no 'Input window not found'), it starts a new PLAXIS; OPTIMUS' own
    leftover is ended, the others are named in case PLAXIS does not start."""
    settings.save({"launched_pid": 71, "launched_at": 1.0, "port": 10000, "password": "pw"})  # started yesterday
    pc["open"] = [{"pid": p, "name": "Plaxis3DInput.exe", "title": ""} for p in (71, 72, 73)]
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    monkeypatch.setattr(L, "gui_start_server", lambda *a, **k: pytest.fail("no PLAXIS window to automate"))
    killed, started = [], []
    monkeypatch.setattr(L, "_kill", lambda pid: (killed.append(pid), pc["open"].remove(next(p for p in pc["open"] if p["pid"] == pid))))
    monkeypatch.setattr(L, "find_input_exes", lambda v: [r"C:\P\PLAXIS 3D 2024\Plaxis3DInput.exe"])
    monkeypatch.setattr(L, "find_input_exe", lambda v: r"C:\P\PLAXIS 3D 2024\Plaxis3DInput.exe")

    class Proc:
        pid, returncode = 90, None

        def poll(self):
            return None

    def popen(args, cwd=None):
        started.append(args)
        pc["listen"].add(10000)
        pc["accept"] = args[2].split("=", 1)[1] if len(args) > 2 else "pw"
        return Proc()
    monkeypatch.setattr(L.subprocess, "Popen", popen)
    assert L.connect("3d") == ("s", "g")
    assert started and killed == [71] and L.LAST["ghosts"] == [72, 73]
    assert "3 PLAXIS Input process(es) without a window ignored" in " ".join(L.LAST["steps"])


def test_owner_drawn_expert_menu_found_by_place(monkeypatch):
    """C1b re-test: PLAXIS draws its Expert items itself ('!! non-supported owner drawn item !!') and in Staged
    construction mode the Down x7 did not reach it. The item is the first one without a submenu after the
    submenus; its command is sent; a greyed item makes OPTIMUS switch PLAXIS to Soil mode and try again."""
    from app import plaxis_gui as G
    st = {"dialog": False, "enabled": False, "soil": 0}
    OD = "!! non-supported owner drawn item !!"

    class Sub:
        def __init__(self, n):
            self.n = n

        def item_count(self):
            return self.n

        def items(self):
            return []

    class It:
        def __init__(self, sub=0, sep=False, target=False, text=OD):
            self.sub, self.sep, self.target, self.t = sub, sep, target, text

        def text(self):
            return self.t

        def type(self):
            return 0x800 if self.sep else 0x100

        def sub_menu(self):
            return Sub(self.sub) if self.sub else None

        def is_enabled(self):
            return st["enabled"] if self.target else True

        def item_id(self):
            return 321 if self.target else 1

        def select(self):
            if self.target and st["enabled"]:
                st["dialog"] = True

        def click_input(self):
            pass
    items = [It(), It(), It(sep=True), It(sub=3), It(sub=2), It(sub=2), It(sub=4), It(target=True), It(), It(), It(sub=1)]

    class ExpMenu:
        def items(self):
            return items

    class Top:
        def __init__(self, t):
            self.t = t

        def text(self):
            return self.t

        def sub_menu(self):
            return ExpMenu()

        def click_input(self):
            pass

    class Menu:
        def item_count(self):
            return 2

        def items(self):
            return [Top("&File"), Top("&Expert")]

    class Main:
        handle = 3

        def menu(self):
            return Menu()

        def set_focus(self):
            pass

    def soil(pid, main):
        st["soil"] += 1
        st["enabled"] = True
        return True
    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    monkeypatch.setattr(G, "_dialog", lambda pid: "DLG" if st["dialog"] else None)
    monkeypatch.setattr(G, "_keys", lambda k: None)
    monkeypatch.setattr(G, "_to_soil_mode", soil)
    assert G._by_place(items)[1] == 7  # separators skipped: Down x7
    G.LOG.clear()
    assert G.open_dialog(1, Main()) == "DLG" and st["soil"] == 1
    assert any("item 7 (id 321, NOT enabled)" in x for x in G.LOG) and any("item 7 (id 321, enabled)" in x for x in G.LOG)


def test_a_late_dialog_is_used_and_a_failed_one_is_closed(monkeypatch):
    """C1b re-test: in Staged construction the dialog came after OPTIMUS gave up and stayed open for Walid. A late
    dialog is now used; when the work in it fails, OPTIMUS closes it (Close only, the server is not stopped)."""
    from app import plaxis_gui as G

    class Main:
        handle = 3

        def menu(self):
            return None

        def set_focus(self):
            pass
    n = {"polls": 0}

    def dialog(pid):
        n["polls"] += 1
        return "DLG" if n["polls"] > 30 else None  # comes late
    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    monkeypatch.setattr(G, "_dialog", dialog)
    monkeypatch.setattr(G, "_keys", lambda k: None)
    monkeypatch.setattr(G, "_open_expert", lambda pid, main: False)
    assert G.open_dialog(1, Main()) == "DLG"

    closed = []

    class Btn:
        def __init__(self, t):
            self.t = t

        def window_text(self):
            return self.t

        def is_visible(self):
            return True

        def click(self):
            closed.append(self.t)

    class Dlg:
        def descendants(self):
            return [Btn("Stop server"), Btn("Close")]

        def window_text(self):
            return "Configure remote scripting server"
    monkeypatch.setattr(G, "_dialog", lambda pid: Dlg())
    monkeypatch.setattr(G, "main_window", lambda pid: Main())
    monkeypatch.setattr(G, "input_process", lambda pid: pid)
    monkeypatch.setattr(G, "dismiss_disclaimer", lambda pid: False)
    monkeypatch.setattr(G, "_in_dialog", lambda pid, dlg, want: (_ for _ in ()).throw(RuntimeError("fields not recognised")))
    with pytest.raises(RuntimeError):
        G.start_server(1)
    assert closed == ["Close"]
