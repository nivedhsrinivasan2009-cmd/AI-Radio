"""Outgoing WhatsApp messages via Twilio (listener replies and alerts to the operator)."""
import logging
import threading
import time

from .. import config

log = logging.getLogger("airadio.notify")

_client = None
_client_lock = threading.Lock()


def _twilio():
    global _client
    if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_WHATSAPP_NUMBER):
        return None
    with _client_lock:
        if _client is None:
            from twilio.rest import Client
            _client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    return _client


def send_whatsapp(to: str, body: str) -> bool:
    client = _twilio()
    if client is None:
        log.info("[whatsapp not configured] to %s: %s", to, body[:200])
        return False
    try:
        client.messages.create(body=body[:1500], from_=config.TWILIO_WHATSAPP_NUMBER, to=to)
        return True
    except Exception as e:
        log.warning("WhatsApp send to %s failed: %s", to, e)
        return False


class Alerter:
    """Serious problems go to the operator's phone (Part 6c); everything is logged regardless."""

    def __init__(self, to: str = "", min_gap_sec: float = 600):
        self.to = to or config.ALERT_WHATSAPP_TO
        self.min_gap = min_gap_sec
        self._last: dict[str, float] = {}
        self.sent: list[str] = []

    def alert(self, key: str, message: str) -> None:
        log.error("ALERT [%s] %s", key, message)
        now = time.time()
        if now - self._last.get(key, 0) < self.min_gap:
            return
        self._last[key] = now
        self.sent.append(message)
        if self.to:
            send_whatsapp(self.to, f"⚠️ AI Radio alert: {message}")
