"""
Notification helpers — macOS native, email (SMTP), and Pushover.
Use whichever you have configured. Falls back gracefully if missing.
"""
from __future__ import annotations
import os, subprocess, smtplib, ssl, json
from email.message import EmailMessage
from pathlib import Path

CONFIG_PATH = Path.home() / ".leaps_signal_config.json"

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            return {}
    return {}

def notify_macos(title: str, message: str, subtitle: str = "") -> bool:
    """Send a native macOS notification via osascript."""
    try:
        escaped_title = title.replace('"', '\\"')
        escaped_msg   = message.replace('"', '\\"')
        escaped_sub   = subtitle.replace('"', '\\"')
        script = (
            f'display notification "{escaped_msg}" '
            f'with title "{escaped_title}"'
            + (f' subtitle "{escaped_sub}"' if subtitle else "")
            + ' sound name "Glass"'
        )
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
        return True
    except Exception as e:
        print(f"  ⚠️  macOS notification failed: {e}")
        return False

def notify_email(subject: str, body: str) -> bool:
    """Send email via SMTP. Requires SMTP_USER, SMTP_PASS, SMTP_TO env vars
    or entries in ~/.leaps_signal_config.json."""
    cfg = load_config()
    smtp_user = os.environ.get("SMTP_USER") or cfg.get("smtp_user")
    smtp_pass = os.environ.get("SMTP_PASS") or cfg.get("smtp_pass")
    smtp_to   = os.environ.get("SMTP_TO")   or cfg.get("smtp_to") or smtp_user
    smtp_host = os.environ.get("SMTP_HOST") or cfg.get("smtp_host", "smtp.gmail.com")
    raw_port  = os.environ.get("SMTP_PORT") or cfg.get("smtp_port", 465)
    try:
        smtp_port = int(str(raw_port).strip().strip('"').strip("'"))
    except (TypeError, ValueError):
        print(f"  ⚠️  SMTP_PORT is not a valid integer (got '{raw_port}'); falling back to 465")
        smtp_port = 465

    # Trim accidental whitespace / quotes from secret values
    if smtp_user: smtp_user = smtp_user.strip().strip('"').strip("'")
    if smtp_pass: smtp_pass = smtp_pass.strip().strip('"').strip("'")
    if smtp_to:   smtp_to   = smtp_to.strip().strip('"').strip("'")
    if smtp_host: smtp_host = smtp_host.strip().strip('"').strip("'")

    if not smtp_user or not smtp_pass:
        print("  ⚠️  SMTP_USER or SMTP_PASS not set — cannot send email")
        return False

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"]    = smtp_user
        msg["To"]      = smtp_to
        msg.set_content(body)

        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"  ⚠️  Email failed: {e}")
        return False

def notify_pushover(title: str, message: str, priority: int = 0) -> bool:
    """Send via Pushover (https://pushover.net). Needs PUSHOVER_USER + PUSHOVER_TOKEN."""
    cfg = load_config()
    user_key = os.environ.get("PUSHOVER_USER")  or cfg.get("pushover_user")
    api_token = os.environ.get("PUSHOVER_TOKEN") or cfg.get("pushover_token")
    if not user_key or not api_token:
        return False
    try:
        import urllib.request, urllib.parse
        data = urllib.parse.urlencode({
            "token": api_token, "user": user_key,
            "title": title, "message": message,
            "priority": priority,
        }).encode()
        with urllib.request.urlopen("https://api.pushover.net/1/messages.json", data=data, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"  ⚠️  Pushover failed: {e}")
        return False

def notify_all(title: str, message: str, subtitle: str = "", priority: int = 0):
    """Try every configured channel."""
    print(f"\n📣 SENDING NOTIFICATION")
    print(f"   Title   : {title}")
    print(f"   Message : {message}")

    sent = {
        "macos":    notify_macos(title, message, subtitle),
        "email":    notify_email(title, message),
        "pushover": notify_pushover(title, message, priority),
    }
    for channel, ok in sent.items():
        if ok:
            print(f"   ✅ {channel}")
    if not any(sent.values()):
        print(f"   ⚠️  No notification channels available — only printed to console")
    return sent

if __name__ == "__main__":
    notify_all(
        title="🟢 SPY LEAPS Signal — TEST",
        message="This is a test notification from notify.py",
        subtitle="If you see this, notifications work",
    )
