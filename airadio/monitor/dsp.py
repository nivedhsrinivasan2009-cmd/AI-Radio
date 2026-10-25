"""Signal-processing building blocks shared by the simulated transmitter and the receiver.

Sample rates (all exact integer ratios, which keeps every stage simple):

    1,140,000 Hz  radio (IQ) samples from the SDR stick
      ÷5  228,000 Hz  "MPX": the FM baseband — mono audio, 19 kHz pilot, stereo, 57 kHz RDS all live here
      ÷5   45,600 Hz  audio we listen to / analyse

228 kHz = 12 × 19 kHz, so the pilot (19 kHz), stereo carrier (38 kHz) and RDS carrier (57 kHz)
repeat every 12 samples — they can be generated from tiny lookup tables with zero drift.
"""
import numpy as np
from scipy.signal import bilinear, firwin, lfilter, lfilter_zi, upfirdn

FS_IQ = 1_140_000
FS_MPX = 228_000
FS_AUDIO = 45_600
CHUNK_S = 0.25                          # every stage works on 0.25 s blocks
CHUNK_IQ = int(FS_IQ * CHUNK_S)         # 285,000
CHUNK_MPX = int(FS_MPX * CHUNK_S)       # 57,000
CHUNK_AUDIO = int(FS_AUDIO * CHUNK_S)   # 11,400

FM_DEVIATION_HZ = 75_000                # broadcast FM: full modulation swings the carrier ±75 kHz
PILOT_HZ = 19_000
EMPHASIS_TAU_S = 50e-6                  # UAE/Europe/India use 50 µs (the US uses 75 µs)


class FIRDecimator:
    """Low-pass filter + keep every M-th sample, carrying state across blocks.

    Uses a polyphase routine (upfirdn) so only the kept outputs are computed.
    Block lengths must be multiples of M.
    """

    def __init__(self, taps: np.ndarray, factor: int, dtype=np.float64):
        self.M = factor
        hist = len(taps) - 1
        hist += (-hist) % factor           # history length rounded up to a multiple of M
        self.h = np.asarray(taps, dtype=np.float64)
        self.hist = np.zeros(hist, dtype=dtype)

    def process(self, x: np.ndarray) -> np.ndarray:
        assert len(x) % self.M == 0, "block length must be a multiple of the decimation factor"
        buf = np.concatenate([self.hist, x])
        y = upfirdn(self.h, buf, up=1, down=self.M)
        start = len(self.hist) // self.M
        out = y[start:start + len(x) // self.M]
        self.hist = buf[len(buf) - len(self.hist):] if len(self.hist) else self.hist
        return out


class FIRInterpolator:
    """Insert M-1 zeros between samples + low-pass filter (the reverse of decimation), with state."""

    def __init__(self, taps: np.ndarray, factor: int, dtype=np.float64):
        self.M = factor
        self.h = np.asarray(taps, dtype=np.float64) * factor   # restore the gain lost to zero-stuffing
        self.n_hist = -(-(len(taps) - 1) // factor)            # input samples of history needed
        self.hist = np.zeros(self.n_hist, dtype=dtype)

    def process(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate([self.hist, x])
        y = upfirdn(self.h, buf, up=self.M, down=1)
        start = self.M * self.n_hist
        out = y[start:start + self.M * len(x)]
        self.hist = buf[len(buf) - self.n_hist:]
        return out


class IIRFilter:
    """A recursive filter that remembers its state between blocks."""

    def __init__(self, b, a):
        self.b, self.a = np.asarray(b), np.asarray(a)
        self.zi = lfilter_zi(self.b, self.a) * 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        y, self.zi = lfilter(self.b, self.a, x, zi=self.zi)
        return y


def preemphasis(fs: float) -> IIRFilter:
    """Treble boost applied before transmission: H(s) = (1 + sτ) / (1 + sτ₂).
    τ₂ just keeps the filter realisable (its corner, ~80 kHz, is far above audio)."""
    tau2 = 1 / (2 * np.pi * 80_000)
    b, a = bilinear([EMPHASIS_TAU_S, 1], [tau2, 1], fs)
    return IIRFilter(b, a)


def deemphasis(fs: float) -> IIRFilter:
    """The receiver's matching treble cut, H(s) = 1 / (1 + sτ). Boost + cut = flat audio, less hiss."""
    b, a = bilinear([1], [EMPHASIS_TAU_S, 1], fs)
    return IIRFilter(b, a)


def lowpass(numtaps: int, cutoff_hz: float, fs: float) -> np.ndarray:
    return firwin(numtaps, cutoff_hz, fs=fs)


def tone_snr_db(x: np.ndarray, fs: float, tone_hz: float, ref_band: tuple[float, float]) -> float:
    """How far a pure tone stands above the noise next to it (used to detect the 19 kHz stereo pilot)."""
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / fs)
    tone = spec[(freqs > tone_hz - 15) & (freqs < tone_hz + 15)].max(initial=0.0)
    noise = np.median(spec[(freqs > ref_band[0]) & (freqs < ref_band[1])]) + 1e-20
    return float(10 * np.log10(tone / noise + 1e-20))


def rms_dbfs(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-9))
