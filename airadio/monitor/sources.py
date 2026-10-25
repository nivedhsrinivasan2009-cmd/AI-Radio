"""Where the radio signal comes from. Every source hands out 0.25 s blocks of IQ samples at 1.14 MHz,
each stamped with the wall-clock time its first sample was received.

    RtlSdrSource     a real RTL-SDR USB stick tuned to the station (needs: pip install pyrtlsdr pyrtlsdrlib,
                     and the WinUSB driver installed with Zadig — see STATION_MODE.md)
    FileSource       a recording (rtl_sdr .cu8 raw file, or .npy of complex samples)
    SimulatedSource  the pretend station played through the simulated transmitter
"""
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from . import dsp
from .program import StationPlayout
from .transmitter import FMTransmitter

log = logging.getLogger("airadio.sdr")

TUNE_OFFSET_HZ = 250_000   # tune slightly off the station so the stick's DC spike isn't on top of it


class SourceError(RuntimeError):
    pass


class RtlSdrSource:
    freq_offset_hz = TUNE_OFFSET_HZ

    def __init__(self, freq_mhz: float = 89.4, gain="auto", ppm: int = 0):
        try:
            from rtlsdr import RtlSdr
        except ImportError as e:
            raise SourceError("RTL-SDR support not installed: pip install pyrtlsdr pyrtlsdrlib") from e
        try:
            self.sdr = RtlSdr()
        except Exception as e:
            raise SourceError(f"no RTL-SDR stick found ({e}). Plugged in? WinUSB driver installed with Zadig?") from e
        self.sdr.sample_rate = dsp.FS_IQ
        # Tune BELOW the station; the station then sits at +offset in our samples, and the receiver shifts it back.
        self.sdr.center_freq = freq_mhz * 1e6 - self.freq_offset_hz
        if ppm:
            self.sdr.freq_correction = ppm
        self.sdr.gain = gain
        log.info("RTL-SDR tuned to %.1f MHz (centre %.3f MHz, gain %s)", freq_mhz, self.sdr.center_freq / 1e6, gain)

    def read(self) -> tuple[datetime, np.ndarray]:
        iq = self.sdr.read_samples(dsp.CHUNK_IQ)
        return datetime.now() - timedelta(seconds=dsp.CHUNK_S), np.asarray(iq, dtype=np.complex64)

    def close(self) -> None:
        try:
            self.sdr.close()
        except Exception:
            pass


class FileSource:
    freq_offset_hz = 0.0

    def __init__(self, path: Path, start: datetime | None = None, freq_offset_hz: float = 0.0, realtime=False):
        path = Path(path)
        if path.suffix == ".npy":
            self.data = np.load(path).astype(np.complex64)
        else:  # rtl_sdr raw: interleaved unsigned 8-bit I and Q
            raw = np.fromfile(path, dtype=np.uint8).astype(np.float32)
            self.data = ((raw[0::2] - 127.5) + 1j * (raw[1::2] - 127.5)).astype(np.complex64) / 127.5
        self.pos = 0
        self.t = start or datetime.now()
        self.freq_offset_hz = freq_offset_hz
        self.realtime = realtime

    def read(self) -> tuple[datetime, np.ndarray]:
        if self.pos + dsp.CHUNK_IQ > len(self.data):
            raise EOFError
        block = self.data[self.pos:self.pos + dsp.CHUNK_IQ]
        self.pos += dsp.CHUNK_IQ
        t = self.t
        self.t += timedelta(seconds=dsp.CHUNK_S)
        if self.realtime:
            time.sleep(dsp.CHUNK_S)
        return t, block

    def close(self) -> None:
        pass


class SimulatedSource:
    """The pretend station, transmitted by the simulated transmitter, received as if by an SDR stick.

    realtime=True: paced to the wall clock (for live runs). realtime=False: as fast as the CPU allows,
    starting at `start` (for tests). Faults can be injected: silence (dead air) and carrier off.
    """
    freq_offset_hz = 0.0

    def __init__(self, playout: StationPlayout, *, transmitter: FMTransmitter | None = None,
                 start: datetime | None = None, realtime: bool = True):
        self.playout = playout
        self.tx = transmitter or FMTransmitter()
        self.realtime = realtime
        now = datetime.now()
        self.t = start or now.replace(microsecond=(now.microsecond // 250_000) * 250_000)
        self.carrier_off_until: datetime | None = None

    def read(self) -> tuple[datetime, np.ndarray]:
        t = self.t
        if self.realtime:
            wait = (t + timedelta(seconds=dsp.CHUNK_S) - datetime.now()).total_seconds()
            if wait > 0:
                time.sleep(wait)
        audio = self.playout.render(t, dsp.CHUNK_AUDIO)
        carrier = not (self.carrier_off_until and t < self.carrier_off_until)
        iq = self.tx.iq(audio, carrier=carrier)
        self.t = t + timedelta(seconds=dsp.CHUNK_S)
        return t, iq

    def close(self) -> None:
        pass
