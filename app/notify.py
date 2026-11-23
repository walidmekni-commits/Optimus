"""Outgoing email. If SMTP isn't configured, the email is printed to the console instead."""
import smtplib
from email.message import EmailMessage

from .config import env

OUTBOX: list[dict] = []  # every email attempted, handy for tests and debugging


def smtp_config() -> dict:
    """Email settings: Settings › Email (data/settings.json) first, then .env."""
    from .settings import load
    s = load()
    return {"host": s.get("smtp_host") or env("SMTP_HOST"), "port": int(s.get("smtp_port") or env("SMTP_PORT", "587")),
            "user": s.get("smtp_user") or env("SMTP_USER"), "password": s.get("smtp_password") or env("SMTP_PASSWORD"),
            "from": s.get("smtp_from") or env("SMTP_FROM")}


def send_mail(to: list[str], subject: str, body: str, raise_errors: bool = False) -> bool:
    to = [t for t in to if t]
    if not to:
        return False
    OUTBOX.append({"to": to, "subject": subject, "body": body})
    c = smtp_config()
    host = c["host"]
    if not host:
        print(f"[email not sent: SMTP_HOST unset]\nTo: {', '.join(to)}\nSubject: {subject}\n\n{body}\n")
        return False
    msg = EmailMessage()
    msg["From"] = c["from"] or c["user"]
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        if c["port"] == 465:
            srv = smtplib.SMTP_SSL(host, 465, timeout=30)
        else:
            srv = smtplib.SMTP(host, c["port"], timeout=30)
            srv.starttls()
        with srv:
            if c["user"]:
                srv.login(c["user"], c["password"])
            srv.send_message(msg)
        return True
    except Exception:
        if raise_errors:
            raise
        print(f"[email failed] {subject}")
        return False


def error_recipients() -> list[str]:
    return [e for e in (env("USER_EMAIL"), env("ADMIN_EMAIL")) if e]
