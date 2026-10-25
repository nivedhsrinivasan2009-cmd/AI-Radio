"""Simulated station playout — plays the station log the way AudioVAULT would.

Songs, ads and IDs come from the project's media folders (any file stands in for any cart);
Kuyil's AI carts are read from the drop folder AT THE MOMENT THEY START, exactly like the real
automation system picks up whatever file is in the cart when it's due. Outside the log's hours it
plays filler songs, like a station's overnight playlist.

This is also the core of the future demo mode (a pretend AudioVAULT you can hear).
"""
import json
import logging
import zlib
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from pydub import AudioSegment

from ..connectors.timeline import LogEvent, Timeline
from . import dsp

log = logging.getLogger("airadio.playout")

AUDIO_EXT = (".mp3", ".wav", ".m4a", ".flac")


def load_audio(path: Path) -> np.ndarray:
    """Any audio file -> (n, 2) float32 at 45.6 kHz."""
    seg = AudioSegment.from_file(path).set_frame_rate(dsp.FS_AUDIO).set_channels(2).set_sample_width(2)
    return np.frombuffer(seg.raw_data, dtype=np.int16).reshape(-1, 2).astype(np.float32) / 32768.0


def _media(folder: Path) -> list[Path]:
    return sorted(p for p in Path(folder).glob("*") if p.suffix.lower() in AUDIO_EXT) if Path(folder).exists() else []


class StationPlayout:
    def __init__(self, timeline: Timeline | None, *, dropbox: Path, songs_dir: Path, ads_dir: Path,
                 ids_dir: Path, nowplaying_path: Path | None = None):
        self.tl = timeline
        self.dropbox = Path(dropbox)
        self.songs, self.ads, self.ids = _media(songs_dir), _media(ads_dir), _media(ids_dir)
        if not self.songs:
            raise FileNotFoundError(f"no audio in {songs_dir} for the simulated station")
        self.nowplaying_path = Path(nowplaying_path) if nowplaying_path else None
        self._cache: OrderedDict = OrderedDict()
        self._current_key = None
        self.silent_until: datetime | None = None   # fault injection for tests: dead air
        self.filler_idx, self.filler_pos = 0, 0

    # ------------------------------------------------------------------ media for an event
    def _file_for(self, ev: LogEvent) -> Path | None:
        if ev.is_ai:
            p = self.dropbox / f"{ev.cart}.wav"
            return p if p.exists() else None
        pool = {"SONG": self.songs, "AD": self.ads or self.songs, "ID": self.ids or self.ads or self.songs}.get(ev.kind)
        if not pool:
            return None
        return pool[zlib.crc32(ev.cart.encode()) % len(pool)]

    def _audio_for(self, key, loader) -> np.ndarray:
        if key not in self._cache:
            self._cache[key] = loader()
            while len(self._cache) > 4:
                self._cache.popitem(last=False)
        return self._cache[key]

    def _event_started(self, ev: LogEvent) -> None:
        if ev.is_ai:
            log.info("on air: %s (%s)", ev.cart, ev.slot_type)
        if self.nowplaying_path:  # behave like an automation system's now-playing output
            try:
                self.nowplaying_path.write_text(json.dumps({"cart": ev.cart, "started": ev.start.isoformat()}),
                                                encoding="utf-8")
            except OSError:
                pass

    # ------------------------------------------------------------------ audio for any moment
    def render(self, t0: datetime, n: int) -> np.ndarray:
        """Return n stereo samples (45.6 kHz) of what the station plays starting at t0."""
        out = np.zeros((n, 2), dtype=np.float32)
        i = 0
        while i < n:
            t = t0 + timedelta(seconds=i / dsp.FS_AUDIO)
            if self.silent_until and t < self.silent_until:
                i += min(n - i, int((self.silent_until - t).total_seconds() * dsp.FS_AUDIO) + 1)
                continue
            ev = self.tl.at(t) if self.tl else None
            if ev is None:
                out[i:] = self._filler(n - i)
                break
            key = (ev.index, ev.start)
            if key != self._current_key:
                self._current_key = key
                self._event_started(ev)
            path = self._file_for(ev)
            # cache AI carts per event start: the file is read once, when the cart begins
            audio = self._audio_for(key if ev.is_ai else path, (lambda p=path: load_audio(p))) if path else None
            pos = int((t - ev.start).total_seconds() * dsp.FS_AUDIO)
            remaining_in_event = int((ev.end - t).total_seconds() * dsp.FS_AUDIO) + 1
            take = min(n - i, remaining_in_event)
            if audio is not None and len(audio) and not ev.is_ai:
                # Stand-in media (e.g. 30 s song previews) is looped to fill its slot, like a full-length cart.
                pos %= len(audio)
                take = min(take, len(audio) - pos)
            if audio is not None and pos < len(audio):
                k = min(take, len(audio) - pos)
                out[i:i + k] = audio[pos:pos + k]
            i += max(take, 1)
        return out

    def _filler(self, n: int) -> np.ndarray:
        """Continuous playlist of the songs folder, for times outside the log."""
        out = np.zeros((n, 2), dtype=np.float32)
        i = 0
        while i < n:
            idx = self.filler_idx % len(self.songs)
            song = self._audio_for(("filler", idx), lambda p=self.songs[idx]: load_audio(p))
            k = min(n - i, len(song) - self.filler_pos)
            out[i:i + k] = song[self.filler_pos:self.filler_pos + k]
            i += k
            self.filler_pos += k
            if self.filler_pos >= len(song):
                self.filler_idx, self.filler_pos = self.filler_idx + 1, 0
        return out
