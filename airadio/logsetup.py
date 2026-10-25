"""Logging to console + a daily file in output/logs (Part 6: log everything)."""
import logging
import sys
from datetime import datetime

from . import config

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    config.APP_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = config.APP_LOG_DIR / f"airadio_{datetime.now():%Y-%m-%d}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    root = logging.getLogger()
    root.setLevel(level)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Windows consoles default to cp1252; Tamil text would crash a plain StreamHandler.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # Quiet chatty libraries.
    for noisy in ("urllib3", "werkzeug", "twilio.http_client", "httpx", "gtts"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
