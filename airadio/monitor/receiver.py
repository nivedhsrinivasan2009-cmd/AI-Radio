"""A software FM receiver: radio samples in, audio + pilot status + RDS text out.

    IQ @ 1.14 MHz ──low-pass, ÷5──▶ IQ @ 228 kHz ──FM demod──▶ MPX @ 228 kHz
        MPX ──low-pass 14 kHz, ÷5──▶ de-emphasis ──▶ mono audio @ 45.6 kHz
        MPX ──FFT──▶ is the 19 kHz pilot there? (= are we hearing a real stereo station?)
        MPX ──×e^(−j·2π·57k·t), low-pass, ÷24──▶ RDS baseband @ 9.5 kHz ──▶ bits ──▶ groups ──▶ PS / RadioText

FM demodulation: the audio is the *rate of change of the carrier's phase*. Multiplying each sample by
the conjugate of the previous one and taking the angle gives exactly that phase step.
"""
from dataclasses import dataclass

import numpy as np

from . import dsp, rds

# Mixing down by 57 kHz at 228 kHz is multiplying by e^(−jπn/2): just 1, −j, −1, +j repeating.
_MIX57 = np.array([1, -1j, -1, 1j])


@dataclass
class ReceiverOutput:
    audio: np.ndarray        # mono, 45.6 kHz, float
    pilot_snr_db: float      # how clearly the 19 kHz pilot stands out
    level_dbfs: float        # loudness of this block of audio
    carrier_ok: bool         # pilot present -> we're receiving a real FM stereo station


class FMReceiver:
    PILOT_OK_DB = 15.0

    def __init__(self, freq_offset_hz: float = 0.0):
        self.freq_offset = freq_offset_hz
        self.n_iq = 0
        self.n_mpx = 0
        self.chan = dsp.FIRDecimator(dsp.lowpass(81, 100_000, dsp.FS_IQ), 5, dtype=complex)
        self.prev = 1 + 0j
        self.to_audio = dsp.FIRDecimator(dsp.lowpass(201, 14_000, dsp.FS_MPX), 5)
        self.deemph = dsp.deemphasis(dsp.FS_AUDIO)
        self.rds1 = dsp.FIRDecimator(dsp.lowpass(129, 5_000, dsp.FS_MPX), 6, dtype=complex)
        self.rds2 = dsp.FIRDecimator(dsp.lowpass(63, 2_400, dsp.FS_MPX / 6), 4, dtype=complex)
        self.bit_recovery = rds.BiphaseBitRecovery()
        self.rds = rds.RDSDecoder()

    def process(self, iq: np.ndarray) -> ReceiverOutput:
        iq = np.asarray(iq, dtype=complex)
        if self.freq_offset:   # the stick was tuned off-centre to dodge its own DC spike; shift back
            n = self.n_iq + np.arange(len(iq))
            iq = iq * np.exp(-2j * np.pi * self.freq_offset * n / dsp.FS_IQ)
        self.n_iq += len(iq)

        x = self.chan.process(iq)
        both = np.concatenate([[self.prev], x])
        self.prev = x[-1]
        mpx = np.angle(both[1:] * np.conj(both[:-1])) * dsp.FS_MPX / (2 * np.pi * dsp.FM_DEVIATION_HZ)

        pilot_snr = dsp.tone_snr_db(mpx, dsp.FS_MPX, dsp.PILOT_HZ, (16_500, 18_500))
        audio = self.deemph.process(self.to_audio.process(mpx))

        idx = (self.n_mpx + np.arange(len(mpx))) % 4
        self.n_mpx += len(mpx)
        z = self.rds2.process(self.rds1.process(mpx * _MIX57[idx]))
        self.rds.feed(self.bit_recovery.process(z))

        return ReceiverOutput(audio=audio, pilot_snr_db=pilot_snr, level_dbfs=dsp.rms_dbfs(audio),
                              carrier_ok=pilot_snr >= self.PILOT_OK_DB)

    @property
    def rds_state(self) -> rds.RDSState:
        return self.rds.state
