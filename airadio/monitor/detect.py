"""Two detectors that work on the off-air audio.

AiredDetector — "did Kuyil's segment actually go out?"
    Every 20 ms of audio is summarised as the energy in 4 speech bands (a tiny "fingerprint").
    The first ~8 s of the WAV we delivered gets the same treatment. Sliding one over the other and
    measuring how well the patterns line up (normalised correlation, 1.0 = identical shape) finds the
    segment even after the station's processor has changed its loudness and tone, and the radio link
    has added noise. Music doesn't line up with a speech pattern, so it scores low.

DeadAirDetector — "is the station silent, or gone?"
    Silence: audio quieter than −50 dBFS for 30 s.  Signal lost: no 19 kHz pilot for 30 s
    (an FM receiver hearing no station outputs loud hiss, not silence — so we check the pilot).
"""
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from . import dsp

log = logging.getLogger("airadio.detect")

FRAME_S = 0.02
BANDS_HZ = [(300, 700), (700, 1500), (1500, 3000), (3000, 5000)]


def features(audio: np.ndarray, fs: float) -> np.ndarray:
    """(frames, 4) log band energies, one row per 20 ms."""
    n = int(round(FRAME_S * fs))
    frames = len(audio) // n
    if frames == 0:
        return np.zeros((0, len(BANDS_HZ)))
    x = audio[:frames * n].reshape(frames, n) * np.hanning(n)
    spec = np.abs(np.fft.rfft(x, axis=1)) ** 2
    freqs = np.fft.rfftfreq(n, 1 / fs)
    out = np.stack([spec[:, (freqs >= lo) & (freqs < hi)].sum(1) for lo, hi in BANDS_HZ], axis=1)
    return 10 * np.log10(out + 1e-10)


def reference_features(wav: Path, seconds: float = 8.0) -> np.ndarray:
    from .program import load_audio
    audio = load_audio(wav).mean(axis=1)[: int(seconds * dsp.FS_AUDIO)]
    return features(audio, dsp.FS_AUDIO)


def best_match(buffer: np.ndarray, ref: np.ndarray) -> tuple[float, int]:
    """Normalised correlation of ref against every position in buffer. Returns (score, frame index)."""
    m = len(ref)
    if m < 50 or len(buffer) < m:
        return 0.0, -1
    r = (ref - ref.mean(0)) / (ref.std(0) + 1.0)          # +1 dB floor: near-flat bands can't fake a match
    win = np.lib.stride_tricks.sliding_window_view(buffer, m, axis=0)   # (positions, bands, m)
    w = (win - win.mean(2, keepdims=True)) / (win.std(2, keepdims=True) + 1.0)
    scores = np.einsum("pbm,mb->p", w, r) / (m * len(BANDS_HZ))
    # Normalise by the best possible score for this reference (self-correlation), so 1.0 = perfect.
    self_score = np.sum(r * ((ref - ref.mean(0)) / (ref.std(0) + 1.0))) / (m * len(BANDS_HZ))
    scores = scores / (self_score + 1e-9)
    i = int(np.argmax(scores))
    return float(scores[i]), i


class AiredDetector:
    def __init__(self, buffer_s: float = 60.0, threshold: float = 0.6):
        self.threshold = threshold
        self.frames: deque = deque(maxlen=int(buffer_s / FRAME_S))
        self.times: deque = deque(maxlen=int(buffer_s / FRAME_S))
        self._pending = np.zeros(0)
        self._pending_t: datetime | None = None

    def add_audio(self, t0: datetime, audio: np.ndarray) -> None:
        if self._pending_t is None or len(self._pending) == 0:
            self._pending_t = t0
        buf = np.concatenate([self._pending, audio])
        n = int(round(FRAME_S * dsp.FS_AUDIO))
        f = features(buf, dsp.FS_AUDIO)
        for i, row in enumerate(f):
            self.frames.append(row)
            self.times.append(self._pending_t + timedelta(seconds=i * FRAME_S))
        used = len(f) * n
        self._pending = buf[used:]
        self._pending_t = self._pending_t + timedelta(seconds=used / dsp.FS_AUDIO)

    def find(self, ref: np.ndarray, not_before: datetime | None = None) -> tuple[float, datetime | None]:
        if not self.frames:
            return 0.0, None
        buf = np.array(self.frames)
        times = list(self.times)
        if not_before is not None:
            first = next((i for i, t in enumerate(times) if t >= not_before), len(times))
            buf, times = buf[first:], times[first:]
        score, i = best_match(buf, ref)
        return (score, times[i]) if i >= 0 else (0.0, None)


@dataclass
class DeadAirEvent:
    kind: str          # "silence", "silence_end", "signal_lost", "signal_back"
    since: datetime
    at: datetime


class DeadAirDetector:
    def __init__(self, silence_dbfs: float = -50.0, after_s: float = 30.0):
        self.silence_dbfs = silence_dbfs
        self.after = timedelta(seconds=after_s)
        self.quiet_since: datetime | None = None
        self.lost_since: datetime | None = None
        self.silence_reported = False
        self.lost_reported = False

    def update(self, t: datetime, level_dbfs: float, carrier_ok: bool) -> list[DeadAirEvent]:
        events = []
        # signal lost (no station at all)
        if not carrier_ok:
            self.lost_since = self.lost_since or t
            if not self.lost_reported and t - self.lost_since >= self.after:
                self.lost_reported = True
                events.append(DeadAirEvent("signal_lost", self.lost_since, t))
        else:
            if self.lost_reported:
                events.append(DeadAirEvent("signal_back", self.lost_since, t))
            self.lost_since, self.lost_reported = None, False
        # silence (station there, but nothing playing) — only meaningful while the carrier is ok
        if carrier_ok and level_dbfs < self.silence_dbfs:
            self.quiet_since = self.quiet_since or t
            if not self.silence_reported and t - self.quiet_since >= self.after:
                self.silence_reported = True
                events.append(DeadAirEvent("silence", self.quiet_since, t))
        else:
            if self.silence_reported:
                events.append(DeadAirEvent("silence_end", self.quiet_since, t))
            self.quiet_since, self.silence_reported = None, False
        return events
