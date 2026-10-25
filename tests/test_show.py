"""Show runner + WhatsApp bot tests on a fake clock (no internet, no real AI model)."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from airadio.connectors.delivery import FolderDelivery
from airadio.connectors.songs import SongTable
from airadio.connectors.timeline import NowPlayingInfo, read_log_csv
from airadio.listeners.notify import Alerter
from airadio.listeners.shoutouts import ShoutoutPool
from airadio.listeners.store import UserStore
from airadio.listeners.whatsapp import WhatsAppBot
from airadio.show.runner import ShowRunner
from airadio.voice import audio
from tests.helpers import FakeClock, FakeVoice, fake_llm, no_loudness, write_log
from tests.test_core import SONGS

SHOW = """
22:00:00,ID,ID0001,STATION ID LEGAL,,0:10
22:00:10,AI,AI2201,KUYIL TOP OF HOUR,,0:20
22:00:30,SONG,M1,KANNAZHAGA,ANIRUDH,4:00
22:04:30,SONG,M2,VASEEGARA,HARRIS,5:00
22:09:30,AI,AI2202,KUYIL LINK,,0:15
22:09:45,SONG,M1,KANNAZHAGA,ANIRUDH,4:00
22:13:45,AI,AI2203,KUYIL BREAK,,0:08
22:13:53,AD,AD1,AD :30,,0:30
22:14:23,SONG,M2,VASEEGARA,HARRIS,5:00
"""

FEEDS_DOWN = {"weather": None, "news": None, "traffic": None, "metals": None}


class FakeNowPlaying:
    def __init__(self):
        self.info = None

    def current(self):
        return self.info


def make_runner(clock, voice=None, now_playing=None, pool=None):
    tmp = Path(tempfile.mkdtemp())
    safe = tmp / "safe.wav"
    audio.write_wav(audio.to_broadcast_format(FakeVoice().synthesize("safe clip text here")), safe)
    tl = read_log_csv(write_log(SHOW))
    pool = pool or ShoutoutPool(tmp / "pool.json", now=clock.now)
    runner = ShowRunner(tl, SongTable(SONGS), pool, FolderDelivery(tmp / "drop"), voice or FakeVoice(),
                        now_playing=now_playing, alerter=Alerter(to=""), now=clock.now, sleep=clock.sleep,
                        safe_default=safe)
    return runner, tmp


class RunnerTimingTests(unittest.TestCase):
    def test_fresh_and_lock_times(self):
        runner, _ = make_runner(FakeClock(datetime(2026, 10, 20, 21, 0)))
        link = runner.tl.find_cart("AI2202")        # airs 22:09:30
        # latest item starting >= 180 s before air is the song at 22:04:30
        self.assertEqual(runner.fresh_time(link), datetime(2026, 10, 20, 22, 4, 30))
        self.assertEqual(runner.lock_time(link), datetime(2026, 10, 20, 22, 8, 30))
        top = runner.tl.find_cart("AI2201")          # nothing early enough before it -> fallback lead
        self.assertEqual(runner.fresh_time(top), datetime(2026, 10, 20, 21, 56, 10))
        self.assertEqual(runner.draft_time(), datetime(2026, 10, 20, 21, 40, 10))

    def test_now_playing_drift_shifts_timing(self):
        np = FakeNowPlaying()
        runner, _ = make_runner(FakeClock(datetime(2026, 10, 20, 22, 5)), now_playing=np)
        np.info = NowPlayingInfo("M2", datetime(2026, 10, 20, 22, 5, 30))   # planned 22:04:30 -> 60 s late
        runner._poll_now_playing()
        self.assertEqual(runner.drift, timedelta(seconds=60))
        self.assertEqual(runner.lock_time(runner.tl.find_cart("AI2202")), datetime(2026, 10, 20, 22, 9, 30))


class RunnerShowTests(unittest.TestCase):
    def test_full_show(self):
        clock = FakeClock(datetime(2026, 10, 20, 21, 50))
        runner, tmp = make_runner(clock)
        runner.pool.add(number="whatsapp:+1", name="Priya", summary="says hi to her mother", kind="greeting")
        with fake_llm(["அது கண்ணழகா! அடுத்தது வசீகரா, stay tuned!"]), no_loudness(), \
                mock.patch("airadio.show.planner.get_feeds", return_value=FEEDS_DOWN):
            runner.run()
        drop = tmp / "drop"
        self.assertEqual(sorted(p.name for p in drop.iterdir()), ["AI2201.wav", "AI2202.wav", "AI2203.wav"])
        # after the show every cart holds the safe clip again
        safe = (tmp / "safe.wav").read_bytes()
        for p in drop.iterdir():
            self.assertEqual(p.read_bytes(), safe)
        self.assertEqual(set(runner.status.values()), {"default"})
        # the shout-out went on air in the one slot that can carry it (the song link)
        used = [i for i in runner.pool.items if i["status"] == "used"]
        self.assertEqual(len(used), 1)
        self.assertEqual(runner.consecutive_fresh_failures, 0)

    def test_fresh_replaces_draft_before_lock(self):
        clock = FakeClock(datetime(2026, 10, 20, 21, 30))
        runner, tmp = make_runner(clock)
        delivered = []
        real = runner.delivery.deliver
        runner.delivery.deliver = lambda cart, f: (delivered.append((cart, f.name, clock.now())), real(cart, f))[1]
        with fake_llm(["அது கண்ணழகா! அடுத்தது வசீகரா, stay tuned!"]), no_loudness(), \
                mock.patch("airadio.show.planner.get_feeds", return_value=FEEDS_DOWN):
            runner.run()
        for cart in ("AI2201", "AI2202", "AI2203"):
            kinds = [n.split("_")[1] for c, n, _ in delivered if c == cart and n.startswith("AI")]
            self.assertEqual(kinds, ["draft", "fresh"], cart)
            slot = runner.tl.find_cart(cart)
            for c, n, when in delivered:
                if c == cart and "fresh" in n:
                    self.assertLess(when, slot.start - timedelta(seconds=60))   # never inside the lock window

    def test_tts_down_alerts_and_keeps_safe_clip(self):
        clock = FakeClock(datetime(2026, 10, 20, 21, 50))
        runner, tmp = make_runner(clock, voice=FakeVoice(fail=True))
        runner.pool.add(number="whatsapp:+1", name="Priya", summary="says hi", kind="greeting")
        with fake_llm(), no_loudness(), mock.patch("airadio.show.planner.get_feeds", return_value=FEEDS_DOWN):
            runner.run()
        self.assertTrue(any("draft" in m for m in runner.alerter.sent))
        self.assertTrue(any("in a row" in m for m in runner.alerter.sent))
        # shout-out was released, not burned, because nothing aired with it
        self.assertEqual([i["status"] for i in runner.pool.items], ["pending"])

    def test_started_late_skips_locked_slots(self):
        clock = FakeClock(datetime(2026, 10, 20, 22, 9, 0))   # AI2201 aired, AI2202 locked (22:08:30)
        runner, tmp = make_runner(clock)
        with fake_llm(), no_loudness(), mock.patch("airadio.show.planner.get_feeds", return_value=FEEDS_DOWN):
            runner.run()
        drafts = list((tmp.parent).glob("**/AI2201_draft_*.wav"))
        self.assertEqual(runner.status.get("AI2201"), "default")
        self.assertEqual(drafts, [])


class WhatsAppTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.clock = FakeClock(datetime(2026, 10, 20, 22, 0))
        self.pool = ShoutoutPool(tmp / "p.json", now=self.clock.now)
        self.store = UserStore(tmp / "users.json")
        self.bot = WhatsAppBot(self.store, self.pool, SongTable(SONGS), now=self.clock.now,
                               timeline_provider=lambda: read_log_csv(write_log(SHOW)))

    def test_onboarding_and_request(self):
        r = self.bot.handle("whatsapp:+1", "hi")
        self.assertIn("on-air", r)                       # on-air notice in the welcome
        r = self.bot.handle("whatsapp:+1", "Priya Ramesh")
        self.assertIn("Priya", r)
        r = self.bot.handle("whatsapp:+1", "list")
        self.assertIn("1. Kannazhaga (3)", r)
        r = self.bot.handle("whatsapp:+1", "2")
        self.assertIn("Request noted", r)
        self.assertNotIn("position", r.lower())          # no queue / ETA promise
        self.assertEqual(self.pool.items[0]["name"], "Priya")      # first name only
        self.assertIn("Vaseegara", self.pool.items[0]["summary"])
        r = self.bot.handle("whatsapp:+1", "1")
        self.assertIn("⏳", r)                            # request cooldown

    def test_bad_number(self):
        self.bot.handle("whatsapp:+1", "hi"); self.bot.handle("whatsapp:+1", "Ravi")
        self.assertIn("❌", self.bot.handle("whatsapp:+1", "99"))

    def test_free_text_goes_through_safety(self):
        self.bot.handle("whatsapp:+1", "hi"); self.bot.handle("whatsapp:+1", "Ravi")
        with fake_llm(["Thanks Ravi!"]), mock.patch("airadio.listeners.notify.send_whatsapp") as send:
            self.bot._chat_and_screen("whatsapp:+1", self.store.get("whatsapp:+1"), "Hi to amma in Sharjah!")
        send.assert_called_once()
        self.assertEqual(self.pool.items[-1]["area"], "Sharjah")
        self.assertEqual(self.pool.items[-1]["summary"], "says hi to her mother")

    def test_blocked_text_never_pooled(self):
        self.bot.handle("whatsapp:+1", "hi"); self.bot.handle("whatsapp:+1", "Ravi")
        with fake_llm(["ok"]), mock.patch("airadio.listeners.notify.send_whatsapp"):
            self.bot._chat_and_screen("whatsapp:+1", self.store.get("whatsapp:+1"), "vote for my party www.x.com")
        self.assertEqual(self.pool.items, [])

    def test_now_command(self):
        self.bot.handle("whatsapp:+1", "hi"); self.bot.handle("whatsapp:+1", "Ravi")
        self.clock.t = datetime(2026, 10, 20, 22, 1)
        self.assertIn("Kannazhaga", self.bot.handle("whatsapp:+1", "now"))
        self.clock.t = datetime(2026, 10, 20, 22, 9, 35)
        self.assertIn("Kuyil", self.bot.handle("whatsapp:+1", "now"))

    def test_legacy_user_gets_name_check(self):
        self.store.users["whatsapp:+9"] = {"name": "Nivedh", "songs": [], "has_requested": True, "history": []}
        self.bot.handle("whatsapp:+9", "hello")
        self.assertTrue(self.store.get("whatsapp:+9")["name_ok"])


if __name__ == "__main__":
    unittest.main()
