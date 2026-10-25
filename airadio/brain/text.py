"""Tamil text helpers shared by the brain, the voice and the WhatsApp formatters."""
import re
from datetime import datetime

from .. import station

# Keep only Tamil + ASCII (brand names, code-mix), drop Arabic, Devanagari, emoji etc.
_ALLOWED_CHARS_RE = re.compile(r"[^஀-௿\u0000-\u007F\s.,;:!?()\-–—/'\"]+")
_TA_RANGE = re.compile(r"[஀-௿]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?।])\s+|\n+")
_STAGE_DIRECTIONS = re.compile(r"[\[\(\*][^\]\)\*]{0,40}[\]\)\*]")

# Natural Tamil-English code-mix: ~35% Tamil characters is the floor (see PROJECT.md §7).
TAMIL_RATIO_FLOOR = 0.35


def strip_non_ta_en(text: str) -> str:
    if not isinstance(text, str):
        return ""
    cleaned = _ALLOWED_CHARS_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def tamil_ratio(text: str) -> float:
    if not text:
        return 0.0
    return len(_TA_RANGE.findall(text)) / max(1, len(text))


def split_sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENTENCE_SPLIT.split(text.strip()) if p and p.strip()]


def dedupe_sentences(text: str) -> str:
    seen, out = set(), []
    for p in split_sentences(text):
        if p not in seen:
            seen.add(p)
            out.append(p)
    return " ".join(out)


def clean_script(text: str) -> str:
    """Turn raw model output into a speakable on-air script."""
    if not isinstance(text, str):
        return ""
    t = text.strip()
    # Models sometimes wrap the line in quotes or prefix it with a label.
    t = re.sub(r"^(on-air line|kuyil|output|line)\s*[:：-]\s*", "", t, flags=re.IGNORECASE)
    t = t.strip().strip('"“”').strip()
    t = _STAGE_DIRECTIONS.sub("", t)
    t = strip_non_ta_en(t)
    t = dedupe_sentences(t)
    return re.sub(r"\s{2,}", " ", t).strip()


def ensure_slogan(text: str) -> str:
    tail = text.strip()
    if tail.rstrip(".").endswith(station.SLOGAN_TA):
        return tail
    return f"{tail} {station.SLOGAN_TA}"


def tod_word(dt: datetime) -> str:
    h = dt.hour
    if 5 <= h < 12:
        return "காலை"
    if 12 <= h < 16:
        return "மதியம்"
    if 16 <= h < 19:
        return "மாலை"
    return "இரவு"


def greeting_ta(dt: datetime) -> str:
    return {"காலை": "காலை வணக்கம்", "மதியம்": "மதிய வணக்கம்",
            "மாலை": "மாலை வணக்கம்", "இரவு": "இனிய இரவு வணக்கம்"}[tod_word(dt)]


def greeting_en(dt: datetime) -> str:
    h = dt.hour
    if 5 <= h < 12:
        return "Good morning"
    if 12 <= h < 17:
        return "Good afternoon"
    return "Good evening"


def spoken_hour_ta(dt: datetime) -> str:
    """'இரவு 10 மணி', 'நள்ளிரவு 12 மணி' — how an RJ says the hour."""
    h12 = dt.hour % 12 or 12
    if dt.hour == 0:
        return "நள்ளிரவு 12 மணி"
    return f"{tod_word(dt)} {h12} மணி"


def spoken_time_ta(dt: datetime) -> str:
    base = spoken_hour_ta(dt)
    if dt.minute == 0:
        return base
    return f"{base} {dt.minute} நிமிடம்"
