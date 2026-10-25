"""A simulated FM broadcast transmitter, so the receiver can be tested without an SDR stick.

It builds the signal exactly the way a real station's stereo encoder + exciter does:

    MPX(t) = 0.45·(L+R)                      mono audio, 30 Hz – 15 kHz (what mono radios play)
           + 0.09·cos(ωp t)                   19 kHz pilot tone ("this is stereo")
           + 0.45·(L−R)·cos(2ωp t)            stereo difference on a 38 kHz subcarrier
           + 0.04·rds(t)·cos(3ωp t)           RDS data on a 57 kHz subcarrier

then frequency-modulates a carrier with it: the carrier's frequency swings ±75 kHz × MPX.
"""
import numpy as np

from . import dsp, rds

PILOT_LEVEL = 0.09
RDS_LEVEL = 0.04
AUDIO_LEVEL = 0.45

# One period of each carrier at 228 kHz is exactly 12 samples (228000 / 19000).
_N12 = np.arange(12)
PILOT_TABLE = np.cos(2 * np.pi * _N12 / 12)
SUB38_TABLE = np.cos(2 * 2 * np.pi * _N12 / 12)
SUB57_TABLE = np.cos(3 * 2 * np.pi * _N12 / 12)


class FMTransmitter:
    def __init__(self, *, ps: str = "TAMIL FM", rt: str = "89.4 Tamil FM - Namma Radio Namma Music",
                 pi: int = 0x2894, snr_db: float = 35.0, seed: int = 1):
        self.interp_l = dsp.FIRInterpolator(dsp.lowpass(101, 15_000, dsp.FS_MPX), 5)
        self.interp_r = dsp.FIRInterpolator(dsp.lowpass(101, 15_000, dsp.FS_MPX), 5)
        self.pre_l, self.pre_r = dsp.preemphasis(dsp.FS_MPX), dsp.preemphasis(dsp.FS_MPX)
        self.interp_iq = dsp.FIRInterpolator(dsp.lowpass(41, 100_000, dsp.FS_IQ), 5)
        self.rds_bits = rds.RDSBitSource(pi, ps, rt)
        self.rds_shape = dsp.FIRDecimator(dsp.lowpass(255, 2_400, dsp.FS_MPX), 1)
        self.rds_e = 0
        self.rds_carry = np.zeros(0)
        self.n = 0                      # MPX sample counter (keeps carriers phase-continuous)
        self.phase = 0.0
        self.noise_std = 10 ** (-snr_db / 20) / np.sqrt(2)
        self.rng = np.random.default_rng(seed)

    def _rds_baseband(self, n: int) -> np.ndarray:
        spb = dsp.FS_MPX / rds.BITRATE              # exactly 192 samples per bit
        need = n - len(self.rds_carry)
        n_bits = int(np.ceil(need / spb))
        wave, self.rds_e = rds.biphase_waveform(self.rds_bits.take(n_bits), self.rds_e, int(spb))
        wave = np.concatenate([self.rds_carry, wave])
        self.rds_carry = wave[n:]
        return self.rds_shape.process(wave[:n])     # smooth the square edges (keeps RDS narrow)

    def mpx(self, stereo_audio: np.ndarray, *, pilot: bool = True) -> np.ndarray:
        """stereo_audio: (n, 2) floats in [-1, 1] at 45.6 kHz -> MPX at 228 kHz."""
        L = np.clip(self.pre_l.process(self.interp_l.process(stereo_audio[:, 0])), -1.5, 1.5)
        R = np.clip(self.pre_r.process(self.interp_r.process(stereo_audio[:, 1])), -1.5, 1.5)
        idx = (self.n + np.arange(len(L))) % 12
        self.n += len(L)
        out = AUDIO_LEVEL * (L + R) + AUDIO_LEVEL * (L - R) * SUB38_TABLE[idx]
        if pilot:
            out += PILOT_LEVEL * PILOT_TABLE[idx] + RDS_LEVEL * self._rds_baseband(len(L)) * SUB57_TABLE[idx]
        return np.clip(out, -1.1, 1.1)

    def iq(self, stereo_audio: np.ndarray, *, carrier: bool = True, pilot: bool = True) -> np.ndarray:
        """stereo_audio at 45.6 kHz -> complex radio samples at 1.14 MHz (what an SDR stick delivers)."""
        m = self.interp_iq.process(self.mpx(stereo_audio, pilot=pilot))
        noise = self.noise_std * (self.rng.standard_normal(len(m)) + 1j * self.rng.standard_normal(len(m)))
        if not carrier:                     # station off air: the receiver hears only noise
            return (noise * 10).astype(np.complex64)
        ph = self.phase + np.cumsum(2 * np.pi * dsp.FM_DEVIATION_HZ * m / dsp.FS_IQ)
        self.phase = float(ph[-1] % (2 * np.pi))
        return (np.exp(1j * ph) + noise).astype(np.complex64)
