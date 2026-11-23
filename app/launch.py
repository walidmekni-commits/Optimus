"""Opens OPTIMUS in its own program window (no address bar, no tabs, its own taskbar icon), like desktop software.

Uses Microsoft Edge (on every Windows 10/11 PC) or Google Chrome in application mode, with a profile of its own
so the window never becomes a tab of the normal browser. Edge / Chrome are found through the Windows registry
(App Paths), the usual install folders and PATH. Only when neither exists, the normal browser opens.
"""
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

URL = "http://127.0.0.1:8000"


def _registry(exe: str) -> list[str]:
    out = []
    try:
        import winreg
    except ImportError:
        return out
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key in (rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}",
                    rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{exe}"):
            try:
                with winreg.OpenKey(root, key) as k:
                    out.append(winreg.QueryValue(k, None))
            except OSError:
                pass
    return out


def find_browser() -> str | None:
    env = os.environ.get
    cands = []
    for exe, rel in (("msedge.exe", r"Microsoft\Edge\Application\msedge.exe"),
                     ("chrome.exe", r"Google\Chrome\Application\chrome.exe")):
        cands += _registry(exe)
        for base in (env("ProgramFiles(x86)"), env("ProgramFiles"), env("LOCALAPPDATA")):
            if base:
                cands.append(os.path.join(base, rel))
        cands.append(shutil.which(exe) or "")
    for c in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):  # other systems
        cands.append(shutil.which(c) or "")
    for c in cands:
        c = (c or "").strip().strip('"')
        if c and Path(c).is_file():
            return c
    return None


def open_app_window(url: str = URL) -> str:
    """Returns how it opened: "app" (own window) or "browser" (fallback)."""
    url = url.rstrip("/") + "/?w=app&login=1"  # the desktop icon opens on the sign-in page
    exe = find_browser()
    if exe:
        profile = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "OPTIMUS" / "window"
        profile.mkdir(parents=True, exist_ok=True)
        flags = 0x00000008 | 0x00000200 if sys.platform.startswith("win") else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1500,950",
                          "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"],
                         creationflags=flags, close_fds=True)
        return "app"
    webbrowser.open(url)
    return "browser"


if __name__ == "__main__":
    print("OPTIMUS opened in its own window." if open_app_window(*(sys.argv[1:2] or [])) == "app"
          else "Edge / Chrome not found: OPTIMUS opened in the browser.")
