"""Sign-in to OPTIMUS: one or more local accounts (name, email, password), kept in data/accounts.json.

- Passwords are never stored: only a salted PBKDF2-SHA256 hash (310 000 iterations).
- On creating the account a recovery key is shown once: it resets the password without email.
- "Forgot password" emails a 6-digit code (valid 15 minutes) when email (SMTP) is set up in Settings.
- Sessions: a random token in a cookie, 12 hours, or 30 days with "Keep me signed in".
- No account yet (or sign-in switched off): the app opens without sign-in, as before.
"""
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path

from .config import data_dir, env

COOKIE = "optimus_session"
ITER = 310_000
_lock = threading.Lock()
_fails: dict = {}  # email -> [count, until]


class AuthError(Exception):
    pass


def _file() -> Path:
    return data_dir() / "accounts.json"


def _load() -> dict:
    try:
        d = json.loads(_file().read_text())
    except (FileNotFoundError, ValueError):
        d = {}
    d.setdefault("users", [])
    d.setdefault("sessions", {})
    d.setdefault("resets", {})
    d.setdefault("login_off", False)
    return d


def _save(d: dict) -> None:
    tmp = _file().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(_file())


def _hash(secret: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt), ITER).hex()


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def password_problem(pw: str) -> str:
    if len(pw or "") < 8:
        return "The password needs at least 8 characters."
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "The password needs at least one letter and one digit."
    return ""


def required() -> bool:
    """Sign-in is asked when an account exists and it is not switched off."""
    if env("OPTIMUS_LOGIN", "").lower() in ("off", "0", "no"):
        return False
    d = _load()
    return bool(d["users"]) and not d["login_off"]


def status(token: str | None) -> dict:
    d = _load()
    u = user(token)
    return {"accounts": len(d["users"]), "login_off": d["login_off"], "required": required(),
            "user": u, "email_ready": email_ready()}


def email_ready() -> bool:
    from .notify import smtp_config
    return bool(smtp_config().get("host"))


def _public(u: dict) -> dict:
    return {"name": u["name"], "email": u["email"]}


def create(name: str, email: str, pw: str) -> dict:
    """A new account; returns {user, token, recovery_key}. The recovery key is shown once."""
    email, name = _norm(email), (name or "").strip()
    if not name:
        raise AuthError("Give your name.")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise AuthError("Give a valid email address.")
    prob = password_problem(pw)
    if prob:
        raise AuthError(prob)
    with _lock:
        d = _load()
        if any(u["email"] == email for u in d["users"]):
            raise AuthError("An account with this email exists already: sign in.")
        salt, rsalt = secrets.token_hex(16), secrets.token_hex(16)
        key = "-".join(secrets.token_hex(2).upper() for _ in range(5))
        u = {"name": name, "email": email, "salt": salt, "hash": _hash(pw, salt), "rsalt": rsalt,
             "recovery": _hash(key.replace("-", ""), rsalt), "created": time.time()}
        d["users"].append(u)
        d["login_off"] = False
        _save(d)
    return {"user": _public(u), "token": _new_session(email, True), "recovery_key": key}


def _new_session(email: str, remember: bool) -> str:
    tok = secrets.token_urlsafe(32)
    with _lock:
        d = _load()
        now = time.time()
        d["sessions"] = {k: v for k, v in d["sessions"].items() if v["exp"] > now}
        d["sessions"][tok] = {"email": email, "exp": now + (30 * 86400 if remember else 12 * 3600)}
        _save(d)
    return tok


def login(email: str, pw: str, remember: bool = False) -> str:
    email = _norm(email)
    f = _fails.get(email)
    if f and f[1] > time.time():
        raise AuthError(f"Too many attempts: wait {int(f[1] - time.time()) + 1} s and try again.")
    d = _load()
    u = next((u for u in d["users"] if u["email"] == email), None)
    if not u or not hmac.compare_digest(_hash(pw or "", u["salt"]), u["hash"]):
        n = (f[0] if f else 0) + 1
        _fails[email] = [n, time.time() + 30 if n >= 5 else 0]
        raise AuthError("The email or the password is not right.")
    _fails.pop(email, None)
    return _new_session(email, remember)


def user(token: str | None) -> dict | None:
    if not token:
        return None
    d = _load()
    s = d["sessions"].get(token)
    if not s or s["exp"] < time.time():
        return None
    u = next((u for u in d["users"] if u["email"] == s["email"]), None)
    return _public(u) if u else None


def logout(token: str | None) -> None:
    with _lock:
        d = _load()
        d["sessions"].pop(token or "", None)
        _save(d)


def forgot(email: str) -> dict:
    """Emails a 6-digit code to reset the password (when email is set up). Never says whether the account exists."""
    from .notify import send_mail
    email = _norm(email)
    d = _load()
    u = next((u for u in d["users"] if u["email"] == email), None)
    sent = False
    if u:
        code = f"{secrets.randbelow(10 ** 6):06d}"
        salt = secrets.token_hex(8)
        with _lock:
            d = _load()
            d["resets"][email] = {"salt": salt, "hash": _hash(code, salt), "exp": time.time() + 900, "tries": 0}
            _save(d)
        sent = send_mail([email], "OPTIMUS password reset code",
                         f"Hello {u['name']},\n\nYour OPTIMUS password reset code is: {code}\n\n"
                         "It is valid for 15 minutes. If you did not ask for it, ignore this email.\n")
    return {"email_ready": email_ready(), "sent": sent}


def reset(email: str, code: str, new_pw: str) -> str:
    """New password with the emailed code or the recovery key; returns a session token."""
    email, code = _norm(email), (code or "").strip().replace("-", "").replace(" ", "").upper()
    prob = password_problem(new_pw)
    if prob:
        raise AuthError(prob)
    with _lock:
        d = _load()
        u = next((u for u in d["users"] if u["email"] == email), None)
        ok = False
        if u:
            r = d["resets"].get(email)
            if r and r["exp"] > time.time() and r["tries"] < 5:
                ok = hmac.compare_digest(_hash(code, r["salt"]), r["hash"])
                r["tries"] += 1
            if not ok and u.get("recovery"):
                ok = hmac.compare_digest(_hash(code, u["rsalt"]), u["recovery"])
        if not ok:
            _save(d)
            raise AuthError("The code or the recovery key is not right (or the code is older than 15 minutes).")
        u["salt"] = secrets.token_hex(16)
        u["hash"] = _hash(new_pw, u["salt"])
        d["resets"].pop(email, None)
        d["sessions"] = {k: v for k, v in d["sessions"].items() if v["email"] != email}  # signed out everywhere
        _save(d)
    return _new_session(email, False)


def change_password(token: str | None, old: str, new: str) -> None:
    u = user(token)
    if not u:
        raise AuthError("Sign in first.")
    login(u["email"], old)  # checks the old password (and counts failed attempts)
    prob = password_problem(new)
    if prob:
        raise AuthError(prob)
    with _lock:
        d = _load()
        x = next(x for x in d["users"] if x["email"] == u["email"])
        x["salt"] = secrets.token_hex(16)
        x["hash"] = _hash(new, x["salt"])
        _save(d)


def set_login_off(off: bool) -> None:
    with _lock:
        d = _load()
        d["login_off"] = bool(off)
        _save(d)
