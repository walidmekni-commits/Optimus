"""Does in PLAXIS Input's own window what the engineer does by hand to switch the remote scripting server on:

    close the "Plaxis disclaimer" window if it is shown,
    Expert › Configure remote scripting server,
    tick "Show password", read the port and the password (or type a strong password when none is set),
    press "Start server", then "Close".

Runs as its own small process (python -m app.plaxis_gui --pid N --action start) so the window automation
(pywinauto) never disturbs the app, and prints one JSON line:
    {"ok": true, "port": 10000, "password": "...", "log": [...]}    or    {"ok": false, "error": "...", "log": [...]}
Actions: start (switch the server on), dismiss (only close the disclaimer), windows (list PLAXIS windows),
menu (what the automation sees of the Expert menu, for QA).
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
    """PLAXIS Input's main window (Delphi class TfrmMain first) of process pid."""
    wins = [w for w in windows_of(pid) if not _is_disclaimer(w) and re.search(r"(?i)plaxis", _text(w))]
    if not wins:
        return None
    frm = [w for w in wins if re.search(r"(?i)^tfrmmain$", _cls(w))]
    wins = frm or wins

    def area(w):
        r = w.rectangle()
        return (r.right - r.left) * (r.bottom - r.top)
    return max(wins, key=area)


def _exe(pid: int) -> str:
    try:
        from pywinauto.application import process_module
        return (process_module(pid) or "").replace("/", "\\").split("\\")[-1].lower()
    except Exception:  # noqa: BLE001
        return ""


def input_process(pid: int) -> int:
    """Re-test of d5016d6 (C1b): PLAXIS 3D 2024 runs helper processes of the same program (its web views) that have
    no window; the PLAXIS Input window (TfrmMain) is in another process. The process that holds it: pid itself when
    it has the window, else the one PLAXIS Input window of the same program, else pid."""
    if main_window(pid) is not None:
        return pid
    from pywinauto import Desktop
    exe = _exe(pid)
    cands = []
    for w in Desktop(backend="win32").windows(visible_only=True):
        if re.search(r"(?i)^tfrmmain$", _cls(w)) and re.search(r"(?i)\bplaxis\b", _text(w)) and \
                not re.search(r"(?i)output|calculat", _text(w)):
            try:
                q = w.process_id()
            except Exception:  # noqa: BLE001
                continue
            if q != pid and (not exe or _exe(q) == exe):
                cands.append(q)
    cands = sorted(set(cands))
    if len(cands) == 1:
        log(f"process {pid} has no PLAXIS Input window (a PLAXIS helper); using process {cands[0]}")
        return cands[0]
    return pid


def _dialog(pid: int):
    """The Configure remote scripting server dialog: by its title, or (its title may differ, e.g. while the server is
    already running) by its own controls: a Start/Stop server button with a Show password box."""
    wins = windows_of(pid)
    for w in wins:
        if re.search(r"(?i)remote scripting|scripting server", _text(w)):
            return w
    for w in wins:
        if _is_disclaimer(w) or re.search(r"(?i)\bplaxis\b", _text(w)):
            continue
        try:
            texts = [_text(k) for k in w.descendants()]
        except Exception:  # noqa: BLE001
            continue
        if any(re.search(r"(?i)^(start|stop) server$", t) for t in texts) and \
                any(re.search(r"(?i)show password|password", t) for t in texts):
            return w
    return None


EXPERT = r"(?i)^expert$"
ITEM = r"(?i)remote\s*scripting"
# PLAXIS 3D 2024 Expert menu, above the item: Examine commands, Run commands, Macro library, Run Python script,
# Run Python tool, Python (used only when the menu's items cannot be read at all)
ITEMS_ABOVE = 6


def _wait_dialog(pid: int, secs: float):
    for _ in range(max(1, int(secs / 0.25))):
        d = _dialog(pid)
        if d:
            return d
        time.sleep(0.25)
    return None


def _via_win32(main, errors: list, opened: bool = False) -> bool:
    """A real Win32 menu bar: the item's command is sent to PLAXIS, no mouse needed."""
    menu = main.menu()
    if menu is None or not menu.item_count():
        errors.append("no Win32 menu bar (PLAXIS draws its own)")
        return False
    tops = menu.items()
    log("win32 menu bar: " + ", ".join(_text(t) or "?" for t in tops))
    exp = next((t for t in tops if re.search(EXPERT, _text(t))), None)
    if exp is None:
        errors.append("win32: no Expert in the menu bar")
        return False
    subs = exp.sub_menu().items()
    log("win32 Expert: " + ", ".join(_text(s) or "?" for s in subs))
    item = next((s for s in subs if re.search(ITEM, _text(s))), None)
    if item is not None and not opened:
        log("win32: selecting " + _text(item))
        item.select()
        return True
    # PLAXIS fills its Expert drop-down only when it opens (its Python items): open it, read it again, click the item
    try:
        main.set_focus()
    except Exception:  # noqa: BLE001
        pass
    exp.click_input()
    time.sleep(0.8)
    subs = exp.sub_menu().items()
    log("win32 Expert (opened): " + ", ".join(_text(s) or "?" for s in subs))
    item = next((s for s in subs if re.search(ITEM, _text(s))), None)
    if item is None:
        _keys("{ESC}{ESC}")
        errors.append("win32: no remote scripting item under Expert")
        return False
    log("win32: clicking " + _text(item))
    try:
        item.click_input()
    except Exception as e:  # noqa: BLE001
        log(f"win32 click: {type(e).__name__}: {e}; sending its command")
        _keys("{ESC}{ESC}")
        item.select()
    return True


def _uia_named(pid: int, main_handle, pattern: str):
    """Every UI Automation element of PLAXIS (its windows and drop-downs) whose name matches; and all names seen."""
    from pywinauto import Desktop
    found, names = [], []
    tops = []
    try:
        tops = [w for w in Desktop(backend="uia").windows() if w.process_id() == pid]
    except Exception as e:  # noqa: BLE001
        log(f"uia windows: {type(e).__name__}: {e}")
    # the opened drop-down is its own top-level window: searched first and whole; the big main window (with the
    # model tree) last and only for its menu items, so the search stays quick
    tops.sort(key=lambda w: w.handle == main_handle)
    for w in tops:
        if found and w.handle == main_handle:
            break
        try:
            els = w.descendants(control_type="MenuItem") if w.handle == main_handle else [w] + w.descendants()
        except Exception:  # noqa: BLE001
            continue
        for el in els:
            n = _text(el)
            if n:
                names.append(n)
            if n and re.search(pattern, n):
                found.append(el)
    return found, names


def _keys(keys: str) -> None:
    from pywinauto.keyboard import send_keys
    send_keys(keys, pause=0.08)


def _press(el) -> None:
    try:
        el.invoke()  # UI Automation Invoke: no mouse
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        el.select()
        return
    except Exception:  # noqa: BLE001
        pass
    el.click_input()


def _open_expert(pid: int, main) -> bool:
    found, _ = _uia_named(pid, main.handle, EXPERT)
    if not found:
        return False
    try:
        found[0].click_input()  # a real click: menus drawn by PLAXIS itself open only this way
    except Exception:  # noqa: BLE001
        _press(found[0])
    time.sleep(0.8)
    return True


def open_dialog(pid: int, main) -> object:
    """Expert › Configure remote scripting server… Three ways, each checked by the dialog appearing:
    1. the Win32 menu (command sent, no mouse);  2. UI Automation: click Expert, then the item in the drop-down;
    3. keyboard: click Expert, Down to the item, Enter (when the drop-down's items cannot be read)."""
    if _dialog(pid):
        return _dialog(pid)
    errors = []
    for opened in (False, True):  # its command sent; else Expert opened by a click and the item clicked
        try:
            if not _via_win32(main, errors, opened):
                break
            d = _wait_dialog(pid, 8)
            if d:
                return d
            errors.append("win32: item " + ("clicked" if opened else "selected") + " but no dialog")
        except Exception as e:  # noqa: BLE001
            errors.append(f"win32 menu: {type(e).__name__}: {e}")
            break
    for way in ("uia", "keys"):
        try:
            try:
                main.set_focus()
            except Exception:  # noqa: BLE001
                pass
            if not _open_expert(pid, main):
                errors.append(f"{way}: Expert not found in the PLAXIS window")
                continue
            found, names = _uia_named(pid, main.handle, ITEM)
            if way == "uia":
                if found:
                    log(f"uia: pressing '{_text(found[0])}'")
                    _press(found[0])
                else:
                    tail = [n for n in names if not re.search(EXPERT, n)][:25]
                    log("uia: item not readable; names seen: " + ", ".join(tail))
                    _keys("{ESC}")
                    errors.append("uia: item not readable in the drop-down")
                    continue
            else:
                log(f"keys: Down x{ITEMS_ABOVE + 1}, Enter")
                _keys("{DOWN}" * (ITEMS_ABOVE + 1) + "{ENTER}")
            d = _wait_dialog(pid, 5)
            if d:
                return d
            _keys("{ESC}{ESC}")
            errors.append(f"{way}: no dialog after pressing the item")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{way}: {type(e).__name__}: {e}")
    raise RuntimeError("could not open Expert > Configure remote scripting server (" + "; ".join(errors) + ")")


def menu_report(pid: int) -> dict:
    """What the automation sees of the Expert menu (for QA): Win32 menu, the names after clicking Expert."""
    pid = input_process(pid)
    main = main_window(pid)
    if main is None:
        return {"main": None}
    out = {"main": _text(main), "class": _cls(main)}
    try:
        m = main.menu()
        out["win32_menu"] = [_text(t) for t in m.items()] if m is not None and m.item_count() else []
    except Exception as e:  # noqa: BLE001
        out["win32_menu"] = f"{type(e).__name__}: {e}"
    try:
        main.set_focus()
        out["expert_clicked"] = _open_expert(pid, main)
        _, names = _uia_named(pid, main.handle, ITEM)
        out["names_after_expert"] = names[:80]
        _keys("{ESC}{ESC}")
    except Exception as e:  # noqa: BLE001
        out["uia"] = f"{type(e).__name__}: {e}"
    out["windows"] = [{"title": _text(w), "class": _cls(w)} for w in windows_of(pid)]
    return out


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
        pid = input_process(pid)
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
        stop = _button(dlg, r"(?i)^stop server$")
        if _button(dlg, r"(?i)server is running") is not None and stop is not None:
            # the server is on with a password that cannot be read: stop it, so a new one can be set and started
            stop.click()
            log("server on with an unreadable password: pressed Stop server")
            time.sleep(1.0)
            edits = _edits(dlg)
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
    return {"port": port, "password": password, "pid": pid}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--action", default="start", choices=("start", "dismiss", "windows", "menu"))
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
        elif a.action == "menu":
            out = {"ok": True, "menu": menu_report(a.pid)}
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
