"""Optional notification backend for t9-ipc.

Telegram is the default backend. Set IPC_TG_TOKEN and IPC_TG_CHAT
environment variables to enable. If not configured, notifications
are silently skipped.
"""
import urllib.request
import urllib.parse

from lib.config import TG_TOKEN, TG_CHAT


def tg_send(text: str, chat_id: str = "") -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    chat_id = chat_id or TG_CHAT
    if not TG_TOKEN or not chat_id:
        return False
    api = f"https://api.telegram.org/bot{TG_TOKEN}"
    for i in range(0, max(len(text), 1), 4000):
        chunk = text[i:i + 4000]
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": chunk}).encode()
        try:
            urllib.request.urlopen(f"{api}/sendMessage", data, timeout=10)
        except Exception:
            return False
    return True


def notify_escalation(subject: str, body: str = "") -> bool:
    """Send an escalation alert via Telegram."""
    return tg_send(f"[ESCALATION] {subject}\n{body[:200]}")
