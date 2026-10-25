"""The off-air monitor: a background thread that listens to the station and reports.

Every 0.25 s block: receive → dead-air check → add audio to the fingerprint buffer → (optionally) record.
Every 2 s: look for each Kuyil segment that is due around now; when one is found, report it as aired.
A segment that is due but never heard within 3 minutes is reported as missing.
"""
import logging
import threading
import wave
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np

from ..listeners.notify import Alerter
from . import dsp
from .detect import AiredDetector, DeadAirDetector, reference_features
from .receiver import FMReceiver

log = logging.getLogger("airadio.monitor")

CHECK_EVERY_S = 2.0
MISSING_AFTER = timedelta(minutes=3)


@dataclass
class ExpectedClip:
    key: str                 # unique per slot per night, e.g. "AI2202|2026-10-21T22:09:30"
    cart: str
    expected_at: datetime
    wav: Path                # the exact file that was delivered into the cart
    info: dict = field(default_factory=dict)


class OffAirMonitor:
    def __init__(self, source, *, expected: Callable[[datetime], list[ExpectedClip]] = lambda now: [],
                 on_aired: Callable[[ExpectedClip, datetime, float], None] | None = None,
                 on_missing: Callable[[ExpectedClip], None] | None = None,
                 alerter: Alerter | None = None, record_path: Path | None = None,
                 status_every_s: float = 60.0, threshold: float = 0.6):
        self.source = source
        self.rx = FMReceiver(freq_offset_hz=getattr(source, "freq_offset_hz", 0.0))
        self.aired = AiredDetector(buffer_s=90, threshold=threshold)
        self.deadair = DeadAirDetector()
        self.expected = expected
        self.on_aired = on_aired
        self.on_missing = on_missing
        self.alerter = alerter or Alerter()
        self.record_path = Path(record_path) if record_path else None
        self.status_every = timedelta(seconds=status_every_s)
        self.confirmed: dict[str, tuple[datetime, float]] = {}
        self.missing: set[str] = set()
        self._refs: dict[str, np.ndarray] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last = None                     # latest ReceiverOutput, for status
        self.stream_time: datetime | None = None
        self._last_ps = self._last_rt = ""
        self.error: Exception | None = None

    # ------------------------------------------------------------------ thread control
    def start(self) -> "OffAirMonitor":
        self._thread = threading.Thread(target=self.run, name="offair-monitor", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------ main loop
    def run(self, max_seconds: float | None = None) -> None:
        wav_out = self._open_recording()
        next_check = next_status = None
        started = None
        try:
            while not self._stop.is_set():
                try:
                    t, iq = self.source.read()
                except EOFError:
                    break
                out = self.rx.process(iq)
                self.last, self.stream_time = out, t + timedelta(seconds=dsp.CHUNK_S)
                started = started or t
                for ev in self.deadair.update(t, out.level_dbfs, out.carrier_ok):
                    self._report_deadair(ev)
                self.aired.add_audio(t, out.audio)
                if wav_out:
                    wav_out.writeframes((np.clip(out.audio, -1, 1) * 32767).astype("<i2").tobytes())
                self._report_rds()
                now = self.stream_time
                if next_check is None or now >= next_check:
                    self.check(now)
                    next_check = now + timedelta(seconds=CHECK_EVERY_S)
                if next_status is None or now >= next_status:
                    self._log_status()
                    next_status = now + self.status_every
                if max_seconds is not None and (now - started).total_seconds() >= max_seconds:
                    break
        except Exception as e:  # never let the monitor take the show down
            self.error = e
            log.exception("off-air monitor stopped: %s", e)
            self.alerter.alert("monitor", f"off-air monitor stopped: {e}")
        finally:
            if wav_out:
                wav_out.close()
            try:
                self.source.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ "did it air?"
    def check(self, now: datetime) -> None:
        for clip in self.expected(now):
            if clip.key in self.confirmed or clip.key in self.missing:
                continue
            ref = self._refs.get(clip.key)
            if ref is None:
                try:
                    ref = reference_features(clip.wav)
                except Exception as e:
                    log.warning("can't read %s for matching: %s", clip.wav, e)
                    continue
                self._refs[clip.key] = ref
            score, when = self.aired.find(ref, not_before=clip.expected_at - timedelta(minutes=3))
            if score >= self.aired.threshold and when is not None:
                self.confirmed[clip.key] = (when, score)
                log.info("ON AIR CONFIRMED %s at %s (match %.2f, %+.0fs vs schedule)", clip.cart,
                         when.strftime("%H:%M:%S"), score, (when - clip.expected_at).total_seconds())
                if self.on_aired:
                    self.on_aired(clip, when, score)
            elif now > clip.expected_at + MISSING_AFTER:
                self.missing.add(clip.key)
                log.warning("NOT HEARD on air: %s (due %s, best match %.2f)", clip.cart,
                            clip.expected_at.strftime("%H:%M:%S"), score)
                if self.on_missing:
                    self.on_missing(clip)

    # ------------------------------------------------------------------ reporting
    def _report_deadair(self, ev) -> None:
        if ev.kind == "silence":
            self.alerter.alert("dead-air", f"89.4 has been SILENT since {ev.since:%H:%M:%S} (off-air monitor).")
        elif ev.kind == "signal_lost":
            self.alerter.alert("signal-lost", f"No 89.4 signal since {ev.since:%H:%M:%S} — station off air, "
                                              f"or the SDR stick/antenna has a problem.")
        else:
            log.info("off-air: %s (%s since %s)", ev.kind.replace("_", " "), ev.kind, ev.since.strftime("%H:%M:%S"))

    def _report_rds(self) -> None:
        st = self.rx.rds_state
        if st.ps and st.ps != self._last_ps:
            self._last_ps = st.ps
            log.info("RDS station name: %r (PI %04X)", st.ps, st.pi or 0)
        if st.rt and st.rt != self._last_rt:
            self._last_rt = st.rt
            log.info("RDS text: %r", st.rt)

    def status(self) -> dict:
        st = self.rx.rds_state
        o = self.last
        return {
            "time": self.stream_time.strftime("%H:%M:%S") if self.stream_time else None,
            "signal": (o.carrier_ok if o else None), "pilot_snr_db": round(o.pilot_snr_db, 1) if o else None,
            "audio_dbfs": round(o.level_dbfs, 1) if o else None, "rds_ps": st.ps, "rds_text": st.rt,
            "rds_groups": st.groups_ok, "confirmed": len(self.confirmed), "missing": len(self.missing),
        }

    def _log_status(self) -> None:
        s = self.status()
        log.info("off-air %s: signal %s (pilot %s dB), audio %s dBFS, RDS %r, confirmed %d, missing %d",
                 s["time"], "OK" if s["signal"] else "NONE", s["pilot_snr_db"], s["audio_dbfs"],
                 s["rds_ps"] or "-", s["confirmed"], s["missing"])

    def _open_recording(self):
        if not self.record_path:
            return None
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        w = wave.open(str(self.record_path), "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(dsp.FS_AUDIO)
        log.info("recording off-air audio to %s", self.record_path)
        return w
