"""The station's schedule (log) and now-playing feed (Part 2).

`read_log_csv` reads the PRETEND station's log format. When the real NaturalLog/AudioVAULT
export format is known, write another reader that returns the same `Timeline`.

Pretend log format (CSV, one row per event, in air order):
    time,type,cart,title,artist,duration
    22:00:00,ID,ID0001,STATION ID LEGAL,,0:10
    22:00:10,AI,AI2201,KUYIL TOP OF HOUR,,0:20
    22:00:30,SONG,M00001,KANNAZHAGA,ANIRUDH,4:12
    22:04:42,AD,AD1001,MALABAR GOLD :30,,0:30
"""
import csv
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("airadio.timeline")

SLOT_TYPES = ("top_of_hour", "song_link", "break_intro", "feature")

_SLOT_KEYWORDS = [("TOP", "top_of_hour"), ("BREAK", "break_intro"), ("FEATURE", "feature"), ("LINK", "song_link")]


def slot_type_from_title(title: str) -> str:
    t = title.upper()
    for kw, slot in _SLOT_KEYWORDS:
        if kw in t:
            return slot
    return "song_link"


def parse_duration(value: str) -> float:
    """'4:12' -> 252.0, '0:30' -> 30.0, '1:02:03' -> 3723.0, '42' -> 42.0"""
    value = (value or "").strip()
    if not value:
        return 0.0
    parts = [float(p) for p in value.split(":")]
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


@dataclass
class LogEvent:
    index: int
    start: datetime
    kind: str            # SONG, AD, ID, AI, or whatever else the log uses
    cart: str
    title: str
    artist: str
    duration_s: float
    slot_type: str | None = None   # only for AI events

    @property
    def end(self) -> datetime:
        return self.start + timedelta(seconds=self.duration_s)

    @property
    def is_ai(self) -> bool:
        return self.kind == "AI"

    @property
    def is_song(self) -> bool:
        return self.kind == "SONG"


@dataclass
class Timeline:
    events: list[LogEvent] = field(default_factory=list)
    source: str = ""

    def ai_slots(self) -> list[LogEvent]:
        return [e for e in self.events if e.is_ai]

    def before(self, ev: LogEvent) -> LogEvent | None:
        return self.events[ev.index - 1] if ev.index > 0 else None

    def after(self, ev: LogEvent) -> LogEvent | None:
        return self.events[ev.index + 1] if ev.index + 1 < len(self.events) else None

    def previous_song(self, ev: LogEvent) -> LogEvent | None:
        """The last song before this slot, as long as no ad break sits in between."""
        for e in reversed(self.events[:ev.index]):
            if e.is_song:
                return e
            if e.kind in ("AD", "ID"):
                return None
        return None

    def next_song(self, ev: LogEvent) -> LogEvent | None:
        """The song that directly follows this slot (nothing else in between)."""
        nxt = self.after(ev)
        return nxt if nxt and nxt.is_song else None

    def find_cart(self, cart: str, near: datetime | None = None) -> LogEvent | None:
        matches = [e for e in self.events if e.cart == cart]
        if not matches:
            return None
        if near is None:
            return matches[0]
        return min(matches, key=lambda e: abs((e.start - near).total_seconds()))

    def at(self, when: datetime) -> LogEvent | None:
        for e in self.events:
            if e.start <= when < e.end:
                return e
        return None

    @property
    def start(self) -> datetime:
        return self.events[0].start

    @property
    def end(self) -> datetime:
        return self.events[-1].end


def read_log_csv(path: Path, log_date: date | None = None) -> Timeline:
    """Read the pretend-station log. Times that go backwards roll over to the next day."""
    path = Path(path)
    if log_date is None:
        try:
            log_date = datetime.strptime(path.stem.replace("log_", ""), "%Y-%m-%d").date()
        except ValueError:
            log_date = date.today()
    events: list[LogEvent] = []
    day_offset = 0
    prev_time = None
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("time"):
                continue
            t = datetime.strptime(row["time"], "%H:%M:%S").time()
            if prev_time is not None and t < prev_time:
                day_offset += 1
            prev_time = t
            start = datetime.combine(log_date + timedelta(days=day_offset), t)
            kind = row.get("type", "").upper()
            title = row.get("title", "")
            events.append(LogEvent(
                index=len(events), start=start, kind=kind, cart=row.get("cart", ""),
                title=title, artist=row.get("artist", ""),
                duration_s=parse_duration(row.get("duration", "")),
                slot_type=(row.get("slot_type") or slot_type_from_title(title)) if kind == "AI" else None,
            ))
    if not events:
        raise ValueError(f"log {path} has no events")
    log.info("loaded log %s: %d events, %d AI slots, %s → %s", path.name, len(events),
             sum(e.is_ai for e in events), events[0].start.strftime("%H:%M"), events[-1].end.strftime("%H:%M"))
    return Timeline(events, str(path))


# ---------------- now-playing ----------------

@dataclass
class NowPlayingInfo:
    cart: str
    started_at: datetime


class LogOnlyNowPlaying:
    """No feed from the automation system: timing comes from the log alone."""
    def current(self) -> NowPlayingInfo | None:
        return None


class FileNowPlaying:
    """Reads a now-playing file written by the automation system.

    Placeholder until the station engineer confirms AudioVAULT's real output. Accepts either
    JSON {"cart": "...", "started": "ISO time"} or a plain line whose first token is the cart.
    Without a start time, the file's modified time is used.
    """
    def __init__(self, path: str):
        self.path = Path(path)

    def current(self) -> NowPlayingInfo | None:
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
            mtime = datetime.fromtimestamp(os.path.getmtime(self.path))
        except OSError:
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            cart = str(data["cart"]).strip()
            started = datetime.fromisoformat(data["started"]) if data.get("started") else mtime
        except (ValueError, KeyError, TypeError):
            cart = raw.split()[0].split("|")[0].strip()
            started = mtime
        return NowPlayingInfo(cart, started) if cart else None


def get_now_playing(path: str = ""):
    return FileNowPlaying(path) if path else LogOnlyNowPlaying()
