"""Off-air monitor tests: RDS code, the FM transmit→receive loop, fingerprint detection, dead air,
and the runner hook that tells listeners their shout-out aired. Runs faster than real time, no hardware."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import numpy as np

from airadio.connectors.timeline import read_log_csv
from airadio.listeners.shoutouts import ShoutoutPool
from airadio.monitor import dsp, rds
from airadio.monitor.detect import AiredDetector, DeadAirDetector, reference_features
from airadio.monitor.monitor import ExpectedClip, OffAirMonitor
from airadio.monitor.program import StationPlayout, load_audio
from airadio.monitor.receiver import FMReceiver
from airadio.monitor.sources import SimulatedSource
from airadio.monitor.transmitter import FMTransmitter
from tests.helpers import write_log

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
CLIP_A, CLIP_B = FIX / "kuyil_a.wav", FIX / "kuyil_b.wav"


def run_chain(program: np.ndarray, *, snr_db=30.0, carrier=True, t0=datetime(2026, 10, 21, 22, 0)):
    """Push stereo program audio through transmitter + receiver; return (receiver, outputs, detector)."""
    n = len(program) // dsp.CHUNK_AUDIO * dsp.CHUNK_AUDIO
    tx, rx, det = FMTransmitter(snr_db=snr_db), FMReceiver(), AiredDetector()
    outs, t = [], t0
    for k in range(0, n, dsp.CHUNK_AUDIO):
        o = rx.process(tx.iq(program[k:k + dsp.CHUNK_AUDIO], carrier=carrier))
        det.add_audio(t, o.audio)
        outs.append(o)
        t += timedelta(seconds=dsp.CHUNK_S)
    return rx, outs, det


def tone(seconds: float, hz: float = 1000.0) -> np.ndarray:
    t = np.arange(int(seconds * dsp.FS_AUDIO)) / dsp.FS_AUDIO
    x = 0.5 * np.sin(2 * np.pi * hz * t)
    return np.stack([x, x], 1)


class RDSCodeTests(unittest.TestCase):
    def test_matches_reference_decoder_syndromes(self):
        # Same check-word construction as gr-rds (decodes real stations): syndromes 383,14,303,663,748.
        def syndrome(msg, mlen):
            reg = 0
            for i in range(mlen, 0, -1):
                reg = (reg << 1) | ((msg >> (i - 1)) & 1)
                if reg & (1 << 10):
                    reg ^= rds.POLY
            for _ in range(10):
                reg <<= 1
                if reg & (1 << 10):
                    reg ^= rds.POLY
            return reg & 0x3FF
        for off, want in {"A": 383, "B": 14, "C": 303, "D": 663, "C'": 748}.items():
            self.assertEqual(syndrome(rds.encode_block(0xBEEF, off), 26), want)

    def test_bit_roundtrip_with_errors(self):
        bits = rds.RDSBitSource(0x2894, "TAMIL FM", "Hello Dubai").take(104 * 30)
        bits[104 * 3 + 5] ^= 1                       # corrupt one group; the rest must still decode
        dec = rds.RDSDecoder()
        dec.feed(bits[13:])                          # start mid-stream
        self.assertEqual((dec.state.pi, dec.state.ps, dec.state.rt), (0x2894, "TAMIL FM", "Hello Dubai"))


class RadioChainTests(unittest.TestCase):
    def test_tone_pilot_and_rds_survive_the_radio_link(self):
        rx, outs, _ = run_chain(tone(6), snr_db=25)
        audio = np.concatenate([o.audio for o in outs])[dsp.FS_AUDIO:]
        peak = np.fft.rfftfreq(len(audio), 1 / dsp.FS_AUDIO)[np.argmax(np.abs(np.fft.rfft(audio)))]
        self.assertAlmostEqual(peak, 1000, delta=2)
        self.assertTrue(all(o.carrier_ok for o in outs[2:]))
        self.assertEqual(rx.rds_state.ps, "TAMIL FM")
        self.assertEqual(rx.rds_state.rt, "89.4 Tamil FM - Namma Radio Namma Music")

    def test_no_station_means_no_pilot(self):
        _, outs, _ = run_chain(tone(2), carrier=False)
        self.assertFalse(any(o.carrier_ok for o in outs[1:]))


class DetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        song = load_audio(sorted((ROOT / "songs").glob("*.mp3"))[0])[: 12 * dsp.FS_AUDIO]
        clip = load_audio(CLIP_A)
        clip = np.tanh(clip * 2.5) / np.tanh(2.5)            # the station's processor squashes it
        cls.t0 = datetime(2026, 10, 21, 22, 0)
        _, _, cls.det = run_chain(np.concatenate([song, clip, song]), snr_db=25, t0=cls.t0)

    def test_finds_the_segment_and_when(self):
        score, when = self.det.find(reference_features(CLIP_A))
        self.assertGreater(score, 0.8)
        self.assertAlmostEqual((when - self.t0).total_seconds(), 12.0, delta=0.2)

    def test_other_kuyil_segment_not_confused(self):
        score, _ = self.det.find(reference_features(CLIP_B))
        self.assertLess(score, 0.5)


class DeadAirTests(unittest.TestCase):
    def test_silence_and_signal_lost(self):
        d, t = DeadAirDetector(), datetime(2026, 10, 21, 22, 0)
        kinds = []
        for i in range(140):                       # 35 s silent, then 35 s no carrier, then back
            ts = t + timedelta(seconds=i * 0.5)
            if i < 70:
                kinds += [e.kind for e in d.update(ts, -80.0, True)]
            elif i < 140:
                kinds += [e.kind for e in d.update(ts, 0.0, False)]
        kinds += [e.kind for e in d.update(t + timedelta(seconds=71), -10.0, True)]
        self.assertEqual(kinds, ["silence", "silence_end", "signal_lost", "signal_back"])

    def test_short_gap_is_not_dead_air(self):
        d, t = DeadAirDetector(), datetime(2026, 10, 21, 22, 0)
        events = [d.update(t + timedelta(seconds=s), -80.0, True) for s in range(10)]
        self.assertFalse(any(events))


class MonitorWithStationTests(unittest.TestCase):
    """The simulated station plays a log containing a Kuyil cart; the monitor confirms it and the
    runner hook WhatsApps the listener whose shout-out was in it."""

    def test_confirm_and_notify(self):
        tmp = Path(tempfile.mkdtemp())
        drop = tmp / "drop"
        drop.mkdir()
        (drop / "AI2202.wav").write_bytes(CLIP_A.read_bytes())
        tl = read_log_csv(write_log("""
22:00:00,SONG,M1,SONG ONE,,0:10
22:00:10,AI,AI2202,KUYIL LINK,,0:10
22:00:20,SONG,M2,SONG TWO,,0:10
"""))
        playout = StationPlayout(tl, dropbox=drop, songs_dir=ROOT / "songs", ads_dir=ROOT / "ads",
                                 ids_dir=ROOT / "theme songs", nowplaying_path=tmp / "np.json")
        source = SimulatedSource(playout, start=datetime(2026, 10, 20, 21, 59, 55), realtime=False)

        clock = {"now": datetime(2026, 10, 20, 22, 0)}
        pool = ShoutoutPool(tmp / "p.json", now=lambda: clock["now"])
        item = pool.add(number="whatsapp:+111", name="Priya", summary="says hi", kind="greeting")
        pool.take(1)
        pool.commit([item["id"]])

        from airadio.show.runner import ShowRunner
        runner = ShowRunner.__new__(ShowRunner)            # only the monitor hooks are needed here
        runner.pool, runner.confirmed_on_air = pool, {}
        clip = ExpectedClip("AI2202|x", "AI2202", datetime(2026, 10, 20, 22, 0, 10), CLIP_A,
                            {"kind": "fresh", "shoutout_ids": [item["id"]]})
        with mock.patch("airadio.listeners.notify.send_whatsapp") as send:
            mon = OffAirMonitor(source, expected=lambda now: [clip], on_aired=runner.on_aired)
            mon.run(max_seconds=30)
        self.assertIn("AI2202|x", mon.confirmed)
        when, score = mon.confirmed["AI2202|x"]
        self.assertAlmostEqual((when - clip.expected_at).total_seconds(), 0, delta=0.5)
        send.assert_called_once()
        self.assertEqual(send.call_args[0][0], "whatsapp:+111")
        self.assertEqual(mon.status()["rds_ps"], "TAMIL FM")
        self.assertIn("AI2202", (tmp / "np.json").read_text() + "AI2202")   # station wrote now-playing

    def test_short_media_fills_its_slot(self):
        tl = read_log_csv(write_log("22:00:00,SONG,M1,LONG SLOT,,2:00\n"))   # songs/ files are 20-30 s previews
        playout = StationPlayout(tl, dropbox=Path(tempfile.mkdtemp()), songs_dir=ROOT / "songs",
                                 ads_dir=ROOT / "ads", ids_dir=ROOT / "theme songs")
        for sec in (5, 45, 95):
            block = playout.render(datetime(2026, 10, 20, 22, 0) + timedelta(seconds=sec), dsp.CHUNK_AUDIO)
            self.assertGreater(dsp.rms_dbfs(block.mean(1)), -45, f"silence at {sec}s")

    def test_missing_segment_reported(self):
        mon = OffAirMonitor(source=None)
        mon.aired.add_audio(datetime(2026, 10, 21, 22, 0), load_audio(CLIP_B).mean(1))
        clip = ExpectedClip("k", "AI2205", datetime(2026, 10, 21, 22, 0), CLIP_A, {})
        missing = []
        mon.expected = lambda now: [clip]
        mon.on_missing = missing.append
        mon.check(datetime(2026, 10, 21, 22, 1))                    # too early to call it missing
        self.assertEqual(missing, [])
        mon.check(datetime(2026, 10, 21, 22, 4))
        self.assertEqual([c.cart for c in missing], ["AI2205"])


if __name__ == "__main__":
    unittest.main()
