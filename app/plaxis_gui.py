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
        # C1b re-test: PLAXIS draws its menu items itself ('!! non-supported owner drawn item !!'): the item is found
        # by its place, the first item without a submenu after the submenus (Macro library, Run Python script, Run
        # Python tool, Python), whatever mode PLAXIS is in and however many items come before
        item, downs, k = _by_place(subs)
        global KEY_DOWNS
        KEY_DOWNS = downs
        enabled = _enabled(item) if item is not None else True
        log(f"win32: items not readable; the item after the submenus is item {downs} (index {k}, id "
            f"{_call(item, 'item_id') if item is not None else '?'}, {'enabled' if enabled else 'NOT enabled'})")
        _keys("{ESC}{ESC}")
        if not enabled:
            errors.append("win32: the Configure remote scripting server item is not enabled in this PLAXIS mode")
            return False
        time.sleep(0.3)
        # C1b re-test (Ashraf, PLAXIS 3D 2024.3 in Staged construction): menu_select('#6->#6') opened the dialog
        i = next(n for n, t in enumerate(tops) if t is exp)
        log(f"win32: menu_select #{i}->#{k}")
        try:
            main.menu_select(f"#{i}->#{k}")
        except Exception as e:  # noqa: BLE001
            log(f"menu_select: {type(e).__name__}: {e}; sending its command")
            if item is None:
                raise
            item.select()
        return True
    log("win32: clicking " + _text(item))
    try:
        item.click_input()
    except Exception as e:  # noqa: BLE001
        log(f"win32 click: {type(e).__name__}: {e}; sending its command")
        _keys("{ESC}{ESC}")
        item.select()
    return True


KEY_DOWNS = ITEMS_ABOVE + 1  # Down presses to the item; set from the menu's shape when it can be read


def _call(o, name, d=None):
    """o.name() or d (C1b re-test: pywinauto's win32 MenuItem has no .type(): every call is guarded)."""
    try:
        return getattr(o, name)()
    except Exception:  # noqa: BLE001
        return d


def _safe(f, d=None):
    try:
        return f()
    except Exception:  # noqa: BLE001
        return d


def _enabled(it) -> bool:
    return bool(_call(it, "is_enabled", True))


def _is_sep(it) -> bool:
    info = _call(it, "_read_item")  # pywinauto: the MENUITEMINFO of the item
    ftype = getattr(info, "fType", None)
    if ftype is None:
        ftype = _call(it, "type", 0) or 0
    return bool(ftype & 0x800)  # MFT_SEPARATOR


def _has_sub(it) -> bool:
    sm = _call(it, "sub_menu")
    return sm is not None and bool(_call(sm, "item_count", 1))


def _by_place(subs):
    """The first item without a submenu that follows a run of submenu items; with the Down presses to reach it
    (separators are skipped by the keyboard) and its index in the menu (for menu_select '#i->#k')."""
    seen_sub, n = False, 0
    for k, it in enumerate(subs):
        if _is_sep(it):
            continue
        n += 1
        if _has_sub(it):
            seen_sub = True
        elif seen_sub:
            return it, n, k
    return None, ITEMS_ABOVE + 1, ITEMS_ABOVE


def _to_soil_mode(pid: int, main) -> bool:
    """Clicks PLAXIS' Soil mode tab (the scripting server item may be greyed in another mode)."""
    found, _ = _uia_named(pid, main.handle, r"(?i)^soil$")
    for el in found:
        try:
            el.click_input()
            time.sleep(3)
            log("switched PLAXIS to Soil mode")
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


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
    switched = False
    for opened in (False, True, True):  # its command sent; else Expert opened by a click and the item clicked
        try:
            if not _via_win32(main, errors, opened):
                if errors and "not enabled" in errors[-1] and not switched:
                    switched = True
                    if _to_soil_mode(pid, main):
                        continue
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
                log(f"keys: Down x{KEY_DOWNS}, Enter")
                _keys("{DOWN}" * KEY_DOWNS + "{ENTER}")
            d = _wait_dialog(pid, 12)  # C1b re-test: in Staged construction the dialog came after more than 5 s
            if d:
                return d
            _keys("{ESC}{ESC}")
            errors.append(f"{way}: no dialog after pressing the item")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{way}: {type(e).__name__}: {e}")
    d = _wait_dialog(pid, 10)  # a dialog that comes late is still used (never left open for the engineer)
    if d:
        log("the dialog came late: used")
        return d
    raise RuntimeError("could not open Expert > Configure remote scripting server (" + "; ".join(errors) + ")")


def close_dialog(pid: int) -> None:
    """Closes the Configure remote scripting server dialog when OPTIMUS opened it and could not finish (Walid: no
    dialog is left open for him). Close only: the server is never stopped here."""
    d = _dialog(pid)
    if d is None:
        return
    b = _button(d, r"(?i)^close$")
    try:
        if b is not None:
            b.click()
        else:
            d.set_focus()
            _keys("{ESC}")
        log("dialog closed")
    except Exception as e:  # noqa: BLE001
        log(f"closing the dialog: {type(e).__name__}: {e}")


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
        try:  # the shape of the Expert drop-down (owner-drawn items have no readable text)
            m = main.menu()
            exp = next((t for t in m.items() if re.search(EXPERT, _text(t))), None)
            subs = exp.sub_menu().items() if exp is not None else []
            out["win32_expert"] = [{"text": _text(it), "separator": _is_sep(it), "submenu": _has_sub(it),
                                    "enabled": _enabled(it), "id": _call(it, "item_id")} for it in subs]
            out["item_by_place"] = {"downs": _by_place(subs)[1], "index": _by_place(subs)[2]}
        except Exception as e:  # noqa: BLE001
            out["win32_expert"] = f"{type(e).__name__}: {e}"
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
    try:
        dlg = open_dialog(pid, main)
        log("dialog open: " + _text(dlg))
        return _in_dialog(pid, dlg, want_port)
    except Exception:
        close_dialog(pid)
        raise


def _running(dlg):
    """True / False when the dialog tells whether the server runs (its label, else its buttons: Stop server enabled
    and Start server greyed), None when it does not say."""
    lab = _button(dlg, r"(?i)server is (not )?running")
    if lab is not None:
        return not re.search(r"(?i)not running", _text(lab))
    stop, start = _button(dlg, r"(?i)^stop server$"), _button(dlg, r"(?i)^start server$")
    if stop is not None and start is not None:
        return _enabled(stop) and not _enabled(start)
    return None


def _click(w, what: str) -> bool:
    """Clicks a control when it is enabled (C1b re-test: a greyed control raised ElementNotEnabled)."""
    if w is None or not _enabled(w):
        log(f"{what}: not available")
        return False
    w.click()
    return True


def _in_dialog(pid: int, dlg, want_port: int | None) -> dict:
    """Reads the port and password of Configure remote scripting server; starts the server only when it is off.
    A server already on is left as it is (C1b re-test: its password field gives the password: never stopped)."""
    running = _running(dlg)
    show = _button(dlg, r"(?i)^show password$")
    if show is not None:
        checked = _call(show, "get_check_state", 0) or 0
        if not checked and _click(show, "Show password"):
            time.sleep(0.3)
    edits = _edits(dlg)
    texts = [_text(e) for e in edits]
    log(f"fields: {len(edits)}; server {'on' if running else 'off' if running is False else 'state not shown'}")
    port_i = next((i for i, t in enumerate(texts) if t.isdigit()), None)
    pw_i = next((i for i, t in enumerate(texts) if i != port_i), None)
    if port_i is None or pw_i is None:
        raise RuntimeError(f"the port and password fields were not recognised ({len(edits)} fields)")
    port = int(texts[port_i])
    password = texts[pw_i]
    readable = bool(password) and "•" not in password and "*" * len(password) != password
    if running and not readable and _click(_button(dlg, r"(?i)^stop server$"), "Stop server"):
        # last resort, only when the password of a running server cannot be read: restarted with a new one
        log("server on with an unreadable password: pressed Stop server")
        time.sleep(1.0)
        edits, running = _edits(dlg), False
    if running:
        if not readable:
            raise RuntimeError("the server is on but its password cannot be read in the dialog")
        log("server already on: password read from PLAXIS")
    else:
        if want_port and want_port != port and _enabled(edits[port_i]):
            edits[port_i].set_edit_text(str(want_port))
            port = want_port
        if readable:
            log("password read from PLAXIS")
        else:
            password = strong_password()
            edits[pw_i].set_edit_text(password)
            log("no readable password: typed a new strong one")
        if not _click(_button(dlg, r"(?i)^start server$"), "Start server"):
            raise RuntimeError("the Start server button was not found or is greyed")
        log("pressed Start server")
        time.sleep(1.5)
        if _running(dlg) is False:
            if _click(_button(dlg, r"(?i)^find available$"), "Find available"):  # the port is taken
                time.sleep(0.5)
                t = _text(_edits(dlg)[port_i])
                if t.isdigit():
                    port = int(t)
                log(f"port taken: PLAXIS found port {port}")
                _click(_button(dlg, r"(?i)^start server$"), "Start server")
                time.sleep(1.5)
            if _running(dlg) is False:
                raise RuntimeError("PLAXIS says the server is not running after pressing Start server")
    _click(_button(dlg, r"(?i)^close$"), "Close")
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
