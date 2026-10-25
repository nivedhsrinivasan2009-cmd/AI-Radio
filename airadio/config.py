"""All runtime settings, read from environment variables (.env).

No secret has a hard-coded fallback here: a missing key simply disables that feature
(feeds fall back to other content, WhatsApp sends are logged instead of sent).
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _path(env_name: str, default: str) -> Path:
    p = Path(os.getenv(env_name, default))
    return p if p.is_absolute() else PROJECT_ROOT / p


def _float(env_name: str, default: float) -> float:
    try:
        return float(os.getenv(env_name, default))
    except ValueError:
        return default


# --- Secrets / external services (empty string = feature disabled) ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "")
ALERT_WHATSAPP_TO = os.getenv("ALERT_WHATSAPP_TO", "")  # your phone, e.g. whatsapp:+9715XXXXXXXX
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
TOMTOM_API_KEY = os.getenv("TOMTOM_API_KEY", "")
METALPRICE_API_KEY = os.getenv("METALPRICE_API_KEY", "")

# --- AI model (Ollama, local) ---
LLM_MODEL = os.getenv("LLM_MODEL", "gemma3:12b")
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "qwen2.5:7b-instruct")
LLM_TIMEOUT_SEC = _float("LLM_TIMEOUT_SEC", 90)

# --- Station files (the "pretend station" until the real engineer details arrive) ---
STATION_DIR = _path("STATION_DIR", "station_data")
STATION_LOG_DIR = _path("STATION_LOG_DIR", "station_data/logs")      # log_YYYY-MM-DD.csv
SONG_TABLE_PATH = _path("SONG_TABLE_PATH", "station_data/songs.csv")
DROPBOX_DIR = _path("DROPBOX_DIR", "station_data/audiovault_dropbox")  # stand-in for AudioVAULT import
NOWPLAYING_FILE = os.getenv("NOWPLAYING_FILE", "")                    # empty = log-only timing
SAFE_DEFAULT_PATH = _path("SAFE_DEFAULT_PATH", "station_data/safe_default.wav")
ARCHIVE_DIR = _path("ARCHIVE_DIR", "output/archive")
APP_LOG_DIR = _path("APP_LOG_DIR", "output/logs")
USERS_FILE = _path("USERS_FILE", "users.json")
SHOUTOUT_FILE = _path("SHOUTOUT_FILE", "output/shoutouts.json")

# --- Audio output (broadcast defaults; change only if the station asks) ---
AUDIO_SAMPLE_RATE = int(_float("AUDIO_SAMPLE_RATE", 48000))
AUDIO_CHANNELS = int(_float("AUDIO_CHANNELS", 2))
AUDIO_SAMPLE_WIDTH = 2  # bytes -> 16-bit
TARGET_LUFS = _float("TARGET_LUFS", -16.0)     # match the station's files once measured
PEAK_CEILING_DBFS = _float("PEAK_CEILING_DBFS", -1.0)

# --- Show timing (Part 3) ---
DRAFT_LEAD_MIN = _float("DRAFT_LEAD_MIN", 20)       # drafts rendered this long before the first slot
FRESH_MIN_LEAD_SEC = _float("FRESH_MIN_LEAD_SEC", 180)  # fresh render starts with >= this much audio left
FRESH_FALLBACK_LEAD_SEC = _float("FRESH_FALLBACK_LEAD_SEC", 240)
LOCK_BEFORE_AIR_SEC = _float("LOCK_BEFORE_AIR_SEC", 60)
NO_NEXT_SONG_LAST_MIN = _float("NO_NEXT_SONG_LAST_MIN", 10)  # Part 2 caution window before top of hour

# --- WhatsApp shout-outs (Part 5b) ---
SHOUTOUT_EXPIRY_MIN = _float("SHOUTOUT_EXPIRY_MIN", 30)
SHOUTOUT_PER_LISTENER_PER_HOUR = int(_float("SHOUTOUT_PER_LISTENER_PER_HOUR", 1))
SHOUTOUTS_PER_LINK = int(_float("SHOUTOUTS_PER_LINK", 1))
SHOUTOUTS_PER_FEATURE = int(_float("SHOUTOUTS_PER_FEATURE", 3))
REQUEST_COOLDOWN_MIN = _float("REQUEST_COOLDOWN_MIN", 30)

# --- Web server ---
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(_float("WEBHOOK_PORT", 5000))

# --- Feeds ---
WEATHER_LAT = _float("WEATHER_LAT", 25.2048)   # Dubai
WEATHER_LON = _float("WEATHER_LON", 55.2708)
TRAFFIC_BBOX = os.getenv("TRAFFIC_BBOX", "54.90,24.85,55.70,25.45")  # Dubai
FEED_CACHE_SEC = _float("FEED_CACHE_SEC", 120)
FEED_TIMEOUT_SEC = _float("FEED_TIMEOUT_SEC", 8)
