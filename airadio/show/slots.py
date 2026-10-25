"""Slot model (Part 1): four slot types, their length ranges, and cart naming."""
from datetime import datetime

# seconds (min, max). Short is always fine on radio; long gets regenerated/cut.
LENGTH_RANGE = {
    "top_of_hour": (15, 20),
    "song_link": (10, 20),
    "break_intro": (5, 10),
    "feature": (45, 60),
}
LENGTH_TOLERANCE_SEC = 2.0

# Feature slots cycle through the current content mix (5a: no night-specific changes).
FEATURE_CYCLE = ["weather", "news", "traffic", "metals", "entertainment"]


def max_seconds(slot_type: str) -> float:
    return LENGTH_RANGE[slot_type][1] + LENGTH_TOLERANCE_SEC


def cart_name(hour: int, position: int) -> str:
    """Cart numbering by position (Part 1, option 1): AI + HH + slot number, e.g. AI2203."""
    return f"AI{hour:02d}{position:02d}"


def hour_of(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)
