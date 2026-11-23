"""Does in PLAXIS Input's own window what the engineer does by hand to switch the remote scripting server on:

    close the "Plaxis disclaimer" window if it is shown,
    Expert › Configure remote scripting server,
    tick "Show password", read the port and the password (or type a strong password when none is set),
    press "Start server", then "Close".

Runs as its own small process (python -m app.plaxis_gui --pid N --action start) so the window automation
(pywinauto) never disturbs the app, and prints one JSON line:
    {"ok": true, "port": 10000, "password": "...", "log": [...]}    or    {"ok": false, "error": "...", "log": [...]}
Actions: start (switch the server on), dismiss (only close the disclaimer), windows (list PLAXIS windows).
"""
import argparse
import json
import re
import secrets
import string
import sys
import time

LOG: list[str] = []
OK_WORDS = r"(?i)^(&?ok|&?accept|i &?agree|&?agree|&?continue|&?yes|&?close|&?start)$"


def log(msg: str) -> None:
    LOG.append(msg)


def strong_password() -> str:
    """Passes PLAXIS' 'strong' check: upper, lower, digits and a sign, 16 characters."""
    rnd = secrets.SystemRandom()
    chars = [rnd.choice(string.ascii_uppercase), rnd.choice(string.ascii_lowercase), rnd.choice(string.digits),
             rnd.choice("-_.")] + [rnd.choice(string.ascii_letters + string.digits) for _ in range(12)]
    rnd.shuffle(chars)
    return "".join(chars)


def _text(w) -> str:
    """Caption of a window / control, or text of a menu item, without the & of keyboard shortcuts."""
    for get in ("window_text", "text"):
        try:
            t = getattr(w, get)()
            return (t or "").split("\t")[0].replace("&", "").strip()
        except Exception:  # noqa: BLE001
            continue
    return ""


def _cls(w) -> str:
    try:
        return w.class_name() or ""
    except Exception:  # noqa: BLE001
        return ""


def windows_of(pid: int):
    from pywinauto import Desktop
    try:
        return Desktop(backend="win32").windows(process=pid, visible_only=True)
    except Exception:  # noqa: BLE001
        return []


def plaxis_windows() -> list[dict]:
    """Every visible PLAXIS window on this PC with its process: finds PLAXIS even when tasklist is slow."""
    from pywinauto import Desktop
    out = []
    for w in Desktop(backend="win32").windows(visible_only=True):
        t = _text(w)
        if re.search(r"(?i)\bplaxis\b", t):
            try:
                out.append({"pid": w.process_id(), "title": t, "class": _cls(w)})
            except Exception:  # noqa: BLE001
                pass
    return out


def _is_disclaimer(w) -> bool:
    return bool(re.search(r"(?i)disclaimer", _text(w) + " " + _cls(w)))


def dismiss_disclaimer(pid: int) -> bool:
    """Closes the 'Plaxis disclaimer' window (as pressing its OK button does). True when one was closed."""
    done = False
    for w in windows_of(pid):
        if not _is_disclaimer(w):
            continue
        kids = w.descendants()
        for k in kids:  # an "I accept" tick box first, when there is one
            if re.search(r"(?i)accept|agree", _text(k)) and re.search(r"(?i)check", _cls(k)):
                try:
                    k.click()
                except Exception:  # noqa: BLE001
                    pass
        buttons = [k for k in kids if re.search(r"(?i)button", _cls(k)) or re.search(OK_WORDS, _text(k))]
        btn = next((b for b in buttons if re.search(OK_WORDS, _text(b))), buttons[0] if buttons else None)
        try:
            if btn is not None:
                log(f"disclaimer: pressing '{_text(btn)}'")
                btn.click()
            else:
                log("disclaimer: pressing Enter")
                w.set_focus()
                from pywinauto.keyboard import send_keys
                send_keys("{ENTER}")
            done = True
        except Exception as e:  # noqa: BLE001
            log(f"disclaimer: {type(e).__name__}: {e}")
    return done


def crash_report(pid: int) -> str | None:
    """PLAXIS' own error window ("An error occurred in the application", madExcept): closes PLAXIS through it and
    returns the exception, else None."""
    for w in windows_of(pid):
        kids = w.descendants()
        texts = [_text(k) for k in kids] + [_text(w)]
        if not any(re.search(r"(?i)error occurred in the application|access violation|bug report", t) for t in texts):
            continue
        detail = next((t for t in texts if re.search(r"(?i)EAccessViolation|Access violation|exception", t)), "")
        for k in kids:
            if re.search(r"(?i)^close application$", _text(k)):
                try:
                    k.click()
                except Exception:  # noqa: BLE001
                    pass
                break
        return detail[:200] or "PLAXIS error window"
    return None


def main_window(pid: int):
    wins = [w for w in windows_of(pid) if not _is_disclaimer(w) and re.search(r"(?i)plaxis", _text(w))]
    if not wins:
        return None

    def area(w):
        r = w.rectangle()
        return (r.right - r.left) * (r.bottom - r.top)
    return max(wins, key=area)


def _dialog(pid: int):
    for w in windows_of(pid):
        if re.search(r"(?i)remote scripting", _text(w)):
            return w
    return None


def open_dialog(pid: int, main) -> object:
    """Expert › Configure remote scripting server… (Win32 menu first, then UI Automation)."""
    if _dialog(pid):
        return _dialog(pid)
    errors = []
    try:
        menu = main.menu()
        if menu is not None and menu.item_count():
            for it in menu.items():
                if _text(it).lower() == "expert":
                    for sub in it.sub_menu().items():
                        if re.search(r"(?i)remote scripting", _text(sub)):
                            log(f"menu: {it.text()} > {sub.text()}")
                            main.menu_select(f"{it.text()}->{sub.text()}")
                            break
                    break
        else:
            errors.append("no Win32 menu")
    except Exception as e:  # noqa: BLE001
        errors.append(f"win32 menu: {type(e).__name__}: {e}")
    for _ in range(20):
        d = _dialog(pid)
        if d:
            return d
        time.sleep(0.25)
    try:  # UI Automation: click Expert, then the item in the drop-down
        from pywinauto import Application, Desktop
        app = Application(backend="uia").connect(process=pid, timeout=10)
        win = app.window(handle=main.handle)
        win.set_focus()
        win.child_window(title="Expert", control_type="MenuItem").click_input()
        time.sleep(0.8)
        item = Desktop(backend="uia").window(process=pid, control_type="Menu").child_window(
            title_re="(?i).*remote scripting.*", control_type="MenuItem")
        log("menu (uia): Expert > Configure remote scripting server")
        item.click_input()
    except Exception as e:  # noqa: BLE001
        errors.append(f"uia menu: {type(e).__name__}: {e}")
    for _ in range(20):
        d = _dialog(pid)
        if d:
            return d
        time.sleep(0.25)
    raise RuntimeError("could not open Expert > Configure remote scripting server (" + "; ".join(errors) + ")")


def _edits(dlg):
    return [k for k in dlg.descendants() if re.search(r"(?i)edit", _cls(k)) and k.is_visible()]


def _button(dlg, pattern):
    for k in dlg.descendants():
        if re.search(pattern, _text(k)) and k.is_visible():
            return k
    return None


def start_server(pid: int, want_port: int | None = None) -> dict:
    for _ in range(3):
        if not dismiss_disclaimer(pid):
            break
        time.sleep(1)
    main = None
    for _ in range(40):  # PLAXIS may still be opening
        main = main_window(pid)
        if main is not None:
            break
        dismiss_disclaimer(pid)
        time.sleep(0.5)
    if main is None:
        raise RuntimeError("the PLAXIS Input window was not found")
    try:
        main.set_focus()
    except Exception:  # noqa: BLE001
        pass
    dlg = open_dialog(pid, main)
    log("dialog open: " + _text(dlg))
    show = _button(dlg, r"(?i)^show password$")
    if show is not None:
        try:
            checked = show.get_check_state() if hasattr(show, "get_check_state") else 0
        except Exception:  # noqa: BLE001
            checked = 0
        if not checked:
            show.click()
            time.sleep(0.3)
    edits = _edits(dlg)
    texts = [_text(e) for e in edits]
    log(f"fields: {len(edits)}")
    port_i = next((i for i, t in enumerate(texts) if t.isdigit()), None)
    pw_i = next((i for i, t in enumerate(texts) if i != port_i and (t or True)), None)
    if port_i is None or pw_i is None:
        raise RuntimeError(f"the port and password fields were not recognised ({len(edits)} fields)")
    port = int(texts[port_i])
    if want_port and want_port != port:
        edits[port_i].set_edit_text(str(want_port))
        port = want_port
    password = texts[pw_i]
    if not password or "•" in password or "*" * len(password) == password:
        password = strong_password()
        edits[pw_i].set_edit_text(password)
        log("no readable password: typed a new strong one")
    else:
        log("password read from PLAXIS")
    running = _button(dlg, r"(?i)server is running")
    if running is None:
        start = _button(dlg, r"(?i)^start server$")
        if start is None:
            raise RuntimeError("the Start server button was not found")
        start.click()
        log("pressed Start server")
        time.sleep(1.5)
    else:
        log("server was already running")
    state = _button(dlg, r"(?i)server is (not )?running")
    if state is not None and re.search(r"(?i)not running", _text(state)):
        find = _button(dlg, r"(?i)^find available$")  # the port is taken by another program
        if find is not None:
            find.click()
            time.sleep(0.5)
            t = _text(_edits(dlg)[port_i])
            if t.isdigit():
                port = int(t)
            log(f"port taken: PLAXIS found port {port}")
            start = _button(dlg, r"(?i)^start server$")
            if start is not None:
                start.click()
                time.sleep(1.5)
        state = _button(dlg, r"(?i)server is (not )?running")
    log("state: " + (_text(state) if state is not None else "?"))
    if state is not None and re.search(r"(?i)not running", _text(state)):
        raise RuntimeError("PLAXIS says the server is not running after pressing Start server")
    close = _button(dlg, r"(?i)^close$")
    if close is not None:
        close.click()
    return {"port": port, "password": password}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--action", default="start", choices=("start", "dismiss", "windows"))
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args(argv)
    try:
        import pywinauto  # noqa: F401
    except ImportError:
        print(json.dumps({"ok": False, "error": "pywinauto is not installed (python -m pip install pywinauto)",
                          "log": LOG}))
        return 0
    try:
        if a.action == "windows":
            out = {"ok": True, "windows": plaxis_windows()}
        elif a.action == "dismiss":
            crash = crash_report(a.pid)
            out = {"ok": True, "crashed": bool(crash), "crash": crash or "",
                   "dismissed": False if crash else dismiss_disclaimer(a.pid)}
        else:
            out = {"ok": True, **start_server(a.pid, a.port or None)}
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    out["log"] = LOG
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
